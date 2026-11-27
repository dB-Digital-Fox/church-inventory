"""Time formatting, image uploads, small helpers."""
import hashlib
import io
import os
import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP", "TIFF", "MPO"}


def tz():
    return ZoneInfo(current_app.config["TIMEZONE"])


def now_local():
    return datetime.now(tz()).replace(tzinfo=None)


def now_local_str():
    """YYYY-MM-DDTHH:MM in local time — the format used by reservations."""
    return now_local().strftime("%Y-%m-%dT%H:%M")


def fmt_dt(value):
    """Audit/created timestamps are stored in UTC; show them in local time with seconds."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz()).strftime("%d.%m.%Y %H:%M:%S")


def fmt_local(value):
    """Local naive values: 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM'."""
    if not value:
        return ""
    try:
        if "T" in value:
            return datetime.fromisoformat(value).strftime("%d.%m.%Y %H:%M")
        return datetime.fromisoformat(value).strftime("%d.%m.%Y")
    except ValueError:
        return value


def parse_local_dt(value):
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%dT%H:%M").strftime("%Y-%m-%dT%H:%M")
    except (ValueError, AttributeError):
        return None


def parse_date(value):
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return None


def parse_time(value):
    value = (value or "").strip()
    if not value:
        return ""
    try:
        return datetime.strptime(value, "%H:%M").strftime("%H:%M")
    except ValueError:
        return None


def to_int(value, default=None, minimum=None):
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    if minimum is not None and n < minimum:
        return default
    return n


def upload_dir():
    return current_app.config["UPLOAD_DIR"]


def save_image(file_storage):
    """Validate, auto-rotate, strip metadata (GPS etc.), resize and store an uploaded image.

    Returns (filename, original_name, sha256 of the stored file) or raises ValueError.
    """
    data = file_storage.read()
    if not data:
        raise ValueError("empty file")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            probe.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ValueError("not an image") from exc
    if fmt not in ALLOWED_IMAGE_FORMATS:
        raise ValueError("unsupported image format")

    with Image.open(io.BytesIO(data)) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode not in ("RGB", "L"):
            background = Image.new("RGB", img.size, "white")
            rgba = img.convert("RGBA")
            background.paste(rgba, mask=rgba.split()[-1])
            img = background
        else:
            img = img.convert("RGB")
        img.thumbnail((2000, 2000))
        name = f"{uuid.uuid4().hex}.jpg"
        out = io.BytesIO()
        img.save(out, "JPEG", quality=85, optimize=True)
        stored = out.getvalue()
        thumb = img.copy()
        thumb.thumbnail((480, 480))
        tout = io.BytesIO()
        thumb.save(tout, "JPEG", quality=80)

    base = upload_dir()
    os.makedirs(os.path.join(base, "thumbs"), exist_ok=True)
    with open(os.path.join(base, name), "wb") as f:
        f.write(stored)
    with open(os.path.join(base, "thumbs", name), "wb") as f:
        f.write(tout.getvalue())
    original = os.path.basename(file_storage.filename or "")[:200]
    return name, original, hashlib.sha256(stored).hexdigest()


def store_photos(db, entity, entity_id, files, user):
    """Save every uploaded file; returns (saved rows, list of rejected file names)."""
    from .audit import record, utcnow

    saved, rejected = [], []
    for fs in files:
        if not fs or not fs.filename:
            continue
        try:
            name, original, digest = save_image(fs)
        except ValueError:
            rejected.append(fs.filename)
            continue
        cur = db.execute(
            "INSERT INTO photos (entity, entity_id, filename, original_name, sha256, uploaded_at,"
            " uploaded_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (entity, entity_id, name, original, digest, utcnow(), user["id"]),
        )
        record(db, "photo_add", entity, entity_id, summary=original or name,
               changes={"photo": [None, name], "sha256": [None, digest]})
        saved.append(cur.lastrowid)
    return saved, rejected


def photos_for(db, entity, entity_id, include_hidden=False):
    sql = "SELECT * FROM photos WHERE entity = ? AND entity_id = ?"
    if not include_hidden:
        sql += " AND hidden = 0"
    return db.execute(sql + " ORDER BY id", (entity, entity_id)).fetchall()
