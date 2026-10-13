"""Business rules. No Flask in here, so the web app, the kiosk and the Arduino
reader can all call the same code."""
import re
from datetime import datetime, time

from db import transaction

TS = "%Y-%m-%d %H:%M:%S"
_UID = re.compile(r"^(?:[0-9A-F]{8}|[0-9A-F]{14}|[0-9A-F]{20})$")   # 4, 7 or 10 bytes


def normalize_uid(raw):
    """'a3:4f:91:0c', 'A34F910C' and 'a3 4f 91 0c' all become 'A3 4F 91 0C'."""
    cleaned = re.sub(r"[\s:\-]", "", str(raw)).upper()
    if not _UID.match(cleaned):
        raise ValueError("Not a valid card ID: %r" % (raw,))
    return " ".join(cleaned[i:i + 2] for i in range(0, len(cleaned), 2))


def get_rules(db):
    rows = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings")}
    return {"work_start": rows["work_start"], "late_after": rows["late_after"],
            "work_end": rows["work_end"],
            "window": int(rows["duplicate_window_seconds"])}


def _hours(start, end):
    minutes = int((datetime.strptime(end, TS) - datetime.strptime(start, TS)).total_seconds() // 60)
    return "%dh %02dm" % divmod(minutes, 60)


def _log(db, stamp, uid, emp, result, **extra):
    db.execute("INSERT INTO tap_log (tapped_at, card_uid, employee_id, result) VALUES (?,?,?,?)",
               (stamp, uid, emp["id"] if emp else None, result))
    employee = None
    if emp:
        employee = {"id": emp["id"], "code": emp["employee_code"],
                    "name": emp["full_name"], "department": emp["department"]}
    return {"result": result, "uid": uid, "time": stamp[11:16], "employee": employee, **extra}


def record_tap(db, raw_uid, now=None):
    """Handle one card tap. Returns a dict the kiosk can display:
       result: check_in | check_out | already_scanned | duplicate |
               unregistered | inactive_card
    """
    now = now or datetime.now()
    uid = normalize_uid(raw_uid)
    stamp, today = now.strftime(TS), now.strftime("%Y-%m-%d")

    with transaction(db):
        rules = get_rules(db)
        card = db.execute("SELECT * FROM cards WHERE uid = ?", (uid,)).fetchone()
        if card is None:
            return _log(db, stamp, uid, None, "unregistered")

        emp = None
        if card["employee_id"]:
            emp = db.execute("SELECT * FROM employees WHERE id = ?", (card["employee_id"],)).fetchone()
        if card["status"] != "active" or emp is None or not emp["is_active"]:
            return _log(db, stamp, uid, emp, "inactive_card")

        row = db.execute("SELECT * FROM attendance WHERE employee_id = ? AND work_date = ?",
                         (emp["id"], today)).fetchone()

        # Double tap: same card tapped again inside the window after its last accepted tap.
        last = db.execute("SELECT tapped_at FROM tap_log WHERE card_uid = ? "
                          "AND result IN ('check_in', 'check_out') ORDER BY id DESC LIMIT 1",
                          (uid,)).fetchone()
        if last and (now - datetime.strptime(last["tapped_at"], TS)).total_seconds() < rules["window"]:
            return _log(db, stamp, uid, emp, "duplicate",
                        checked_in_at=row["check_in"][11:16] if row else None)

        if row is None:                                   # first tap of the day
            late = now.time() > time.fromisoformat(rules["late_after"])
            status = "late" if late else "on_time"
            db.execute("INSERT INTO attendance (employee_id, work_date, check_in, status) "
                       "VALUES (?,?,?,?)", (emp["id"], today, stamp, status))
            return _log(db, stamp, uid, emp, "check_in", status=status)

        if row["check_out"] is None:                      # second tap: leaving
            db.execute("UPDATE attendance SET check_out = ? WHERE id = ?", (stamp, row["id"]))
            early = now.time() < time.fromisoformat(rules["work_end"])
            return _log(db, stamp, uid, emp, "check_out",
                        hours=_hours(row["check_in"], stamp), left_early=early)

        return _log(db, stamp, uid, emp, "already_scanned",     # day already complete
                    checked_in_at=row["check_in"][11:16], checked_out_at=row["check_out"][11:16])
