"""Reports for the parish council, the diocese and insurance."""
import csv
import io
from datetime import date

from flask import Blueprint, Response, g, render_template, request

from .. import audit
from ..assets import book_value, depreciation_in_year, total_value
from .. import xlsx
from ..auth import require
from ..db import get_db, transaction
from ..i18n import _, type_name
from ..utils import fmt_dt, now_local, parse_date, to_int

bp = Blueprint("reports", __name__, url_prefix="/reports")


def asset_rows(db, year):
    end = date(year, 12, 31)
    rows = []
    for item in db.execute("SELECT * FROM items WHERE archived = 0 ORDER BY kind, category,"
                           " name COLLATE NOCASE"):
        acquired = item["purchase_date"] or ""
        if acquired and acquired > end.isoformat():
            continue
        if item["kind"] == "space" and item["unit_value"] is None:
            continue  # rooms are usually part of the building, not a movable asset
        rows.append({
            "item": item,
            "total": total_value(item),
            "depreciation": depreciation_in_year(item, year),
            "book_value": book_value(item, end),
        })
    return rows


def asset_report(db, year):
    rows = asset_rows(db, year)
    donations = [r for r in rows if r["item"]["acquisition"] == "donation"
                 and (r["item"]["purchase_date"] or "").startswith(str(year))]
    def total(key, subset=rows):
        return round(sum(r[key] or 0 for r in subset), 2)
    return {
        "rows": rows, "donations": donations,
        "totals": {"total": total("total"), "depreciation": total("depreciation"),
                   "book_value": total("book_value"), "donations": total("total", donations)},
        "missing_value": sum(1 for r in rows if r["total"] is None),
    }


@bp.route("/")
@require("reports.view")
def index():
    return render_template("reports/index.html", year=now_local().year,
                           today=now_local().strftime("%Y-%m-%d"))


@bp.route("/assets")
@require("reports.view")
def assets():
    db = get_db()
    year = to_int(request.args.get("year"), now_local().year, minimum=1900)
    report = asset_report(db, year)
    fmt = request.args.get("format")
    if fmt == "csv":
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["id", "name", "kind", "category", "location", "acquisition", "donor", "acquired",
                    "quantity", "unit_value_eur", "total_value_eur", "useful_life_years",
                    f"depreciation_{year}_eur", f"book_value_{year}-12-31_eur"])
        for r in report["rows"]:
            i = r["item"]
            w.writerow([i["id"], i["name"], i["kind"], i["category"], i["location"], i["acquisition"],
                        i["donor"], i["purchase_date"], i["quantity"], i["unit_value"], r["total"],
                        i["useful_life_years"], r["depreciation"], r["book_value"]])
        log_export(db, f"assets {year} CSV")
        return Response("﻿" + out.getvalue(), mimetype="text/csv", headers={
            "Content-Disposition": f"attachment; filename=assets-{year}.csv"})
    if fmt == "xlsx":
        wb = xlsx.new_workbook()
        heads = [_("#"), _("Name"), _("Type of record"), _("Category"), _("Location"), _("Origin"),
                 _("Donor"), _("Acquired on"), _("Qty"), _("Value of one piece (€)"), _("Value"),
                 _("Useful life (years)"), _("Depreciation in {y}", y=year),
                 _("Book value on 31.12.{y}", y=year)]
        money = heads[9:11] + heads[12:]
        xlsx.sheet(wb, _("All assets"), heads, [
            [i["id"], i["name"], _(i["kind"]), i["category"], i["location"], _(i["acquisition"]),
             i["donor"], i["purchase_date"], i["quantity"], i["unit_value"], r["total"],
             i["useful_life_years"] or None, r["depreciation"], r["book_value"]]
            for r in report["rows"] for i in [r["item"]]], money=money, first=True,
            notes=[f"{_('Assets and donations')} {year}", f"{_('Generated')}: {fmt_dt(audit.utcnow())}"
                   f" · {g.user['full_name']}"])
        xlsx.sheet(wb, _("Donations received in {y}", y=year),
                   [_("#"), _("Name"), _("Donor"), _("Acquired on"), _("Qty"), _("Value")],
                   [[r["item"]["id"], r["item"]["name"], r["item"]["donor"], r["item"]["purchase_date"],
                     r["item"]["quantity"], r["total"]] for r in report["donations"]],
                   money=[_("Value")])
        log_export(db, f"assets {year} Excel")
        return xlsx.response(wb, f"assets-{year}.xlsx")
    log_export(db, f"assets {year}")
    return render_template("reports/assets.html", year=year, generated_by=g.user["full_name"],
                           generated_at=audit.utcnow(), **report)


def log_export(db, summary):
    with transaction(db):
        audit.record(db, "export", "report", None, summary=summary, changes={})


def _range(args):
    today = now_local().date()
    start = parse_date(args.get("from", "")) or date(today.year, 1, 1).isoformat()
    end = parse_date(args.get("to", "")) or today.isoformat()
    if end < start:
        start, end = end, start
    return start, end


def event_summary(db, start, end):
    events = db.execute(
        "SELECT e.*, t.name_sk, t.name_en, t.color FROM events e JOIN event_types t ON t.id = e.type_id"
        " WHERE e.archived = 0 AND e.event_date BETWEEN ? AND ? ORDER BY e.event_date, e.start_time",
        (start, end)).fetchall()
    ids = [e["id"] for e in events] or [0]
    marks = ",".join("?" * len(ids))
    people = {}
    for p in db.execute(f"SELECT p.event_id, u.full_name FROM event_participants p JOIN users u"
                        f" ON u.id = p.user_id WHERE p.event_id IN ({marks}) ORDER BY u.full_name", ids):
        people.setdefault(p["event_id"], []).append(p["full_name"])
    duties = {}
    for s in db.execute(f"SELECT s.event_id, u.full_name, r.name_sk, r.name_en FROM roster_slots s"
                        f" JOIN users u ON u.id = s.user_id JOIN roster_roles r ON r.id = s.role_id"
                        f" WHERE s.event_id IN ({marks}) ORDER BY r.id", ids):
        duties.setdefault(s["event_id"], []).append(s)
    by_type, by_person = {}, {}
    for e in events:
        key = type_name(e)
        by_type.setdefault(key, {"count": 0, "color": e["color"]})["count"] += 1
        for name in set(people.get(e["id"], []) + [d["full_name"] for d in duties.get(e["id"], [])]):
            by_person[name] = by_person.get(name, 0) + 1
    photos = db.execute(f"SELECT COUNT(*) FROM photos WHERE entity = 'event' AND hidden = 0"
                        f" AND entity_id IN ({marks})", ids).fetchone()[0]
    return {"events": events, "people": people, "duties": duties, "photos": photos,
            "by_type": sorted(by_type.items(), key=lambda kv: -kv[1]["count"]),
            "by_person": sorted(by_person.items(), key=lambda kv: (-kv[1], kv[0]))}


@bp.route("/events")
@require("reports.view")
def events():
    db = get_db()
    start, end = _range(request.args)
    data = event_summary(db, start, end)
    if request.args.get("format") == "xlsx":
        wb = xlsx.new_workbook()
        xlsx.sheet(wb, _("Events"), [_("Date"), _("Start"), _("End"), _("Type"), _("Title"),
                                     _("Location"), _("Who took part / did it"), _("Roster"),
                                     _("Short write-up")],
                   [[e["event_date"], e["start_time"], e["end_time"], type_name(e), e["title"],
                     e["location"], ", ".join(data["people"].get(e["id"], []) + (
                         [e["other_participants"]] if e["other_participants"] else [])),
                     "; ".join(f"{type_name(d)}: {d['full_name']}" for d in data["duties"].get(e["id"], [])),
                     e["description"]] for e in data["events"]],
                   wrap=[_("Short write-up"), _("Roster")], first=True,
                   notes=[f"{_('Event summary')} {start} – {end}"])
        xlsx.sheet(wb, _("By type"), [_("Type"), _("Events")],
                   [[k, v["count"]] for k, v in data["by_type"]])
        xlsx.sheet(wb, _("By person"), [_("Person"), _("Events")], data["by_person"])
        log_export(db, f"events {start}..{end} Excel")
        return xlsx.response(wb, f"events-{start}-{end}.xlsx")
    log_export(db, f"events {start}..{end}")
    return render_template("reports/events.html", start=start, end=end, generated_by=g.user["full_name"],
                           generated_at=audit.utcnow(), **data)


@bp.route("/inventory.xlsx")
@require("reports.view")
def inventory_xlsx():
    from .items import query_items
    db = get_db()
    last = {r["item_id"]: r for r in db.execute(
        "SELECT c.item_id, MAX(c.checked_at) AS checked_at, u.full_name FROM item_checks c"
        " LEFT JOIN users u ON u.id = c.user_id GROUP BY c.item_id")}
    rows = []
    for kind in ("item", "space"):
        args = dict(request.args, kind=kind)
        for i in query_items(db, args):
            check = last.get(i["id"])
            rows.append([i["id"], i["name"], _(i["kind"]), i["category"], i["location"], i["quantity"],
                         _(i["condition"]), i["serial_number"], _(i["acquisition"]), i["donor"],
                         i["purchase_date"], i["unit_value"], total_value(i), i["description"],
                         check["checked_at"][:10] if check else "", check["full_name"] if check else ""])
    wb = xlsx.new_workbook()
    heads = [_("#"), _("Name"), _("Type of record"), _("Category"), _("Location"), _("Qty"),
             _("Condition"), _("Serial number"), _("Origin"), _("Donor"), _("Acquired on"),
             _("Value of one piece (€)"), _("Value"), _("Description / notes"), _("Last checked"),
             _("Checked by")]
    xlsx.sheet(wb, _("Inventory"), heads, rows, money=heads[11:13], first=True,
               notes=[f"{_('Inventory list')} — {fmt_dt(audit.utcnow())} · {g.user['full_name']}"])
    log_export(db, "inventory Excel")
    return xlsx.response(wb, f"inventory-{now_local():%Y-%m-%d}.xlsx")


@bp.route("/maintenance.xlsx")
@require("reports.view")
def maintenance_xlsx():
    db = get_db()
    year = to_int(request.args.get("year"), now_local().year, minimum=1900)
    rows = db.execute(
        "SELECT l.*, i.name AS item_name, t.title AS task_title FROM maintenance_log l"
        " JOIN items i ON i.id = l.item_id LEFT JOIN maintenance_tasks t ON t.id = l.task_id"
        " WHERE substr(l.done_on, 1, 4) = ? ORDER BY l.done_on", (str(year),)).fetchall()
    wb = xlsx.new_workbook()
    xlsx.sheet(wb, _("Maintenance"), [_("Date"), _("Item"), _("Task"), _("Done by"), _("Result"),
                                      _("Cost"), _("Notes")],
               [[r["done_on"], r["item_name"], r["task_title"] or "", r["performed_by"], _(r["result"]),
                 r["cost"], r["notes"]] for r in rows], wrap=[_("Notes")], first=True,
               notes=[f"{_('Maintenance and inspections')} {year}"])
    log_export(db, f"maintenance {year} Excel")
    return xlsx.response(wb, f"maintenance-{year}.xlsx")


@bp.route("/audit-year.xlsx")
@require("audit.view")
def audit_year():
    """Everything recorded in one calendar year, plus the integrity check, for the yearly audit."""
    import json
    from ..utils import tz
    from datetime import datetime, timezone
    db = get_db()
    year = to_int(request.args.get("year"), now_local().year, minimum=1900)

    def bound(y):
        return datetime(y, 1, 1, tzinfo=tz()).astimezone(timezone.utc).isoformat(timespec="microseconds")
    rows = db.execute("SELECT * FROM audit_log WHERE ts >= ? AND ts < ? ORDER BY id",
                      (bound(year), bound(year + 1))).fetchall()
    state = audit.verify(db)
    wb = xlsx.new_workbook()
    first, last = (rows[0], rows[-1]) if rows else (None, None)
    xlsx.sheet(wb, _("Integrity"), [_("Check"), _("Result")], [
        [_("Year"), year],
        [_("Entries in this year"), len(rows)],
        [_("First entry"), f"#{first['id']} {fmt_dt(first['ts'])}" if first else "—"],
        [_("Last entry"), f"#{last['id']} {fmt_dt(last['ts'])}" if last else "—"],
        [_("Fingerprint of the last entry of the year"), last["hash"] if last else "—"],
        [_("Whole log checked now"), f"{state['count']} · " + (_("chain intact") if state["ok"] else
                                                              _("BROKEN at entry #{id}", id=state["broken_id"]))],
        [_("Latest fingerprint"), state["last_hash"]],
        [_("Generated"), f"{fmt_dt(audit.utcnow())} · {g.user['full_name']}"],
    ], first=True)
    out = []
    for r in rows:
        try:
            changes = json.loads(r["changes"] or "{}")
        except ValueError:
            changes = {}
        out.append([r["id"], fmt_dt(r["ts"]), r["username"], _(r["action"]), _(r["entity"]),
                    r["entity_id"], r["summary"],
                    "\n".join(f"{_(k)}: {v[0] if v[0] not in (None, '') else '—'} → "
                              f"{v[1] if v[1] not in (None, '') else '—'}" for k, v in changes.items()
                              if isinstance(v, list) and len(v) == 2),
                    r["ip"], r["prev_hash"], r["hash"]])
    xlsx.sheet(wb, _("Audit log"), [_("#"), _("Time"), _("User"), _("Action"), _("Record"), "ID",
                                    _("Summary"), _("Changes"), "IP", _("Previous fingerprint"),
                                    _("Fingerprint")], out, wrap=[_("Changes")])
    log_export(db, f"audit {year} Excel")
    return xlsx.response(wb, f"audit-{year}.xlsx")
