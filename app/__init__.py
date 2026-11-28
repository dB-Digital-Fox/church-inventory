"""Church Inventory — inventory, event log and reservations with a tamper-evident audit trail.

Copyright (C) 2026 the Church Inventory contributors. Licensed under the GNU AGPL v3.
"""
import os
import secrets
from datetime import timedelta

from flask import Flask, g, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from . import auth, i18n, utils
from .db import close_db, connect, get_state, init_db, set_state

__version__ = "0.2.0"


def _bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def load_config(overrides=None):
    env = os.environ
    data_dir = env.get("DATA_DIR", os.path.abspath("data"))
    cfg = {
        "DATA_DIR": data_dir,
        "DATABASE": os.path.join(data_dir, "church.db"),
        "UPLOAD_DIR": os.path.join(data_dir, "uploads"),
        "BACKUP_DIR": env.get("BACKUP_DIR", os.path.join(data_dir, "backups")),
        "BACKUP_HOUR": int(env.get("BACKUP_HOUR", "2")),
        "BACKUP_KEEP": int(env.get("BACKUP_KEEP", "14")),
        "REMINDER_HOUR": int(env.get("REMINDER_HOUR", "18")),
        "ORG_NAME": env.get("ORG_NAME", "Church Inventory"),
        "BASE_URL": env.get("BASE_URL", ""),
        "TIMEZONE": env.get("TZ", "Europe/Bratislava"),
        "DEFAULT_LANGUAGE": env.get("DEFAULT_LANGUAGE", "sk"),
        "SOURCE_URL": env.get("SOURCE_URL", "https://github.com/dB-Digital-Fox/church-inventory"),
        "TRUST_PROXY": int(env.get("TRUST_PROXY", "1")),
        "SCHEDULER": _bool(env.get("SCHEDULER"), True),
        "SECRET_KEY": env.get("SECRET_KEY", ""),
        "SESSION_COOKIE_SECURE": _bool(env.get("SECURE_COOKIES"), False),
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "PERMANENT_SESSION_LIFETIME": timedelta(days=int(env.get("SESSION_DAYS", "14"))),
        "MAX_CONTENT_LENGTH": int(env.get("MAX_UPLOAD_MB", "40")) * 1024 * 1024,
        "SMTP_HOST": env.get("SMTP_HOST", ""),
        "SMTP_PORT": int(env.get("SMTP_PORT", "587")),
        "SMTP_USER": env.get("SMTP_USER", ""),
        "SMTP_PASSWORD": env.get("SMTP_PASSWORD", ""),
        "SMTP_FROM": env.get("SMTP_FROM", ""),
        "SMTP_STARTTLS": _bool(env.get("SMTP_STARTTLS"), True),
        "SMTP_SSL": _bool(env.get("SMTP_SSL"), False),
        "WHATSAPP_TOKEN": env.get("WHATSAPP_TOKEN", ""),
        "WHATSAPP_PHONE_NUMBER_ID": env.get("WHATSAPP_PHONE_NUMBER_ID", ""),
        "WHATSAPP_TEMPLATE": env.get("WHATSAPP_TEMPLATE", ""),
        "WHATSAPP_TEMPLATE_LANG": env.get("WHATSAPP_TEMPLATE_LANG", "sk"),
        "WHATSAPP_API_VERSION": env.get("WHATSAPP_API_VERSION", "v21.0"),
    }
    if overrides:
        cfg.update(overrides)
        if "DATA_DIR" in overrides:
            d = overrides["DATA_DIR"]
            for key, sub in (("DATABASE", "church.db"), ("UPLOAD_DIR", "uploads"), ("BACKUP_DIR", "backups")):
                if key not in overrides:
                    cfg[key] = os.path.join(d, sub)
    return cfg


def create_app(overrides=None):
    app = Flask(__name__)
    app.config.update(load_config(overrides))
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)
    init_db(app.config["DATABASE"])

    if not app.config["SECRET_KEY"]:
        # Generate once and keep it in the database so sessions survive restarts.
        conn = connect(app.config["DATABASE"])
        try:
            key = get_state(conn, "secret_key")
            if not key:
                key = secrets.token_hex(32)
                set_state(conn, "secret_key", key)
            app.config["SECRET_KEY"] = key
        finally:
            conn.close()

    if app.config["TRUST_PROXY"]:
        n = app.config["TRUST_PROXY"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)

    app.teardown_appcontext(close_db)

    @app.before_request
    def _before():
        auth.load_user()
        auth.check_csrf()

    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; frame-ancestors 'none'; form-action 'self'")
        return resp

    @app.context_processor
    def _inject():
        return {
            "_": i18n._,
            "lang": i18n.current_lang(),
            "LANGUAGES": i18n.LANGUAGES,
            "can": auth.can,
            "csrf_token": auth.csrf_token,
            "type_name": i18n.type_name,
            "fmt_value": i18n.fmt_value,
            "org_name": app.config["ORG_NAME"],
            "source_url": app.config["SOURCE_URL"],
            "version": __version__,
        }

    app.jinja_env.filters["dt"] = utils.fmt_dt
    app.jinja_env.filters["local"] = utils.fmt_local
    from .assets import money
    app.jinja_env.filters["money"] = money

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def _error(err):
        return render_template("error.html", error=err), err.code

    from .views import (admin, audit_views, calendar, events, items, main, maintenance, reports,
                        reservations, users)
    for module in (main, items, events, reservations, users, audit_views, calendar, admin,
                   maintenance, reports):
        app.register_blueprint(module.bp)

    from . import cli
    cli.register(app)

    if app.config["SCHEDULER"] and not app.config.get("TESTING"):
        from . import scheduler
        scheduler.start(app)

    return app
