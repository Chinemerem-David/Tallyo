"""Menus and the shared page frame (sidebar + top bar) for signed-in pages."""
from datetime import date, datetime

from flask import session, url_for
from werkzeug.routing import BuildError

import auth

# (label, endpoint). A page that isn't built yet simply shows "#", and its link
# switches on by itself the moment the page exists. No menu edits needed.
ADMIN_MENU = [
    ("Manage", [("Overview", "admin_home"), ("Employees", "admin.employees"),
                ("Cards", "admin.cards"), ("Users", "admin.users"),
                ("Settings", "admin.settings")]),
    ("Records", [("Attendance", "admin.attendance"), ("Reports", "admin.reports")]),
]
HR_MENU = [("Records", [("Today", "hr_home"), ("Attendance", "hr.attendance"),
                        ("Reports", "hr.reports"), ("Employees", "hr.employees")])]
EMPLOYEE_MENU = [("My attendance", [("This month", "portal_home")])]
MENUS = {"admin": ADMIN_MENU, "hr": HR_MENU, "employee": EMPLOYEE_MENU}
DEFAULT_ACTIVE = {"admin": "Overview", "hr": "Today", "employee": "This month"}


def safe_url(endpoint, **values):
    """url_for that returns '#' instead of crashing while a page doesn't exist yet."""
    try:
        return url_for(endpoint, **values)
    except BuildError:
        return "#"


def initials(name):
    parts = name.split()
    return (parts[0][0] + parts[1][0] if len(parts) > 1 else name[:2]).upper()


def shell_context(active=None):
    """Everything app_shell.html needs. Pass the name of the current menu item."""
    user = session["user"]
    role = user["role"]
    nav = [{"label": label, "links": [{"name": name, "href": safe_url(endpoint)}
                                      for name, endpoint in links]}
           for label, links in MENUS[role]]
    return {"nav": nav, "active": active or DEFAULT_ACTIVE[role],
            "role_label": auth.ROLE_LABELS[role],
            "user_name": user["name"], "user_initials": initials(user["name"])}


def friendly_when(stamp, today=None):
    """'2026-10-13 08:42:10' -> 'Today 08:42', or '12 Oct 08:42' for earlier days."""
    if not stamp:
        return "Never"
    today = today or date.today().isoformat()
    if stamp[:10] == today:
        return "Today " + stamp[11:16]
    day = datetime.strptime(stamp[:10], "%Y-%m-%d")
    return "%d %s %s" % (day.day, day.strftime("%b"), stamp[11:16])
