-- Church Inventory schema. All statements are idempotent.
-- Records are never hard-deleted: they are archived, and every change is in audit_log.

CREATE TABLE IF NOT EXISTS users (
    id               INTEGER PRIMARY KEY,
    username         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name        TEXT NOT NULL,
    email            TEXT NOT NULL DEFAULT '',
    phone            TEXT NOT NULL DEFAULT '',          -- WhatsApp number, international format
    role             TEXT NOT NULL CHECK (role IN ('admin','manager','member','auditor')),
    password_hash    TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    language         TEXT NOT NULL DEFAULT '',
    active           INTEGER NOT NULL DEFAULT 1,
    notify_email     INTEGER NOT NULL DEFAULT 1,
    notify_whatsapp  INTEGER NOT NULL DEFAULT 0,
    ical_token       TEXT NOT NULL UNIQUE,
    session_version  INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT '',
    location        TEXT NOT NULL DEFAULT '',
    quantity        INTEGER NOT NULL DEFAULT 1 CHECK (quantity >= 0),
    serial_number   TEXT NOT NULL DEFAULT '',
    purchase_date   TEXT NOT NULL DEFAULT '',
    purchase_value  TEXT NOT NULL DEFAULT '',
    condition       TEXT NOT NULL DEFAULT 'good',
    description     TEXT NOT NULL DEFAULT '',
    reservable      INTEGER NOT NULL DEFAULT 1,
    kind            TEXT NOT NULL DEFAULT 'item',      -- 'item' or 'space' (room, hall, kitchen…)
    acquisition     TEXT NOT NULL DEFAULT 'purchase',  -- 'purchase', 'donation', 'other'
    donor           TEXT NOT NULL DEFAULT '',
    unit_value      REAL,                              -- value of one piece in EUR
    useful_life_years INTEGER NOT NULL DEFAULT 0,      -- straight-line depreciation, 0 = none
    archived        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    created_by      INTEGER REFERENCES users(id),
    updated_at      TEXT NOT NULL,
    updated_by      INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS event_types (
    id        INTEGER PRIMARY KEY,
    name_sk   TEXT NOT NULL,
    name_en   TEXT NOT NULL,
    color     TEXT NOT NULL DEFAULT '#6b7280',
    active    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY,
    title         TEXT NOT NULL,
    type_id       INTEGER NOT NULL REFERENCES event_types(id),
    event_date    TEXT NOT NULL,                 -- YYYY-MM-DD (local)
    start_time    TEXT NOT NULL DEFAULT '',      -- HH:MM (local)
    end_time      TEXT NOT NULL DEFAULT '',
    location      TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',
    other_participants TEXT NOT NULL DEFAULT '',
    gallery       TEXT NOT NULL DEFAULT 'off',   -- photo gallery: 'off', 'members' or 'public'
    archived      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    created_by    INTEGER REFERENCES users(id),
    updated_at    TEXT NOT NULL,
    updated_by    INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS event_participants (
    event_id  INTEGER NOT NULL REFERENCES events(id),
    user_id   INTEGER NOT NULL REFERENCES users(id),
    PRIMARY KEY (event_id, user_id)
);

-- Volunteer rosters: roles (reader, altar server…) and who serves at which event.
CREATE TABLE IF NOT EXISTS roster_roles (
    id        INTEGER PRIMARY KEY,
    name_sk   TEXT NOT NULL,
    name_en   TEXT NOT NULL,
    active    INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS roster_slots (
    id             INTEGER PRIMARY KEY,
    event_id       INTEGER NOT NULL REFERENCES events(id),
    role_id        INTEGER NOT NULL REFERENCES roster_roles(id),
    user_id        INTEGER REFERENCES users(id),      -- NULL = open, anyone can sign up
    swap_requested INTEGER NOT NULL DEFAULT 0,         -- the person is looking for a replacement
    swap_note      TEXT NOT NULL DEFAULT '',
    created_at     TEXT NOT NULL,
    created_by     INTEGER REFERENCES users(id),
    updated_at     TEXT NOT NULL,
    updated_by     INTEGER REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS roster_slots_event ON roster_slots(event_id);
CREATE INDEX IF NOT EXISTS roster_slots_user ON roster_slots(user_id);

-- Quick checks from the phone app (stocktaking): seen, where, in what condition.
CREATE TABLE IF NOT EXISTS item_checks (
    id          INTEGER PRIMARY KEY,
    uid         TEXT NOT NULL UNIQUE,               -- made on the phone; makes syncing safe to repeat
    item_id     INTEGER NOT NULL REFERENCES items(id),
    checked_at  TEXT NOT NULL,                      -- local time on the phone, YYYY-MM-DDTHH:MM
    condition   TEXT NOT NULL DEFAULT '',
    location    TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',
    user_id     INTEGER REFERENCES users(id),
    synced_at   TEXT NOT NULL                       -- UTC, when the server received it
);
CREATE INDEX IF NOT EXISTS item_checks_item ON item_checks(item_id, checked_at);

-- Photos for items and events. Files are never deleted; "removing" a photo hides it.
CREATE TABLE IF NOT EXISTS photos (
    id            INTEGER PRIMARY KEY,
    entity        TEXT NOT NULL,                 -- 'item', 'event', 'maintenance'
    entity_id     INTEGER NOT NULL,
    filename      TEXT NOT NULL,
    original_name TEXT NOT NULL DEFAULT '',
    sha256        TEXT NOT NULL,
    hidden        INTEGER NOT NULL DEFAULT 0,
    uploaded_at   TEXT NOT NULL,
    uploaded_by   INTEGER REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS photos_entity ON photos(entity, entity_id);

CREATE TABLE IF NOT EXISTS reservations (
    id            INTEGER PRIMARY KEY,
    item_id       INTEGER NOT NULL REFERENCES items(id),
    user_id       INTEGER NOT NULL REFERENCES users(id),
    event_id      INTEGER REFERENCES events(id),
    quantity      INTEGER NOT NULL DEFAULT 1 CHECK (quantity > 0),
    start_at      TEXT NOT NULL,                 -- YYYY-MM-DDTHH:MM (local)
    end_at        TEXT NOT NULL,
    purpose       TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','checked_out','returned','cancelled')),
    decision_note TEXT NOT NULL DEFAULT '',
    return_condition TEXT NOT NULL DEFAULT '',
    return_note   TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    created_by    INTEGER REFERENCES users(id),
    updated_at    TEXT NOT NULL,
    updated_by    INTEGER REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS reservations_item ON reservations(item_id, start_at, end_at);

-- Recurring maintenance and inspections (fire extinguishers, electrical checks, tuning…).
CREATE TABLE IF NOT EXISTS maintenance_tasks (
    id             INTEGER PRIMARY KEY,
    item_id        INTEGER NOT NULL REFERENCES items(id),
    title          TEXT NOT NULL,
    interval_days  INTEGER NOT NULL DEFAULT 365,   -- 0 = one-off
    next_due       TEXT NOT NULL,                  -- YYYY-MM-DD
    responsible_id INTEGER REFERENCES users(id),
    contractor     TEXT NOT NULL DEFAULT '',
    notes          TEXT NOT NULL DEFAULT '',
    active         INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL,
    created_by     INTEGER REFERENCES users(id),
    updated_at     TEXT NOT NULL,
    updated_by     INTEGER REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS maintenance_log (
    id           INTEGER PRIMARY KEY,
    task_id      INTEGER REFERENCES maintenance_tasks(id),
    item_id      INTEGER NOT NULL REFERENCES items(id),
    done_on      TEXT NOT NULL,                    -- YYYY-MM-DD
    performed_by TEXT NOT NULL DEFAULT '',         -- person or company
    result       TEXT NOT NULL DEFAULT 'ok' CHECK (result IN ('ok','issues','failed')),
    cost         TEXT NOT NULL DEFAULT '',
    notes        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    created_by   INTEGER REFERENCES users(id),
    updated_at   TEXT NOT NULL,
    updated_by   INTEGER REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS maintenance_log_item ON maintenance_log(item_id, done_on);

-- Append-only, hash-chained audit log.
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY,
    ts          TEXT NOT NULL,                   -- UTC ISO-8601
    user_id     INTEGER,
    username    TEXT NOT NULL DEFAULT '',
    action      TEXT NOT NULL,
    entity      TEXT NOT NULL,
    entity_id   INTEGER,
    summary     TEXT NOT NULL DEFAULT '',
    changes     TEXT NOT NULL DEFAULT '{}',      -- JSON {field: [before, after]}
    ip          TEXT NOT NULL DEFAULT '',
    prev_hash   TEXT NOT NULL,
    hash        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_entity ON audit_log(entity, entity_id);

CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

-- Internal key/value state (scheduler bookkeeping, secret key). Not user data.
CREATE TABLE IF NOT EXISTS app_state (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
