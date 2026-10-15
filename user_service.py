"""Staff accounts (admin, HR), password resets and password changes."""
import re

from werkzeug.security import check_password_hash, generate_password_hash

import sqlite3

from db import transaction
from employee_service import ValidationError, _tidy, temp_password

USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,29}$")
STAFF_ROLES = {"hr": "HR manager", "admin": "Admin"}
MIN_PASSWORD, MAX_PASSWORD = 8, 128


class UserError(Exception):
    """A problem the admin should read. The message is shown on the page."""


def validate_new(db, form):
    values = {"full_name": _tidy(form.get("full_name")),
              "username": _tidy(form.get("username")).lower(),
              "role": form.get("role", "")}
    errors = {}
    if not 2 <= len(values["full_name"]) <= 80:
        errors["full_name"] = "Enter the full name (2 to 80 characters)."
    name = values["username"]
    if not USERNAME_RE.match(name):
        errors["username"] = "Use 3 to 30 lowercase letters, numbers, dots, dashes or underscores."
    elif db.execute("SELECT 1 FROM users WHERE username = ? COLLATE NOCASE UNION "
                    "SELECT 1 FROM employees WHERE employee_code = ? COLLATE NOCASE", (name, name)).fetchone():
        errors["username"] = "That username is already in use."
    if values["role"] not in STAFF_ROLES:
        errors["role"] = "Choose a role."
    if errors:
        raise ValidationError(errors, values)
    return values


def create_user(db, clean):
    """Returns (user_id, temporary_password). They must change it at first sign-in."""
    password = temp_password()
    try:
        with transaction(db):
            cur = db.execute("INSERT INTO users (username, full_name, role, password_hash, must_change_password)"
                             " VALUES (?,?,?,?,1)",
                             (clean["username"], clean["full_name"], clean["role"],
                              generate_password_hash(password)))
    except sqlite3.IntegrityError:
        raise ValidationError({"username": "That username is already in use."}, clean)
    return cur.lastrowid, password


def update_user(db, user_id, full_name, is_active, acting_user_id):
    full_name = _tidy(full_name)
    if not 2 <= len(full_name) <= 80:
        raise ValidationError({"full_name": "Enter the full name (2 to 80 characters)."}, {"full_name": full_name})
    with transaction(db):
        user = db.execute("SELECT * FROM users WHERE id = ? AND role IN ('admin', 'hr')", (user_id,)).fetchone()
        if user is None:
            raise UserError("That account doesn't exist.")
        if not is_active:
            if user_id == acting_user_id:
                raise UserError("You can't deactivate your own account.")
            if user["role"] == "admin" and db.execute(
                    "SELECT COUNT(*) FROM users WHERE role = 'admin' AND is_active = 1 AND id <> ?",
                    (user_id,)).fetchone()[0] == 0:
                raise UserError("There must be at least one active admin.")
        db.execute("UPDATE users SET full_name = ?, is_active = ? WHERE id = ?",
                   (full_name, int(is_active), user_id))


def _reset(db, user):
    if user is None:
        raise UserError("That account doesn't exist.")
    if not user["is_active"]:
        raise UserError("Activate the account before resetting its password.")
    password = temp_password()
    with transaction(db):
        db.execute("UPDATE users SET password_hash = ?, must_change_password = 1 WHERE id = ?",
                   (generate_password_hash(password), user["id"]))
    return user, password


def reset_password(db, user_id):
    """New temporary password for an admin or HR account. Returns (user, password)."""
    return _reset(db, db.execute("SELECT * FROM users WHERE id = ? AND role IN ('admin', 'hr')",
                                 (user_id,)).fetchone())


def reset_employee_password(db, employee_id):
    return _reset(db, db.execute("SELECT * FROM users WHERE employee_id = ?", (employee_id,)).fetchone())


def change_password(db, user_id, current, new, confirm):
    """Let a signed-in person choose their own password."""
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    errors = {}
    if user is None or not check_password_hash(user["password_hash"], current or ""):
        errors["current"] = "Your current password is wrong."
    if not MIN_PASSWORD <= len(new or "") <= MAX_PASSWORD:
        errors["new"] = "Use %d to %d characters." % (MIN_PASSWORD, MAX_PASSWORD)
    elif new == current:
        errors["new"] = "Choose a different password from the current one."
    elif user and new.lower() == user["username"].lower():
        errors["new"] = "Don't use your username as your password."
    if new != confirm:
        errors["confirm"] = "The two passwords don't match."
    if errors:
        raise ValidationError(errors, {})
    with transaction(db):
        db.execute("UPDATE users SET password_hash = ?, must_change_password = 0 WHERE id = ?",
                   (generate_password_hash(new), user_id))
