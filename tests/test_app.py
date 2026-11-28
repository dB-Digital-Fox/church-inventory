import io
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


def test_english_labels_for_internal_codes():
    from app.translations_en import EN
    from .extract_strings import DYNAMIC
    raw = sorted(code for code in DYNAMIC if "_" in code and code not in EN)
    assert not raw, f"codes shown raw in English: {raw}"


def test_handed_out_status_is_readable(admin):
    item_id = add_item(admin, "Lamp")
    r = reserve(admin, item_id, "2026-12-01T10:00", "2026-12-01T12:00")
    rid = int(r.headers["Location"].split("/")[-1])
    admin.post(f"/reservations/{rid}/status", {"action": "checkout"})
    page = admin.get(f"/reservations/{rid}").get_data(as_text=True)
    assert "handed out" in page and ">checked_out<" not in page


# --- rooms and spaces --------------------------------------------------------------------------

def test_space_booking_and_clash(admin, app):
    hall = add_item(admin, "Parish hall", kind="space", location="Ground floor")
    assert db_of(app).execute("SELECT kind FROM items WHERE id = ?", (hall,)).fetchone()[0] == "space"
    rooms = admin.get("/items/?kind=space").get_data(as_text=True)
    assert "Parish hall" in rooms
    assert "Parish hall" not in admin.get("/items/").get_data(as_text=True)
    assert reserve(admin, hall, "2026-12-10T17:00", "2026-12-10T19:00").status_code == 302
    r = reserve(admin, hall, "2026-12-10T18:00", "2026-12-10T20:00")
    assert r.status_code == 200 and "clashes" in r.get_data(as_text=True)
    form = admin.get("/reservations/new").get_data(as_text=True)
    assert "<optgroup" in form and "Parish hall" in form
    token = db_of(app).execute("SELECT ical_token FROM users WHERE id = 1").fetchone()[0]
    assert "🏛 Parish hall" in Client(app).get(f"/calendar/{token}.ics").get_data(as_text=True)


# --- maintenance -------------------------------------------------------------------------------

def add_task(c, item_id, title="Fire extinguisher check", interval=365, next_due="2026-12-01", **extra):
    data = {"item_id": str(item_id), "title": title, "interval_days": str(interval),
            "next_due": next_due}
    data.update(extra)
    r = c.post("/maintenance/tasks/new", data)
    assert r.status_code == 302, r.get_data(as_text=True)
    return int(r.headers["Location"].split("/")[-1])


def test_maintenance_task_and_log_advance_due_date(admin, app):
    item_id = add_item(admin, "Organ")
    task_id = add_task(admin, item_id, "Organ tuning", interval=180, next_due="2026-11-01")
    assert "Organ tuning" in admin.get("/maintenance/").get_data(as_text=True)
    assert "Organ tuning" in admin.get("/").get_data(as_text=True)  # overdue on the dashboard
    member = login_as(app, admin, "jan", "member")
    assert member.post("/maintenance/tasks/new", {"item_id": str(item_id), "title": "x",
                                                  "interval_days": "1",
                                                  "next_due": "2026-12-01"}).status_code == 403
    r = member.post("/maintenance/log/new", {"task_id": str(task_id), "done_on": "2026-11-20",
                                             "performed_by": "Organ Ltd.", "result": "issues",
                                             "cost": "120 €", "notes": "one pipe replaced",
                                             "photos": (jpeg_bytes(), "pipe.jpg")},
                    content_type="multipart/form-data")
    assert r.status_code == 302
    log_id = int(r.headers["Location"].split("/")[-1])
    db = db_of(app)
    assert db.execute("SELECT next_due FROM maintenance_tasks WHERE id = ?",
                      (task_id,)).fetchone()[0] == "2027-05-19"
    assert db.execute("SELECT COUNT(*) FROM photos WHERE entity = 'maintenance' AND entity_id = ?",
                      (log_id,)).fetchone()[0] == 1
    page = admin.get(f"/items/{item_id}").get_data(as_text=True)
    assert "Organ tuning" in page and "Organ Ltd." in page
    # future dates are refused
    r = member.post("/maintenance/log/new", {"task_id": str(task_id), "done_on": "2099-01-01"})
    assert r.status_code == 200 and "future" in r.get_data(as_text=True)
    # the person who logged it can edit; the change is in the history
    r = member.post(f"/maintenance/log/{log_id}/edit", {"done_on": "2026-11-20",
                                                         "performed_by": "Organ Ltd.",
                                                         "result": "ok", "notes": "fixed"})
    assert r.status_code == 302
    actions = [a for (a,) in db.execute("SELECT action FROM audit_log WHERE entity = 'maintenance'"
                                        " AND entity_id = ? ORDER BY id", (log_id,))]
    assert actions == ["create", "photo_add", "update"]
    other = login_as(app, admin, "peter", "member")
    assert other.post(f"/maintenance/log/{log_id}/edit", {"done_on": "2026-11-20"}).status_code == 403


def test_one_off_task_is_closed_when_done(admin, app):
    item_id = add_item(admin, "Roof")
    task_id = add_task(admin, item_id, "Repair gutter", interval=0, next_due="2026-11-25")
    admin.post("/maintenance/log/new", {"task_id": str(task_id), "done_on": "2026-11-26"})
    assert db_of(app).execute("SELECT active FROM maintenance_tasks WHERE id = ?",
                              (task_id,)).fetchone()[0] == 0
    # a task can be switched off by unticking "active"
    task2 = add_task(admin, item_id, "Clean chimney")
    admin.post(f"/maintenance/tasks/{task2}/edit", {"item_id": str(item_id), "title": "Clean chimney",
                                                     "interval_days": "365", "next_due": "2026-12-01"})
    assert db_of(app).execute("SELECT active FROM maintenance_tasks WHERE id = ?",
                              (task2,)).fetchone()[0] == 0


def test_reminder_schedule():
    from datetime import date
    from app.views.maintenance import reminder_due
    task = {"next_due": "2026-12-15"}
    hits = [d for d in range(1, 32) if reminder_due(task, date(2026, 12, d))]
    assert hits == [1, 8, 14, 15, 22, 29]  # 14, 7, 1 days before, on the day, weekly overdue
    assert not reminder_due({"next_due": ""}, date(2026, 12, 1))


def test_maintenance_reminders_are_sent(admin, app, monkeypatch):
    from datetime import datetime
    from app import scheduler, utils
    app.config.update(SMTP_HOST="smtp.test", SMTP_FROM="church@test")
    item_id = add_item(admin, "Boiler")
    add_task(admin, item_id, "Boiler service", next_due="2026-12-08")
    add_task(admin, item_id, "Boiler inspection", next_due="2026-12-09")
    sent = []
    monkeypatch.setattr(scheduler, "deliver", lambda cfg, people, build: sent.extend(
        (p["username"],) + build("en") for p in people))
    monkeypatch.setattr(utils, "now_local", lambda: datetime(2026, 12, 1, 8, 0))
    scheduler.send_daily_notifications(app)
    subjects = [subject for _u, subject, _b in sent]
    assert "Maintenance due: Boiler service" in subjects  # 7 days before
    assert not any("inspection" in s for s in subjects)  # 8 days before: no reminder


# --- donations and asset value -----------------------------------------------------------------

def test_depreciation_and_book_value():
    from datetime import date
    from app.assets import book_value, depreciation_in_year, money, parse_money, total_value
    item = {"unit_value": 1000.0, "quantity": 2, "purchase_date": "2024-01-01", "useful_life_years": 4}
    assert total_value(item) == 2000.0
    assert book_value(item, date(2024, 1, 1)) == 2000.0
    assert abs(book_value(item, date(2026, 1, 1)) - 1000.0) < 2
    assert book_value(item, date(2030, 1, 1)) == 0.0
    assert abs(depreciation_in_year(item, 2025) - 500.0) < 2
    assert depreciation_in_year(item, 2023) == 0.0
    no_life = dict(item, useful_life_years=0)
    assert book_value(no_life, date(2030, 1, 1)) == 2000.0
    assert parse_money("1 234,50 €") == 1234.5 and parse_money("") is None
    assert money(1234.5) == "1\u00a0234,50 €" and money(None) == "—"


def test_donation_and_assets_report(admin, app):
    add_item(admin, "Chalice", acquisition="donation", donor="Novák family", unit_value="1 200,00",
             useful_life_years="0", purchase_date="2026-03-01")
    add_item(admin, "Laptop", acquisition="purchase", donor="ignored", unit_value="800",
             useful_life_years="4", purchase_date="2026-01-01")
    add_item(admin, "Old chair")  # no value
    db = db_of(app)
    assert db.execute("SELECT donor FROM items WHERE name = 'Laptop'").fetchone()[0] == ""
    r = admin.post("/items/new", {"name": "Bad", "quantity": "1", "condition": "good",
                                  "unit_value": "abc"})
    assert r.status_code == 200
    page = admin.get("/reports/assets?year=2026").get_data(as_text=True)
    assert "Novák family" in page and "1\u00a0200,00 €" in page
    csv_text = admin.get("/reports/assets?year=2026&format=csv").get_data(as_text=True)
    assert "Chalice" in csv_text and "donation" in csv_text and "book_value_2026-12-31_eur" in csv_text
    assert db.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'export' AND entity = 'report'"
                      ).fetchone()[0] == 2
    auditor = login_as(app, admin, "audit", "auditor")
    assert auditor.get("/reports/assets").status_code == 200
    member = login_as(app, admin, "mia", "member")
    assert member.get("/reports/assets").status_code == 403


def test_old_database_is_migrated(tmp_path):
    """A database made by v0.1 gets the new columns, keeps values and accepts maintenance photos."""
    import os
    import sqlite3
    from app.db import init_db
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    with open(os.path.join(os.path.dirname(__file__), "schema_v01.sql"), encoding="utf-8") as f:
        conn.executescript(f.read())
    now = "2026-01-01T00:00:00Z"
    conn.execute("INSERT INTO users (username, full_name, role, password_hash, ical_token, created_at,"
                 " updated_at) VALUES ('a', 'A', 'admin', 'x', 't', ?, ?)", (now, now))
    conn.execute("INSERT INTO items (name, purchase_value, created_at, updated_at)"
                 " VALUES ('Piano', '2 500,50 €', ?, ?)", (now, now))
    conn.execute("INSERT INTO items (name, purchase_value, created_at, updated_at)"
                 " VALUES ('Cross', 'priceless', ?, ?)", (now, now))
    conn.execute("INSERT INTO photos (entity, entity_id, filename, sha256, uploaded_at)"
                 " VALUES ('item', 1, 'abc.jpg', 'h', ?)", (now,))
    conn.commit()
    conn.close()
    init_db(path)
    init_db(path)  # running again is harmless
    conn = connect(path)
    rows = {r["name"]: r for r in conn.execute("SELECT * FROM items")}
    assert rows["Piano"]["unit_value"] == 2500.5 and rows["Piano"]["kind"] == "item"
    assert rows["Cross"]["unit_value"] is None and rows["Cross"]["acquisition"] == "purchase"
    assert conn.execute("SELECT filename FROM photos WHERE id = 1").fetchone()[0] == "abc.jpg"
    conn.execute("INSERT INTO photos (entity, entity_id, filename, sha256, uploaded_at)"
                 " VALUES ('maintenance', 1, 'def.jpg', 'h', ?)", (now,))
    assert conn.execute("SELECT COUNT(*) FROM maintenance_tasks").fetchone()[0] == 0
    assert "gallery" in {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    assert conn.execute("SELECT COUNT(*) FROM roster_roles").fetchone()[0] == 7


def test_new_pages_render(admin, app):
    item_id = add_item(admin, "Bell")
    task_id = add_task(admin, item_id, "Bell rope check")
    admin.post("/maintenance/log/new", {"task_id": str(task_id), "done_on": "2026-11-20"})
    for lang in ("en", "sk"):
        admin.post(f"/language/{lang}", {"next": "/"})
        for url in ("/items/?kind=space", "/items/new?kind=space", "/items/?acquisition=donation",
                    "/maintenance/", "/maintenance/tasks/new", f"/maintenance/tasks/{task_id}",
                    f"/maintenance/tasks/{task_id}/edit", "/maintenance/log/new",
                    f"/maintenance/log/new?task_id={task_id}", "/maintenance/log/1",
                    "/maintenance/log/1/edit", "/reports/", "/reports/assets",
                    f"/items/{item_id}", "/audit/"):
            r = admin.get(url)
            assert r.status_code == 200, (lang, url)
            assert "built-in method" not in r.get_data(as_text=True), (lang, url)


# --- volunteer rosters -------------------------------------------------------------------------

def add_event(c, title="Sunday Mass", date="2026-12-06", **extra):
    data = {"title": title, "type_id": "1", "event_date": date, "start_time": "10:00"}
    data.update(extra)
    r = c.post("/events/new", data)
    assert r.status_code == 302, r.get_data(as_text=True)
    return int(r.headers["Location"].split("/")[-1])


def slots_of(app, event_id):
    return [dict(r) for r in db_of(app).execute(
        "SELECT * FROM roster_slots WHERE event_id = ? ORDER BY id", (event_id,))]


def test_roster_assign_signup_and_swap(admin, app, monkeypatch):
    from datetime import datetime
    from app import utils
    monkeypatch.setattr(utils, "now_local", lambda: datetime(2026, 12, 1, 9, 0))
    eva = login_as(app, admin, "eva", "member")
    ivan = login_as(app, admin, "ivan", "member")
    auditor = login_as(app, admin, "aud", "auditor")
    ids = {u: i for i, u in db_of(app).execute("SELECT id, username FROM users")}
    event_id = add_event(admin)
    app.extensions["sent_notifications"] = []
    # one reader assigned to Eva, two open server places
    admin.post(f"/rosters/event/{event_id}/add", {"role_id": "1", "user_id": str(ids["eva"])})
    admin.post(f"/rosters/event/{event_id}/add", {"role_id": "2", "count": "2"})
    slots = slots_of(app, event_id)
    assert [(s["role_id"], s["user_id"]) for s in slots] == [(1, ids["eva"]), (2, None), (2, None)]
    assert any(u == "eva" and "roster" in subj for u, subj, _b in app.extensions["sent_notifications"])
    # members cannot add places to someone else's event
    assert eva.post(f"/rosters/event/{event_id}/add", {"role_id": "1"}).status_code == 403
    # Ivan signs up for an open place; auditors cannot
    assert auditor.post(f"/rosters/{slots[1]['id']}/take").status_code == 403
    ivan.post(f"/rosters/{slots[1]['id']}/take")
    assert slots_of(app, event_id)[1]["user_id"] == ids["ivan"]
    # taking a place that is already filled does nothing
    eva.post(f"/rosters/{slots[1]['id']}/take")
    assert slots_of(app, event_id)[1]["user_id"] == ids["ivan"]
    # Eva can't come: she asks for a replacement and stays on until someone takes over
    assert ivan.post(f"/rosters/{slots[0]['id']}/swap", {"note": "x"}).status_code == 403
    eva.post(f"/rosters/{slots[0]['id']}/swap", {"note": "sick"})
    s0 = slots_of(app, event_id)[0]
    assert s0["swap_requested"] == 1 and s0["user_id"] == ids["eva"] and s0["swap_note"] == "sick"
    page = ivan.get("/rosters/").get_data(as_text=True)
    assert "Help needed" in page and "sick" in page
    assert "Help needed on the roster" in ivan.get("/").get_data(as_text=True)
    app.extensions["sent_notifications"] = []
    ivan.post(f"/rosters/{slots[0]['id']}/take")
    s0 = slots_of(app, event_id)[0]
    assert s0["user_id"] == ids["ivan"] and s0["swap_requested"] == 0
    assert any(u == "eva" and "replacement" in subj for u, subj, _b in app.extensions["sent_notifications"])
    # every step is in the audit log, with names instead of ids
    db = db_of(app)
    actions = [a for (a,) in db.execute("SELECT action FROM audit_log WHERE entity = 'roster'"
                                        " ORDER BY id")]
    assert actions == ["create", "create", "create", "signup", "swap_request", "swap"]
    last = db.execute("SELECT changes FROM audit_log WHERE action = 'swap'").fetchone()[0]
    assert "eva" in last and "ivan" in last
    # the manager removes a place
    admin.post(f"/rosters/{slots[2]['id']}/remove")
    assert len(slots_of(app, event_id)) == 2
    # Ivan's calendar shows his duty; "mine" includes events where he serves
    token = db.execute("SELECT ical_token FROM users WHERE username = 'ivan'").fetchone()[0]
    feed = Client(app).get(f"/calendar/{token}.ics?scope=mine").get_data(as_text=True)
    assert "Sunday Mass" in feed and "🙋" in feed


def test_roster_reminders(admin, app, monkeypatch):
    from datetime import datetime
    from app import scheduler, utils
    app.config.update(SMTP_HOST="smtp.test", SMTP_FROM="church@test")
    eva = login_as(app, admin, "eva", "member")
    event_id = add_event(admin, date="2026-12-02")
    admin.post(f"/rosters/event/{event_id}/add", {"role_id": "1", "user_id": "2"})
    other = add_event(admin, "Vespers", date="2026-12-03")
    admin.post(f"/rosters/event/{other}/add", {"role_id": "3"})
    assert eva  # created
    sent = []
    monkeypatch.setattr(scheduler, "deliver", lambda cfg, people, build: sent.extend(
        (p["username"],) + build("en") for p in people))
    monkeypatch.setattr(utils, "now_local", lambda: datetime(2026, 12, 1, 8, 0))
    scheduler.send_daily_notifications(app)
    assert ("eva", "Reminder: you serve tomorrow") in [(u, s) for u, s, _b in sent]
    gap = [b for u, s, b in sent if s == "Roster gaps in two days"]
    assert gap and "Cantor" in gap[0] and "Vespers" in gap[0]


def test_roster_roles_admin(admin, app):
    admin.post("/admin/roster-roles", {"name_sk": "Žalmista", "name_en": "Psalmist"})
    admin.post("/admin/roster-roles", {"id": "1", "name_sk": "Lektor", "name_en": "Reader"})
    rows = {r["name_en"]: r["active"] for r in db_of(app).execute("SELECT * FROM roster_roles")}
    assert rows["Psalmist"] == 1 and rows["Reader"] == 0  # unticked = switched off
    event_id = add_event(admin)
    page = admin.get(f"/events/{event_id}").get_data(as_text=True)
    assert "Psalmist" in page and ">Reader<" not in page


# --- event galleries ---------------------------------------------------------------------------

def test_event_galleries(admin, app):
    pub = add_event(admin, "Advent concert", gallery="public",
                    description="internal note: key under the mat")
    mem = add_event(admin, "Youth camp", gallery="members")
    off = add_event(admin, "Council meeting")
    for eid in (pub, mem, off):
        admin.post(f"/events/{eid}/photos", {"photos": (jpeg_bytes(), "p.jpg")},
                   content_type="multipart/form-data")
    db = db_of(app)
    photo = {eid: db.execute("SELECT id FROM photos WHERE entity = 'event' AND entity_id = ?",
                             (eid,)).fetchone()[0] for eid in (pub, mem, off)}
    anon = Client(app)
    page = anon.get("/gallery/").get_data(as_text=True)
    assert "Advent concert" in page and "Youth camp" not in page and "Council" not in page
    page = anon.get(f"/gallery/{pub}").get_data(as_text=True)
    assert "Advent concert" in page and "key under the mat" not in page
    assert anon.get(f"/gallery/{mem}").status_code == 302  # asked to log in
    r = anon.get(f"/gallery/photo/{photo[pub]}/thumb")
    assert r.status_code == 200 and r.mimetype == "image/jpeg" and "public" in r.headers["Cache-Control"]
    assert anon.get(f"/gallery/photo/{photo[mem]}").status_code == 404
    assert anon.get(f"/gallery/photo/{photo[off]}").status_code == 404
    assert anon.get(f"/uploads/x.jpg").status_code == 302  # normal uploads still need login
    # members see members-only galleries too, but not the ones switched off
    page = admin.get("/gallery/").get_data(as_text=True)
    assert "Youth camp" in page and "Council" not in page
    assert admin.get(f"/gallery/photo/{photo[mem]}").status_code == 200
    assert admin.get(f"/gallery/{off}").status_code == 404
    # a hidden photo disappears from the gallery
    admin.post(f"/items/photos/{photo[pub]}/hide")
    hidden = db.execute("SELECT hidden FROM photos WHERE id = ?", (photo[pub],)).fetchone()[0]
    assert hidden == 1
    assert anon.get(f"/gallery/photo/{photo[pub]}").status_code == 404
    # the setting change is in the event history
    admin.post(f"/events/{pub}/edit", {"title": "Advent concert", "type_id": "1",
                                       "event_date": "2026-12-06", "gallery": "off"})
    assert anon.get(f"/gallery/{pub}").status_code == 302
    entry = db.execute("SELECT changes FROM audit_log WHERE entity = 'event' AND entity_id = ?"
                       " ORDER BY id DESC", (pub,)).fetchone()[0]
    assert "gallery" in entry


def test_roster_and_gallery_pages_render(admin, app):
    event_id = add_event(admin, gallery="public")
    admin.post(f"/rosters/event/{event_id}/add", {"role_id": "1", "user_id": "1"})
    admin.post(f"/rosters/event/{event_id}/add", {"role_id": "2"})
    for lang in ("en", "sk"):
        admin.post(f"/language/{lang}", {"next": "/"})
        for url in ("/rosters/", f"/events/{event_id}", f"/events/{event_id}/edit", "/gallery/",
                    "/admin/", "/", "/events/", "/audit/"):
            r = admin.get(url)
            assert r.status_code == 200, (lang, url)
            assert "built-in method" not in r.get_data(as_text=True), (lang, url)


# --- reports and exports -----------------------------------------------------------------------

def read_xlsx(response):
    from openpyxl import load_workbook
    assert response.status_code == 200, response.get_data(as_text=True)[:500]
    assert response.mimetype.endswith("spreadsheetml.sheet")
    return load_workbook(io.BytesIO(response.get_data()))


def test_excel_exports(admin, app):
    add_item(admin, "Chalice", acquisition="donation", donor="Novák family", unit_value="1200",
             purchase_date="2026-03-01")
    add_item(admin, "=HYPERLINK(\"http://evil\")")  # typed text never becomes a formula
    wb = read_xlsx(admin.get("/reports/inventory.xlsx"))
    ws = wb.worksheets[0]
    values = [c.value for row in ws.iter_rows() for c in row]
    assert "Chalice" in values and "Novák family" in values
    formula = [c for row in ws.iter_rows() for c in row if str(c.value).startswith("=HYPERLINK")][0]
    assert formula.data_type == "s"
    wb = read_xlsx(admin.get("/reports/assets?year=2026&format=xlsx"))
    assert len(wb.worksheets) == 2 and wb.worksheets[1].max_row >= 2
    event_id = add_event(admin, "Choir rehearsal", date="2026-11-10",
                         description="New songs for Advent", participants=["1"])
    admin.post(f"/rosters/event/{event_id}/add", {"role_id": "3", "user_id": "1"})
    page = admin.get("/reports/events?from=2026-11-01&to=2026-11-30").get_data(as_text=True)
    assert "Choir rehearsal" in page and "New songs for Advent" in page and "Cantor" in page
    wb = read_xlsx(admin.get("/reports/events?from=2026-11-01&to=2026-11-30&format=xlsx"))
    assert [ws.title for ws in wb.worksheets] == ["Events", "By type", "By person"]
    assert wb["By person"]["A2"].value == "Admin Person" and wb["By person"]["B2"].value == 1
    wb = read_xlsx(admin.get("/reports/maintenance.xlsx?year=2026"))
    assert wb.worksheets[0].title == "Maintenance"


def test_annual_audit_export(admin, app):
    add_item(admin, "Bell")
    wb = read_xlsx(admin.get("/reports/audit-year.xlsx?year=2026"))
    integrity = {r[0].value: r[1].value for r in wb["Integrity"].iter_rows(min_row=2)}
    assert integrity["Year"] == 2026 and "chain intact" in integrity["Whole log checked now"]
    log = wb["Audit log"]
    summaries = [r[6].value for r in log.iter_rows(min_row=2)]
    assert "Bell" in summaries
    db = db_of(app)
    last_hash = db.execute("SELECT hash FROM audit_log WHERE ts LIKE '2026%' ORDER BY id DESC"
                           ).fetchone()[0]
    assert integrity["Fingerprint of the last entry of the year"] is not None
    assert len(last_hash) == 64
    # an empty year still works; members may not export the audit log
    assert read_xlsx(admin.get("/reports/audit-year.xlsx?year=2001"))["Integrity"]["B3"].value == 0
    member = login_as(app, admin, "mia", "member")
    assert member.get("/reports/audit-year.xlsx").status_code == 403
    assert member.get("/reports/events").status_code == 403
    # every export is logged
    assert db.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'export'").fetchone()[0] >= 2


# --- phone app: offline checks -----------------------------------------------------------------

def test_offline_checks_sync(admin, app):
    item_id = add_item(admin, "Projector", location="Sacristy")
    member = login_as(app, admin, "eva", "member")
    page = member.get("/m/").get_data(as_text=True)
    assert "Scan QR label" in page and "mobile.js" in page
    items = member.get("/m/items.json").json["items"]
    assert any(i["id"] == item_id and i["name"] == "Projector" for i in items)
    entries = [
        {"uid": "aaaaaaaa0001", "item_id": item_id, "checked_at": "2026-11-27T10:15",
         "condition": "damaged", "location": "Parish hall", "note": "lens scratched"},
        {"uid": "aaaaaaaa0002", "item_id": 9999, "checked_at": "2026-11-27T10:16", "condition": "good"},
        {"uid": "aaaaaaaa0003", "item_id": item_id, "checked_at": "nonsense"},
        {"uid": "bad uid!", "item_id": item_id, "checked_at": "2026-11-27T10:17"},
    ]
    token = member.token()
    r = member.c.post("/m/sync", json={"entries": entries}, headers={"X-CSRF-Token": token})
    assert r.status_code == 200
    assert r.json["saved"] == ["aaaaaaaa0001"]
    assert set(r.json["errors"]) == {"aaaaaaaa0002", "aaaaaaaa0003"}
    # sending the same check again (e.g. the connection dropped before the answer) is harmless
    r = member.c.post("/m/sync", json={"entries": entries[:1]}, headers={"X-CSRF-Token": token})
    assert r.json["saved"] == [] and r.json["duplicate"] == ["aaaaaaaa0001"]
    db = db_of(app)
    item = db.execute("SELECT condition, location FROM items WHERE id = ?", (item_id,)).fetchone()
    assert tuple(item) == ("damaged", "Parish hall")
    assert db.execute("SELECT COUNT(*) FROM item_checks").fetchone()[0] == 1
    actions = [a for (a,) in db.execute("SELECT action FROM audit_log WHERE entity = 'item'"
                                        " AND entity_id = ? ORDER BY id", (item_id,))]
    assert actions[-2:] == ["check", "update"]
    page = admin.get(f"/items/{item_id}").get_data(as_text=True)
    assert "lens scratched" in page and "eva" in page
    wb = read_xlsx(admin.get("/reports/inventory.xlsx"))
    row = [r for r in wb.worksheets[0].iter_rows(values_only=True) if r[1] == "Projector"][0]
    assert row[-2] == "2026-11-27" or str(row[-2]).startswith("2026-11-27")
    # no CSRF token, not logged in, or auditor: refused
    assert member.c.post("/m/sync", json={"entries": []}).status_code == 400
    assert Client(app).c.post("/m/sync", json={"entries": []},
                              headers={"X-CSRF-Token": "x"}).status_code in (400, 401)
    auditor = login_as(app, admin, "aud", "auditor")
    assert auditor.c.post("/m/sync", json={"entries": []},
                          headers={"X-CSRF-Token": auditor.token()}).status_code == 403


def test_pwa_files(admin, app):
    c = Client(app)
    r = c.get("/manifest.webmanifest")
    assert r.status_code == 200 and r.json["start_url"] == "/m/" and r.json["display"] == "standalone"
    r = c.get("/sw.js")
    body = r.get_data(as_text=True)
    assert r.mimetype == "application/javascript" and "__VERSION__" not in body
    assert r.headers["Service-Worker-Allowed"] == "/"
    for name in ("icon-192.png", "icon-512.png", "mobile.js", "vendor/jsQR.js"):
        assert c.get(f"/static/{name}").status_code == 200, name
    page = admin.get("/").get_data(as_text=True)
    assert 'rel="manifest"' in page and "data-sw=" in page


def test_export_pages_render(admin, app):
    add_item(admin, "Lamp")
    for lang in ("en", "sk"):
        admin.post(f"/language/{lang}", {"next": "/"})
        for url in ("/reports/", "/reports/events", "/m/", "/items/1", "/items/"):
            r = admin.get(url)
            assert r.status_code == 200, (lang, url)
            assert "built-in method" not in r.get_data(as_text=True), (lang, url)
