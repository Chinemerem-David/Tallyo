"""Giving cards to employees, replacing them and retiring them.

The admin page, the future "tap to register" button and any import script all
call these same functions, so the rules are identical whichever way a card
arrives."""
from datetime import date

from db import transaction
from services import normalize_uid

RETIRED = ("lost", "deactivated")


class CardError(Exception):
    """A problem the admin should read. The message is shown on the page."""


def current_card(db, employee_id):
    return db.execute("SELECT * FROM cards WHERE employee_id = ? AND status = 'active'",
                      (employee_id,)).fetchone()


def past_cards(db, employee_id):
    return db.execute("SELECT * FROM cards WHERE employee_id = ? AND status IN ('lost', 'deactivated')"
                      " ORDER BY id DESC", (employee_id,)).fetchall()


def available_cards(db):
    return db.execute("SELECT * FROM cards WHERE status = 'available' ORDER BY id").fetchall()


def recent_unregistered(db, limit=5):
    """Cards that were tapped on the reader but belong to nobody yet. This is
    how a brand-new card shows up for the admin without typing anything."""
    return db.execute("""
        SELECT card_uid, MAX(tapped_at) AS last_tap, COUNT(*) AS taps
        FROM tap_log
        WHERE result = 'unregistered' AND card_uid NOT IN (SELECT uid FROM cards)
        GROUP BY card_uid ORDER BY last_tap DESC LIMIT ?""", (limit,)).fetchall()


def assign_card(db, employee_id, raw_uid, replace_old_as=None, today=None):
    """Give a card to an employee. Returns the normalised card ID.

    If they already have an active card, replace_old_as must say what happens
    to it: 'lost' or 'deactivated'."""
    try:
        uid = normalize_uid(raw_uid)
    except ValueError:
        raise CardError("That doesn't look like a card ID. Use 8, 14 or 20 letters and "
                        "numbers, like A3 4F 91 0C.")
    today = (today or date.today()).isoformat()

    with transaction(db):
        emp = db.execute("SELECT * FROM employees WHERE id = ?", (employee_id,)).fetchone()
        if emp is None or not emp["is_active"]:
            raise CardError("Only active employees can be given a card.")

        old = current_card(db, employee_id)
        if old and old["uid"] == uid:
            raise CardError("This card is already assigned to %s." % emp["full_name"])
        if old and replace_old_as not in RETIRED:
            raise CardError("%s already has an active card. Choose what happens to it."
                            % emp["full_name"])

        card = db.execute("SELECT * FROM cards WHERE uid = ?", (uid,)).fetchone()
        if card and card["status"] != "available":
            if card["status"] == "active":
                owner = db.execute("SELECT full_name FROM employees WHERE id = ?",
                                   (card["employee_id"],)).fetchone()
                raise CardError("That card already belongs to %s." % owner["full_name"])
            raise CardError("That card is marked %s and can't be used again." % card["status"])

        if old:                                  # retire first: only ONE active card per person
            db.execute("UPDATE cards SET status = ? WHERE id = ?", (replace_old_as, old["id"]))
        if card:
            db.execute("UPDATE cards SET employee_id = ?, status = 'active', assigned_on = ? "
                       "WHERE id = ?", (employee_id, today, card["id"]))
        else:
            db.execute("INSERT INTO cards (uid, employee_id, status, assigned_on) "
                       "VALUES (?, ?, 'active', ?)", (uid, employee_id, today))
    return uid


def retire_card(db, employee_id, status):
    """Mark the employee's active card as 'lost' or 'deactivated'. Returns its ID."""
    if status not in RETIRED:
        raise CardError("Choose lost or deactivated.")
    with transaction(db):
        card = current_card(db, employee_id)
        if card is None:
            raise CardError("This employee has no active card.")
        db.execute("UPDATE cards SET status = ? WHERE id = ?", (status, card["id"]))
    return card["uid"]
