"""Reports for the parish council, the diocese and insurance."""
import csv
import io
from datetime import date

from flask import Blueprint, Response, g, render_template, request

from .. import audit
from ..assets import book_value, depreciation_in_year, total_value
from ..auth import require
from ..db import get_db, transaction
from ..utils import now_local, to_int

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
    return render_template("reports/index.html", year=now_local().year)


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
    log_export(db, f"assets {year}")
    return render_template("reports/assets.html", year=year, generated_by=g.user["full_name"],
                           generated_at=audit.utcnow(), **report)


def log_export(db, summary):
    with transaction(db):
        audit.record(db, "export", "report", None, summary=summary, changes={})
