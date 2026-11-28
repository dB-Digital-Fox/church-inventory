"""Private iCalendar feeds (subscribe once in Google Calendar, Outlook or Apple Calendar)."""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, abort, current_app, request, url_for

from ..db import get_db
from ..i18n import translate as tr, type_name
from ..utils import tz

bp = Blueprint("calendar", __name__, url_prefix="/calendar")


def esc(text):
    return (str(text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n"))


def fold(line):
    """RFC 5545: lines longer than 75 octets are folded."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, current = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(current) + len(b) > (75 if not parts else 74):
            parts.append(current.decode("utf-8"))
            current = b""
        current += b
    parts.append(current.decode("utf-8"))
    return "\r\n ".join(parts)


def utc(local_str):
    dt = datetime.strptime(local_str, "%Y-%m-%dT%H:%M").replace(tzinfo=tz())
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def stamp(iso):
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        dt = datetime.now(timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@bp.route("/<token>.ics")
def feed(token):
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE ical_token = ? AND active = 1", (token,)).fetchone()
    if user is None:
        abort(404)
    lang = user["language"] or current_app.config["DEFAULT_LANGUAGE"]
    mine = request.args.get("scope") == "mine"
    base = current_app.config["BASE_URL"].rstrip("/") or request.host_url.rstrip("/")
    host = base.split("://")[-1].split("/")[0] or "church-inventory"
    since = (datetime.now() - timedelta(days=730)).strftime("%Y-%m-%d")

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//church-inventory//EN", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{esc(current_app.config['ORG_NAME'])}",
             "X-PUBLISHED-TTL:PT1H", "REFRESH-INTERVAL;VALUE=DURATION:PT1H"]

    ev_sql = ("SELECT e.*, t.name_sk, t.name_en FROM events e JOIN event_types t ON t.id = e.type_id"
              " WHERE e.archived = 0 AND e.event_date >= ?")
    params = [since]
    if mine:
        ev_sql += " AND e.id IN (SELECT event_id FROM event_participants WHERE user_id = ?)"
        params.append(user["id"])
    for e in db.execute(ev_sql, params).fetchall():
        people = ", ".join(r[0] for r in db.execute(
            "SELECT u.full_name FROM event_participants p JOIN users u ON u.id = p.user_id"
            " WHERE p.event_id = ? ORDER BY u.full_name", (e["id"],)))
        lines += ["BEGIN:VEVENT", f"UID:event-{e['id']}@{host}", f"DTSTAMP:{stamp(e['updated_at'])}"]
        if e["start_time"]:
            start = f"{e['event_date']}T{e['start_time']}"
            end_time = e["end_time"] if e["end_time"] and e["end_time"] > e["start_time"] else None
            if end_time:
                end = f"{e['event_date']}T{end_time}"
            else:
                end = (datetime.strptime(start, "%Y-%m-%dT%H:%M") + timedelta(hours=1)
                       ).strftime("%Y-%m-%dT%H:%M")
            lines += [f"DTSTART:{utc(start)}", f"DTEND:{utc(end)}"]
        else:
            day = datetime.strptime(e["event_date"], "%Y-%m-%d")
            lines += [f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
                      f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}"]
        desc = [type_name(e, lang)]
        if people:
            desc.append(tr("Participants", lang) + ": " + people)
        if e["other_participants"]:
            desc.append(e["other_participants"])
        if e["description"]:
            desc.append("")
            desc.append(e["description"])
        desc.append(base + url_for("events.detail", event_id=e["id"]))
        lines += [f"SUMMARY:{esc(type_name(e, lang) + ': ' + e['title'])}",
                  f"DESCRIPTION:{esc(chr(10).join(desc))}",
                  f"CATEGORIES:{esc(type_name(e, lang))}",
                  f"URL:{base}{url_for('events.detail', event_id=e['id'])}"]
        if e["location"]:
            lines.append(f"LOCATION:{esc(e['location'])}")
        lines.append("END:VEVENT")

    res_sql = ("SELECT r.*, i.name AS item_name, i.location AS item_location, i.kind AS item_kind,"
               " u.full_name"
               " FROM reservations r JOIN items i ON i.id = r.item_id JOIN users u ON u.id = r.user_id"
               " WHERE r.status IN ('pending','approved','checked_out','returned') AND r.end_at >= ?")
    params = [since]
    if mine:
        res_sql += " AND r.user_id = ?"
        params.append(user["id"])
    for r in db.execute(res_sql, params).fetchall():
        status = tr(r["status"], lang)
        if r["item_kind"] == "space":
            title = f"🏛 {r['item_name']} — {r['full_name']}"
        else:
            title = f"📦 {r['item_name']} ×{r['quantity']} — {r['full_name']}"
        if r["status"] == "pending":
            title += f" ({status})"
        desc = [f"{tr('Status', lang)}: {status}"]
        if r["purpose"]:
            desc.append(r["purpose"])
        desc.append(base + url_for("reservations.detail", rid=r["id"]))
        lines += ["BEGIN:VEVENT", f"UID:reservation-{r['id']}@{host}",
                  f"DTSTAMP:{stamp(r['updated_at'])}", f"DTSTART:{utc(r['start_at'])}",
                  f"DTEND:{utc(r['end_at'])}", f"SUMMARY:{esc(title)}",
                  f"DESCRIPTION:{esc(chr(10).join(desc))}",
                  "STATUS:" + ("TENTATIVE" if r["status"] == "pending" else "CONFIRMED"),
                  "TRANSP:TRANSPARENT"]
        if r["item_location"]:
            lines.append(f"LOCATION:{esc(r['item_location'])}")
        lines.append("END:VEVENT")

    lines.append("END:VCALENDAR")
    body = "\r\n".join(fold(line) for line in lines) + "\r\n"
    return Response(body, mimetype="text/calendar",
                    headers={"Content-Disposition": "inline; filename=church.ics",
                             "Cache-Control": "private, max-age=300"})
