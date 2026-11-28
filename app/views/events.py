import csv
import io

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, url_for

from .. import audit
from ..auth import can, login_required, require
from ..db import get_db, transaction
from ..i18n import _, type_name
from ..utils import parse_date, parse_time, photos_for, store_photos, to_int
from .reservations import BASE_SQL as RES_SQL, annotate_clashes

bp = Blueprint("events", __name__, url_prefix="/events")

FIELDS = ("title", "type_id", "event_date", "start_time", "end_time", "location", "description",
          "other_participants", "gallery")
GALLERY_MODES = ("off", "members", "public")

EVENT_SQL = ("SELECT e.*, t.name_sk, t.name_en, t.color, cu.full_name AS creator_name"
             " FROM events e JOIN event_types t ON t.id = e.type_id"
             " LEFT JOIN users cu ON cu.id = e.created_by ")


def can_edit_event(event):
    if event is None or g.user is None:
        return False
    if can("event.edit_any"):
        return True
    return can("event.create") and event["created_by"] == g.user["id"]


def load(db, event_id):
    row = db.execute(EVENT_SQL + "WHERE e.id = ?", (event_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def participants(db, event_id):
    return db.execute("SELECT u.id, u.full_name FROM event_participants p JOIN users u"
                      " ON u.id = p.user_id WHERE p.event_id = ? ORDER BY u.full_name",
                      (event_id,)).fetchall()


def participant_names(db, event_id):
    return sorted(r["full_name"] for r in participants(db, event_id))


def types(db, include_inactive=False):
    sql = "SELECT * FROM event_types" + ("" if include_inactive else " WHERE active = 1")
    return db.execute(sql + " ORDER BY id").fetchall()


def parse_form(db, form):
    data = {
        "title": form.get("title", "").strip(),
        "type_id": to_int(form.get("type_id")),
        "event_date": parse_date(form.get("event_date", "")),
        "start_time": parse_time(form.get("start_time")),
        "end_time": parse_time(form.get("end_time")),
        "location": form.get("location", "").strip(),
        "description": form.get("description", "").strip(),
        "other_participants": form.get("other_participants", "").strip(),
        "gallery": form.get("gallery") if form.get("gallery") in GALLERY_MODES else "off",
    }
    errors = []
    if not data["title"]:
        errors.append(_("Title is required."))
    if not data["type_id"] or not db.execute("SELECT 1 FROM event_types WHERE id = ?",
                                             (data["type_id"],)).fetchone():
        errors.append(_("Choose an event type."))
    if not data["event_date"]:
        errors.append(_("Enter a valid date."))
    if data["start_time"] is None or data["end_time"] is None:
        errors.append(_("Enter a valid time (HH:MM)."))
    people = sorted({to_int(x) for x in form.getlist("participants") if to_int(x)})
    if people:
        marks = ",".join("?" * len(people))
        people = [r[0] for r in db.execute(f"SELECT id FROM users WHERE id IN ({marks})", people)]
    return data, people, errors


def query_events(db, args):
    where, params = [], []
    if not args.get("archived"):
        where.append("e.archived = 0")
    if to_int(args.get("type")):
        where.append("e.type_id = ?")
        params.append(to_int(args.get("type")))
    if to_int(args.get("user")):
        where.append("(e.id IN (SELECT event_id FROM event_participants WHERE user_id = ?)"
                     " OR e.created_by = ?)")
        params += [to_int(args.get("user"))] * 2
    if parse_date(args.get("from", "")):
        where.append("e.event_date >= ?")
        params.append(parse_date(args["from"]))
    if parse_date(args.get("to", "")):
        where.append("e.event_date <= ?")
        params.append(parse_date(args["to"]))
    q = args.get("q", "").strip()
    if q:
        where.append("(e.title LIKE ? OR e.description LIKE ? OR e.location LIKE ?"
                     " OR e.other_participants LIKE ?)")
        params += [f"%{q}%"] * 4
    sql = EVENT_SQL + ("WHERE " + " AND ".join(where) if where else "")
    return db.execute(sql + " ORDER BY e.event_date DESC, e.start_time DESC LIMIT 1000",
                      params).fetchall()


@bp.route("/")
@login_required
def index():
    db = get_db()
    rows = query_events(db, request.args)
    people = {}
    if rows:
        ids = [r["id"] for r in rows]
        marks = ",".join("?" * len(ids))
        for p in db.execute(f"SELECT p.event_id, u.full_name FROM event_participants p JOIN users u"
                            f" ON u.id = p.user_id WHERE p.event_id IN ({marks})"
                            " ORDER BY u.full_name", ids):
            people.setdefault(p["event_id"], []).append(p["full_name"])
    users = db.execute("SELECT id, full_name FROM users ORDER BY full_name").fetchall()
    return render_template("events/index.html", rows=rows, people=people, types=types(db, True),
                           users=users, args=request.args)


@bp.route("/export.csv")
@login_required
def export_csv():
    db = get_db()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "date", "start", "end", "type", "title", "location", "participants",
                "other_participants", "description", "created_by", "created_at", "updated_at"])
    for r in query_events(db, request.args):
        w.writerow([r["id"], r["event_date"], r["start_time"], r["end_time"], type_name(r),
                    r["title"], r["location"], "; ".join(participant_names(db, r["id"])),
                    r["other_participants"], r["description"], r["creator_name"],
                    r["created_at"], r["updated_at"]])
    return Response("﻿" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=events.csv"})


def _form_context(db, event=None, selected=None):
    return {"types": types(db), "users": db.execute(
        "SELECT id, full_name FROM users WHERE active = 1 ORDER BY full_name").fetchall(),
        "selected": set(selected or [])}


@bp.route("/new", methods=["GET", "POST"])
@require("event.create")
def new():
    db = get_db()
    if request.method == "POST":
        data, people, errors = parse_form(db, request.form)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("events/form.html", event=request.form, editing=False,
                                   **_form_context(db, selected=people))
        now = audit.utcnow()
        with transaction(db):
            cur = db.execute(
                "INSERT INTO events (title, type_id, event_date, start_time, end_time, location,"
                " description, other_participants, gallery, created_at, created_by, updated_at,"
                " updated_by) VALUES (:title, :type_id, :event_date, :start_time, :end_time,"
                " :location, :description, :other_participants, :gallery, :now, :uid, :now, :uid)",
                dict(data, now=now, uid=g.user["id"]))
            event_id = cur.lastrowid
            db.executemany("INSERT INTO event_participants (event_id, user_id) VALUES (?, ?)",
                           [(event_id, uid) for uid in people])
            after = dict(data, participants=participant_names(db, event_id))
            audit.record(db, "create", "event", event_id, summary=data["title"], after=after)
            _saved, rejected = store_photos(db, "event", event_id, request.files.getlist("photos"),
                                            g.user)
        for name in rejected:
            flash(_("{name} is not a supported image and was skipped.", name=name), "warning")
        flash(_("Event saved."), "success")
        return redirect(url_for("events.detail", event_id=event_id))
    selected = [g.user["id"]]
    return render_template("events/form.html", event={}, editing=False,
                           **_form_context(db, selected=selected))


@bp.route("/<int:event_id>")
@login_required
def detail(event_id):
    db = get_db()
    event = load(db, event_id)
    reservations = annotate_clashes(db, db.execute(
        RES_SQL + "WHERE r.event_id = ? ORDER BY r.start_at", (event_id,)).fetchall())
    from .rosters import can_manage, event_slots, roles
    slots = event_slots(db, event_id)
    return render_template("events/detail.html", event=event, people=participants(db, event_id),
                           photos=photos_for(db, "event", event_id), reservations=reservations,
                           history=audit.history(db, "event", event_id),
                           can_edit=can_edit_event(event), slots=slots,
                           can_roster=can_manage(event), roster_roles=roles(db),
                           active_users=db.execute("SELECT id, full_name FROM users WHERE active = 1"
                                                   " AND role != 'auditor' ORDER BY full_name"
                                                   ).fetchall())


@bp.route("/<int:event_id>/edit", methods=["GET", "POST"])
@login_required
def edit(event_id):
    db = get_db()
    event = load(db, event_id)
    if not can_edit_event(event):
        abort(403)
    current_people = [p["id"] for p in participants(db, event_id)]
    if request.method == "POST":
        data, people, errors = parse_form(db, request.form)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("events/form.html", event=request.form, editing=True,
                                   event_id=event_id, **_form_context(db, selected=people))
        before = dict({k: event[k] for k in FIELDS}, participants=participant_names(db, event_id))
        with transaction(db):
            db.execute("UPDATE events SET title=:title, type_id=:type_id, event_date=:event_date,"
                       " start_time=:start_time, end_time=:end_time, location=:location,"
                       " description=:description, other_participants=:other_participants,"
                       " gallery=:gallery, updated_at=:now, updated_by=:uid WHERE id=:id",
                       dict(data, now=audit.utcnow(), uid=g.user["id"], id=event_id))
            db.execute("DELETE FROM event_participants WHERE event_id = ?", (event_id,))
            db.executemany("INSERT INTO event_participants (event_id, user_id) VALUES (?, ?)",
                           [(event_id, uid) for uid in people])
            after = dict(data, participants=participant_names(db, event_id))
            if audit.diff(before, after):
                audit.record(db, "update", "event", event_id, summary=data["title"],
                             before=before, after=after)
            _saved, rejected = store_photos(db, "event", event_id, request.files.getlist("photos"),
                                            g.user)
        for name in rejected:
            flash(_("{name} is not a supported image and was skipped.", name=name), "warning")
        flash(_("Changes saved."), "success")
        return redirect(url_for("events.detail", event_id=event_id))
    return render_template("events/form.html", event=event, editing=True, event_id=event_id,
                           **_form_context(db, selected=current_people))


@bp.route("/<int:event_id>/photos", methods=["POST"])
@login_required
def add_photos(event_id):
    db = get_db()
    event = load(db, event_id)
    if not can_edit_event(event):
        abort(403)
    with transaction(db):
        saved, rejected = store_photos(db, "event", event_id, request.files.getlist("photos"), g.user)
    for name in rejected:
        flash(_("{name} is not a supported image and was skipped.", name=name), "warning")
    if saved:
        flash(_("Photos uploaded."), "success")
    return redirect(url_for("events.detail", event_id=event_id))


@bp.route("/<int:event_id>/archive", methods=["POST"])
@require("event.edit_any")
def archive(event_id):
    db = get_db()
    event = load(db, event_id)
    new_value = 0 if event["archived"] else 1
    reason = request.form.get("reason", "").strip()
    with transaction(db):
        db.execute("UPDATE events SET archived = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                   (new_value, audit.utcnow(), g.user["id"], event_id))
        audit.record(db, "archive" if new_value else "restore", "event", event_id,
                     summary=event["title"] + (f" — {reason}" if reason else ""),
                     before={"archived": event["archived"]}, after={"archived": new_value})
    flash(_("Event archived.") if new_value else _("Event restored."), "success")
    return redirect(url_for("events.detail", event_id=event_id))
