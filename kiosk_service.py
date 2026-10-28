"""What the kiosk screen should show.

Every tap source (the simulator, simulate_tap.py, later the Arduino reader) writes to
tap_log, so the kiosk just asks "anything new since tap number N?". No live link to
the reader is needed, and it works even if the reader runs in a different process.

The kiosk is a PUBLIC screen: it only ever gets a first name, a time and a result.
Never a card ID, a department or an employee ID."""
from datetime import datetime, timedelta

from services import TS, _hours

KIND = {"check_in": "check_in", "check_out": "check_out",
        "duplicate": "already", "already_scanned": "already",
        "unregistered": "denied", "inactive_card": "denied"}


def latest_id(db):
    return db.execute("SELECT COALESCE(MAX(id), 0) FROM tap_log").fetchone()[0]


def events_after(db, after_id, now=None, max_age=20, limit=20):
    """Returns (last_id_seen, [events]). Taps older than max_age seconds are skipped,
    so a kiosk that was offline never replays old taps when it comes back."""
    now = now or datetime.now()
    oldest = (now - timedelta(seconds=max_age)).strftime(TS)
    rows = db.execute("""
        SELECT t.id, t.tapped_at, t.result, e.full_name, a.check_in, a.check_out
        FROM tap_log t
        LEFT JOIN employees e ON e.id = t.employee_id
        LEFT JOIN attendance a ON a.employee_id = t.employee_id AND a.work_date = substr(t.tapped_at, 1, 10)
        WHERE t.id > ? ORDER BY t.id LIMIT ?""", (after_id, limit)).fetchall()

    last, events = after_id, []
    for r in rows:
        last = r["id"]
        if r["tapped_at"] < oldest:
            continue
        kind = KIND[r["result"]]
        event = {"id": r["id"], "kind": kind, "time": r["tapped_at"][11:16]}
        if kind != "denied" and r["full_name"]:
            event["name"] = r["full_name"].split()[0]                  # first name only
        if kind == "check_out" and r["check_in"] and r["check_out"]:
            event["hours"] = _hours(r["check_in"], r["check_out"])
        if kind == "already" and r["check_in"]:
            event["checked_in_at"] = r["check_in"][11:16]
            if r["check_out"]:
                event["checked_out_at"] = r["check_out"][11:16]
        events.append(event)
    return last, events
