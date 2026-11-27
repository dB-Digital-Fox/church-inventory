import json

from app import audit
from app.db import connect
from app.translations_sk import SK

from .conftest import ADMIN_PW, Client, jpeg_bytes, login_as
from .extract_strings import strings


def db_of(app):
    return connect(app.config["DATABASE"])


def add_item(c, name="Projector", quantity=1, **extra):
    data = {"name": name, "quantity": str(quantity), "condition": "good", "reservable": "on"}
    data.update(extra)
    r = c.post("/items/new", data)
    assert r.status_code == 302, r.get_data(as_text=True)
    return int(r.headers["Location"].rstrip("/").split("/")[-1])


def reserve(c, item_id, start, end, qty=1, **extra):
    data = {"item_id": str(item_id), "quantity": str(qty), "start_at": start, "end_at": end}
    data.update(extra)
    return c.post("/reservations/new", data)


# --- setup & auth ---------------------------------------------------------------

def test_first_run_redirects_to_setup(client):
    assert client.get("/").headers["Location"].startswith("/login")
    assert client.get("/login").headers["Location"] == "/setup"


def test_setup_only_once(admin, app):
    c = Client(app)
    r = c.post("/setup", {"username": "evil", "full_name": "x", "password": ADMIN_PW,
                          "password2": ADMIN_PW})
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")
    assert db_of(app).execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_login_logout_and_failed_login_is_audited(admin, app):
    admin.logout()
    assert admin.login("admin", "wrong").status_code == 200
    assert admin.login("admin", ADMIN_PW).status_code == 302
    actions = [r[0] for r in db_of(app).execute("SELECT action FROM audit_log ORDER BY id")]
    assert "login_failed" in actions and actions[-1] == "login"


def test_csrf_required(admin):
    r = admin.c.post("/items/new", data={"name": "x", "quantity": "1"})
    assert r.status_code == 400


def test_temp_password_must_be_changed(admin, app):
    from .conftest import make_user
    temp = make_user(admin, "jana", "member")
    c = Client(app)
    c.login("jana", temp)
    r = c.get("/items/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/profile")


# --- roles ------------------------------------------------------------------------

def test_role_permissions(admin, app):
    auditor = login_as(app, admin, "audit", "auditor")
    member = login_as(app, admin, "mem", "member")
    manager = login_as(app, admin, "man", "manager")

    assert auditor.get("/items/new").status_code == 403
    assert auditor.get("/events/new").status_code == 403
    assert auditor.get("/audit/").status_code == 200
    assert member.get("/audit/").status_code == 403
    assert member.get("/items/new").status_code == 403
    assert member.get("/events/new").status_code == 200
    assert manager.get("/items/new").status_code == 200
    assert manager.get("/admin/").status_code == 403
    assert admin.get("/admin/").status_code == 200


def test_member_cannot_edit_someone_elses_event(admin, app):
    m1 = login_as(app, admin, "m1", "member")
    m2 = login_as(app, admin, "m2", "member")
    r = m1.post("/events/new", {"title": "Cleanup", "type_id": "5", "event_date": "2026-11-28"})
    event_id = int(r.headers["Location"].split("/")[-1])
    assert m2.get(f"/events/{event_id}/edit").status_code == 403
    assert m1.get(f"/events/{event_id}/edit").status_code == 200
    assert admin.get(f"/events/{event_id}/edit").status_code == 200


def test_last_admin_cannot_be_demoted(admin, app):
    r = admin.post("/people/1/edit", {"full_name": "Admin Person", "role": "member", "active": "1"})
    assert r.status_code == 200
    assert db_of(app).execute("SELECT role FROM users WHERE id = 1").fetchone()[0] == "admin"


# --- inventory & audit ----------------------------------------------------------------

def test_item_edit_is_timestamped_and_in_history(admin, app):
    item_id = add_item(admin, "Mixer", 1, location="Sacristy")
    admin.post(f"/items/{item_id}/edit", {"name": "Mixer", "quantity": "2", "location": "Hall",
                                          "condition": "good", "reservable": "on"})
    db = db_of(app)
    rows = db.execute("SELECT * FROM audit_log WHERE entity = 'item' AND entity_id = ? ORDER BY id",
                      (item_id,)).fetchall()
    assert [r["action"] for r in rows] == ["create", "update"]
    changes = json.loads(rows[1]["changes"])
    assert changes == {"location": ["Sacristy", "Hall"], "quantity": [1, 2]}
    assert rows[1]["username"] == "admin"
    page = admin.get(f"/items/{item_id}").get_data(as_text=True)
    assert "Change history" in page and "Sacristy" in page


def test_audit_log_is_append_only_and_tamper_evident(admin, app):
    add_item(admin, "Chairs", 50)
    db = db_of(app)
    assert audit.verify(db)["ok"]
    import sqlite3
    try:
        db.execute("UPDATE audit_log SET summary = 'x' WHERE id = 1")
        raise AssertionError("update should be blocked")
    except sqlite3.IntegrityError:
        pass
    try:
        db.execute("DELETE FROM audit_log WHERE id = 1")
        raise AssertionError("delete should be blocked")
    except sqlite3.IntegrityError:
        pass
    # Someone with file access drops the trigger and rewrites history: the chain detects it.
    db.execute("DROP TRIGGER audit_log_no_update")
    db.execute("UPDATE audit_log SET summary = 'forged' WHERE id = 2")
    result = audit.verify(db)
    assert not result["ok"] and result["broken_id"] == 2
    r = admin.post("/audit/verify", follow_redirects=True)
    assert "WARNING" in r.get_data(as_text=True)


def test_audit_export_and_report(admin):
    add_item(admin, "Candles", 100)
    csv_text = admin.get("/audit/export.csv").get_data(as_text=True)
    assert "Candles" in csv_text and "prev_hash" in csv_text
    assert admin.get("/audit/report").status_code == 200


def test_archive_instead_of_delete(admin, app):
    item_id = add_item(admin, "Old amp")
    admin.post(f"/items/{item_id}/archive", {"reason": "broken"})
    assert "Old amp" not in admin.get("/items/").get_data(as_text=True)
    assert "Old amp" in admin.get("/items/?archived=1").get_data(as_text=True)
    row = db_of(app).execute("SELECT summary FROM audit_log WHERE action = 'archive'").fetchone()
    assert "broken" in row[0]


def test_photo_upload_strips_and_hashes(admin, app):
    item_id = add_item(admin, "Cross")
    r = admin.post(f"/items/{item_id}/photos", {"photos": (jpeg_bytes(), "cross.jpg")},
                   content_type="multipart/form-data")
    assert r.status_code == 302
    photo = db_of(app).execute("SELECT * FROM photos").fetchone()
    assert len(photo["sha256"]) == 64
    assert admin.get(f"/uploads/{photo['filename']}").status_code == 200
    assert admin.get(f"/uploads/thumbs/{photo['filename']}").status_code == 200
    bad = admin.post(f"/items/{item_id}/photos", {"photos": (jpeg_bytes().__class__(b"nope"), "x.jpg")},
                     content_type="multipart/form-data", follow_redirects=True)
    assert "not a supported image" in bad.get_data(as_text=True)
    admin.post(f"/items/photos/{photo['id']}/hide")
    assert db_of(app).execute("SELECT hidden FROM photos").fetchone()[0] == 1


def test_qr_and_labels(admin):
    item_id = add_item(admin, "Microphone")
    r = admin.get(f"/items/{item_id}/qr.png")
    assert r.status_code == 200 and r.data[:4] == b"\x89PNG"
    assert "Microphone" in admin.get(f"/items/labels?id={item_id}").get_data(as_text=True)


# --- events -------------------------------------------------------------------------------

def test_event_with_participants_and_photo(admin, app):
    login_as(app, admin, "peter", "member")
    r = admin.post("/events/new", {"title": "Christmas concert", "type_id": "6",
                                   "event_date": "2026-12-24", "start_time": "18:00",
                                   "end_time": "20:00", "participants": ["1", "2"],
                                   "description": "Choir + brass", "photos": (jpeg_bytes(), "c.jpg")},
                   content_type="multipart/form-data")
    assert r.status_code == 302
    event_id = int(r.headers["Location"].split("/")[-1])
    page = admin.get(f"/events/{event_id}").get_data(as_text=True)
    assert "Christmas concert" in page and "peter" in page and "Choir + brass" in page
    assert "Concert" in admin.get("/events/?user=2").get_data(as_text=True)
    assert "Christmas concert" in admin.get("/events/export.csv").get_data(as_text=True)
    # editing participants is recorded
    admin.post(f"/events/{event_id}/edit", {"title": "Christmas concert", "type_id": "6",
                                            "event_date": "2026-12-24", "participants": ["1"]})
    last = db_of(app).execute("SELECT changes FROM audit_log WHERE entity='event' ORDER BY id DESC"
                              " LIMIT 1").fetchone()[0]
    assert "participants" in json.loads(last)


# --- reservations ------------------------------------------------------------------------

def test_reservation_clash_detection(admin, app):
    item_id = add_item(admin, "Projector", quantity=1)
    member = login_as(app, admin, "eva", "member")
    assert reserve(admin, item_id, "2026-12-01T10:00", "2026-12-01T12:00").status_code == 302
    # overlapping booking for the only projector is a clash: needs confirmation
    r = reserve(member, item_id, "2026-12-01T11:00", "2026-12-01T13:00")
    assert r.status_code == 200 and "clashes" in r.get_data(as_text=True)
    r = reserve(member, item_id, "2026-12-01T11:00", "2026-12-01T13:00", confirm_clash="1")
    assert r.status_code == 302
    # back-to-back booking is not a clash
    assert reserve(member, item_id, "2026-12-01T12:00", "2026-12-01T14:00").status_code == 200
    listing = admin.get("/reservations/").get_data(as_text=True)
    assert "clash" in listing
    # managers were notified, including the clash warning
    sent = app.extensions["sent_notifications"]
    assert any("clashes" in body for _u, _s, body in sent)


def test_quantity_aware_clash(admin):
    item_id = add_item(admin, "Chairs", quantity=10)
    assert reserve(admin, item_id, "2026-12-05T08:00", "2026-12-05T18:00", qty=6).status_code == 302
    assert reserve(admin, item_id, "2026-12-05T09:00", "2026-12-05T10:00", qty=4).status_code == 302
    r = reserve(admin, item_id, "2026-12-05T09:30", "2026-12-05T11:00", qty=1)
    assert r.status_code == 200  # 6 + 4 + 1 > 10
    r = admin.get(f"/reservations/availability?item_id={item_id}&start_at=2026-12-05T10:00"
                  "&end_at=2026-12-05T11:00&quantity=4")
    assert r.json == {**r.json, "ok": True, "clash": False, "available": 4}


def test_reservation_workflow(admin, app):
    item_id = add_item(admin, "Speaker")
    member = login_as(app, admin, "ivan", "member")
    r = reserve(member, item_id, "2026-12-02T10:00", "2026-12-02T12:00")
    rid = int(r.headers["Location"].split("/")[-1])
    db = db_of(app)
    assert db.execute("SELECT status FROM reservations WHERE id = ?", (rid,)).fetchone()[0] == "pending"
    assert member.post(f"/reservations/{rid}/status", {"action": "approve"}).status_code == 403
    admin.post(f"/reservations/{rid}/status", {"action": "approve", "note": "ok"})
    admin.post(f"/reservations/{rid}/status", {"action": "checkout"})
    admin.post(f"/reservations/{rid}/status", {"action": "return", "condition": "damaged",
                                               "update_item_condition": "1", "note": "cable cut"})
    assert db.execute("SELECT status FROM reservations WHERE id = ?", (rid,)).fetchone()[0] == "returned"
    assert db.execute("SELECT condition FROM items WHERE id = ?", (item_id,)).fetchone()[0] == "damaged"
    actions = [r[0] for r in db.execute("SELECT action FROM audit_log WHERE entity='reservation'"
                                        " AND entity_id = ? ORDER BY id", (rid,))]
    assert actions == ["create", "approve", "checkout", "return"]


# --- calendar --------------------------------------------------------------------------------

def test_ical_feed(admin, app):
    item_id = add_item(admin, "Projector")
    reserve(admin, item_id, "2026-12-01T10:00", "2026-12-01T12:00")
    admin.post("/events/new", {"title": "Sunday Mass, 10am", "type_id": "1", "event_date": "2026-12-06",
                               "start_time": "10:00", "participants": ["1"]})
    token = db_of(app).execute("SELECT ical_token FROM users WHERE id = 1").fetchone()[0]
    c = Client(app)  # not logged in: the token is the key
    r = c.get(f"/calendar/{token}.ics")
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and r.mimetype == "text/calendar"
    assert "BEGIN:VEVENT" in body and "Sunday Mass\\, 10am" in body and "Projector" in body
    assert "DTSTART:20261201T090000Z" in body  # 10:00 Bratislava (UTC+1) in UTC
    assert all(len(line.encode()) <= 75 for line in body.split("\r\n"))
    assert c.get("/calendar/wrong.ics").status_code == 404


# --- backups & i18n ---------------------------------------------------------------------------

def test_backup(admin, app):
    add_item(admin, "Bible")
    r = admin.post("/admin/backup", follow_redirects=True)
    assert "church-inventory-" in r.get_data(as_text=True)
    import os
    import tarfile
    files = os.listdir(app.config["BACKUP_DIR"])
    assert len(files) == 1
    with tarfile.open(os.path.join(app.config["BACKUP_DIR"], files[0])) as tar:
        assert "church.db" in tar.getnames()


def test_slovak_translation_complete():
    missing = sorted(s for s in strings() if s not in SK)
    assert not missing, f"missing Slovak translations: {missing}"


def test_slovak_ui(admin):
    admin.post("/language/sk", {"next": "/"})
    page = admin.get("/").get_data(as_text=True)
    assert "Prehľad" in page and "Inventár" in page


def test_pages_render(admin, app):
    item_id = add_item(admin, "Altar cloth")
    reserve(admin, item_id, "2026-12-01T10:00", "2026-12-01T12:00")
    for lang in ("en", "sk"):
        admin.post(f"/language/{lang}", {"next": "/"})
        for url in ("/", "/items/", f"/items/{item_id}", f"/items/{item_id}/edit", "/items/new",
                    "/events/", "/events/new", "/reservations/", "/reservations/new",
                    "/reservations/1", "/reservations/1/edit", "/people/", "/people/1",
                    "/people/new", "/people/1/edit", "/audit/", "/audit/report", "/admin/",
                    "/profile", "/items/export.csv", "/items/labels"):
            r = admin.get(url)
            assert r.status_code == 200, (lang, url)
            assert "built-in method" not in r.get_data(as_text=True), (lang, url)
