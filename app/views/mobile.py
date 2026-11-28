"""Phone app (PWA): scan a QR label, record a check offline, sync when back online."""
import json
import os
import re
from datetime import datetime

from flask import Blueprint, Response, current_app, g, jsonify, render_template, request

from .. import audit
from ..auth import login_required
from ..db import get_db, transaction
from ..i18n import _
from .reservations import CONDITIONS

bp = Blueprint("mobile", __name__)

UID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_BATCH = 200


@bp.route("/m/")
@login_required
def home():
    db = get_db()
    recent = db.execute(
        "SELECT c.*, i.name AS item_name FROM item_checks c JOIN items i ON i.id = c.item_id"
        " WHERE c.user_id = ? ORDER BY c.id DESC LIMIT 10", (g.user["id"],)).fetchall()
    return render_template("mobile/index.html", conditions=CONDITIONS, recent=recent)


@bp.route("/m/items.json")
@login_required
def items_json():
    """Item names for offline lookup after scanning a label."""
    db = get_db()
    rows = db.execute("SELECT id, name, kind, location, condition, quantity FROM items"
                      " WHERE archived = 0 ORDER BY id").fetchall()
    resp = jsonify({"items": [dict(r) for r in rows], "user": g.user["full_name"]})
    resp.headers["Cache-Control"] = "private, no-cache"
    return resp


def _valid_time(value):
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M")
        return True
    except (TypeError, ValueError):
        return False


@bp.route("/m/sync", methods=["POST"])
def sync():
    if g.user is None:
        return jsonify({"ok": False, "error": "login"}), 401
    if g.user["role"] == "auditor":
        return jsonify({"ok": False, "error": "forbidden"}), 403
    payload = request.get_json(silent=True) or {}
    entries = payload.get("entries") or []
    if not isinstance(entries, list) or len(entries) > MAX_BATCH:
        return jsonify({"ok": False, "error": "bad request"}), 400
    db = get_db()
    saved, duplicate, errors = [], [], {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        uid = str(e.get("uid", ""))
        if not UID.match(uid):
            continue
        if db.execute("SELECT 1 FROM item_checks WHERE uid = ?", (uid,)).fetchone():
            duplicate.append(uid)  # already received earlier: the phone can forget it
            continue
        try:
            item_id = int(e.get("item_id"))
        except (TypeError, ValueError):
            item_id = 0
        item = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        checked_at = str(e.get("checked_at", ""))[:16]
        condition = e.get("condition") if e.get("condition") in CONDITIONS else ""
        location = str(e.get("location", "")).strip()[:200]
        note = str(e.get("note", "")).strip()[:1000]
        if item is None:
            errors[uid] = _("Item #{id} does not exist.", id=e.get("item_id"))
            continue
        if not _valid_time(checked_at):
            errors[uid] = _("Enter a valid date.")
            continue
        now = audit.utcnow()
        with transaction(db):
            cur = db.execute(
                "INSERT INTO item_checks (uid, item_id, checked_at, condition, location, note, user_id,"
                " synced_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uid, item_id, checked_at, condition, location, note, g.user["id"], now))
            audit.record(db, "check", "item", item_id,
                         summary=f"{item['name']} — {checked_at.replace('T', ' ')}"
                                 + (f" — {note}" if note else ""),
                         after={"condition": condition, "location": location, "note": note,
                                "checked_at": checked_at, "check_id": cur.lastrowid})
            changes = {}
            if condition and condition != item["condition"]:
                changes["condition"] = condition
            if location and location != item["location"]:
                changes["location"] = location
            if changes:
                db.execute("UPDATE items SET " + ", ".join(f"{k} = :{k}" for k in changes)
                           + ", updated_at = :now, updated_by = :uid WHERE id = :id",
                           dict(changes, now=now, uid=g.user["id"], id=item_id))
                audit.record(db, "update", "item", item_id, summary=item["name"],
                             before={k: item[k] for k in changes}, after=changes)
        saved.append(uid)
    return jsonify({"ok": True, "saved": saved, "duplicate": duplicate, "errors": errors})


@bp.route("/sw.js")
def service_worker():
    """Served from the root so it may control every page; the cache name follows the version."""
    path = os.path.join(current_app.static_folder, "sw.js")
    with open(path, encoding="utf-8") as f:
        body = f.read().replace("__VERSION__", current_app.config["ASSET_VERSION"])
    return Response(body, mimetype="application/javascript",
                    headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


@bp.route("/manifest.webmanifest")
def manifest():
    name = current_app.config["ORG_NAME"]
    data = {
        "name": name, "short_name": name[:12], "lang": "sk",
        "start_url": "/m/", "scope": "/", "display": "standalone",
        "background_color": "#f6f5f9", "theme_color": "#5b21b6",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png",
             "purpose": "maskable"},
        ],
    }
    return Response(json.dumps(data), mimetype="application/manifest+json")
