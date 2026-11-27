import secrets

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request,
                   send_from_directory, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .. import audit
from ..auth import can, login_required, login_user, throttle
from ..db import get_db, transaction
from ..i18n import LANGUAGES, _
from ..utils import now_local, now_local_str
from .reservations import annotate_clashes

bp = Blueprint("main", __name__)

MIN_PASSWORD = 10


def password_problem(pw, pw2):
    if len(pw) < MIN_PASSWORD:
        return _("Password must have at least {n} characters.", n=MIN_PASSWORD)
    if pw != pw2:
        return _("Passwords do not match.")
    return None


def _safe_next(target):
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("main.dashboard")


def user_count(db):
    return db.execute("SELECT COUNT(*) FROM users").fetchone()[0]


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    db = get_db()
    if user_count(db) > 0:
        return redirect(url_for("main.login"))
    if request.method == "POST":
        f = request.form
        username = f.get("username", "").strip()
        full_name = f.get("full_name", "").strip()
        problem = password_problem(f.get("password", ""), f.get("password2", ""))
        if not username or not full_name:
            problem = _("Please fill in all required fields.")
        if problem:
            flash(problem, "error")
        else:
            now = audit.utcnow()
            with transaction(db):
                if user_count(db) > 0:  # someone else finished setup meanwhile
                    abort(409)
                cur = db.execute(
                    "INSERT INTO users (username, full_name, email, role, password_hash, language,"
                    " ical_token, created_at, updated_at) VALUES (?, ?, ?, 'admin', ?, ?, ?, ?, ?)",
                    (username, full_name, f.get("email", "").strip(),
                     generate_password_hash(f["password"]), f.get("language", "sk"),
                     secrets.token_urlsafe(24), now, now))
                user = db.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
                audit.record(db, "create", "user", user["id"], summary=f"{username} (admin, setup)",
                             after={"username": username, "full_name": full_name, "role": "admin"},
                             user=user)
            login_user(user)
            flash(_("Welcome! Your administrator account is ready."), "success")
            return redirect(url_for("main.dashboard"))
    return render_template("setup.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    db = get_db()
    if user_count(db) == 0:
        return redirect(url_for("main.setup"))
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        ip = request.remote_addr or "?"
        keys = (f"ip:{ip}", f"user:{username.lower()}")
        if throttle.blocked(*keys):
            flash(_("Too many failed attempts. Try again in 15 minutes."), "error")
            return render_template("login.html"), 429
        user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and user["active"] and check_password_hash(user["password_hash"], password):
            throttle.reset(*keys)
            with transaction(db):
                audit.record(db, "login", "user", user["id"], summary=username, changes={}, user=user)
            login_user(user)
            return redirect(_safe_next(request.args.get("next")))
        throttle.fail(*keys)
        with transaction(db):
            audit.record(db, "login_failed", "user", user["id"] if user else None,
                         summary=username[:80], changes={})
        flash(_("Wrong username or password."), "error")
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("main.login"))


@bp.route("/language/<code>", methods=["POST"])
def set_language(code):
    if code not in LANGUAGES:
        abort(404)
    session["lang"] = code
    if g.user:
        db = get_db()
        with transaction(db):
            db.execute("UPDATE users SET language = ?, updated_at = ? WHERE id = ?",
                       (code, audit.utcnow(), g.user["id"]))
    return redirect(_safe_next(request.form.get("next")))


@bp.route("/")
@login_required
def dashboard():
    db = get_db()
    today = now_local().strftime("%Y-%m-%d")
    now = now_local_str()
    upcoming = db.execute(
        "SELECT e.*, t.name_sk, t.name_en, t.color FROM events e JOIN event_types t ON t.id = e.type_id"
        " WHERE e.archived = 0 AND e.event_date >= ? ORDER BY e.event_date, e.start_time LIMIT 8",
        (today,)).fetchall()
    recent = db.execute(
        "SELECT e.*, t.name_sk, t.name_en, t.color FROM events e JOIN event_types t ON t.id = e.type_id"
        " WHERE e.archived = 0 AND e.event_date < ? ORDER BY e.event_date DESC, e.start_time DESC"
        " LIMIT 8", (today,)).fetchall()
    res_sql = ("SELECT r.*, i.name AS item_name, i.quantity AS item_quantity, u.full_name"
               " FROM reservations r JOIN items i ON i.id = r.item_id JOIN users u ON u.id = r.user_id ")
    mine = annotate_clashes(db, db.execute(
        res_sql + "WHERE r.user_id = ? AND r.status IN ('pending','approved','checked_out')"
        " ORDER BY r.start_at LIMIT 10", (g.user["id"],)).fetchall())
    pending = []
    overdue = []
    if can("reservation.manage"):
        pending = annotate_clashes(db, db.execute(
            res_sql + "WHERE r.status = 'pending' ORDER BY r.start_at").fetchall())
        overdue = db.execute(res_sql + "WHERE r.status = 'checked_out' AND r.end_at < ?"
                             " ORDER BY r.end_at", (now,)).fetchall()
    activity = []
    if can("audit.view"):
        activity = [audit.with_changes(r) for r in db.execute(
            "SELECT * FROM audit_log WHERE action NOT IN ('login','login_failed')"
            " ORDER BY id DESC LIMIT 10")]
    counts = {
        "item_count": db.execute("SELECT COUNT(*) FROM items WHERE archived = 0").fetchone()[0],
        "event_count": db.execute("SELECT COUNT(*) FROM events WHERE archived = 0").fetchone()[0],
        "active_res": db.execute("SELECT COUNT(*) FROM reservations WHERE status IN"
                                 " ('pending','approved','checked_out')").fetchone()[0],
    }
    return render_template("dashboard.html", upcoming=upcoming, recent=recent, mine=mine,
                           pending=pending, overdue=overdue, activity=activity, counts=counts,
                           now=now)


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    db = get_db()
    user = g.user
    if request.method == "POST":
        action = request.form.get("action")
        f = request.form
        if action == "details":
            after = {
                "full_name": f.get("full_name", "").strip() or user["full_name"],
                "email": f.get("email", "").strip(),
                "phone": f.get("phone", "").strip(),
                "language": f.get("language") if f.get("language") in LANGUAGES else "",
                "notify_email": 1 if f.get("notify_email") else 0,
                "notify_whatsapp": 1 if f.get("notify_whatsapp") else 0,
            }
            before = {k: user[k] for k in after}
            with transaction(db):
                db.execute("UPDATE users SET full_name=:full_name, email=:email, phone=:phone,"
                           " language=:language, notify_email=:notify_email,"
                           " notify_whatsapp=:notify_whatsapp, updated_at=:now WHERE id=:id",
                           dict(after, now=audit.utcnow(), id=user["id"]))
                audit.record(db, "update", "user", user["id"], summary=user["username"],
                             before=before, after=after)
            if after["language"]:
                session["lang"] = after["language"]
            flash(_("Profile saved."), "success")
        elif action == "password":
            if not check_password_hash(user["password_hash"], f.get("current", "")):
                flash(_("Current password is wrong."), "error")
            else:
                problem = password_problem(f.get("password", ""), f.get("password2", ""))
                if problem:
                    flash(problem, "error")
                else:
                    with transaction(db):
                        db.execute("UPDATE users SET password_hash = ?, must_change_password = 0,"
                                   " session_version = session_version + 1, updated_at = ?"
                                   " WHERE id = ?",
                                   (generate_password_hash(f["password"]), audit.utcnow(), user["id"]))
                        audit.record(db, "password_change", "user", user["id"],
                                     summary=user["username"], changes={})
                    fresh = db.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
                    login_user(fresh)
                    flash(_("Password changed."), "success")
        elif action == "calendar_token":
            with transaction(db):
                db.execute("UPDATE users SET ical_token = ?, updated_at = ? WHERE id = ?",
                           (secrets.token_urlsafe(24), audit.utcnow(), user["id"]))
                audit.record(db, "calendar_link_reset", "user", user["id"],
                             summary=user["username"], changes={})
            flash(_("A new calendar link was created. The old one no longer works."), "success")
        return redirect(url_for("main.profile"))
    base = current_app.config["BASE_URL"].rstrip("/") or request.host_url.rstrip("/")
    feed = f"{base}{url_for('calendar.feed', token=user['ical_token'])}"
    return render_template("profile.html", feed=feed, min_password=MIN_PASSWORD)


@bp.route("/uploads/<path:name>")
@login_required
def upload(name):
    if not name.replace("thumbs/", "", 1).replace(".jpg", "").isalnum():
        abort(404)
    return send_from_directory(current_app.config["UPLOAD_DIR"], name, max_age=86400)


@bp.route("/healthz")
def healthz():
    get_db().execute("SELECT 1").fetchone()
    return "ok"
