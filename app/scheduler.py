"""Daily background jobs: backups, reservation reminders, audit-log anchor digest."""
import logging
import threading
import time
from datetime import timedelta

from . import audit
from .backup import create_backup
from .db import connect, get_state, set_state, transaction
from .i18n import translate as tr
from .notify import deliver, email_enabled, whatsapp_enabled

log = logging.getLogger(__name__)


def run_backup(app):
    cfg = app.config
    path = create_backup(cfg["DATABASE"], cfg["UPLOAD_DIR"], cfg["BACKUP_DIR"], cfg["BACKUP_KEEP"])
    db = connect(cfg["DATABASE"])
    try:
        with transaction(db):
            audit.record(db, "backup", "system", None, summary=path.rsplit("/", 1)[-1], changes={})
    finally:
        db.close()
    return path


def send_daily_notifications(app):
    cfg = app.config
    if not (email_enabled(cfg) or whatsapp_enabled(cfg)):
        return
    db = connect(cfg["DATABASE"])
    try:
        with app.app_context():
            from .utils import fmt_local, now_local
            now = now_local()
            tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
            now_s = now.strftime("%Y-%m-%dT%H:%M")
            base = cfg.get("BASE_URL", "").rstrip("/")

            upcoming = db.execute(
                "SELECT r.*, i.name AS item_name, u.* , r.id AS rid, r.quantity AS rqty"
                " FROM reservations r JOIN items i ON i.id = r.item_id JOIN users u ON u.id = r.user_id"
                " WHERE r.status = 'approved' AND substr(r.start_at, 1, 10) = ? AND u.active = 1",
                (tomorrow,)).fetchall()
            for r in upcoming:
                deliver(cfg, [r], lambda lang, r=r: (
                    tr("Reminder: reservation tomorrow", lang),
                    tr("{item} ×{qty} from {start} to {end}.", lang, item=r["item_name"],
                       qty=r["rqty"], start=fmt_local(r["start_at"]), end=fmt_local(r["end_at"]))
                    + (f"\n{base}/reservations/{r['rid']}" if base else "")))

            overdue = db.execute(
                "SELECT r.*, i.name AS item_name, u.*, r.id AS rid, r.quantity AS rqty"
                " FROM reservations r JOIN items i ON i.id = r.item_id JOIN users u ON u.id = r.user_id"
                " WHERE r.status = 'checked_out' AND r.end_at < ? AND u.active = 1", (now_s,)).fetchall()
            for r in overdue:
                deliver(cfg, [r], lambda lang, r=r: (
                    tr("Overdue: please return borrowed item", lang),
                    tr("{item} ×{qty} was due back on {end}.", lang, item=r["item_name"],
                       qty=r["rqty"], end=fmt_local(r["end_at"]))
                    + (f"\n{base}/reservations/{r['rid']}" if base else "")))

            from .views.maintenance import TASK_SQL, reminder_due
            for t in db.execute(TASK_SQL + "WHERE t.active = 1 AND i.archived = 0").fetchall():
                if not reminder_due(t, now.date()):
                    continue
                people = {u["id"]: u for u in db.execute(
                    "SELECT * FROM users WHERE active = 1 AND (role IN ('admin','manager') OR id = ?)",
                    (t["responsible_id"] or 0,)).fetchall()}
                overdue = t["next_due"] < now.strftime("%Y-%m-%d")
                deliver(cfg, list(people.values()), lambda lang, t=t, overdue=overdue: (
                    tr("Maintenance overdue: {title}", lang, title=t["title"]) if overdue
                    else tr("Maintenance due: {title}", lang, title=t["title"]),
                    tr("{item}: due on {date}.", lang, item=t["item_name"], date=fmt_local(t["next_due"]))
                    + (f"\n{base}/maintenance/tasks/{t['id']}" if base else "")))

            from .views.rosters import gaps, reminders, role_name
            for s in reminders(db, tomorrow):
                person = db.execute("SELECT * FROM users WHERE id = ? AND active = 1",
                                    (s["user_id"],)).fetchone()
                if person is None:
                    continue
                deliver(cfg, [person], lambda lang, s=s: (
                    tr("Reminder: you serve tomorrow", lang),
                    tr("{role} — {event}, {date} {time}", lang, role=role_name(s, lang),
                       event=s["event_title"], date=fmt_local(s["event_date"]), time=s["start_time"])
                    + (f" · {s['location']}" if s["location"] else "")
                    + (f"\n{base}/events/{s['event_id']}" if base else "")))
            in_two_days = (now + timedelta(days=2)).strftime("%Y-%m-%d")
            open_slots = gaps(db, in_two_days)
            if open_slots:
                managers = db.execute("SELECT * FROM users WHERE active = 1"
                                      " AND role IN ('admin','manager')").fetchall()
                deliver(cfg, managers, lambda lang: (
                    tr("Roster gaps in two days", lang),
                    "\n".join(f"• {role_name(s, lang)} — {s['event_title']}"
                              + (f" ({s['full_name']})" if s["full_name"] else "")
                              for s in open_slots)
                    + (f"\n{base}/rosters/" if base else "")))

            auditors = db.execute(
                "SELECT * FROM users WHERE role = 'auditor' AND active = 1").fetchall()
            if auditors:
                state = audit.verify(db)
                deliver(cfg, auditors, lambda lang: (
                    tr("Daily audit log fingerprint", lang),
                    tr("Entries: {count}. Chain intact: {ok}. Latest hash: {hash}. Keep this message "
                       "- it proves the log has not been rewritten since today.", lang,
                       count=state["count"], ok=tr("yes" if state["ok"] else "NO", lang),
                       hash=state["last_hash"])))
    finally:
        db.close()


def _loop(app):
    while True:
        try:
            with app.app_context():
                from .utils import now_local
                now = now_local()
                today = now.strftime("%Y-%m-%d")
                db = connect(app.config["DATABASE"])
                try:
                    jobs = []
                    if now.hour >= app.config["BACKUP_HOUR"] and get_state(db, "last_backup") != today:
                        set_state(db, "last_backup", today)
                        jobs.append(run_backup)
                    if now.hour >= app.config["REMINDER_HOUR"] and get_state(db, "last_reminders") != today:
                        set_state(db, "last_reminders", today)
                        jobs.append(send_daily_notifications)
                finally:
                    db.close()
                for job in jobs:
                    try:
                        job(app)
                    except Exception:  # noqa: BLE001 - keep the scheduler alive
                        log.exception("scheduled job %s failed", job.__name__)
        except Exception:  # noqa: BLE001
            log.exception("scheduler tick failed")
        time.sleep(60)


def start(app):
    t = threading.Thread(target=_loop, args=(app,), name="scheduler", daemon=True)
    t.start()
    return t
