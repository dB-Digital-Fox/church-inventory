import csv
import io
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, flash, g, redirect, render_template, request, url_for

from .. import audit
from ..auth import require
from ..db import get_db, transaction
from ..i18n import _
from ..utils import parse_date, to_int, tz

bp = Blueprint("audit", __name__, url_prefix="/audit")

PAGE = 100
ENTITY_LINKS = {
    "item": ("items.detail", "item_id"),
    "event": ("events.detail", "event_id"),
    "reservation": ("reservations.detail", "rid"),
    "user": ("users.detail", "user_id"),
}


def entity_url(entity, entity_id):
    if entity in ENTITY_LINKS and entity_id:
        endpoint, arg = ENTITY_LINKS[entity]
        return url_for(endpoint, **{arg: entity_id})
    return None


def _utc_bound(day, end=False):
    """Convert a local YYYY-MM-DD to a UTC ISO bound for comparing with stored timestamps."""
    d = datetime.strptime(day, "%Y-%m-%d")
    if end:
        d += timedelta(days=1)
    return d.replace(tzinfo=tz()).astimezone(timezone.utc).isoformat(timespec="microseconds")


def query(db, args, limit=None, offset=0):
    where, params = [], []
    for field in ("entity", "action"):
        if args.get(field):
            where.append(f"{field} = ?")
            params.append(args[field])
    if to_int(args.get("entity_id")):
        where.append("entity_id = ?")
        params.append(to_int(args.get("entity_id")))
    if args.get("user"):
        where.append("username = ?")
        params.append(args["user"])
    if parse_date(args.get("from", "")):
        where.append("ts >= ?")
        params.append(_utc_bound(parse_date(args["from"])))
    if parse_date(args.get("to", "")):
        where.append("ts < ?")
        params.append(_utc_bound(parse_date(args["to"]), end=True))
    if args.get("q", "").strip():
        where.append("(summary LIKE ? OR changes LIKE ?)")
        params += [f"%{args['q'].strip()}%"] * 2
    sql = "FROM audit_log" + (" WHERE " + " AND ".join(where) if where else "")
    total = db.execute("SELECT COUNT(*) " + sql, params).fetchone()[0]
    sql = "SELECT * " + sql + " ORDER BY id DESC"
    if limit:
        sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
    return total, [audit.with_changes(r) for r in db.execute(sql, params)]


@bp.route("/")
@require("audit.view")
def index():
    db = get_db()
    page = to_int(request.args.get("page"), 1, minimum=1)
    total, rows = query(db, request.args, PAGE, (page - 1) * PAGE)
    actions = [r[0] for r in db.execute("SELECT DISTINCT action FROM audit_log ORDER BY action")]
    entities = [r[0] for r in db.execute("SELECT DISTINCT entity FROM audit_log ORDER BY entity")]
    users = [r[0] for r in db.execute("SELECT DISTINCT username FROM audit_log ORDER BY username")]
    last = db.execute("SELECT id, ts, hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
    args = {k: v for k, v in request.args.items() if k != "page"}
    return render_template("audit/index.html", rows=rows, total=total, page=page, pages=max(
        (total + PAGE - 1) // PAGE, 1), actions=actions, entities=entities, users=users,
        args=args, last=last, entity_url=entity_url)


@bp.route("/verify", methods=["POST"])
@require("audit.view")
def verify():
    db = get_db()
    result = audit.verify(db)
    with transaction(db):
        audit.record(db, "verify", "audit_log", None,
                     summary=("OK" if result["ok"] else f"BROKEN at #{result['broken_id']}")
                     + f" ({result['count']} entries)", changes={})
    if result["ok"]:
        flash(_("Audit log verified: all {n} entries are intact.", n=result["count"]), "success")
    else:
        flash(_("WARNING: the audit log was altered. The chain breaks at entry #{id}.",
                id=result["broken_id"]), "error")
    return redirect(url_for("audit.index"))


@bp.route("/export.csv")
@require("audit.view")
def export_csv():
    db = get_db()
    _total, rows = query(db, request.args)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "timestamp_utc", "user", "action", "entity", "entity_id", "summary", "changes",
                "ip", "prev_hash", "hash"])
    for r in reversed(rows):
        w.writerow([r["id"], r["ts"], r["username"], r["action"], r["entity"], r["entity_id"],
                    r["summary"], r["changes"], r["ip"], r["prev_hash"], r["hash"]])
    with transaction(db):
        audit.record(db, "export", "audit_log", None, summary=f"CSV, {len(rows)} entries", changes={})
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    return Response("﻿" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=audit-log-{stamp}.csv"})


@bp.route("/report")
@require("audit.view")
def report():
    """Printable report (use the browser's Print → Save as PDF)."""
    db = get_db()
    total, rows = query(db, request.args, 5000)
    result = audit.verify(db)
    with transaction(db):
        audit.record(db, "export", "audit_log", None, summary=f"report, {len(rows)} entries",
                     changes={})
    return render_template("audit/report.html", rows=rows, total=total, result=result,
                           args=request.args, generated_by=g.user["full_name"], generated_at=audit.utcnow(),
                           entity_url=entity_url)
