"""The RFID reader, as the rest of the app sees it.

Two programs share the database:
  * reader_bridge.py talks to the Arduino over USB and calls heartbeat() and handle_uid().
  * The web app calls status() to show "Reader connected", and start_listening() when an
    admin wants to register a new card by tapping it.
Because they only share the database, either can be restarted without the other.
"""
import math
from datetime import datetime, timedelta

from services import TS, normalize_uid, record_tap

STALE_SECONDS = 10          # no sign of life for this long = not connected
LISTEN_SECONDS = 60         # how long "Waiting for a card…" lasts
PURPOSES = ("register", "test")
FEEDBACK = {"check_in": "ok", "check_out": "ok", "duplicate": "again", "already_scanned": "again",
            "unregistered": "denied", "inactive_card": "denied"}     # what the Arduino's LEDs show


def _ensure_row(db):
    db.execute("INSERT OR IGNORE INTO reader_status (id) VALUES (1)")


def heartbeat(db, state, port=None, detail=None, now=None):
    """The reader program says 'I am here'. state: ok | fault | disconnected."""
    _ensure_row(db)
    db.execute("UPDATE reader_status SET state = ?, port = ?, detail = ?, seen_at = ? WHERE id = 1",
               (state, port, detail, (now or datetime.now()).strftime(TS)))


def status(db, now=None):
    """What to show on screen: {'connected', 'state', 'label', 'port', 'detail'}."""
    now = now or datetime.now()
    row = db.execute("SELECT * FROM reader_status WHERE id = 1").fetchone()
    fresh = bool(row and row["seen_at"] and
                 (now - datetime.strptime(row["seen_at"], TS)).total_seconds() <= STALE_SECONDS)
    if not fresh:
        return {"connected": False, "state": "disconnected", "label": "Reader not connected",
                "port": None, "detail": "No signal from the reader program."}
    if row["state"] == "ok":
        return {"connected": True, "state": "ok", "label": "Reader connected",
                "port": row["port"], "detail": row["detail"]}
    if row["state"] == "fault":
        return {"connected": False, "state": "fault", "label": "Reader fault: check wiring",
                "port": row["port"], "detail": row["detail"] or "The card reader module isn't answering."}
    return {"connected": False, "state": "disconnected", "label": "Reader not connected",
            "port": None, "detail": row["detail"] or "The Arduino isn't plugged in."}


# ---- "Waiting for a card…": capture the next tap instead of recording attendance --------

def start_listening(db, purpose, seconds=LISTEN_SECONDS, now=None):
    if purpose not in PURPOSES:
        raise ValueError("purpose must be one of %s" % (PURPOSES,))
    _ensure_row(db)
    until = (now or datetime.now()) + timedelta(seconds=seconds)
    db.execute("UPDATE reader_status SET listen_purpose = ?, listen_until = ?, captured_uid = NULL WHERE id = 1",
               (purpose, until.strftime(TS)))


def stop_listening(db):
    _ensure_row(db)
    db.execute("UPDATE reader_status SET listen_purpose = NULL, listen_until = NULL, captured_uid = NULL WHERE id = 1")


def listening(db, now=None):
    """{'waiting', 'uid', 'purpose', 'seconds_left'}. waiting = the next tap will be captured;
    uid = what was captured (kept until the window ends or it is cancelled)."""
    now = now or datetime.now()
    row = db.execute("SELECT listen_purpose, listen_until, captured_uid FROM reader_status WHERE id = 1").fetchone()
    idle = {"waiting": False, "uid": None, "purpose": None, "seconds_left": 0}
    if not row or not row["listen_purpose"] or not row["listen_until"]:
        return idle
    left = (datetime.strptime(row["listen_until"], TS) - now).total_seconds()
    if left <= 0:
        return idle
    return {"waiting": row["captured_uid"] is None, "uid": row["captured_uid"],
            "purpose": row["listen_purpose"], "seconds_left": int(math.ceil(left))}


def card_note(db, uid):
    """A sentence for the admin about a card that was just captured."""
    card = db.execute("""SELECT c.status, e.full_name, e.employee_code FROM cards c
                         LEFT JOIN employees e ON e.id = c.employee_id WHERE c.uid = ?""", (uid,)).fetchone()
    if card is None:
        return "A new card. It isn't registered yet."
    owner = "%s (%s)" % (card["full_name"], card["employee_code"]) if card["full_name"] else None
    return {"available": "A spare card that is already registered.",
            "active": "Already assigned to %s." % owner,
            "lost": "Marked lost%s." % (" (was %s's)" % card["full_name"] if owner else ""),
            "deactivated": "Deactivated%s." % (" (was %s's)" % card["full_name"] if owner else "")}[card["status"]]


# ---- The one place a card ID is handled ----------------------------------------------

def handle_uid(db, raw_uid, now=None):
    """A card was tapped. If an admin is waiting to register a card, capture it; otherwise
    record attendance. The result says what the Arduino's LEDs should show."""
    try:
        uid = normalize_uid(raw_uid)
    except ValueError:
        return {"mode": "invalid", "feedback": "denied"}
    if listening(db, now)["waiting"]:
        db.execute("UPDATE reader_status SET captured_uid = ? WHERE id = 1", (uid,))
        return {"mode": "captured", "uid": uid, "feedback": "ok"}
    tap = record_tap(db, uid, now)
    return {"mode": "tap", "result": tap["result"], "feedback": FEEDBACK[tap["result"]], "tap": tap}
