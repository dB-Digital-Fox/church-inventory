import csv
import io

import qrcode
from flask import (Blueprint, Response, abort, current_app, flash, g, redirect, render_template,
                   request, url_for)

from .. import audit
from ..auth import can, login_required, require
from ..db import get_db, transaction
from ..i18n import _
from ..utils import parse_date, photos_for, store_photos, to_int
from .reservations import BASE_SQL as RES_SQL, CONDITIONS, annotate_clashes

bp = Blueprint("items", __name__, url_prefix="/items")

FIELDS = ("name", "category", "location", "quantity", "serial_number", "purchase_date",
          "purchase_value", "condition", "description", "reservable")


def load(db, item_id):
    item = db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if item is None:
        abort(404)
    return item


def suggestions(db):
    cats = [r[0] for r in db.execute("SELECT DISTINCT category FROM items WHERE category != ''"
                                     " ORDER BY category")]
    locs = [r[0] for r in db.execute("SELECT DISTINCT location FROM items WHERE location != ''"
                                     " ORDER BY location")]
    return cats, locs


def parse_form(form):
    data = {
        "name": form.get("name", "").strip(),
        "category": form.get("category", "").strip(),
        "location": form.get("location", "").strip(),
        "quantity": to_int(form.get("quantity"), None, minimum=0),
        "serial_number": form.get("serial_number", "").strip(),
        "purchase_date": form.get("purchase_date", "").strip(),
        "purchase_value": form.get("purchase_value", "").strip(),
        "condition": form.get("condition", "good"),
        "description": form.get("description", "").strip(),
        "reservable": 1 if form.get("reservable") else 0,
    }
    errors = []
    if not data["name"]:
        errors.append(_("Name is required."))
    if data["quantity"] is None:
        errors.append(_("Quantity must be a whole number (0 or more)."))
    if data["purchase_date"] and not parse_date(data["purchase_date"]):
        errors.append(_("Enter a valid date."))
    if data["condition"] not in CONDITIONS:
        data["condition"] = "good"
    return data, errors


def query_items(db, args):
    where, params = [], []
    if not args.get("archived"):
        where.append("archived = 0")
    q = args.get("q", "").strip()
    if q:
        where.append("(name LIKE ? OR description LIKE ? OR serial_number LIKE ? OR category LIKE ?"
                     " OR location LIKE ?)")
        params += [f"%{q}%"] * 5
    for field in ("category", "location", "condition"):
        if args.get(field):
            where.append(f"{field} = ?")
            params.append(args[field])
    sql = "SELECT * FROM items" + (" WHERE " + " AND ".join(where) if where else "")
    return db.execute(sql + " ORDER BY name COLLATE NOCASE", params).fetchall()


@bp.route("/")
@login_required
def index():
    db = get_db()
    rows = query_items(db, request.args)
    thumbs = {r["entity_id"]: r["filename"] for r in db.execute(
        "SELECT entity_id, MIN(id), filename FROM photos WHERE entity = 'item' AND hidden = 0"
        " GROUP BY entity_id")}
    cats, locs = suggestions(db)
    return render_template("items/index.html", rows=rows, thumbs=thumbs, cats=cats, locs=locs,
                           conditions=CONDITIONS, args=request.args)


@bp.route("/export.csv")
@login_required
def export_csv():
    db = get_db()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id"] + list(FIELDS) + ["archived", "created_at", "updated_at"])
    for r in query_items(db, request.args):
        w.writerow([r["id"]] + [r[f] for f in FIELDS] + [r["archived"], r["created_at"], r["updated_at"]])
    return Response("﻿" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=inventory.csv"})


@bp.route("/new", methods=["GET", "POST"])
@require("item.edit")
def new():
    db = get_db()
    if request.method == "POST":
        data, errors = parse_form(request.form)
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            now = audit.utcnow()
            with transaction(db):
                cur = db.execute(
                    "INSERT INTO items (name, category, location, quantity, serial_number,"
                    " purchase_date, purchase_value, condition, description, reservable,"
                    " created_at, created_by, updated_at, updated_by) VALUES (:name, :category,"
                    " :location, :quantity, :serial_number, :purchase_date, :purchase_value,"
                    " :condition, :description, :reservable, :now, :uid, :now, :uid)",
                    dict(data, now=now, uid=g.user["id"]))
                item_id = cur.lastrowid
                audit.record(db, "create", "item", item_id, summary=data["name"], after=data)
                _saved, rejected = store_photos(db, "item", item_id,
                                                request.files.getlist("photos"), g.user)
            for name in rejected:
                flash(_("{name} is not a supported image and was skipped.", name=name), "warning")
            flash(_("Item added."), "success")
            return redirect(url_for("items.detail", item_id=item_id))
    cats, locs = suggestions(db)
    return render_template("items/form.html", item=request.form or {"quantity": 1, "reservable": 1},
                           cats=cats, locs=locs, conditions=CONDITIONS, editing=False)


@bp.route("/<int:item_id>")
@login_required
def detail(item_id):
    db = get_db()
    item = load(db, item_id)
    reservations = annotate_clashes(db, db.execute(
        RES_SQL + "WHERE r.item_id = ? AND r.status IN ('pending','approved','checked_out')"
        " ORDER BY r.start_at", (item_id,)).fetchall())
    past = db.execute(RES_SQL + "WHERE r.item_id = ? AND r.status IN ('returned')"
                      " ORDER BY r.start_at DESC LIMIT 20", (item_id,)).fetchall()
    return render_template("items/detail.html", item=item, photos=photos_for(db, "item", item_id),
                           reservations=reservations, past=past,
                           history=audit.history(db, "item", item_id))


@bp.route("/<int:item_id>/edit", methods=["GET", "POST"])
@require("item.edit")
def edit(item_id):
    db = get_db()
    item = load(db, item_id)
    if request.method == "POST":
        data, errors = parse_form(request.form)
        if errors:
            for e in errors:
                flash(e, "error")
        else:
            before = {k: item[k] for k in FIELDS}
            with transaction(db):
                db.execute(
                    "UPDATE items SET name=:name, category=:category, location=:location,"
                    " quantity=:quantity, serial_number=:serial_number, purchase_date=:purchase_date,"
                    " purchase_value=:purchase_value, condition=:condition, description=:description,"
                    " reservable=:reservable, updated_at=:now, updated_by=:uid WHERE id=:id",
                    dict(data, now=audit.utcnow(), uid=g.user["id"], id=item_id))
                if audit.diff(before, data):
                    audit.record(db, "update", "item", item_id, summary=data["name"],
                                 before=before, after=data)
                _saved, rejected = store_photos(db, "item", item_id,
                                                request.files.getlist("photos"), g.user)
            for name in rejected:
                flash(_("{name} is not a supported image and was skipped.", name=name), "warning")
            flash(_("Changes saved."), "success")
            return redirect(url_for("items.detail", item_id=item_id))
    cats, locs = suggestions(db)
    return render_template("items/form.html", item=request.form if request.method == "POST" else item,
                           cats=cats, locs=locs, conditions=CONDITIONS, editing=True, item_id=item_id)


@bp.route("/<int:item_id>/archive", methods=["POST"])
@require("item.edit")
def archive(item_id):
    db = get_db()
    item = load(db, item_id)
    new_value = 0 if item["archived"] else 1
    reason = request.form.get("reason", "").strip()
    with transaction(db):
        db.execute("UPDATE items SET archived = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                   (new_value, audit.utcnow(), g.user["id"], item_id))
        audit.record(db, "archive" if new_value else "restore", "item", item_id,
                     summary=item["name"] + (f" — {reason}" if reason else ""),
                     before={"archived": item["archived"]}, after={"archived": new_value})
    flash(_("Item archived.") if new_value else _("Item restored."), "success")
    return redirect(url_for("items.detail", item_id=item_id))


@bp.route("/<int:item_id>/photos", methods=["POST"])
@require("item.edit")
def add_photos(item_id):
    db = get_db()
    load(db, item_id)
    with transaction(db):
        saved, rejected = store_photos(db, "item", item_id, request.files.getlist("photos"), g.user)
    for name in rejected:
        flash(_("{name} is not a supported image and was skipped.", name=name), "warning")
    if saved:
        flash(_("Photos uploaded."), "success")
    return redirect(url_for("items.detail", item_id=item_id))


@bp.route("/photos/<int:photo_id>/hide", methods=["POST"])
@login_required
def hide_photo(photo_id):
    """Photos are evidence: they are hidden, never deleted, and the file stays on disk."""
    from .events import can_edit_event
    db = get_db()
    photo = db.execute("SELECT * FROM photos WHERE id = ?", (photo_id,)).fetchone()
    if photo is None:
        abort(404)
    if photo["entity"] == "item":
        if not can("item.edit"):
            abort(403)
        target = url_for("items.detail", item_id=photo["entity_id"])
    else:
        event = db.execute("SELECT * FROM events WHERE id = ?", (photo["entity_id"],)).fetchone()
        if not can_edit_event(event):
            abort(403)
        target = url_for("events.detail", event_id=photo["entity_id"])
    with transaction(db):
        db.execute("UPDATE photos SET hidden = 1 WHERE id = ?", (photo_id,))
        audit.record(db, "photo_hide", photo["entity"], photo["entity_id"],
                     summary=photo["original_name"] or photo["filename"],
                     changes={"photo": [photo["filename"], None], "sha256": [photo["sha256"], None]})
    flash(_("Photo removed from view. The file is kept for the audit trail."), "success")
    return redirect(target)


def item_url(item_id):
    base = current_app.config["BASE_URL"].rstrip("/") or request.host_url.rstrip("/")
    return f"{base}{url_for('items.detail', item_id=item_id)}"


@bp.route("/<int:item_id>/qr.png")
@login_required
def qr(item_id):
    load(get_db(), item_id)
    img = qrcode.make(item_url(item_id), box_size=8, border=2)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return Response(buf.getvalue(), mimetype="image/png",
                    headers={"Cache-Control": "private, max-age=86400"})


@bp.route("/labels")
@login_required
def labels():
    db = get_db()
    ids = [to_int(x) for x in request.args.getlist("id") if to_int(x)]
    if ids:
        marks = ",".join("?" * len(ids))
        rows = db.execute(f"SELECT * FROM items WHERE id IN ({marks}) ORDER BY name", ids).fetchall()
    else:
        rows = query_items(db, request.args)
    copies = min(to_int(request.args.get("copies"), 1, minimum=1), 20)
    return render_template("items/labels.html", rows=rows, copies=copies)
