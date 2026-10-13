"""Creating and editing employees. No Flask in here, so it's easy to test."""
import re
import secrets
import sqlite3
from datetime import date

from werkzeug.security import generate_password_hash

from db import transaction

CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{2,19}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"      # no look-alikes (l 1 i o 0)


class ValidationError(Exception):
    """errors: {field: message}.  values: what the admin typed, to refill the form."""
    def __init__(self, errors, values):
        super().__init__("invalid employee")
        self.errors, self.values = errors, values


def _tidy(text):
    return " ".join(str(text or "").split())              # trims and collapses spaces


def next_code(db):
    """EMP-0160 after EMP-0159."""
    numbers = [int(r[0][4:]) for r in db.execute(
        "SELECT employee_code FROM employees WHERE employee_code GLOB 'EMP-[0-9]*'")
        if r[0][4:].isdigit()]
    return "EMP-%04d" % (max(numbers) + 1 if numbers else 1)


def validate(db, form, creating, today=None):
    """Return clean values, or raise ValidationError with a message per field."""
    today = today or date.today()
    values = {"employee_code": _tidy(form.get("employee_code")).upper(),
              "full_name": _tidy(form.get("full_name")),
              "department": _tidy(form.get("department")),
              "email": _tidy(form.get("email")).lower(),
              "joined_on": _tidy(form.get("joined_on")),
              "is_active": form.get("is_active") == "on"}
    errors = {}

    if creating:
        code = values["employee_code"]
        if not CODE_RE.match(code):
            errors["employee_code"] = "Use 3 to 20 letters, numbers or dashes, e.g. EMP-0160."
        elif db.execute("SELECT 1 FROM employees WHERE employee_code = ? COLLATE NOCASE "
                        "UNION SELECT 1 FROM users WHERE username = ? COLLATE NOCASE",
                        (code, code)).fetchone():
            errors["employee_code"] = "That ID is already in use."

    if not 2 <= len(values["full_name"]) <= 80:
        errors["full_name"] = "Enter the full name (2 to 80 characters)."
    if not 1 <= len(values["department"]) <= 40:
        errors["department"] = "Enter a department (up to 40 characters)."
    if values["email"] and (len(values["email"]) > 120 or not EMAIL_RE.match(values["email"])):
        errors["email"] = "That doesn't look like an email address."
    try:
        joined = date.fromisoformat(values["joined_on"])
        if joined > today:
            errors["joined_on"] = "The joining date can't be in the future."
    except ValueError:
        errors["joined_on"] = "Enter a valid date."

    if errors:
        raise ValidationError(errors, values)
    clean = dict(values, email=values["email"] or None)
    if creating:
        clean["is_active"] = True
    return clean


def temp_password():
    return "".join(secrets.choice(PASSWORD_ALPHABET) for _ in range(8))


def create_employee(db, clean):
    """Adds the employee AND their sign-in account (username = employee ID).
    Returns (employee_id, temporary_password)."""
    password = temp_password()
    try:
        with transaction(db):
            cur = db.execute(
                "INSERT INTO employees (employee_code, full_name, department, email, joined_on)"
                " VALUES (?,?,?,?,?)",
                (clean["employee_code"], clean["full_name"], clean["department"],
                 clean["email"], clean["joined_on"]))
            db.execute("INSERT INTO users (username, full_name, role, password_hash, employee_id)"
                       " VALUES (?,?,?,?,?)",
                       (clean["employee_code"], clean["full_name"], "employee",
                        generate_password_hash(password), cur.lastrowid))
    except sqlite3.IntegrityError:                  # someone else took the ID a moment ago
        raise ValidationError({"employee_code": "That ID is already in use."}, clean)
    return cur.lastrowid, password


def update_employee(db, employee_id, clean):
    """The ID never changes (it is the employee's sign-in name). Switching
    'active' off also switches their account off."""
    with transaction(db):
        db.execute("UPDATE employees SET full_name=?, department=?, email=?, joined_on=?,"
                   " is_active=? WHERE id=?",
                   (clean["full_name"], clean["department"], clean["email"],
                    clean["joined_on"], int(clean["is_active"]), employee_id))
        db.execute("UPDATE users SET full_name=?, is_active=? WHERE employee_id=?",
                   (clean["full_name"], int(clean["is_active"]), employee_id))
