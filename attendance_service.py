"""Manual attendance entries and corrections. Every change needs a reason, which is
saved with the record, so it's clear later who fixed what and why."""
import re
import sqlite3
from datetime import date, time

from db import transaction
from employee_service import ValidationError, _tidy
from services import get_rules

_HHMM = re.compile(r"^\d{2}:\d{2}$")


def _clock(text):
    if not _HHMM.match(text or ""):
        raise ValueError
    return time.fromisoformat(text)


def validate(db, form, existing=None, today=None):
    """existing = the attendance row being edited, or None when adding a new one."""
    today = today or date.today()
    values = {"employee_id": existing["employee_id"] if existing else _tidy(form.get("employee_id")),
              "work_date": existing["work_date"] if existing else _tidy(form.get("work_date")),
              "check_in": _tidy(form.get("check_in")), "check_out": _tidy(form.get("check_out")),
              "reason": _tidy(form.get("reason"))}
    errors = {}

    if existing is None:
        emp = db.execute("SELECT 1 FROM employees WHERE id = ? AND is_active = 1",
                         (values["employee_id"] if str(values["employee_id"]).isdigit() else -1,)).fetchone()
        if not emp:
            errors["employee_id"] = "Choose an employee."
        try:
            if date.fromisoformat(values["work_date"]) > today:
                errors["work_date"] = "The date can't be in the future."
        except ValueError:
            errors["work_date"] = "Enter a valid date."

    try:
        check_in = _clock(values["check_in"])
    except ValueError:
        check_in = None
        errors["check_in"] = "Enter the check-in time, like 08:42."
    check_out = None
    if values["check_out"]:
        try:
            check_out = _clock(values["check_out"])
            if check_in and check_out <= check_in:
                errors["check_out"] = "Check-out must be after check-in."
        except ValueError:
            errors["check_out"] = "Enter the check-out time, like 17:04, or leave it empty."
    if not 3 <= len(values["reason"]) <= 200:
        errors["reason"] = "Say why (3 to 200 characters), e.g. forgot card."
    if errors:
        raise ValidationError(errors, values)

    day = values["work_date"]
    late = check_in > time.fromisoformat(get_rules(db)["late_after"])
    return {"employee_id": int(values["employee_id"]), "work_date": day,
            "check_in": "%s %s:00" % (day, values["check_in"]),
            "check_out": "%s %s:00" % (day, values["check_out"]) if check_out else None,
            "status": "late" if late else "on_time", "note": values["reason"]}


def create_record(db, clean):
    try:
        with transaction(db):
            cur = db.execute("INSERT INTO attendance (employee_id, work_date, check_in, check_out, status, source, note)"
                             " VALUES (?,?,?,?,?, 'manual', ?)",
                             (clean["employee_id"], clean["work_date"], clean["check_in"], clean["check_out"],
                              clean["status"], clean["note"]))
    except sqlite3.IntegrityError:
        raise ValidationError({"employee_id": "That employee already has a record for this day. Edit it instead."},
                              {"employee_id": clean["employee_id"], "work_date": clean["work_date"],
                               "check_in": clean["check_in"][11:16], "check_out": (clean["check_out"] or "")[11:16],
                               "reason": clean["note"]})
    return cur.lastrowid


def update_record(db, record_id, clean):
    with transaction(db):
        db.execute("UPDATE attendance SET check_in = ?, check_out = ?, status = ?, source = 'manual', note = ? "
                   "WHERE id = ?", (clean["check_in"], clean["check_out"], clean["status"], clean["note"], record_id))


def delete_record(db, record_id):
    with transaction(db):
        db.execute("DELETE FROM attendance WHERE id = ?", (record_id,))
