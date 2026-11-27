"""SQLite access. One connection per request, explicit transactions."""
import os
import sqlite3
from contextlib import contextmanager

from flask import current_app, g

DEFAULT_EVENT_TYPES = [
    ("Omša / bohoslužba", "Mass / service", "#7c3aed"),
    ("Konferencia", "Conference", "#2563eb"),
    ("Nahrávanie", "Recording", "#db2777"),
    ("Stretnutie", "Meeting", "#0891b2"),
    ("Upratovanie", "Cleanup", "#16a34a"),
    ("Koncert", "Concert", "#ea580c"),
    ("Oprava", "Repair", "#854d0e"),
]


def connect(path):
    conn = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@contextmanager
def transaction(db=None):
    """BEGIN IMMEDIATE so the audit hash chain is written strictly in order."""
    db = db or get_db()
    db.execute("BEGIN IMMEDIATE")
    try:
        yield db
    except BaseException:
        db.execute("ROLLBACK")
        raise
    else:
        db.execute("COMMIT")


def init_db(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        with open(os.path.join(os.path.dirname(__file__), "schema.sql"), encoding="utf-8") as f:
            conn.executescript(f.read())
        if conn.execute("SELECT COUNT(*) FROM event_types").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO event_types (name_sk, name_en, color) VALUES (?, ?, ?)",
                DEFAULT_EVENT_TYPES,
            )
    finally:
        conn.close()


def get_state(db, key, default=None):
    row = db.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(db, key, value):
    db.execute(
        "INSERT INTO app_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
