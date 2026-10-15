"""Read-only questions the dashboards ask the database."""
import re


def overview_stats(db, today):
    row = db.execute("""
        SELECT
          (SELECT COUNT(*) FROM employees WHERE is_active = 1) AS total,
          (SELECT COUNT(*) FROM attendance a JOIN employees e ON e.id = a.employee_id
            WHERE a.work_date = :d AND e.is_active = 1) AS present,
          (SELECT COUNT(*) FROM attendance a JOIN employees e ON e.id = a.employee_id
            WHERE a.work_date = :d AND a.status = 'late' AND e.is_active = 1) AS late,
          (SELECT COUNT(*) FROM employees e WHERE e.is_active = 1 AND NOT EXISTS
            (SELECT 1 FROM cards c WHERE c.employee_id = e.id AND c.status = 'active')) AS no_card
    """, {"d": today}).fetchone()
    stats = dict(row)
    stats["not_in"] = stats["total"] - stats["present"]
    return stats


def latest_taps(db, today, limit=7):
    rows = db.execute("""
        SELECT t.tapped_at, t.result, e.full_name, a.status
        FROM tap_log t
        JOIN employees e ON e.id = t.employee_id
        LEFT JOIN attendance a ON a.employee_id = e.id AND a.work_date = substr(t.tapped_at, 1, 10)
        WHERE t.result IN ('check_in', 'check_out') AND substr(t.tapped_at, 1, 10) = ?
        ORDER BY t.tapped_at DESC, t.id DESC LIMIT ?""", (today, limit)).fetchall()
    taps = []
    for r in rows:
        parts = r["full_name"].split()
        if r["result"] == "check_out":
            label, css = "Out", "pill"
        else:
            label, css = "In", ("pill pill-late" if r["status"] == "late" else "pill pill-ok")
        taps.append({"name": r["full_name"], "initials": (parts[0][0] + parts[-1][0]).upper(),
                     "time": r["tapped_at"][11:16], "label": label, "css": css})
    return taps


def _names(names):
    if len(names) <= 3:
        return ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1]
    return "%s and %d more" % (", ".join(names[:3]), len(names) - 3)


def attention_items(db, today, backup=None):
    """Things only an admin needs to act on. Empty list = all clear.
    backup: pass backup_service.status(...) to include the backup reminder."""
    items = []

    for result, one, many, detail, action in (
            ("unregistered", "Unregistered card tapped", "%d unregistered card taps",
             "Card %s at %s was rejected.", "Register this card"),
            ("inactive_card", "A lost or deactivated card was used", "%d taps with inactive cards",
             "Card %s at %s was rejected.", "Review cards")):
        n = db.execute("SELECT COUNT(*) FROM tap_log WHERE result = ? AND substr(tapped_at,1,10) = ?",
                       (result, today)).fetchone()[0]
        if n:
            last = db.execute("SELECT card_uid, tapped_at FROM tap_log WHERE result = ? AND "
                              "substr(tapped_at,1,10) = ? ORDER BY tapped_at DESC LIMIT 1",
                              (result, today)).fetchone()
            items.append({"title": one if n == 1 else many % n,
                          "detail": detail % (last["card_uid"], last["tapped_at"][11:16]),
                          "action": action, "endpoint": "admin.cards", "params": {}})

    if backup is not None and (backup["age_days"] is None or backup["age_days"] >= 7):
        never = backup["age_days"] is None
        items.append({"title": "No backup yet" if never else "Backup is %d days old" % backup["age_days"],
                      "detail": "Back up the database so records can be restored if something goes wrong."
                                if never else "Last backup: %s." % backup["latest"]["taken_at"][:10],
                      "action": "Back up now", "endpoint": "admin.settings", "params": {}})

    names = [r[0] for r in db.execute("""
        SELECT full_name FROM employees e WHERE is_active = 1 AND NOT EXISTS
          (SELECT 1 FROM cards c WHERE c.employee_id = e.id AND c.status = 'active')
        ORDER BY full_name""")]
    if names:
        items.append({"title": "1 employee has no card" if len(names) == 1
                               else "%d employees have no card" % len(names),
                      "detail": _names(names) + ".", "action": "Assign cards",
                      "endpoint": "admin.employees", "params": {"status": "no_card"}})
    return items


# ---- Employees list ----------------------------------------------------------

PER_PAGE = 10
_NO_ACTIVE_CARD = ("NOT EXISTS (SELECT 1 FROM cards c "
                   "WHERE c.employee_id = e.id AND c.status = 'active')")


def _like(text):
    """Make user text safe inside LIKE: % and _ are wildcards otherwise."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def employee_counts(db):
    return dict(db.execute(f"""
        SELECT SUM(is_active = 1) AS active, SUM(is_active = 0) AS inactive,
               SUM(is_active = 1 AND {_NO_ACTIVE_CARD}) AS no_card
        FROM employees e""").fetchone())


def departments(db):
    return [r[0] for r in db.execute("SELECT DISTINCT department FROM employees ORDER BY 1")]


def page_numbers(page, pages):
    """[1, None, 4, 5, 6, None, 9]: None means an ellipsis."""
    if pages <= 7:
        return list(range(1, pages + 1))
    wanted = sorted({1, pages, page - 1, page, page + 1} & set(range(1, pages + 1)))
    out = []
    for n in wanted:
        if out and n - out[-1] > 1:
            out.append(None)
        out.append(n)
    return out


def list_employees(db, q="", dept="", status="", page=1, per_page=PER_PAGE):
    """status: '' = active people, 'no_card' = active without a card, 'inactive'."""
    where, params = [], []
    where.append("e.is_active = 0" if status == "inactive" else "e.is_active = 1")
    if status == "no_card":
        where.append(_NO_ACTIVE_CARD)
    if dept:
        where.append("e.department = ?"); params.append(dept)
    if q:
        where.append("(e.full_name LIKE ? ESCAPE '\\' OR e.employee_code LIKE ? ESCAPE '\\')")
        params += ["%" + _like(q) + "%"] * 2
    clause = " AND ".join(where)

    total = db.execute(f"SELECT COUNT(*) FROM employees e WHERE {clause}", params).fetchone()[0]
    pages = max((total + per_page - 1) // per_page, 1)
    page = min(max(page, 1), pages)
    rows = db.execute(f"""
        SELECT e.*, (SELECT c.uid FROM cards c
                     WHERE c.employee_id = e.id AND c.status = 'active') AS card_uid
        FROM employees e WHERE {clause}
        ORDER BY e.full_name COLLATE NOCASE LIMIT ? OFFSET ?""",
        params + [per_page, (page - 1) * per_page]).fetchall()

    people = []
    for r in rows:
        p = dict(r)
        parts = p["full_name"].split()
        p["initials"] = (parts[0][0] + parts[-1][0]).upper()
        people.append(p)
    return {"rows": people, "total": total, "page": page, "pages": pages,
            "first": (page - 1) * per_page + 1 if total else 0,
            "last": min(page * per_page, total), "numbers": page_numbers(page, pages)}


def get_employee(db, employee_id):
    return db.execute("SELECT * FROM employees WHERE id = ?", (employee_id,)).fetchone()


# ---- Cards list --------------------------------------------------------------

CARD_STATUSES = ("active", "available", "lost", "deactivated")


def card_counts(db):
    rows = {r["status"]: r["n"] for r in db.execute("SELECT status, COUNT(*) AS n FROM cards GROUP BY status")}
    counts = {s: rows.get(s, 0) for s in CARD_STATUSES}
    counts["all"] = sum(counts.values())
    return counts


def get_card(db, card_id):
    return db.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()


def list_cards(db, q="", status="", page=1, per_page=PER_PAGE):
    """Search matches the card ID (spaces and colons ignored), the owner's name or ID."""
    where, params = [], []
    if status in CARD_STATUSES:
        where.append("c.status = ?"); params.append(status)
    if q:
        like = "%" + _like(q) + "%"
        parts, extra = ["e.full_name LIKE ? ESCAPE '\\'", "e.employee_code LIKE ? ESCAPE '\\'"], [like, like]
        compact = re.sub(r"[\s:\-]", "", q).upper()
        if compact:
            parts.append("REPLACE(c.uid, ' ', '') LIKE ? ESCAPE '\\'")
            extra.append("%" + _like(compact) + "%")
        where.append("(" + " OR ".join(parts) + ")"); params += extra
    clause = " AND ".join(where) or "1 = 1"
    base = "FROM cards c LEFT JOIN employees e ON e.id = c.employee_id WHERE " + clause

    total = db.execute("SELECT COUNT(*) " + base, params).fetchone()[0]
    pages = max((total + per_page - 1) // per_page, 1)
    page = min(max(page, 1), pages)
    rows = db.execute("""
        SELECT c.*, e.full_name AS owner, e.employee_code AS owner_code, e.id AS owner_id,
               (SELECT MAX(t.tapped_at) FROM tap_log t
                 WHERE t.card_uid = c.uid AND t.result IN ('check_in', 'check_out')) AS last_tap
        """ + base + """
        ORDER BY (e.full_name IS NULL), e.full_name COLLATE NOCASE, c.id
        LIMIT ? OFFSET ?""", params + [per_page, (page - 1) * per_page]).fetchall()
    return {"rows": [dict(r) for r in rows], "total": total, "page": page, "pages": pages,
            "first": (page - 1) * per_page + 1 if total else 0, "last": min(page * per_page, total),
            "numbers": page_numbers(page, pages)}


# ---- Settings page helpers ---------------------------------------------------

def last_tap(db):
    row = db.execute("SELECT MAX(tapped_at) FROM tap_log WHERE result IN ('check_in','check_out')").fetchone()
    return row[0]


def attendance_count(db):
    return db.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]


# ---- Users -------------------------------------------------------------------

def staff_accounts(db):
    """Admin and HR accounts (employee accounts are managed from Employees)."""
    people = []
    for r in db.execute("""SELECT id, username, full_name, role, is_active, last_login_at
                           FROM users WHERE role IN ('admin', 'hr')
                           ORDER BY (role <> 'admin'), full_name COLLATE NOCASE"""):
        p = dict(r)
        parts = p["full_name"].split()
        p["initials"] = (parts[0][0] + parts[1][0] if len(parts) > 1 else p["full_name"][:2]).upper()
        people.append(p)
    return people


def employee_account_count(db):
    return db.execute("SELECT COUNT(*) FROM users WHERE role = 'employee'").fetchone()[0]


def get_user(db, user_id):
    return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
