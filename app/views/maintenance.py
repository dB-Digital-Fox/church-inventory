"""Recurring maintenance and inspections with due dates, history and reminders."""
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from .. import audit
from ..auth import can, login_required, require
from ..db import get_db, transaction
from ..i18n import _
from ..utils import now_local, parse_date, photos_for, store_photos, to_int

bp = Blueprint("maintenance", __name__, url_prefix="/maintenance")

RESULTS = ("ok", "issues", "failed")
TASK_FIELDS = ("item_id", "title", "interval_days", "next_due", "responsible_id", "contractor",
               "notes", "active")
LOG_FIELDS = ("task_id", "item_id", "done_on", "performed_by", "result", "cost", "notes")

TASK_SQL = ("SELECT t.*, i.name AS item_name, i.kind AS item_kind, u.full_name AS responsible_name"
            " FROM maintenance_tasks t JOIN items i ON i.id = t.item_id"
            " LEFT JOIN users u ON u.id = t.responsible_id ")
LOG_SQL = ("SELECT l.*, i.name AS item_name, t.title AS task_title, cu.full_name AS creator_name"
           " FROM maintenance_log l JOIN items i ON i.id = l.item_id"
           " LEFT JOIN maintenance_tasks t ON t.id = l.task_id"
           " LEFT JOIN users cu ON cu.id = l.created_by ")


def today():
    return now_local().date()


def days_left(task):
    try:
        due = datetime.strptime(task["next_due"], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None
    return (due - today()).days


def annotate(rows):
    out = []
    for r in rows:
        d = dict(r)
        d["days_left"] = days_left(r)
        if not r["active"]:
            d["due_state"] = "inactive"
        elif d["days_left"] is not None and d["days_left"] < 0:
            d["due_state"] = "overdue"
        elif d["days_left"] is not None and d["days_left"] <= 30:
            d["due_state"] = "soon"
        else:
            d["due_state"] = "ok"
        out.append(d)
    return out


def item_tasks(db, item_id):
    return annotate(db.execute(TASK_SQL + "WHERE t.item_id = ? ORDER BY t.active DESC, t.next_due",
                               (item_id,)).fetchall())


def item_log(db, item_id):
    return db.execute(LOG_SQL + "WHERE l.item_id = ? ORDER BY l.done_on DESC, l.id DESC LIMIT 50",
                      (item_id,)).fetchall()


def due_tasks(db, within_days=30):
    limit = (today() + timedelta(days=within_days)).isoformat()
    return annotate(db.execute(TASK_SQL + "WHERE t.active = 1 AND i.archived = 0 AND t.next_due <= ?"
                               " ORDER BY t.next_due", (limit,)).fetchall())


def load_task(db, task_id):
    row = db.execute(TASK_SQL + "WHERE t.id = ?", (task_id,)).fetchone()
    if row is None:
        abort(404)
    return row


def choices(db):
    items = db.execute("SELECT id, name, kind FROM items WHERE archived = 0"
                       " ORDER BY kind, name COLLATE NOCASE").fetchall()
    users = db.execute("SELECT id, full_name FROM users WHERE active = 1 ORDER BY full_name").fetchall()
    return items, users


@bp.route("/")
@login_required
def index():
    db = get_db()
    tasks = annotate(db.execute(TASK_SQL + "WHERE i.archived = 0 ORDER BY t.active DESC, t.next_due")
                     .fetchall())
    log = db.execute(LOG_SQL + "ORDER BY l.done_on DESC, l.id DESC LIMIT 30").fetchall()
    return render_template("maintenance/index.html", tasks=tasks, log=log,
                           overdue=[t for t in tasks if t["due_state"] == "overdue"],
                           soon=[t for t in tasks if t["due_state"] == "soon"])


def parse_task(db, form):
    data = {
        "item_id": to_int(form.get("item_id")),
        "title": form.get("title", "").strip(),
        "interval_days": to_int(form.get("interval_days") or 0, None, minimum=0),
        "next_due": parse_date(form.get("next_due", "")),
        "responsible_id": to_int(form.get("responsible_id")) or None,
        "contractor": form.get("contractor", "").strip(),
        "notes": form.get("notes", "").strip(),
        "active": 1 if form.get("active", "1") else 0,
    }
    errors = []
    if not data["item_id"] or not db.execute("SELECT 1 FROM items WHERE id = ?",
                                              (data["item_id"],)).fetchone():
        errors.append(_("Choose an item."))
    if not data["title"]:
        errors.append(_("Title is required."))
    if data["interval_days"] is None:
        errors.append(_("Repeat every must be a whole number of days."))
    if not data["next_due"]:
        errors.append(_("Enter a valid date."))
    return data, errors


@bp.route("/tasks/new", methods=["GET", "POST"])
@require("maintenance.manage")
def task_new():
    db = get_db()
    items, users = choices(db)
    if request.method == "POST":
        data, errors = parse_task(db, request.form)
        if not errors:
            now = audit.utcnow()
            with transaction(db):
                cols = ", ".join(TASK_FIELDS)
                vals = ", ".join(f":{f}" for f in TASK_FIELDS)
                cur = db.execute(f"INSERT INTO maintenance_tasks ({cols}, created_at, created_by,"
                                 f" updated_at, updated_by) VALUES ({vals}, :now, :uid, :now, :uid)",
                                 dict(data, now=now, uid=g.user["id"]))
                audit.record(db, "create", "maintenance_task", cur.lastrowid, summary=data["title"],
                             after=data)
            flash(_("Maintenance task saved."), "success")
            return redirect(url_for("maintenance.task_detail", task_id=cur.lastrowid))
        for e in errors:
            flash(e, "error")
        return render_template("maintenance/task_form.html", task=request.form, items=items,
                               users=users, editing=False)
    task = {"item_id": request.args.get("item_id", ""), "interval_days": 365, "active": 1,
            "next_due": (today() + timedelta(days=30)).isoformat()}
    return render_template("maintenance/task_form.html", task=task, items=items, users=users,
                           editing=False)


@bp.route("/tasks/<int:task_id>")
@login_required
def task_detail(task_id):
    db = get_db()
    task = annotate([load_task(db, task_id)])[0]
    log = db.execute(LOG_SQL + "WHERE l.task_id = ? ORDER BY l.done_on DESC", (task_id,)).fetchall()
    return render_template("maintenance/task_detail.html", task=task, log=log,
                           history=audit.history(db, "maintenance_task", task_id))


@bp.route("/tasks/<int:task_id>/edit", methods=["GET", "POST"])
@require("maintenance.manage")
def task_edit(task_id):
    db = get_db()
    task = load_task(db, task_id)
    items, users = choices(db)
    if request.method == "POST":
        form = request.form.to_dict()
        form.setdefault("active", "")
        data, errors = parse_task(db, form)
        if not errors:
            before = {k: task[k] for k in TASK_FIELDS}
            with transaction(db):
                sets = ", ".join(f"{f}=:{f}" for f in TASK_FIELDS)
                db.execute(f"UPDATE maintenance_tasks SET {sets}, updated_at=:now, updated_by=:uid"
                           " WHERE id=:id", dict(data, now=audit.utcnow(), uid=g.user["id"], id=task_id))
                if audit.diff(before, data):
                    audit.record(db, "update", "maintenance_task", task_id, summary=data["title"],
                                 before=before, after=data)
            flash(_("Changes saved."), "success")
            return redirect(url_for("maintenance.task_detail", task_id=task_id))
        for e in errors:
            flash(e, "error")
        return render_template("maintenance/task_form.html", task=dict(form, id=task_id), items=items,
                               users=users, editing=True)
    return render_template("maintenance/task_form.html", task=task, items=items, users=users,
                           editing=True)


@bp.route("/log/new", methods=["GET", "POST"])
@require("maintenance.log")
def log_new():
    db = get_db()
    items, _users = choices(db)
    task_id = to_int(request.values.get("task_id"))
    task = load_task(db, task_id) if task_id else None
    if request.method == "POST":
        f = request.form
        data = {
            "task_id": task["id"] if task else None,
            "item_id": task["item_id"] if task else to_int(f.get("item_id")),
            "done_on": parse_date(f.get("done_on", "")),
            "performed_by": f.get("performed_by", "").strip(),
            "result": f.get("result") if f.get("result") in RESULTS else "ok",
            "cost": f.get("cost", "").strip(),
            "notes": f.get("notes", "").strip(),
        }
        errors = []
        if not data["item_id"] or not db.execute("SELECT 1 FROM items WHERE id = ?",
                                                  (data["item_id"],)).fetchone():
            errors.append(_("Choose an item."))
        if not data["done_on"]:
            errors.append(_("Enter a valid date."))
        elif data["done_on"] > today().isoformat():
            errors.append(_("The date cannot be in the future."))
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("maintenance/log_form.html", form=f, task=task, items=items,
                                   results=RESULTS)
        now = audit.utcnow()
        with transaction(db):
            cols = ", ".join(LOG_FIELDS)
            vals = ", ".join(f":{k}" for k in LOG_FIELDS)
            cur = db.execute(f"INSERT INTO maintenance_log ({cols}, created_at, created_by, updated_at,"
                             f" updated_by) VALUES ({vals}, :now, :uid, :now, :uid)",
                             dict(data, now=now, uid=g.user["id"]))
            log_id = cur.lastrowid
            audit.record(db, "create", "maintenance", log_id,
                         summary=(task["title"] if task else _("Maintenance")) + f" — {data['done_on']}",
                         after=data)
            _saved, rejected = store_photos(db, "maintenance", log_id, request.files.getlist("photos"),
                                            g.user)
            if task and task["active"]:
                # Schedule the next one counted from the day the work was done.
                if task["interval_days"]:
                    done = datetime.strptime(data["done_on"], "%Y-%m-%d").date()
                    next_due = (done + timedelta(days=task["interval_days"])).isoformat()
                    changes = {"next_due": next_due}
                else:
                    changes = {"active": 0}
                sets = ", ".join(f"{k} = :{k}" for k in changes)
                db.execute(f"UPDATE maintenance_tasks SET {sets}, updated_at = :now, updated_by = :uid"
                           " WHERE id = :id", dict(changes, now=now, uid=g.user["id"], id=task["id"]))
                audit.record(db, "update", "maintenance_task", task["id"], summary=task["title"],
                             before={k: task[k] for k in changes}, after=changes)
        for name in rejected:
            flash(_("{name} is not a supported image and was skipped.", name=name), "warning")
        flash(_("Maintenance recorded."), "success")
        return redirect(url_for("maintenance.log_detail", log_id=log_id))
    form = {"done_on": today().isoformat(), "item_id": request.args.get("item_id", ""),
            "performed_by": task["contractor"] if task and task["contractor"] else g.user["full_name"]}
    return render_template("maintenance/log_form.html", form=form, task=task, items=items,
                           results=RESULTS)


@bp.route("/log/<int:log_id>")
@login_required
def log_detail(log_id):
    db = get_db()
    entry = db.execute(LOG_SQL + "WHERE l.id = ?", (log_id,)).fetchone()
    if entry is None:
        abort(404)
    can_edit = can("maintenance.manage") or (can("maintenance.log")
                                            and entry["created_by"] == g.user["id"])
    return render_template("maintenance/log_detail.html", entry=entry, can_edit=can_edit,
                           photos=photos_for(db, "maintenance", log_id),
                           history=audit.history(db, "maintenance", log_id),
                           can_hide=can("maintenance.manage"))


@bp.route("/log/<int:log_id>/edit", methods=["GET", "POST"])
@login_required
def log_edit(log_id):
    db = get_db()
    entry = db.execute(LOG_SQL + "WHERE l.id = ?", (log_id,)).fetchone()
    if entry is None:
        abort(404)
    if not (can("maintenance.manage") or (can("maintenance.log") and entry["created_by"] == g.user["id"])):
        abort(403)
    if request.method == "POST":
        f = request.form
        data = {
            "done_on": parse_date(f.get("done_on", "")),
            "performed_by": f.get("performed_by", "").strip(),
            "result": f.get("result") if f.get("result") in RESULTS else "ok",
            "cost": f.get("cost", "").strip(),
            "notes": f.get("notes", "").strip(),
        }
        if not data["done_on"]:
            flash(_("Enter a valid date."), "error")
        else:
            before = {k: entry[k] for k in data}
            with transaction(db):
                sets = ", ".join(f"{k}=:{k}" for k in data)
                db.execute(f"UPDATE maintenance_log SET {sets}, updated_at=:now, updated_by=:uid"
                           " WHERE id=:id", dict(data, now=audit.utcnow(), uid=g.user["id"], id=log_id))
                if audit.diff(before, data):
                    audit.record(db, "update", "maintenance", log_id,
                                 summary=entry["task_title"] or entry["item_name"],
                                 before=before, after=data)
                store_photos(db, "maintenance", log_id, request.files.getlist("photos"), g.user)
            flash(_("Changes saved."), "success")
            return redirect(url_for("maintenance.log_detail", log_id=log_id))
    task = load_task(db, entry["task_id"]) if entry["task_id"] else None
    return render_template("maintenance/log_form.html", form=entry, task=task, items=[],
                           results=RESULTS, editing=True)


def reminder_due(task, on=None):
    """Send reminders 14, 7 and 1 day before, on the day, and weekly while overdue."""
    on = on or date.today()
    try:
        due = datetime.strptime(task["next_due"], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return False
    left = (due - on).days
    return left in (14, 7, 1, 0) or (left < 0 and (-left) % 7 == 0)
