"""Volunteer rosters: who reads, serves, sings or cleans at which event, with shift swaps."""
from datetime import timedelta

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request,
                   url_for)

from .. import audit
from ..auth import can, login_required
from ..db import get_db, transaction
from ..i18n import _, translate as tr, type_name
from ..notify import notify
from ..utils import fmt_local, now_local, to_int
from .events import can_edit_event, load as load_event

bp = Blueprint("rosters", __name__, url_prefix="/rosters")

SLOT_SQL = ("SELECT s.*, r.name_sk AS role_sk, r.name_en AS role_en, u.full_name, u.username,"
            " e.title AS event_title, e.event_date, e.start_time, e.end_time, e.location,"
            " e.created_by AS event_created_by, e.archived AS event_archived"
            " FROM roster_slots s JOIN roster_roles r ON r.id = s.role_id"
            " JOIN events e ON e.id = s.event_id LEFT JOIN users u ON u.id = s.user_id ")


def role_name(row, lang=None):
    return type_name({"name_sk": row["role_sk"], "name_en": row["role_en"]}, lang)


def roles(db, include_inactive=False):
    sql = "SELECT * FROM roster_roles" + ("" if include_inactive else " WHERE active = 1")
    return db.execute(sql + " ORDER BY id").fetchall()


def event_slots(db, event_id):
    return db.execute(SLOT_SQL + "WHERE s.event_id = ? ORDER BY r.id, s.id", (event_id,)).fetchall()


def upcoming_slots(db, days=60, user_id=None, need_only=False):
    today = now_local().strftime("%Y-%m-%d")
    until = (now_local() + timedelta(days=days)).strftime("%Y-%m-%d")
    sql = SLOT_SQL + "WHERE e.archived = 0 AND e.event_date BETWEEN ? AND ?"
    params = [today, until]
    if user_id:
        sql += " AND s.user_id = ?"
        params.append(user_id)
    if need_only:
        sql += " AND (s.user_id IS NULL OR s.swap_requested = 1)"
    return db.execute(sql + " ORDER BY e.event_date, e.start_time, r.id, s.id", params).fetchall()


def slot_summary(slot, lang=None):
    when = fmt_local(slot["event_date"]) + (f" {slot['start_time']}" if slot["start_time"] else "")
    return f"{role_name(slot, lang)} — {slot['event_title']}, {when}"


def can_manage(event):
    return can("roster.manage") or can_edit_event(event)


def _slot(db, slot_id):
    row = db.execute(SLOT_SQL + "WHERE s.id = ?", (slot_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def _event_link(event_id):
    base = current_app.config["BASE_URL"].rstrip("/") or request.host_url.rstrip("/")
    return f"{base}{url_for('events.detail', event_id=event_id)}#roster"


def _tell(users, subject_key, slot, extra=""):
    users = [u for u in users if u is not None and u["id"] != g.user["id"]]
    if not users:
        return
    link = _event_link(slot["event_id"])
    notify(current_app._get_current_object(), users, lambda lang: (
        tr(subject_key, lang),
        slot_summary(slot, lang) + (f"\n{extra}" if extra else "") + "\n" + link))


def _user(db, user_id):
    if not user_id:
        return None
    return db.execute("SELECT * FROM users WHERE id = ? AND active = 1", (user_id,)).fetchone()


def _managers(db):
    return db.execute("SELECT * FROM users WHERE active = 1 AND role IN ('admin','manager')").fetchall()


def _back(event_id):
    return redirect(request.form.get("next") or url_for("events.detail", event_id=event_id) + "#roster")


def _person_name(db, user_id):
    if not user_id:
        return ""
    row = db.execute("SELECT full_name FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["full_name"] if row else str(user_id)


def _update(db, slot, changes, action, summary=None):
    before = {k: slot[k] for k in changes}
    after = dict(changes)
    if "user_id" in changes:  # the log shows names, not database ids
        before["user_id"] = _person_name(db, slot["user_id"])
        after["user_id"] = _person_name(db, changes["user_id"])
    db.execute("UPDATE roster_slots SET " + ", ".join(f"{k} = :{k}" for k in changes)
               + ", updated_at = :now, updated_by = :uid WHERE id = :id",
               dict(changes, now=audit.utcnow(), uid=g.user["id"], id=slot["id"]))
    audit.record(db, action, "roster", slot["id"], summary=summary or slot_summary(slot),
                 before=before, after=after)


@bp.route("/")
@login_required
def index():
    db = get_db()
    slots = upcoming_slots(db, days=to_int(request.args.get("days"), 60, minimum=7))
    events = {}
    for s in slots:
        events.setdefault(s["event_id"], []).append(s)
    mine = [s for s in slots if s["user_id"] == g.user["id"]]
    needed = [s for s in slots if s["user_id"] is None or s["swap_requested"]]
    return render_template("rosters/index.html", events=events, mine=mine, needed=needed)


@bp.route("/event/<int:event_id>/add", methods=["POST"])
@login_required
def add(event_id):
    db = get_db()
    event = load_event(db, event_id)
    if not can_manage(event):
        abort(403)
    role_id = to_int(request.form.get("role_id"))
    if not db.execute("SELECT 1 FROM roster_roles WHERE id = ?", (role_id,)).fetchone():
        flash(_("Choose a role."), "error")
        return _back(event_id)
    count = to_int(request.form.get("count"), 1, minimum=1)
    count = min(count, 20)
    person = _user(db, to_int(request.form.get("user_id")))
    now = audit.utcnow()
    with transaction(db):
        new_ids = []
        for i in range(count):
            uid = person["id"] if person and i == 0 else None
            cur = db.execute("INSERT INTO roster_slots (event_id, role_id, user_id, created_at,"
                             " created_by, updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
                             (event_id, role_id, uid, now, g.user["id"], now, g.user["id"]))
            new_ids.append(cur.lastrowid)
        for sid in new_ids:
            slot = _slot(db, sid)
            audit.record(db, "create", "roster", sid, summary=slot_summary(slot),
                         after={"event_id": event_id, "role_id": role_id,
                                "user_id": slot["full_name"] or ""})
    if person:
        _tell([person], "You were added to the roster", _slot(db, new_ids[0]))
    flash(_("Roster updated."), "success")
    return _back(event_id)


@bp.route("/<int:slot_id>/assign", methods=["POST"])
@login_required
def assign(slot_id):
    db = get_db()
    slot = _slot(db, slot_id)
    if not can_manage(load_event(db, slot["event_id"])):
        abort(403)
    person = _user(db, to_int(request.form.get("user_id")))
    previous = _user(db, slot["user_id"])
    with transaction(db):
        _update(db, slot, {"user_id": person["id"] if person else None, "swap_requested": 0,
                           "swap_note": ""}, "assign")
    fresh = _slot(db, slot_id)
    if person and (not previous or previous["id"] != person["id"]):
        _tell([person], "You were added to the roster", fresh)
    if previous and (not person or previous["id"] != person["id"]):
        _tell([previous], "You were taken off the roster", slot)
    flash(_("Roster updated."), "success")
    return _back(slot["event_id"])


@bp.route("/<int:slot_id>/remove", methods=["POST"])
@login_required
def remove(slot_id):
    db = get_db()
    slot = _slot(db, slot_id)
    if not can_manage(load_event(db, slot["event_id"])):
        abort(403)
    with transaction(db):
        db.execute("DELETE FROM roster_slots WHERE id = ?", (slot_id,))
        audit.record(db, "delete", "roster", slot_id, summary=slot_summary(slot),
                     before={"role_id": role_name(slot), "user_id": slot["full_name"] or ""},
                     after={})
    if slot["user_id"]:
        _tell([_user(db, slot["user_id"])], "You were taken off the roster", slot)
    flash(_("Roster updated."), "success")
    return _back(slot["event_id"])


def _open_for_me(slot):
    return slot["user_id"] is None or (slot["swap_requested"] and slot["user_id"] != g.user["id"])


@bp.route("/<int:slot_id>/take", methods=["POST"])
@login_required
def take(slot_id):
    """Sign up for an open place, or take over from someone who asked for a replacement."""
    db = get_db()
    slot = _slot(db, slot_id)
    if slot["event_archived"] or g.user["role"] == "auditor":
        abort(403)
    if not _open_for_me(slot):
        flash(_("Someone else already took this place."), "warning")
        return _back(slot["event_id"])
    previous = _user(db, slot["user_id"])
    with transaction(db):
        _update(db, slot, {"user_id": g.user["id"], "swap_requested": 0, "swap_note": ""},
                "swap" if previous else "signup",
                summary=slot_summary(slot) + (f" — {previous['full_name']} → {g.user['full_name']}"
                                              if previous else f" — {g.user['full_name']}"))
    fresh = _slot(db, slot_id)
    if previous:
        _tell([previous], "Your replacement is found", fresh,
              tr("{name} takes over.", previous["language"] or "sk", name=g.user["full_name"]))
        _tell(_managers(db), "Roster swap", fresh,
              f"{previous['full_name']} → {g.user['full_name']}")
    flash(_("Thank you! You are on the roster."), "success")
    return _back(slot["event_id"])


@bp.route("/<int:slot_id>/swap", methods=["POST"])
@login_required
def request_swap(slot_id):
    """The assigned person can't come and asks for someone to take over."""
    db = get_db()
    slot = _slot(db, slot_id)
    if slot["user_id"] != g.user["id"]:
        abort(403)
    note = request.form.get("note", "").strip()[:200]
    cancel = request.form.get("cancel") == "1"
    with transaction(db):
        _update(db, slot, {"swap_requested": 0 if cancel else 1, "swap_note": "" if cancel else note},
                "swap_cancel" if cancel else "swap_request")
    if not cancel:
        # Ask the people who usually do this role, plus the managers.
        regulars = db.execute(
            "SELECT DISTINCT u.* FROM roster_slots s JOIN users u ON u.id = s.user_id"
            " WHERE s.role_id = ? AND u.active = 1 AND u.id != ?", (slot["role_id"], g.user["id"]))
        people = {u["id"]: u for u in list(regulars) + list(_managers(db))}
        _tell(list(people.values()), "Replacement needed", _slot(db, slot_id),
              f"{g.user['full_name']}: {note}" if note else g.user["full_name"])
        flash(_("Your request was sent. You stay on the roster until someone takes over."), "success")
    else:
        flash(_("Roster updated."), "success")
    return _back(slot["event_id"])


def reminders(db, tomorrow):
    """Slots for events on the given day, for the daily reminder job."""
    return db.execute(SLOT_SQL + "WHERE e.archived = 0 AND e.event_date = ? AND s.user_id IS NOT NULL",
                      (tomorrow,)).fetchall()


def gaps(db, day):
    return db.execute(SLOT_SQL + "WHERE e.archived = 0 AND e.event_date = ?"
                      " AND (s.user_id IS NULL OR s.swap_requested = 1)", (day,)).fetchall()
