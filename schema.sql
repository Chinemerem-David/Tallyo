-- Tallyo database schema (SQLite). Safe to run more than once.
-- Dates are stored as text: 'YYYY-MM-DD' and 'YYYY-MM-DD HH:MM:SS' (local time).

CREATE TABLE IF NOT EXISTS employees (
    id            INTEGER PRIMARY KEY,
    employee_code TEXT    NOT NULL UNIQUE,              -- e.g. EMP-0142
    full_name     TEXT    NOT NULL,
    department    TEXT    NOT NULL,
    email         TEXT,
    joined_on     TEXT    NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
);

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,  -- employees use their code
    full_name     TEXT    NOT NULL,
    role          TEXT    NOT NULL CHECK (role IN ('admin', 'hr', 'employee')),
    password_hash TEXT    NOT NULL,
    employee_id   INTEGER UNIQUE REFERENCES employees(id),
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    must_change_password INTEGER NOT NULL DEFAULT 0 CHECK (must_change_password IN (0, 1)),
    last_login_at TEXT,
    -- an employee account must point at an employee; the others must not
    CHECK ((role = 'employee') = (employee_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS cards (
    id          INTEGER PRIMARY KEY,
    uid         TEXT NOT NULL UNIQUE,                   -- normalised: 'A3 4F 91 0C'
    employee_id INTEGER REFERENCES employees(id),
    status      TEXT NOT NULL DEFAULT 'available'
                CHECK (status IN ('available', 'active', 'lost', 'deactivated')),
    assigned_on TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    CHECK (status <> 'active'    OR employee_id IS NOT NULL),
    CHECK (status <> 'available' OR employee_id IS NULL)
);
-- An employee can have only ONE active card at a time.
CREATE UNIQUE INDEX IF NOT EXISTS one_active_card_per_employee
    ON cards (employee_id) WHERE status = 'active';

-- One row per employee per day: check-in and check-out live together.
CREATE TABLE IF NOT EXISTS attendance (
    id          INTEGER PRIMARY KEY,
    employee_id INTEGER NOT NULL REFERENCES employees(id),
    work_date   TEXT    NOT NULL,
    check_in    TEXT    NOT NULL,
    check_out   TEXT,
    status      TEXT    NOT NULL CHECK (status IN ('on_time', 'late')),
    source      TEXT    NOT NULL DEFAULT 'card' CHECK (source IN ('card', 'manual')),
    note        TEXT,
    UNIQUE (employee_id, work_date),                    -- blocks duplicate records
    CHECK (check_out IS NULL OR check_out > check_in)   -- blocks invalid records
);
CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance (work_date);

-- Every tap, accepted or rejected. Feeds the "needs attention" alerts.
CREATE TABLE IF NOT EXISTS tap_log (
    id          INTEGER PRIMARY KEY,
    tapped_at   TEXT NOT NULL,
    card_uid    TEXT NOT NULL,
    employee_id INTEGER REFERENCES employees(id),
    result      TEXT NOT NULL CHECK (result IN
                ('check_in', 'check_out', 'already_scanned',
                 'duplicate', 'unregistered', 'inactive_card'))
);
CREATE INDEX IF NOT EXISTS idx_tap_log_time ON tap_log (tapped_at);
CREATE INDEX IF NOT EXISTS idx_tap_log_card ON tap_log (card_uid);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO settings (key, value) VALUES
    ('work_start', '08:30'),
    ('late_after', '09:00'),
    ('work_end', '17:00'),
    ('duplicate_window_seconds', '60');
