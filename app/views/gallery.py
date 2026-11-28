"""Photo galleries of finished events, for members or for everyone (no login)."""
from flask import Blueprint, abort, current_app, g, redirect, render_template, send_from_directory, url_for

from ..db import get_db

bp = Blueprint("gallery", __name__, url_prefix="/gallery")

GALLERY_MODES = ("off", "members", "public")

LIST_SQL = ("SELECT e.*, t.name_sk, t.name_en, t.color,"
            " (SELECT COUNT(*) FROM photos p WHERE p.entity = 'event' AND p.entity_id = e.id"
            "  AND p.hidden = 0) AS photo_count,"
            " (SELECT p.id FROM photos p WHERE p.entity = 'event' AND p.entity_id = e.id"
            "  AND p.hidden = 0 ORDER BY p.id LIMIT 1) AS cover_id"
            " FROM events e JOIN event_types t ON t.id = e.type_id ")


def visible_modes():
    return ("members", "public") if g.user else ("public",)


def _event(db, event_id):
    row = db.execute(LIST_SQL + "WHERE e.id = ? AND e.archived = 0", (event_id,)).fetchone()
    if row is None or row["gallery"] not in visible_modes():
        return None
    return row


@bp.route("/")
def index():
    db = get_db()
    modes = visible_modes()
    rows = db.execute(LIST_SQL + "WHERE e.archived = 0 AND e.gallery IN ({}) ORDER BY e.event_date DESC"
                      .format(",".join("?" * len(modes))), modes).fetchall()
    return render_template("gallery/index.html", rows=[r for r in rows if r["photo_count"]])


@bp.route("/<int:event_id>")
def event(event_id):
    db = get_db()
    row = _event(db, event_id)
    if row is None:
        if g.user is None:
            # Members-only or unknown: let people log in instead of showing an error.
            return redirect(url_for("main.login", next=url_for("gallery.event", event_id=event_id)))
        abort(404)
    photos = db.execute("SELECT id, uploaded_at FROM photos WHERE entity = 'event' AND entity_id = ?"
                        " AND hidden = 0 ORDER BY id", (event_id,)).fetchall()
    return render_template("gallery/event.html", event=row, photos=photos)


@bp.route("/photo/<int:photo_id>")
@bp.route("/photo/<int:photo_id>/thumb", endpoint="thumb", defaults={"thumb": True})
def photo(photo_id, thumb=False):
    db = get_db()
    p = db.execute("SELECT * FROM photos WHERE id = ? AND entity = 'event' AND hidden = 0",
                   (photo_id,)).fetchone()
    event_row = _event(db, p["entity_id"]) if p else None
    if event_row is None:
        abort(404)
    name = f"thumbs/{p['filename']}" if thumb else p["filename"]
    resp = send_from_directory(current_app.config["UPLOAD_DIR"], name, max_age=3600)
    resp.headers["Cache-Control"] = ("public" if event_row["gallery"] == "public" else "private") \
        + ", max-age=3600"
    return resp
