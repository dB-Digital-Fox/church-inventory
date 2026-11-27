import secrets

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from .. import audit
from ..auth import ROLES, login_required, require
from ..db import get_db, transaction
from ..i18n import LANGUAGES, _
from .events import EVENT_SQL
from .reservations import BASE_SQL as RES_SQL, annotate_clashes

bp = Blueprint("users", __name__, url_prefix="/people")

FIELDS = ("full_name", "email", "phone", "role", "active", "language")


def load(db, user_id):
    row = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def temp_password():
    return secrets.token_urlsafe(9)


@bp.route("/")
@login_required
def index():
    db = get_db()
    rows = db.execute(
        "SELECT u.*, (SELECT COUNT(*) FROM event_participants p JOIN events e ON e.id = p.event_id"
        " WHERE p.user_id = u.id AND e.archived = 0) AS event_count,"
        " (SELECT MAX(e.event_date) FROM event_participants p JOIN events e ON e.id = p.event_id"
        " WHERE p.user_id = u.id AND e.archived = 0) AS last_event"
        " FROM users u ORDER BY u.active DESC, u.full_name").fetchall()
    return render_template("users/index.html", rows=rows)


@bp.route("/<int:user_id>")
@login_required
def detail(user_id):
    db = get_db()
    person = load(db, user_id)
    events = db.execute(EVENT_SQL + "WHERE e.archived = 0 AND e.id IN (SELECT event_id FROM"
                        " event_participants WHERE user_id = ?) ORDER BY e.event_date DESC",
                        (user_id,)).fetchall()
    by_type = {}
    for e in events:
        key = (e["name_sk"], e["name_en"], e["color"])
        by_type[key] = by_type.get(key, 0) + 1
    reservations = annotate_clashes(db, db.execute(
        RES_SQL + "WHERE r.user_id = ? ORDER BY r.start_at DESC LIMIT 100", (user_id,)).fetchall())
    return render_template("users/detail.html", person=person, events=events, by_type=by_type,
                           reservations=reservations,
                           history=audit.history(db, "user", user_id) if g.user["role"] in
                           ("admin", "auditor") else [])


def parse_form(form, existing=None):
    data = {
        "full_name": form.get("full_name", "").strip(),
        "email": form.get("email", "").strip(),
        "phone": form.get("phone", "").strip(),
        "role": form.get("role", "member"),
        "active": 1 if form.get("active") or existing is None else 0,
        "language": form.get("language") if form.get("language") in LANGUAGES else "",
    }
    errors = []
    if not data["full_name"]:
        errors.append(_("Full name is required."))
    if data["role"] not in ROLES:
        errors.append(_("Choose a role."))
    return data, errors


def admins_left(db, excluding):
    return db.execute("SELECT COUNT(*) FROM users WHERE role = 'admin' AND active = 1 AND id != ?",
                      (excluding,)).fetchone()[0]


@bp.route("/new", methods=["GET", "POST"])
@require("admin")
def new():
    db = get_db()
    if request.method == "POST":
        data, errors = parse_form(request.form)
        username = request.form.get("username", "").strip()
        if not username or not username.replace(".", "").replace("-", "").replace("_", "").isalnum():
            errors.append(_("Username may contain letters, numbers, dot, dash and underscore."))
        elif db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            errors.append(_("This username is already taken."))
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("users/form.html", person=request.form, editing=False, roles=ROLES)
        password = temp_password()
        now = audit.utcnow()
        with transaction(db):
            cur = db.execute(
                "INSERT INTO users (username, full_name, email, phone, role, active, language,"
                " password_hash, must_change_password, ical_token, created_at, updated_at)"
                " VALUES (:username, :full_name, :email, :phone, :role, :active, :language, :pw, 1,"
                " :token, :now, :now)",
                dict(data, username=username, pw=generate_password_hash(password),
                     token=secrets.token_urlsafe(24), now=now))
            audit.record(db, "create", "user", cur.lastrowid, summary=f"{username} ({data['role']})",
                         after=dict(data, username=username))
        return render_template("users/password.html", person={"username": username,
                                                              "full_name": data["full_name"]},
                               password=password)
    return render_template("users/form.html", person={"role": "member", "active": 1},
                           editing=False, roles=ROLES)


@bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@require("admin")
def edit(user_id):
    db = get_db()
    person = load(db, user_id)
    if request.method == "POST":
        data, errors = parse_form(request.form, existing=person)
        if person["role"] == "admin" and (data["role"] != "admin" or not data["active"]) \
                and admins_left(db, user_id) == 0:
            errors.append(_("There must always be at least one active administrator."))
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("users/form.html", person=dict(request.form, id=user_id,
                                   username=person["username"]), editing=True, roles=ROLES)
        before = {k: person[k] for k in FIELDS}
        bump = 1 if (data["role"] != person["role"] or data["active"] != person["active"]) else 0
        with transaction(db):
            db.execute("UPDATE users SET full_name=:full_name, email=:email, phone=:phone, role=:role,"
                       " active=:active, language=:language, session_version = session_version + :bump,"
                       " updated_at=:now WHERE id=:id",
                       dict(data, bump=bump, now=audit.utcnow(), id=user_id))
            audit.record(db, "update", "user", user_id, summary=person["username"],
                         before=before, after=data)
        flash(_("Changes saved."), "success")
        return redirect(url_for("users.detail", user_id=user_id))
    return render_template("users/form.html", person=person, editing=True, roles=ROLES)


@bp.route("/<int:user_id>/reset-password", methods=["POST"])
@require("admin")
def reset_password(user_id):
    db = get_db()
    person = load(db, user_id)
    password = temp_password()
    with transaction(db):
        db.execute("UPDATE users SET password_hash = ?, must_change_password = 1,"
                   " session_version = session_version + 1, updated_at = ? WHERE id = ?",
                   (generate_password_hash(password), audit.utcnow(), user_id))
        audit.record(db, "password_reset", "user", user_id, summary=person["username"], changes={})
    return render_template("users/password.html", person=person, password=password)
