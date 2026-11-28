"""Fill an empty data directory with realistic demo data (used for screenshots and trying the app).

    DATA_DIR=./demo-data python scripts/seed_demo.py

Creates the admin "admin" / password "demo password 1" and a few volunteers
(password "demo password 1" for all). Never run this against a real installation.
"""
import io
import os
import re
import sys
from datetime import date, timedelta

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import create_app  # noqa: E402

PW = "demo password 1"


def picture(c1, c2, shape):
    img = Image.new("RGB", (800, 600), c1)
    d = ImageDraw.Draw(img)
    for y in range(600):
        t = y / 600
        d.line([(0, y), (800, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(c1, c2)))
    if shape == "cross":
        d.rectangle([370, 120, 430, 480], fill=(250, 240, 210))
        d.rectangle([280, 210, 520, 270], fill=(250, 240, 210))
    elif shape == "screen":
        d.rectangle([200, 150, 600, 420], fill=(30, 30, 40))
        d.rectangle([215, 165, 585, 405], fill=(90, 140, 220))
    elif shape == "mic":
        d.ellipse([340, 120, 460, 260], fill=(60, 60, 70))
        d.rectangle([385, 250, 415, 470], fill=(40, 40, 45))
    elif shape == "chairs":
        for x in (180, 330, 480):
            d.rectangle([x, 260, x + 110, 300], fill=(150, 90, 50))
            d.rectangle([x, 160, x + 20, 300], fill=(150, 90, 50))
            d.rectangle([x + 5, 300, x + 15, 420], fill=(110, 60, 30))
            d.rectangle([x + 95, 300, x + 105, 420], fill=(110, 60, 30))
    elif shape == "candles":
        for x in (300, 380, 460):
            d.rectangle([x, 260, x + 40, 470], fill=(250, 248, 235))
            d.ellipse([x + 10, 200, x + 30, 250], fill=(255, 190, 60))
    elif shape == "choir":
        for i, x in enumerate(range(120, 700, 90)):
            d.ellipse([x, 220 + (i % 2) * 30, x + 60, 280 + (i % 2) * 30], fill=(240, 210, 180))
            d.rectangle([x - 5, 285 + (i % 2) * 30, x + 65, 460], fill=(120, 30, 50))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    buf.seek(0)
    return buf


class C:
    def __init__(self, app):
        self.c = app.test_client()

    def post(self, url, data=None, **kw):
        with self.c.session_transaction() as s:
            s.setdefault("csrf", "seed")
            tok = s["csrf"]
        data = dict(data or {}, csrf_token=tok)
        r = self.c.post(url, data=data, content_type="multipart/form-data", **kw)
        assert r.status_code in (200, 302), (url, r.status_code)
        return r


def new_id(r):
    return int(r.headers["Location"].rstrip("/").split("/")[-1])


def main():
    app = create_app({"SCHEDULER": False})
    today = date.today()
    d = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731

    admin = C(app)
    admin.post("/setup", {"username": "admin", "full_name": "Daniel Novák", "password": PW,
                          "password2": PW, "language": "en"})
    people = [("maria", "Mária Kováčová", "manager"), ("peter", "Peter Horváth", "member"),
              ("jana", "Jana Szabóová", "member"), ("tomas", "Tomáš Baláž", "member"),
              ("audit", "Ing. Eva Králová", "auditor")]
    clients = {}
    for username, name, role in people:
        r = admin.post("/people/new", {"username": username, "full_name": name, "role": role,
                                       "language": "en", "email": f"{username}@example.org"})
        temp = re.search(r'<code class="big">([^<]+)</code>', r.get_data(as_text=True)).group(1)
        c = C(app)
        c.post("/login", {"username": username, "password": temp})
        c.post("/profile", {"action": "password", "current": temp, "password": PW, "password2": PW})
        clients[username] = c

    # name, qty, category, location, photo, serial, acquired, unit value, life (years), donor
    items = [
        ("Projector Epson EB-X51", 1, "Audio & video", "Sacristy", "screen", ((40, 60, 110), (20, 25, 50)), "X5QK123456", "2023-03-14", "489", "5", ""),
        ("Wireless microphone Shure", 4, "Audio & video", "Choir loft", "mic", ((200, 200, 210), (120, 120, 140)), "SH-BLX24", "2022-11-02", "259", "6", ""),
        ("Folding chairs", 60, "Furniture", "Parish hall", "chairs", ((235, 225, 205), (200, 185, 160)), "", "2019-05-20", "18", "10", ""),
        ("Processional cross", 1, "Liturgy", "Sacristy", "cross", ((110, 30, 50), (60, 10, 25)), "", "1998-04-12", "1500", "", "Horváth family"),
        ("Altar candles (large)", 24, "Liturgy", "Storage", "candles", ((60, 40, 70), (20, 10, 30)), "", "2026-10-01", "6", "", ""),
        ("Mixing desk Yamaha MG10", 1, "Audio & video", "Choir loft", "screen", ((50, 50, 55), (15, 15, 20)), "YMG10-88812", "2021-09-09", "199", "5", ""),
        ("Extension cords 25 m", 6, "Technical", "Storage", None, None, "", "", "", "", ""),
        ("Christmas nativity set", 1, "Decorations", "Attic", None, None, "", f"{today.year}-01-06", "850", "", "Ladies' rosary group"),
        ("Fire extinguisher 6 kg", 4, "Safety", "Church, hall, sacristy", None, None, "", "2020-02-01", "45", "10", ""),
    ]
    item_ids = {}
    for name, qty, cat, loc, shape, colors, serial, bought, value, life, donor in items:
        data = {"name": name, "quantity": str(qty), "category": cat, "location": loc,
                "condition": "good", "reservable": "on", "serial_number": serial,
                "purchase_date": bought, "unit_value": value, "useful_life_years": life,
                "acquisition": "donation" if donor else "purchase", "donor": donor}
        if shape:
            data["photos"] = (picture(*colors, shape), f"{shape}.jpg")
        item_ids[name] = new_id(admin.post("/items/new", data))
    for name, loc, text in (
            ("Parish hall", "Ground floor", "80 seats, kitchenette, projector screen."),
            ("Meeting room", "Rectory, 1st floor", "12 seats, whiteboard."),
            ("Church", "", "Book for weddings, concerts and recordings."),
            ("Kitchen", "Parish hall", "Fridge, oven, dishes for 60 people.")):
        item_ids[name] = new_id(admin.post("/items/new", {
            "name": name, "kind": "space", "quantity": "1", "location": loc, "condition": "good",
            "reservable": "on", "description": text}))
    # an edit, so the history shows something
    pid = item_ids["Projector Epson EB-X51"]
    admin.post(f"/items/{pid}/edit", {"name": "Projector Epson EB-X51", "quantity": "1",
                                      "category": "Audio & video", "location": "Parish hall",
                                      "condition": "fair", "reservable": "on",
                                      "serial_number": "X5QK123456", "purchase_date": "2023-03-14",
                                      "unit_value": "489", "useful_life_years": "5",
                                      "description": "Lamp replaced in 2025. Remote is in the drawer."})

    events = [
        ("Sunday Mass – 1st Sunday of Advent", 1, -1, "10:30", "11:45", "Church", ["admin", "peter", "jana"], "", "Sound by Peter, readings Jana. New wireless mics worked well.", None),
        ("Parish hall cleanup", 5, -6, "09:00", "12:00", "Parish hall", ["tomas", "jana", "maria"], "3 confirmation candidates", "Cleaned the hall and the storage room after the harvest festival. Two chairs broken (see inventory).", None),
        ("Recording: Advent catechesis", 3, -9, "18:00", "19:30", "Church", ["peter"], "", "Recorded and uploaded to YouTube. Mixing desk channel 3 crackles.", None),
        ("Mixing desk repair", 7, -4, "17:00", "18:00", "Choir loft", ["admin"], "", "Cleaned the channel 3 fader, works again.", None),
        ("Pastoral council meeting", 4, -12, "19:00", "20:30", "Rectory", ["maria", "admin"], "Fr. Jozef", "Christmas schedule agreed.", None),
        ("Advent concert", 6, 16, "17:00", "19:00", "Church", ["peter", "jana", "maria"], "Children's choir, guest brass quartet", "", "choir"),
        ("Youth conference 2026", 2, 30, "09:00", "16:00", "Parish hall", ["tomas", "maria"], "", "", None),
    ]
    event_ids = {}
    for title, type_id, offset, start, end, loc, who, others, text, photo in events:
        name_to_id = {"admin": "1", "maria": "2", "peter": "3", "jana": "4", "tomas": "5"}
        data = {"title": title, "type_id": str(type_id), "event_date": d(offset), "start_time": start,
                "end_time": end, "location": loc, "other_participants": others, "description": text,
                "participants": [name_to_id[w] for w in who]}
        if photo:
            data["photos"] = (picture((120, 30, 50), (40, 10, 20), "choir"), "choir.jpg")
        author = clients.get(who[0], admin)
        event_ids[title] = new_id(author.post("/events/new", data))
    ce = event_ids["Parish hall cleanup"]
    admin.post(f"/events/{ce}/photos", {"photos": (picture((235, 225, 205), (200, 185, 160), "chairs"), "hall.jpg")})
    # galleries: the cleanup and the recording are shown to everyone, the Mass to members only
    for title, mode, shots in (("Parish hall cleanup", "public", [((180, 200, 230), (90, 120, 170), "chairs"), ((240, 230, 200), (210, 190, 150), "candles")]),
                               ("Recording: Advent catechesis", "public", [((40, 40, 60), (10, 10, 20), "mic"), ((60, 40, 70), (20, 10, 30), "screen")]),
                               ("Sunday Mass – 1st Sunday of Advent", "members", [((110, 30, 50), (60, 10, 25), "cross"), ((60, 40, 70), (20, 10, 30), "candles"), ((120, 30, 50), (40, 10, 20), "choir")])):
        eid = event_ids[title]
        for colors in shots:
            admin.post(f"/events/{eid}/photos", {"photos": (picture(*colors[:2], colors[2]), f"{colors[2]}.jpg")})
        ev = [e for e in events if e[0] == title][0]
        name_to_id = {"admin": "1", "maria": "2", "peter": "3", "jana": "4", "tomas": "5"}
        admin.post(f"/events/{eid}/edit", {"title": title, "type_id": str(ev[1]), "event_date": d(ev[2]),
                                           "start_time": ev[3], "end_time": ev[4], "location": ev[5],
                                           "participants": [name_to_id[w] for w in ev[6]],
                                           "other_participants": ev[7], "description": ev[8],
                                           "gallery": mode})

    mic = item_ids["Wireless microphone Shure"]
    chairs = item_ids["Folding chairs"]
    concert = str(event_ids["Advent concert"])
    conf = str(event_ids["Youth conference 2026"])
    peter, jana, tomas, maria = (clients[k] for k in ("peter", "jana", "tomas", "maria"))
    r1 = peter.post("/reservations/new", {"item_id": pid, "quantity": "1", "start_at": f"{d(16)}T15:00", "end_at": f"{d(16)}T20:00", "event_id": concert, "purpose": "Lyrics on the screen"})
    maria.post(f"/reservations/{new_id(r1)}/status", {"action": "approve"})
    peter.post("/reservations/new", {"item_id": mic, "quantity": "3", "start_at": f"{d(16)}T15:00", "end_at": f"{d(16)}T20:00", "event_id": concert, "purpose": "Soloists"})
    tomas.post("/reservations/new", {"item_id": pid, "quantity": "1", "start_at": f"{d(16)}T18:00", "end_at": f"{d(16)}T21:00", "purpose": "Youth film night", "confirm_clash": "1"})
    tomas.post("/reservations/new", {"item_id": chairs, "quantity": "50", "start_at": f"{d(30)}T07:00", "end_at": f"{d(30)}T18:00", "event_id": conf})
    r = jana.post("/reservations/new", {"item_id": chairs, "quantity": "20", "start_at": f"{d(-3)}T10:00", "end_at": f"{d(-1)}T18:00", "purpose": "Family celebration in the garden"})
    rid = new_id(r)
    maria.post(f"/reservations/{rid}/status", {"action": "approve"})
    maria.post(f"/reservations/{rid}/status", {"action": "checkout"})
    r = peter.post("/reservations/new", {"item_id": mic, "quantity": "2", "start_at": f"{d(-9)}T17:00", "end_at": f"{d(-9)}T20:00", "event_id": str(event_ids["Recording: Advent catechesis"])})
    rid = new_id(r)
    for a in ("approve", "checkout"):
        maria.post(f"/reservations/{rid}/status", {"action": a})
    maria.post(f"/reservations/{rid}/status", {"action": "return", "condition": "good", "note": "All batteries charged"})
    hall = item_ids["Parish hall"]
    room = item_ids["Meeting room"]
    r = jana.post("/reservations/new", {"item_id": hall, "quantity": "1", "start_at": f"{d(2)}T16:00", "end_at": f"{d(2)}T18:00", "purpose": "Children's catechesis"})
    maria.post(f"/reservations/{new_id(r)}/status", {"action": "approve"})
    tomas.post("/reservations/new", {"item_id": hall, "quantity": "1", "start_at": f"{d(30)}T07:00", "end_at": f"{d(30)}T18:00", "event_id": conf})
    maria.post("/reservations/new", {"item_id": room, "quantity": "1", "start_at": f"{d(5)}T19:00", "end_at": f"{d(5)}T21:00", "purpose": "Pastoral council"})

    # volunteer rosters for the coming Sundays
    ids = {"admin": "1", "maria": "2", "peter": "3", "jana": "4", "tomas": "5"}
    for offset, title in ((6, "Sunday Mass – 2nd Sunday of Advent"), (13, "Sunday Mass – 3rd Sunday of Advent")):
        eid = new_id(admin.post("/events/new", {"title": title, "type_id": "1", "event_date": d(offset),
                                                "start_time": "10:30", "end_time": "11:45", "location": "Church"}))
        for role, who in ((1, "jana"), (1, "" if offset == 13 else "tomas"), (2, "tomas"), (2, ""), (3, "maria" if offset == 6 else "admin"), (6, "peter")):
            admin.post(f"/rosters/event/{eid}/add", {"role_id": str(role), "user_id": ids.get(who, "")})
    from app.db import connect
    db = connect(app.config["DATABASE"])
    # Peter is away on the 2nd Sunday and asks for a replacement
    slot = db.execute("SELECT s.id FROM roster_slots s JOIN events e ON e.id = s.event_id"
                      " WHERE e.event_date = ? AND s.user_id = 3", (d(6),)).fetchone()[0]
    peter.post(f"/rosters/{slot}/swap", {"note": "I'm away that weekend"})
    concert_id = event_ids["Advent concert"]
    for role, who in ((6, "peter"), (5, "tomas"), (5, "")):
        admin.post(f"/rosters/event/{concert_id}/add", {"role_id": str(role), "user_id": ids.get(who, "")})
    db.close()

    # maintenance and inspections
    ext = item_ids["Fire extinguisher 6 kg"]
    tasks = [
        (ext, "Fire extinguisher inspection", 365, d(-3), "", "Hasiči Trnava s.r.o."),
        (item_ids["Projector Epson EB-X51"], "Clean the air filter", 180, d(5), "3", ""),
        (item_ids["Wireless microphone Shure"], "Check batteries and charger", 30, d(12), "3", ""),
        (hall, "Electrical inspection (revízia)", 1460, d(45), "", "Elektro Revízie SK"),
        (item_ids["Church"], "Organ tuning", 365, d(120), "2", "Organ workshop Rieger"),
    ]
    task_ids = []
    for item, title, every, due, who, firm in tasks:
        task_ids.append(new_id(admin.post("/maintenance/tasks/new", {
            "item_id": str(item), "title": title, "interval_days": str(every), "next_due": due,
            "responsible_id": who, "contractor": firm})))
    peter.post("/maintenance/log/new", {"task_id": str(task_ids[2]), "done_on": d(-18),
                                        "performed_by": "Peter Horváth", "result": "issues",
                                        "notes": "One battery no longer holds charge, ordered a new one."})
    admin.post("/maintenance/log/new", {"item_id": str(item_ids["Mixing desk Yamaha MG10"]),
                                        "done_on": d(-4), "performed_by": "Daniel Novák", "result": "ok",
                                        "cost": "0 €", "notes": "Cleaned the channel 3 fader."})
    print("Demo data ready. Log in as admin /", PW)


if __name__ == "__main__":
    main()
