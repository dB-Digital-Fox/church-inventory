import os
import re

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request,
                   send_from_directory, url_for)

from .. import audit, notify
from ..auth import require
from ..backup import list_backups
from ..db import get_db, transaction
from ..i18n import _, translate as tr
from ..scheduler import run_backup
from ..utils import to_int

bp = Blueprint("admin", __name__, url_prefix="/admin")

COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


@bp.route("/")
@require("admin")
def index():
    db = get_db()
    cfg = current_app.config
    db_size = os.path.getsize(cfg["DATABASE"]) if os.path.exists(cfg["DATABASE"]) else 0
    return render_template(
        "admin/index.html",
        types=db.execute("SELECT * FROM event_types ORDER BY id").fetchall(),
        roster_roles=db.execute("SELECT * FROM roster_roles ORDER BY id").fetchall(),
        backups=list_backups(cfg["BACKUP_DIR"]),
        email_on=notify.email_enabled(cfg), whatsapp_on=notify.whatsapp_enabled(cfg),
        db_size=db_size, audit_count=db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
        backup_dir=cfg["BACKUP_DIR"], backup_hour=cfg["BACKUP_HOUR"], backup_keep=cfg["BACKUP_KEEP"])


@bp.route("/event-types", methods=["POST"])
@require("admin")
def event_type_save():
    db = get_db()
    f = request.form
    type_id = to_int(f.get("id"))
    data = {"name_sk": f.get("name_sk", "").strip(), "name_en": f.get("name_en", "").strip(),
            "color": f.get("color", "#6b7280"), "active": 1 if f.get("active") else 0}
    if not data["name_sk"] or not data["name_en"]:
        flash(_("Enter the name in both languages."), "error")
        return redirect(url_for("admin.index"))
    if not COLOR.match(data["color"]):
        data["color"] = "#6b7280"
    with transaction(db):
        if type_id:
            before = db.execute("SELECT name_sk, name_en, color, active FROM event_types WHERE id = ?",
                                (type_id,)).fetchone()
            if before is None:
                abort(404)
            db.execute("UPDATE event_types SET name_sk=:name_sk, name_en=:name_en, color=:color,"
                       " active=:active WHERE id=:id", dict(data, id=type_id))
            audit.record(db, "update", "event_type", type_id, summary=data["name_en"],
                         before=dict(before), after=data)
        else:
            data["active"] = 1
            cur = db.execute("INSERT INTO event_types (name_sk, name_en, color, active) VALUES"
                             " (:name_sk, :name_en, :color, :active)", data)
            audit.record(db, "create", "event_type", cur.lastrowid, summary=data["name_en"], after=data)
    flash(_("Changes saved."), "success")
    return redirect(url_for("admin.index"))


@bp.route("/roster-roles", methods=["POST"])
@require("admin")
def roster_role_save():
    db = get_db()
    f = request.form
    role_id = to_int(f.get("id"))
    data = {"name_sk": f.get("name_sk", "").strip(), "name_en": f.get("name_en", "").strip(),
            "active": 1 if f.get("active") or not role_id else 0}
    if not data["name_sk"] or not data["name_en"]:
        flash(_("Enter the name in both languages."), "error")
        return redirect(url_for("admin.index") + "#roster-roles")
    with transaction(db):
        if role_id:
            before = db.execute("SELECT name_sk, name_en, active FROM roster_roles WHERE id = ?",
                                (role_id,)).fetchone()
            if before is None:
                abort(404)
            db.execute("UPDATE roster_roles SET name_sk=:name_sk, name_en=:name_en, active=:active"
                       " WHERE id=:id", dict(data, id=role_id))
            audit.record(db, "update", "roster_role", role_id, summary=data["name_en"],
                         before=dict(before), after=data)
        else:
            cur = db.execute("INSERT INTO roster_roles (name_sk, name_en, active) VALUES"
                             " (:name_sk, :name_en, :active)", data)
            audit.record(db, "create", "roster_role", cur.lastrowid, summary=data["name_en"],
                         after=data)
    flash(_("Changes saved."), "success")
    return redirect(url_for("admin.index") + "#roster-roles")


@bp.route("/backup", methods=["POST"])
@require("admin")
def backup_now():
    try:
        path = run_backup(current_app._get_current_object())
    except OSError as exc:
        flash(_("Backup failed: {error}", error=str(exc)), "error")
    else:
        flash(_("Backup created: {name}", name=os.path.basename(path)), "success")
    return redirect(url_for("admin.index"))


@bp.route("/backup/<name>")
@require("admin")
def backup_download(name):
    if not re.fullmatch(r"church-inventory-\d{8}-\d{6}\.tar\.gz", name):
        abort(404)
    db = get_db()
    with transaction(db):
        audit.record(db, "download", "backup", None, summary=name, changes={})
    return send_from_directory(current_app.config["BACKUP_DIR"], name, as_attachment=True)


@bp.route("/test-notification", methods=["POST"])
@require("admin")
def test_notification():
    cfg = dict(current_app.config)
    user = dict(g.user)
    user["notify_email"] = 1
    user["notify_whatsapp"] = 1
    results = notify.deliver(cfg, [user], lambda lang: (
        tr("Test notification", lang),
        tr("If you can read this, notifications from {org} work.", lang, org=cfg["ORG_NAME"])))
    if not results:
        flash(_("Nothing was sent. Check that email/WhatsApp is configured and that your profile "
                "has an email address and phone number."), "warning")
    for who, channel, status in results:
        flash(f"{channel}: {status}", "success" if status == "ok" else "error")
    return redirect(url_for("admin.index"))
