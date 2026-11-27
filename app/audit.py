"""Tamper-evident audit log.

Every change in the app writes one row here, inside the same transaction as the change.
Rows are chained: each row's hash covers its own content plus the previous row's hash,
so editing, inserting or deleting any past row breaks the chain from that point on.
SQLite triggers additionally refuse UPDATE and DELETE on the table.
"""
import hashlib
import json
from datetime import datetime, timezone

from flask import g, has_request_context, request

GENESIS = "0" * 64
HASHED_FIELDS = ("id", "ts", "user_id", "username", "action", "entity", "entity_id",
                 "summary", "changes", "ip", "prev_hash")


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def compute_hash(row):
    payload = {k: row[k] for k in HASHED_FIELDS}
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def diff(before, after):
    """{field: [old, new]} for fields that changed. Missing side is None."""
    before = dict(before or {})
    after = dict(after or {})
    out = {}
    for key in sorted(set(before) | set(after)):
        if key in ("updated_at", "updated_by", "password_hash", "ical_token", "session_version"):
            continue
        old, new = before.get(key), after.get(key)
        if old in (None, "", []) and new in (None, "", []):
            continue
        if old != new:
            out[key] = [old, new]
    return out


def record(db, action, entity, entity_id=None, summary="", before=None, after=None,
           changes=None, user=None):
    """Append one audit entry. Must be called inside db.transaction()."""
    if user is None and has_request_context():
        user = getattr(g, "user", None)
    ip = ""
    if has_request_context():
        ip = request.remote_addr or ""
    if changes is None:
        changes = diff(before, after)
    last = db.execute("SELECT id, hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    row = {
        "id": (last["id"] + 1) if last else 1,
        "ts": utcnow(),
        "user_id": user["id"] if user else None,
        "username": user["username"] if user else "system",
        "action": action,
        "entity": entity,
        "entity_id": entity_id,
        "summary": summary or "",
        "changes": json.dumps(changes, sort_keys=True, ensure_ascii=False, default=str),
        "ip": ip,
        "prev_hash": last["hash"] if last else GENESIS,
    }
    row["hash"] = compute_hash(row)
    db.execute(
        "INSERT INTO audit_log (id, ts, user_id, username, action, entity, entity_id, summary,"
        " changes, ip, prev_hash, hash) VALUES (:id, :ts, :user_id, :username, :action, :entity,"
        " :entity_id, :summary, :changes, :ip, :prev_hash, :hash)",
        row,
    )
    return row


def verify(db):
    """Recompute the whole chain. Returns dict(ok, count, broken_id, last_hash)."""
    prev = GENESIS
    expected_id = 1
    count = 0
    for row in db.execute("SELECT * FROM audit_log ORDER BY id"):
        row = dict(row)
        if row["id"] != expected_id or row["prev_hash"] != prev or compute_hash(row) != row["hash"]:
            return {"ok": False, "count": count, "broken_id": row["id"], "last_hash": prev}
        prev = row["hash"]
        expected_id += 1
        count += 1
    return {"ok": True, "count": count, "broken_id": None, "last_hash": prev}


def history(db, entity, entity_id):
    rows = db.execute(
        "SELECT * FROM audit_log WHERE entity = ? AND entity_id = ? ORDER BY id DESC",
        (entity, entity_id),
    ).fetchall()
    return [with_changes(r) for r in rows]


def with_changes(row):
    d = dict(row)
    try:
        d["changes_dict"] = json.loads(d["changes"] or "{}")
    except ValueError:
        d["changes_dict"] = {}
    return d
