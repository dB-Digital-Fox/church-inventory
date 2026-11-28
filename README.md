# Church Inventory

A small, self-hosted web app for a parish or church community to keep track of:

- **Inventory**: what the church owns, where it is, its condition, photos, serial numbers and purchase info, with **printable QR labels**.
- **Events**: who did which event (Mass/service, conference, recording, meeting, cleanup, concert, repair, or your own types), when, a short write-up and photos.
- **Rooms and spaces** (parish hall, meeting room, kitchen, the church itself) are booked the same way as items and show up in the same calendar, with the same clash warning.
- **Maintenance and inspections**: recurring tasks such as fire extinguisher checks, electrical inspections (revízie), organ tuning or boiler service, with due dates, a responsible person or contractor, reminders by email/WhatsApp (14, 7 and 1 day before, then weekly while overdue), and a history of the work done with photos, result and cost.
- **Donations and asset value**: how each item was acquired (purchase or donation, and from whom), its value, and straight-line depreciation over its useful life. A yearly **assets and donations report** for the diocese or the insurer can be printed, saved as PDF or downloaded as CSV.
- **Volunteer rosters**: who reads, serves at the altar, sings, ushers, does the sound or cleans at each event. Places can be assigned or left open for people to sign up. Someone who can't come asks for a replacement and stays on until another person takes over. Everyone gets a reminder the day before, and managers hear about gaps two days ahead. Duties appear in each person's calendar.
- **Photo galleries** for finished events, either for members only or public (no login, for the parish website or Facebook). Public galleries show only the title, date and photos, never the write-up or names.
- **Phone app (works offline)**: install the site on a phone's home screen, scan the QR label on an item or room, and note where it is and its condition. This works without signal, in the cellar or the bell tower. Checks wait on the phone and are sent when it's back online. Good for the yearly stocktaking. See [docs/phone-app.md](docs/phone-app.md).
- **Reports and exports** as Excel files or printable pages (save as PDF): the inventory list, the assets and donations report, an **event summary for the parish council** (by type, who helped how often, every event with its write-up), the year's maintenance, and an **annual audit export** with the year's changes and an integrity check of the log.
- **Reservations and borrowing**: request, approve, hand out and return items, with a **clear warning when bookings clash** (quantity-aware: 10 chairs can be booked by several people as long as no more than 10 are out at once).
- **Audit trail**: every change is recorded with who, when, and exactly what changed (before → after). The log is **append-only and hash-chained**, so it can serve as evidence. Records are edited with full history and archived instead of deleted. Photos are never deleted, only hidden.
- **Calendar**: each person gets a private calendar link (iCal) for Google Calendar, Outlook or iPhone that shows events and reservations and updates automatically.
- **Notifications** by **email** and **WhatsApp**: new reservation requests (with the clash warning), approvals and rejections, a reminder the day before, overdue returns, maintenance due dates, roster duties and swaps, and a daily audit fingerprint for auditors.
- **Slovak and English** interface (Slovak by default; each person can switch).
- **Nightly backups** of the database and photos.

It runs in a single Docker container with an SQLite database and is light enough for a **Raspberry Pi 4/5**, a mini PC or any home server.

License: **GNU AGPL-3.0**. You can use, change and share it freely. If you run a modified version for others, you must share your changes (the app links to its source code in the footer).

## Screenshots

![Overview with approvals, overdue returns, upcoming events and recent activity](docs/screenshots/dashboard.png)

| | |
|---|---|
| **Inventory** with photos, categories, locations and condition<br>![Inventory](docs/screenshots/inventory.png) | **Item detail** with QR code, reservations and the full change history<br>![Item](docs/screenshots/item.png) |
| **Event** with participants, write-up, photos and items used<br>![Event](docs/screenshots/event.png) | **Events log**, filterable by type, person and date<br>![Events](docs/screenshots/events.png) |
| **Rooms and spaces**, booked like items<br>![Rooms](docs/screenshots/rooms.png) | **Maintenance and inspections** with due dates and history<br>![Maintenance](docs/screenshots/maintenance.png) |
| **Assets and donations report** for the diocese or insurance (print, PDF, CSV)<br>![Assets report](docs/screenshots/assets-report.png) | **Overview** for managers: approvals, overdue returns, maintenance due<br>![Overview](docs/screenshots/dashboard.png) |
| **Volunteer rosters**: my duties, places that need help, sign up or take over<br>![Rosters](docs/screenshots/rosters.png) | **Roster on the event page**, assigned by a manager<br>![Event roster](docs/screenshots/event-roster.png) |
| **Public photo galleries**, no login needed<br>![Public gallery](docs/screenshots/gallery-public.png) | **Phone app**: scan a label and record a check, also offline<br><img src="docs/screenshots/mobile-quick-check.png" alt="Quick check" width="260"> |
| **Reports**: printable or Excel<br>![Reports](docs/screenshots/reports.png) | **Event summary** for the parish council<br>![Event summary](docs/screenshots/event-summary.png) |
| **Clash warning** while booking, shown live as you pick the time<br>![Reservation clash](docs/screenshots/reservation-clash.png) | **Reservations** with clashes highlighted<br>![Reservations](docs/screenshots/reservations.png) |
| **Audit log**: who changed what and when, before → after, with integrity check<br>![Audit log](docs/screenshots/audit-log.png) | **People** and how many events each person did<br>![People](docs/screenshots/people.png) |
| **Calendar link** for Google, Outlook and iPhone<br>![Calendar](docs/screenshots/calendar.png) | **Printable QR labels**<br>![QR labels](docs/screenshots/qr-labels.png) |
| **Slovak interface**<br>![Slovak](docs/screenshots/inventory-sk.png) | **Phone, dark mode**<br><img src="docs/screenshots/mobile-dark.png" alt="Mobile" width="260"> |

---

## Roles

| Role | Can do |
|---|---|
| **Admin** | Everything, including people, event types, backups and settings |
| **Manager** | Manage inventory and rooms, approve and hand out reservations, edit any event and roster, plan maintenance, see reports and the audit log |
| **Member** | Log events, edit their own events and their rosters, sign up for duties and swap them, request reservations, cancel their own requests, record maintenance work |
| **Auditor** | Read-only access to everything, including the audit log, all reports, the annual audit export and the Excel/CSV exports |

---

## Install (Raspberry Pi, mini PC or server)

You need Docker. On Raspberry Pi OS (64-bit) or Debian/Ubuntu:

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER   # log out and back in afterwards
```

Then:

```bash
git clone https://github.com/dB-Digital-Fox/church-inventory.git
cd church-inventory
cp .env.example .env
nano .env                        # set ORG_NAME, BASE_URL, email/WhatsApp if wanted
mkdir -p data backups && sudo chown -R 1000:1000 data backups
docker compose up -d --build
```

Open `http://<device-ip>:8000` from a computer on the same network. The first visit asks you to create the administrator account. **Do this right away, before exposing the app to the internet.**

Then add people under **People → Add person**. Each person gets a temporary password that they must change at first login.

To update later:

```bash
git pull && docker compose up -d --build
```

The database is upgraded automatically on start and nothing is removed. When upgrading from 0.1, values typed in the old "purchase value" field (like `489 €`) are copied into the new value field. Make a backup first (**Administration → Back up now**) to be safe.

### Which device?

All of these work. Pick based on what you have:

- **Raspberry Pi 5**: quiet, low power (~5 W). Use a **USB SSD instead of the SD card** if you can, since SD cards wear out with constant database writes. Keep backups on a separate USB drive.
- **HP mini PC (EliteDesk/ProDesk G5)**: the most robust choice. It has an internal SSD, is still low power, and is faster. Recommended if it's free.
- **Dell server**: works fine, but uses much more power for an app this small. It makes sense if it's already running 24/7 for other things.

---

## Access from outside the church

Pick one:

- **[Cloudflare Tunnel](docs/cloudflare-tunnel.md)**: public HTTPS address like `https://inventar.yourparish.sk`, no ports opened on your router. Good when many volunteers need access from their phones. Free.
- **[Tailscale](docs/tailscale.md)**: private network, only people you invite can reach the app at all. The most secure option, but everyone needs the Tailscale app.
- **Local only**: do nothing. The app is reachable only inside the church network.

When using HTTPS (both options above), set `SECURE_COOKIES=1` and `BASE_URL=https://…` in `.env`.

---

## Notifications

- **Email**: fill in `SMTP_*` in `.env`. Any mail provider works (e.g. a Gmail/Google Workspace account with an app password, Websupport, Seznam…).
- **WhatsApp**: uses the official WhatsApp Cloud API. See **[docs/whatsapp.md](docs/whatsapp.md)**, which explains the Meta business setup it requires.

People choose in **My profile** whether they want email and/or WhatsApp. Admins can send a test message from **Administration**.

---

## Public galleries and privacy

A gallery is off by default. On the event form choose **Members only** or **Public**. A public gallery is at `https://your-address/gallery/` and works without logging in, so it can be linked from the parish website. Only the title, date, type and photos are shown. Photos are re-encoded without GPS/EXIF data. Publish only photos the people in them agreed to, especially children (GDPR). A photo hidden on the event page disappears from the gallery at once.

If you use Cloudflare Access to protect the whole site, add a bypass rule for `/gallery/*` so visitors can see public galleries (see [docs/cloudflare-tunnel.md](docs/cloudflare-tunnel.md)).

---

## The audit trail (for auditors)

- Every create, edit, archive, photo upload/removal, reservation step, login (including failed ones), password change, export and backup is written to the `audit_log` table, in the same database transaction as the change itself.
- Each entry stores the time (UTC), user, IP address, the record, and the changed fields as before → after values.
- Entries are **chained with SHA-256**: each entry's fingerprint covers its content and the previous entry's fingerprint. Editing, inserting or deleting any past entry breaks the chain. **Audit log → Verify integrity** (or `docker compose exec app flask --app app verify-audit`) recomputes the whole chain.
- The database refuses `UPDATE`/`DELETE` on the log (SQLite triggers).
- Someone with full access to the server could still rebuild the whole chain from scratch. To make that detectable, people with the **Auditor** role receive a daily message with the current fingerprint. Keeping those messages (or noting the fingerprint shown on the audit page) lets you prove later that history wasn't rewritten.
- **CSV export** and a **printable report** (browser → Save as PDF) are on the audit page. **Reports → Annual audit export** gives an Excel file with every entry of the year (before → after values, fingerprints) and an integrity sheet: whether the whole chain is intact, and the fingerprint of the year's last entry. Exports are themselves logged.
- Uploaded photos are re-encoded (removing GPS/EXIF data), and their SHA-256 is logged, so a swapped image file is detectable.

---

## Backups and restore

See **[docs/backups.md](docs/backups.md)**. In short: a full backup (database + photos) is written every night to `BACKUP_PATH` (point it at a USB drive), the newest 14 are kept, and admins can create and download backups from **Administration**. The doc also shows how to add an encrypted off-site copy with `rclone`.

---

## Command-line helpers

```bash
docker compose exec app flask --app app verify-audit           # check the hash chain
docker compose exec app flask --app app backup                 # back up now
docker compose exec app flask --app app reset-password daniel  # locked out? temporary password
```

## Try it with demo data

```bash
DATA_DIR=./demo-data python scripts/seed_demo.py     # sample items, events, people, reservations
DATA_DIR=./demo-data flask --app wsgi run            # log in as admin / demo password 1
```

The screenshots above come from this demo data (`scripts/screenshots.js` regenerates them).

## Development

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
DATA_DIR=./data flask --app wsgi run --debug
```

Code layout: `app/views/` (one file per section), `app/templates/`, `app/audit.py` (hash-chained log), `app/translations_sk.py` (Slovak texts, and a test fails if one is missing).
