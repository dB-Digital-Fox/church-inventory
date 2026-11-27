from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request,
                   url_for)

from .. import audit
from ..auth import can, login_required, require
from ..db import get_db, transaction
from ..i18n import _, translate as tr
from ..notify import notify
from ..utils import fmt_local, now_local_str, parse_local_dt, to_int

bp = Blueprint("reservations", __name__, url_prefix="/reservations")

ACTIVE = ("pending", "approved", "checked_out")
STATUSES = ("pending", "approved", "rejected", "checked_out", "returned", "cancelled")
CONDITIONS = ("good", "fair", "damaged", "broken", "missing")
FIELDS = ("item_id", "user_id", "event_id", "quantity", "start_at", "end_at", "purpose")

BASE_SQL = ("SELECT r.*, i.name AS item_name, i.quantity AS item_quantity, u.full_name,"
            " e.title AS event_title FROM reservations r JOIN items i ON i.id = r.item_id"
            " JOIN users u ON u.id = r.user_id LEFT JOIN events e ON e.id = r.event_id ")


def find_clashes(db, item_id, start, end, quantity, exclude_id=None):
    """Active reservations of the same item overlapping [start, end).

    Returns dict(overlaps=[rows], booked=int, available=int, clash=bool) where clash means the
    total booked quantity in that time would exceed how many pieces the item has.
    """
    item = db.execute("SELECT quantity FROM items WHERE id = ?", (item_id,)).fetchone()
    total = item["quantity"] if item else 0
    overlaps = db.execute(
        BASE_SQL + "WHERE r.item_id = ? AND r.status IN ('pending','approved','checked_out')"
        " AND r.start_at < ? AND r.end_at > ? AND r.id != ? ORDER BY r.start_at",
        (item_id, end, start, exclude_id or 0)).fetchall()
    booked = peak_booked(overlaps, start, end)
    return {"overlaps": overlaps, "booked": booked, "available": max(total - booked, 0),
            "total": total, "clash": booked + quantity > total}


def peak_booked(rows, start, end):
    """Highest number of pieces booked at the same moment inside [start, end)."""
    points = sorted({start} | {r["start_at"] for r in rows if start <= r["start_at"] < end})
    peak = 0
    for p in points:
        peak = max(peak, sum(r["quantity"] for r in rows if r["start_at"] <= p < r["end_at"]))
    return peak


def annotate_clashes(db, rows):
    out = []
    for r in rows:
        d = dict(r)
        if r["status"] in ACTIVE:
            info = find_clashes(db, r["item_id"], r["start_at"], r["end_at"], r["quantity"], r["id"])
            d["clash"] = info["clash"]
            d["overlap_count"] = len(info["overlaps"])
        else:
            d["clash"], d["overlap_count"] = False, 0
        d["overdue"] = r["status"] == "checked_out" and r["end_at"] < now_local_str()
        out.append(d)
    return out


def managers(db, exclude_id=None):
    return db.execute("SELECT * FROM users WHERE active = 1 AND role IN ('admin','manager')"
                      " AND id != ?", (exclude_id or 0,)).fetchall()


def _link(rid):
    base = current_app.config["BASE_URL"].rstrip("/") or request.host_url.rstrip("/")
    return f"{base}{url_for('reservations.detail', rid=rid)}"


def _describe(lang, r):
    return tr("{item} ×{qty} from {start} to {end}.", lang, item=r["item_name"], qty=r["quantity"],
              start=fmt_local(r["start_at"]), end=fmt_local(r["end_at"]))


def load(db, rid):
    row = db.execute(BASE_SQL + "WHERE r.id = ?", (rid,)).fetchone()
    if row is None:
        abort(404)
    return row


def form_choices(db):
    items = db.execute("SELECT id, name, quantity, location FROM items WHERE archived = 0"
                       " AND reservable = 1 ORDER BY name").fetchall()
    events = db.execute(
        "SELECT id, title, event_date FROM events WHERE archived = 0 AND event_date >= date('now','-1 day')"
        " ORDER BY event_date LIMIT 100").fetchall()
    users = db.execute("SELECT id, full_name FROM users WHERE active = 1 ORDER BY full_name").fetchall()
    return items, events, users


def parse_form(db, form, existing=None):
    errors = []
    data = {
        "item_id": to_int(form.get("item_id")),
        "quantity": to_int(form.get("quantity"), minimum=1),
        "start_at": parse_local_dt(form.get("start_at", "")),
        "end_at": parse_local_dt(form.get("end_at", "")),
        "purpose": form.get("purpose", "").strip(),
        "event_id": to_int(form.get("event_id")) or None,
        "user_id": existing["user_id"] if existing else g.user["id"],
    }
    if can("reservation.manage") and to_int(form.get("user_id")):
        data["user_id"] = to_int(form.get("user_id"))
    item = data["item_id"] and db.execute(
        "SELECT * FROM items WHERE id = ? AND archived = 0", (data["item_id"],)).fetchone()
    if not item:
        errors.append(_("Choose an item."))
    elif not item["reservable"] and not existing:
        errors.append(_("This item cannot be reserved."))
    if not data["quantity"]:
        errors.append(_("Quantity must be at least 1."))
    elif item and data["quantity"] > item["quantity"]:
        errors.append(_("Only {n} pieces of this item exist.", n=item["quantity"]))
    if not data["start_at"] or not data["end_at"]:
        errors.append(_("Enter a valid start and end."))
    elif data["end_at"] <= data["start_at"]:
        errors.append(_("The end must be after the start."))
    if data["event_id"] and not db.execute("SELECT 1 FROM events WHERE id = ?",
                                           (data["event_id"],)).fetchone():
        data["event_id"] = None
    if not db.execute("SELECT 1 FROM users WHERE id = ? AND active = 1", (data["user_id"],)).fetchone():
        errors.append(_("Choose a person."))
    return data, errors


@bp.route("/")
@login_required
def index():
    db = get_db()
    status = request.args.get("status", "active")
    item_id = to_int(request.args.get("item"))
    user_id = to_int(request.args.get("user"))
    where, params = [], []
    if status == "active":
        where.append("r.status IN ('pending','approved','checked_out')")
    elif status in STATUSES:
        where.append("r.status = ?")
        params.append(status)
    if item_id:
        where.append("r.item_id = ?")
        params.append(item_id)
    if user_id:
        where.append("r.user_id = ?")
        params.append(user_id)
    sql = BASE_SQL + ("WHERE " + " AND ".join(where) if where else "")
    order = " ORDER BY r.start_at" if status in ("active", "pending", "approved", "checked_out") \
        else " ORDER BY r.start_at DESC"
    rows = annotate_clashes(db, db.execute(sql + order + " LIMIT 500", params).fetchall())
    items, _events, users = form_choices(db)
    return render_template("reservations/index.html", rows=rows, status=status, statuses=STATUSES,
                           item_id=item_id, user_id=user_id, items=items, users=users)


@bp.route("/new", methods=["GET", "POST"])
@require("reservation.create")
def new():
    db = get_db()
    items, events, users = form_choices(db)
    clash = None
    if request.method == "POST":
        data, errors = parse_form(db, request.form)
        if not errors:
            clash = find_clashes(db, data["item_id"], data["start_at"], data["end_at"], data["quantity"])
            if clash["clash"] and not request.form.get("confirm_clash"):
                errors.append(_("This booking clashes with other reservations. Review them below and "
                                "confirm if you still want to send the request."))
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("reservations/form.html", form=request.form, items=items,
                                   events=events, users=users, clash=clash, existing=None)
        status = "approved" if can("reservation.manage") else "pending"
        now = audit.utcnow()
        with transaction(db):
            cur = db.execute(
                "INSERT INTO reservations (item_id, user_id, event_id, quantity, start_at, end_at,"
                " purpose, status, created_at, created_by, updated_at, updated_by) VALUES"
                " (:item_id, :user_id, :event_id, :quantity, :start_at, :end_at, :purpose, :status,"
                " :now, :uid, :now, :uid)", dict(data, status=status, now=now, uid=g.user["id"]))
            rid = cur.lastrowid
            row = load(db, rid)
            audit.record(db, "create", "reservation", rid,
                         summary=f"{row['item_name']} ×{row['quantity']} — {row['full_name']}"
                                 + (" [CLASH]" if clash and clash["clash"] else ""),
                         after=dict({k: data[k] for k in FIELDS}, status=status))
        link = _link(rid)
        has_clash = bool(clash and clash["clash"])

        def build(lang):
            subject = tr("New reservation request", lang) if status == "pending" \
                else tr("New reservation", lang)
            body = tr("{who} reserved:", lang, who=row["full_name"]) + "\n" + _describe(lang, row)
            if has_clash:
                body += "\n⚠ " + tr("This booking clashes with other reservations.", lang)
            return subject, body + "\n" + link
        notify(current_app._get_current_object(), managers(db, g.user["id"]), build)
        if row["user_id"] != g.user["id"]:
            owner = db.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchall()
            notify(current_app._get_current_object(), owner, lambda lang: (
                tr("Reservation made for you", lang), _describe(lang, row) + "\n" + link))
        flash(_("Reservation saved.") if status == "approved"
              else _("Reservation request sent for approval."), "success")
        return redirect(url_for("reservations.detail", rid=rid))
    form = {"item_id": request.args.get("item_id", ""), "event_id": request.args.get("event_id", ""),
            "quantity": "1"}
    return render_template("reservations/form.html", form=form, items=items, events=events,
                           users=users, clash=None, existing=None)


def can_edit(row):
    if can("reservation.manage"):
        return row["status"] in ("pending", "approved")
    return (can("reservation.create") and row["user_id"] == g.user["id"]
            and row["status"] == "pending")


@bp.route("/<int:rid>")
@login_required
def detail(rid):
    db = get_db()
    row = load(db, rid)
    clash = find_clashes(db, row["item_id"], row["start_at"], row["end_at"], row["quantity"], rid)
    return render_template("reservations/detail.html", r=row, clash=clash, can_edit=can_edit(row),
                           history=audit.history(db, "reservation", rid), conditions=CONDITIONS,
                           overdue=row["status"] == "checked_out" and row["end_at"] < now_local_str())


@bp.route("/<int:rid>/edit", methods=["GET", "POST"])
@login_required
def edit(rid):
    db = get_db()
    row = load(db, rid)
    if not can_edit(row):
        abort(403)
    items, events, users = form_choices(db)
    clash = None
    if request.method == "POST":
        data, errors = parse_form(db, request.form, existing=row)
        if not errors:
            clash = find_clashes(db, data["item_id"], data["start_at"], data["end_at"],
                                 data["quantity"], rid)
            if clash["clash"] and not request.form.get("confirm_clash"):
                errors.append(_("This booking clashes with other reservations. Review them below and "
                                "confirm if you still want to send the request."))
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("reservations/form.html", form=request.form, items=items,
                                   events=events, users=users, clash=clash, existing=row)
        before = {k: row[k] for k in FIELDS}
        with transaction(db):
            db.execute("UPDATE reservations SET item_id=:item_id, user_id=:user_id, event_id=:event_id,"
                       " quantity=:quantity, start_at=:start_at, end_at=:end_at, purpose=:purpose,"
                       " updated_at=:now, updated_by=:uid WHERE id=:id",
                       dict(data, now=audit.utcnow(), uid=g.user["id"], id=rid))
            audit.record(db, "update", "reservation", rid, summary=row["item_name"],
                         before=before, after={k: data[k] for k in FIELDS})
        flash(_("Changes saved."), "success")
        return redirect(url_for("reservations.detail", rid=rid))
    return render_template("reservations/form.html", form=row, items=items, events=events,
                           users=users, clash=None, existing=row)


TRANSITIONS = {
    "approve": ({"pending"}, "approved", "reservation.manage"),
    "reject": ({"pending", "approved"}, "rejected", "reservation.manage"),
    "checkout": ({"approved", "pending"}, "checked_out", "reservation.manage"),
    "return": ({"checked_out"}, "returned", "reservation.manage"),
    "cancel": ({"pending", "approved"}, "cancelled", None),
}


@bp.route("/<int:rid>/status", methods=["POST"])
@login_required
def change_status(rid):
    db = get_db()
    row = load(db, rid)
    action = request.form.get("action")
    if action not in TRANSITIONS:
        abort(400)
    allowed_from, new_status, perm = TRANSITIONS[action]
    if perm and not can(perm):
        abort(403)
    if action == "cancel" and not (can("reservation.manage") or row["user_id"] == g.user["id"]):
        abort(403)
    if row["status"] not in allowed_from:
        flash(_("This action is not possible in the current state."), "error")
        return redirect(url_for("reservations.detail", rid=rid))
    note = request.form.get("note", "").strip()
    sets = {"status": new_status}
    if action in ("approve", "reject"):
        sets["decision_note"] = note
    if action == "return":
        cond = request.form.get("condition", "good")
        sets["return_condition"] = cond if cond in CONDITIONS else "good"
        sets["return_note"] = note
    before = {k: row[k] for k in sets}
    with transaction(db):
        assignments = ", ".join(f"{k} = :{k}" for k in sets)
        db.execute(f"UPDATE reservations SET {assignments}, updated_at = :now, updated_by = :uid"
                   " WHERE id = :id", dict(sets, now=audit.utcnow(), uid=g.user["id"], id=rid))
        audit.record(db, action, "reservation", rid, summary=f"{row['item_name']} — {row['full_name']}",
                     before=before, after=sets)
        if action == "return" and request.form.get("update_item_condition"):
            item = db.execute("SELECT condition, name FROM items WHERE id = ?",
                              (row["item_id"],)).fetchone()
            if item["condition"] != sets["return_condition"]:
                db.execute("UPDATE items SET condition = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                           (sets["return_condition"], audit.utcnow(), g.user["id"], row["item_id"]))
                audit.record(db, "update", "item", row["item_id"],
                             summary=f"{item['name']} (return of reservation #{rid})",
                             before={"condition": item["condition"]},
                             after={"condition": sets["return_condition"]})
    if action in ("approve", "reject") and row["user_id"] != g.user["id"]:
        owner = db.execute("SELECT * FROM users WHERE id = ?", (row["user_id"],)).fetchall()
        link = _link(rid)
        subject_key = "Reservation approved" if action == "approve" else "Reservation rejected"
        notify(current_app._get_current_object(), owner, lambda lang: (
            tr(subject_key, lang),
            _describe(lang, row) + (f"\n{note}" if note else "") + "\n" + link))
    flash(_("Reservation updated."), "success")
    return redirect(url_for("reservations.detail", rid=rid))


@bp.route("/availability")
@login_required
def availability():
    """Small JSON helper the form uses to show clashes while the user types."""
    db = get_db()
    item_id = to_int(request.args.get("item_id"))
    start = parse_local_dt(request.args.get("start_at", ""))
    end = parse_local_dt(request.args.get("end_at", ""))
    qty = to_int(request.args.get("quantity"), 1, minimum=1)
    if not (item_id and start and end and end > start):
        return {"ok": False}
    info = find_clashes(db, item_id, start, end, qty, to_int(request.args.get("exclude")))
    return {
        "ok": True, "clash": info["clash"], "total": info["total"], "booked": info["booked"],
        "available": info["available"],
        "overlaps": [{"id": r["id"], "who": r["full_name"], "quantity": r["quantity"],
                      "start": fmt_local(r["start_at"]), "end": fmt_local(r["end_at"]),
                      "status": _(r["status"])} for r in info["overlaps"]],
    }
