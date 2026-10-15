"""Work rules: when work starts, when a check-in counts as late, and so on.
services.record_tap reads these same rows on every tap, so a change applies at once."""
import re
from datetime import time

from db import transaction

KEYS = ("work_start", "late_after", "work_end", "duplicate_window_seconds")
_TIME = re.compile(r"^\d{2}:\d{2}$")
WINDOW_MIN, WINDOW_MAX = 5, 600


class SettingsError(Exception):
    def __init__(self, errors, values):
        super().__init__("invalid settings")
        self.errors, self.values = errors, values


def get_settings(db):
    rows = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings")}
    return {k: rows[k] for k in KEYS}


def _parse_time(text):
    if not _TIME.match(text or ""):
        raise ValueError
    return time.fromisoformat(text)


def validate(form):
    values = {k: (form.get(k) or "").strip() for k in KEYS}
    errors, parsed = {}, {}
    for key, label in (("work_start", "work start"), ("late_after", "late time"), ("work_end", "work end")):
        try:
            parsed[key] = _parse_time(values[key])
        except ValueError:
            errors[key] = "Enter the %s as hours and minutes, like 08:30." % label
    if not errors:
        if parsed["late_after"] < parsed["work_start"]:
            errors["late_after"] = "The late time can't be before work starts."
        if parsed["work_end"] <= parsed["late_after"]:
            errors["work_end"] = "Work must end after the late time."
    try:
        window = int(values["duplicate_window_seconds"])
        if not WINDOW_MIN <= window <= WINDOW_MAX:
            raise ValueError
    except ValueError:
        errors["duplicate_window_seconds"] = "Enter a whole number of seconds from %d to %d." % (WINDOW_MIN, WINDOW_MAX)
    if errors:
        raise SettingsError(errors, values)
    return {**values, "duplicate_window_seconds": str(window)}


def save_settings(db, clean):
    with transaction(db):
        for key in KEYS:
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                       "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, clean[key]))
