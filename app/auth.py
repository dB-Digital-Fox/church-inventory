"""Login, roles and permissions, CSRF."""
import secrets
import threading
import time
from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for

from .i18n import _

from .db import get_db

ROLES = ("admin", "manager", "member", "auditor")

PERMISSIONS = {
    "item.edit": {"admin", "manager"},
    "event.create": {"admin", "manager", "member"},
    "event.edit_any": {"admin", "manager"},
    "reservation.create": {"admin", "manager", "member"},
    "reservation.manage": {"admin", "manager"},
    "audit.view": {"admin", "manager", "auditor"},
    "maintenance.manage": {"admin", "manager"},
    "maintenance.log": {"admin", "manager", "member"},
    "reports.view": {"admin", "manager", "auditor"},
    "admin": {"admin"},
}


def can(perm, user=None):
    user = user if user is not None else g.get("user")
    return bool(user) and user["role"] in PERMISSIONS[perm]


def load_user():
    g.user = None
    uid = session.get("uid")
    if uid is None:
        return
    user = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if user is None or not user["active"] or user["session_version"] != session.get("sv"):
        session.clear()
        return
    g.user = user


def login_user(user):
    session.clear()
    session["uid"] = user["id"]
    session["sv"] = user["session_version"]
    session["csrf"] = secrets.token_urlsafe(32)
    session.permanent = True


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("main.login", next=request.full_path))
        if g.user["must_change_password"] and request.endpoint not in ("main.profile", "main.logout",
                                                                       "main.set_language"):
            flash(_("Please set a new password before continuing."), "warning")
            return redirect(url_for("main.profile"))
        return view(*args, **kwargs)
    return wrapped


def require(perm):
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not can(perm):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf():
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token")
        if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
            abort(400, description="Invalid or missing CSRF token. Reload the page and try again.")


class LoginThrottle:
    """Simple in-memory limit on failed logins per IP and per username."""

    def __init__(self, limit=10, window=900):
        self.limit, self.window = limit, window
        self._fails = {}
        self._lock = threading.Lock()

    def _recent(self, key, now):
        stamps = [t for t in self._fails.get(key, []) if now - t < self.window]
        self._fails[key] = stamps
        return stamps

    def blocked(self, *keys):
        now = time.time()
        with self._lock:
            return any(len(self._recent(k, now)) >= self.limit for k in keys)

    def fail(self, *keys):
        now = time.time()
        with self._lock:
            for k in keys:
                self._recent(k, now).append(now)

    def reset(self, *keys):
        with self._lock:
            for k in keys:
                self._fails.pop(k, None)


throttle = LoginThrottle()
