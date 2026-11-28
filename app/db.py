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
        migrate(conn)
        if conn.execute("SELECT COUNT(*) FROM event_types").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO event_types (name_sk, name_en, color) VALUES (?, ?, ?)",
                DEFAULT_EVENT_TYPES,
            )
        if conn.execute("SELECT COUNT(*) FROM roster_roles").fetchone()[0] == 0:
            conn.executemany("INSERT INTO roster_roles (name_sk, name_en) VALUES (?, ?)",
                             DEFAULT_ROSTER_ROLES)
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


DEFAULT_ROSTER_ROLES = [
    ("Lektor", "Reader"),
    ("Miništrant", "Altar server"),
    ("Kantor", "Cantor"),
    ("Mimoriadny rozdávateľ", "Extraordinary minister"),
    ("Usporiadateľ", "Usher"),
    ("Ozvučenie", "Sound"),
    ("Upratovanie", "Cleaning"),
]

EVENT_COLUMNS = {
    "gallery": "TEXT NOT NULL DEFAULT 'off'",
}

ITEM_COLUMNS = {
    "kind": "TEXT NOT NULL DEFAULT 'item'",
    "acquisition": "TEXT NOT NULL DEFAULT 'purchase'",
    "donor": "TEXT NOT NULL DEFAULT ''",
    "unit_value": "REAL",
    "useful_life_years": "INTEGER NOT NULL DEFAULT 0",
}


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn):
    """Bring databases created by older versions up to the current schema."""
    have = _columns(conn, "items")
    for name, ddl in ITEM_COLUMNS.items():
        if name not in have:
            conn.execute(f"ALTER TABLE items ADD COLUMN {name} {ddl}")
    have_events = _columns(conn, "events")
    for name, ddl in EVENT_COLUMNS.items():
        if name not in have_events:
            conn.execute(f"ALTER TABLE events ADD COLUMN {name} {ddl}")
    if "unit_value" not in have:
        # Older versions kept the value as free text; carry over plain numbers like "489 €".
        for row in conn.execute("SELECT id, purchase_value FROM items").fetchall():
            text = (row[1] or "").replace("€", "").replace(" ", "").replace(",", ".")
            try:
                conn.execute("UPDATE items SET unit_value = ? WHERE id = ?", (float(text), row[0]))
            except ValueError:
                pass
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'photos'").fetchone()[0]
    if "CHECK (entity IN" in sql:
        # Allow photos on more kinds of records (maintenance reports). SQLite can't drop a CHECK,
        # so the table is copied; ids, files and hashes stay the same.
        conn.executescript("""
            BEGIN;
            ALTER TABLE photos RENAME TO photos_old;
            CREATE TABLE photos (
                id INTEGER PRIMARY KEY, entity TEXT NOT NULL, entity_id INTEGER NOT NULL,
                filename TEXT NOT NULL, original_name TEXT NOT NULL DEFAULT '', sha256 TEXT NOT NULL,
                hidden INTEGER NOT NULL DEFAULT 0, uploaded_at TEXT NOT NULL,
                uploaded_by INTEGER REFERENCES users(id));
            INSERT INTO photos SELECT * FROM photos_old;
            DROP TABLE photos_old;
            CREATE INDEX IF NOT EXISTS photos_entity ON photos(entity, entity_id);
            COMMIT;
        """)
