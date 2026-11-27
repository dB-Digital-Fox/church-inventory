"""Command-line helpers: `flask --app app <command>` (or `docker compose exec app flask ...`)."""
import secrets

import click
from werkzeug.security import generate_password_hash

from . import audit
from .db import connect, transaction


def register(app):
    @app.cli.command("verify-audit")
    def verify_audit():
        """Check that the audit log hash chain is intact."""
        db = connect(app.config["DATABASE"])
        result = audit.verify(db)
        db.close()
        if result["ok"]:
            click.echo(f"OK: {result['count']} entries, latest hash {result['last_hash']}")
        else:
            click.echo(f"BROKEN at entry #{result['broken_id']} (after {result['count']} good entries)")
            raise SystemExit(1)

    @app.cli.command("backup")
    def backup():
        """Create a backup now."""
        from .scheduler import run_backup
        click.echo(run_backup(app))

    @app.cli.command("reset-password")
    @click.argument("username")
    def reset_password(username):
        """Set a temporary password for a user (e.g. if the only admin is locked out)."""
        db = connect(app.config["DATABASE"])
        user = db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user is None:
            raise click.ClickException("no such user")
        password = secrets.token_urlsafe(9)
        with transaction(db):
            db.execute("UPDATE users SET password_hash = ?, must_change_password = 1, active = 1,"
                       " session_version = session_version + 1 WHERE id = ?",
                       (generate_password_hash(password), user["id"]))
            audit.record(db, "password_reset", "user", user["id"], summary=f"{username} (CLI)",
                         changes={}, user=None)
        db.close()
        click.echo(f"Temporary password for {username}: {password}")
