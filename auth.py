"""Sign-in, sign-out and role protection.

TEMPORARY: DEMO_USERS stands in for the database. In the database step we
replace it with a real `users` table and nothing else in this file changes.
"""
import hmac
import secrets
from functools import wraps

from flask import (Blueprint, abort, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

bp = Blueprint("auth", __name__)

# One source of truth for the three roles. The template draws the tabs from
# this list, and the JavaScript reads the same text from data-attributes.
ROLES = [
    {"key": "admin", "tab": "Admin", "label": "Username",
     "placeholder": "admin", "sub": "Choose your role, then sign in.", "note": ""},
    {"key": "hr", "tab": "HR manager", "label": "Username",
     "placeholder": "chiamaka", "sub": "Choose your role, then sign in.", "note": ""},
    {"key": "employee", "tab": "Employee", "label": "Employee ID",
     "placeholder": "EMP-0142", "sub": "Sign in to view your own attendance.",
     "note": "Employee accounts are view-only. Ask HR if you cannot sign in."},
]
ROLE_BY_KEY = {r["key"]: r for r in ROLES}
ROLE_LABELS = {"admin": "Admin", "hr": "HR manager", "employee": "Employee"}

# Where each role lands after signing in (these are endpoint names in app.py).
ROLE_HOME = {"admin": "admin_home", "hr": "hr_home", "employee": "portal_home"}

# Passwords are stored as hashes, never as plain text, even in the demo.
DEMO_USERS = {
    "admin":    {"role": "admin",    "name": "Admin",
                 "hash": generate_password_hash("admin123")},
    "chiamaka": {"role": "hr",       "name": "Chiamaka Eze",
                 "hash": generate_password_hash("hr123")},
    "emp-0142": {"role": "employee", "name": "Amara Okafor",
                 "hash": generate_password_hash("emp123")},
}
# Checked when the username doesn't exist, so a wrong username takes as long
# as a wrong password and an attacker can't tell the two apart by timing.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


# ---- CSRF protection ---------------------------------------------------------
# Every form carries a secret token that only our own pages know. A forged form
# on another website can't include it, so the server rejects it.

def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def verify_csrf():
    sent = request.form.get("csrf_token", "").encode()
    expected = session.get("csrf", "").encode()
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400)


# ---- Access control decorators ----------------------------------------------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped


def role_required(*roles):
    """Only let the listed roles in. Everyone else gets a 403 page."""
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if session["user"]["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


# ---- Views -------------------------------------------------------------------

def _render_login(role, error=None, username="", status=200):
    return render_template("login.html", roles=ROLES, current=ROLE_BY_KEY[role],
                           error=error, username=username), status


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if "user" in session:
            return redirect(url_for(ROLE_HOME[session["user"]["role"]]))
        role = request.args.get("role", "admin")
        return _render_login(role if role in ROLE_BY_KEY else "admin")

    verify_csrf()
    role = request.form.get("role", "admin")
    if role not in ROLE_BY_KEY:
        role = "admin"
    username = request.form.get("username", "").strip()[:64]
    password = request.form.get("password", "")[:256]

    record = DEMO_USERS.get(username.lower())
    password_ok = check_password_hash(record["hash"] if record else _DUMMY_HASH, password)

    # One vague message for every failure: wrong name, wrong password, or the
    # right person on the wrong tab. We never reveal which part was wrong.
    if not (record and password_ok and record["role"] == role):
        return _render_login(role, "Incorrect username or password for this role.",
                             username, 401)

    session.clear()                       # new session on sign-in
    session["user"] = {"username": username.lower(), "name": record["name"],
                       "role": record["role"]}
    session.permanent = bool(request.form.get("remember"))
    return redirect(url_for(ROLE_HOME[record["role"]]))


@bp.route("/logout", methods=["POST"])
def logout():
    verify_csrf()
    session.clear()
    return redirect(url_for("auth.login"))
