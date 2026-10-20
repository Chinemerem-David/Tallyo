"""Read-only questions the dashboards ask the database."""
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta


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


def list_employees(db, q="", dept="", status="", page=1, per_page=PER_PAGE, with_cards=True):
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
    card_column = ("(SELECT c.uid FROM cards c WHERE c.employee_id = e.id AND c.status = 'active')"
                   if with_cards else "NULL")
    rows = db.execute(f"""
        SELECT e.*, {card_column} AS card_uid
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


# ---- Attendance log ------------------------------------------------------------

ATTENDANCE_PER_PAGE = 12
ATTENDANCE_MAX_DAYS = 92
ATTENDANCE_STATUSES = ("on_time", "late", "absent")

# Absences aren't stored. A working day (Mon to Fri, before today) with no record
# for an active employee who had already joined counts as absent.
_RECORDS = """
WITH RECURSIVE days(d) AS (
    SELECT :start UNION ALL SELECT date(d, '+1 day') FROM days WHERE d < :end
),
records AS (
    SELECT a.id, a.work_date, a.employee_id, a.check_in, a.check_out, a.status, a.source, a.note
    FROM attendance a WHERE a.work_date BETWEEN :start AND :end
    UNION ALL
    SELECT NULL, days.d, e.id, NULL, NULL, 'absent', NULL, NULL
    FROM days JOIN employees e ON e.is_active = 1 AND e.joined_on <= days.d
    WHERE strftime('%w', days.d) NOT IN ('0', '6') AND days.d < :today
      AND NOT EXISTS (SELECT 1 FROM attendance a WHERE a.employee_id = e.id AND a.work_date = days.d)
)
"""


def resolve_range(from_text, to_text, today):
    """Turn the date boxes into a safe (start, end, was_trimmed). Bad or missing
    values fall back to the last 30 days; the range is capped at 92 days."""
    def parse(text):
        try:
            return date.fromisoformat(text)
        except (TypeError, ValueError):
            return None
    end = min(parse(to_text) or today, today)
    start = parse(from_text) or end - timedelta(days=29)
    start = min(start, end)
    trimmed = (end - start).days > ATTENDANCE_MAX_DAYS - 1
    if trimmed:
        start = end - timedelta(days=ATTENDANCE_MAX_DAYS - 1)
    return start, end, trimmed


def _attendance_where(q, dept, status, employee_id=None):
    where, params = ["1 = 1"], {}
    if employee_id:
        where.append("r.employee_id = :emp"); params["emp"] = int(employee_id)
    if dept:
        where.append("e.department = :dept"); params["dept"] = dept
    if q:
        where.append("(e.full_name LIKE :like ESCAPE '\\' OR e.employee_code LIKE :like ESCAPE '\\')")
        params["like"] = "%" + _like(q) + "%"
    base = " AND ".join(where)
    if status in ATTENDANCE_STATUSES:
        where.append("r.status = :status"); params["status"] = status
    return base, " AND ".join(where), params


def _hours_text(start, end):
    minutes = int((datetime.strptime(end, "%Y-%m-%d %H:%M:%S") - datetime.strptime(start, "%Y-%m-%d %H:%M:%S")).total_seconds() // 60)
    return "%dh %02dm" % divmod(minutes, 60)


def _decorate(row, today):
    p = dict(row)
    parts = p["full_name"].split()
    p["initials"] = (parts[0][0] + parts[-1][0]).upper()
    p["in_time"] = p["check_in"][11:16] if p["check_in"] else "–"
    if p["check_in"] and p["check_out"]:
        p["out_time"], p["hours"] = p["check_out"][11:16], _hours_text(p["check_in"], p["check_out"])
    elif p["check_in"]:
        p["out_time"] = "Still in" if p["work_date"] == today else "Missing"
        p["hours"] = "–"
    else:
        p["out_time"] = p["hours"] = "–"
    return p


def list_attendance(db, start, end, today, q="", dept="", status="", page=1,
                    per_page=ATTENDANCE_PER_PAGE, paginate=True, employee_id=None):
    """Present and absent days together. Returns rows, total, per-status counts."""
    base, where, params = _attendance_where(q, dept, status, employee_id)
    params.update(start=start.isoformat(), end=end.isoformat(), today=today.isoformat())
    join = " FROM records r JOIN employees e ON e.id = r.employee_id WHERE "

    counts = {s: 0 for s in ATTENDANCE_STATUSES}
    for r in db.execute(_RECORDS + "SELECT r.status AS s, COUNT(*) AS n" + join + base + " GROUP BY r.status",
                        {k: v for k, v in params.items() if k != "status"}):
        counts[r["s"]] = r["n"]
    counts["all"] = sum(counts.values())

    total = db.execute(_RECORDS + "SELECT COUNT(*)" + join + where, params).fetchone()[0]
    pages = max((total + per_page - 1) // per_page, 1) if paginate else 1
    page = min(max(page, 1), pages)
    sql = (_RECORDS + "SELECT r.*, e.full_name, e.employee_code, e.department" + join + where +
           " ORDER BY r.work_date DESC, e.full_name COLLATE NOCASE")
    if paginate:
        sql += " LIMIT :limit OFFSET :offset"
        params.update(limit=per_page, offset=(page - 1) * per_page)
    rows = [_decorate(r, today.isoformat()) for r in db.execute(sql, params)]
    return {"rows": rows, "total": total, "counts": counts, "page": page, "pages": pages,
            "first": (page - 1) * per_page + 1 if total and paginate else (1 if total else 0),
            "last": min(page * per_page, total) if paginate else total, "numbers": page_numbers(page, pages)}


def get_attendance(db, record_id):
    return db.execute("""SELECT a.*, e.full_name, e.employee_code, e.department
                         FROM attendance a JOIN employees e ON e.id = a.employee_id
                         WHERE a.id = ?""", (record_id,)).fetchone()


def active_employees(db):
    return db.execute("SELECT id, employee_code, full_name, department FROM employees "
                      "WHERE is_active = 1 ORDER BY full_name COLLATE NOCASE").fetchall()


# ---- Monthly report -------------------------------------------------------------

_MONTH = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
LOW_RATE = 92          # days below this attendance rate are drawn in amber


def resolve_month(text, today):
    """'2026-10' -> date(2026, 10, 1). Bad input, or a month in the future, means this month."""
    this_month = today.replace(day=1)
    m = _MONTH.match(text or "")
    if not m:
        return this_month
    first = date(int(m.group(1)), int(m.group(2)), 1)
    return min(max(first, date(2000, 1, 1)), this_month)


def month_neighbours(first, today):
    """(previous month, next month or None when that would be in the future)."""
    prev = (first - timedelta(days=1)).replace(day=1)
    nxt = (first + timedelta(days=32)).replace(day=1)
    return prev, (nxt if nxt <= today.replace(day=1) else None)


def _pct(part, whole):
    return round(100 * part / whole) if whole else None


def report_month(db, first, today):
    """Working days only (Mon to Fri), and only days that have finished, so a
    day that is still in progress never drags the numbers down."""
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    end = min(last, today - timedelta(days=1))
    empty = {"first": first, "end": end, "days": [], "totals": None, "avg_arrival": None,
             "top_late": [], "departments": []}
    if end < first:
        return empty

    rows = db.execute(_RECORDS + """
        SELECT r.work_date, r.employee_id, r.status, r.check_in, e.full_name, e.department
        FROM records r JOIN employees e ON e.id = r.employee_id
        WHERE strftime('%w', r.work_date) NOT IN ('0', '6')""",
        {"start": first.isoformat(), "end": end.isoformat(), "today": today.isoformat()}).fetchall()
    if not rows:
        return empty

    by_day = defaultdict(lambda: {"present": 0, "late": 0, "total": 0})
    by_dept = defaultdict(lambda: {"present": 0, "late": 0, "total": 0})
    late_by_person, minutes = Counter(), []
    names = {}
    for r in rows:
        present = r["status"] != "absent"
        for bucket in (by_day[r["work_date"]], by_dept[r["department"]]):
            bucket["total"] += 1
            bucket["present"] += present
            bucket["late"] += r["status"] == "late"
        if r["status"] == "late":
            late_by_person[r["employee_id"]] += 1
            names[r["employee_id"]] = (r["full_name"], r["department"])
        if r["check_in"]:
            h, m, s = (int(x) for x in r["check_in"][11:19].split(":"))
            minutes.append(h * 60 + m + s / 60)

    days = []
    for d in sorted(by_day):
        c = by_day[d]
        days.append({"date": d, "day_num": int(d[8:]), "present": c["present"], "late": c["late"],
                     "absent": c["total"] - c["present"], "total": c["total"], "rate": _pct(c["present"], c["total"])})
    present = sum(c["present"] for c in by_day.values())
    total = sum(c["total"] for c in by_day.values())
    avg = None
    if minutes:
        mean = round(sum(minutes) / len(minutes))
        avg = "%02d:%02d" % divmod(mean, 60)
    top = sorted(late_by_person.items(), key=lambda kv: (-kv[1], names[kv[0]][0].lower()))[:5]
    return {
        "first": first, "end": end, "days": days, "avg_arrival": avg,
        "totals": {"present": present, "total": total, "rate": _pct(present, total),
                   "late": sum(c["late"] for c in by_day.values()), "absent": total - present},
        "top_late": [{"name": names[i][0], "department": names[i][1], "times": n,
                      "initials": "".join(w[0] for w in names[i][0].split()[:2]).upper()} for i, n in top],
        "departments": sorted(({"name": k, "rate": _pct(v["present"], v["total"]), "late": v["late"]}
                               for k, v in by_dept.items()), key=lambda x: x["name"].lower()),
    }


# ---- HR dashboard ----------------------------------------------------------------

def hr_today(db, day):
    """Who is in, who is late, who hasn't arrived, and how each department is doing."""
    iso, working = day.isoformat(), day.weekday() < 5
    people = db.execute("""SELECT id, employee_code, full_name, department FROM employees
                           WHERE is_active = 1 AND joined_on <= ? ORDER BY full_name COLLATE NOCASE""", (iso,)).fetchall()
    records = {r["employee_id"]: r for r in db.execute(
        "SELECT employee_id, check_in, status FROM attendance WHERE work_date = ?", (iso,))}
    late, not_in, everyone, depts, present = [], [], [], {}, 0
    for p in people:
        parts = p["full_name"].split()
        row = {"name": p["full_name"], "code": p["employee_code"], "department": p["department"],
               "initials": (parts[0][0] + parts[-1][0]).upper()}
        d = depts.setdefault(p["department"], {"name": p["department"], "present": 0, "total": 0})
        d["total"] += 1
        rec = records.get(p["id"])
        if rec:
            present += 1
            d["present"] += 1
            if rec["status"] == "late":
                late.append({**row, "time": rec["check_in"][11:16], "stamp": rec["check_in"]})
            everyone.append({**row, "status": rec["status"], "time": rec["check_in"][11:16]})
        else:
            if working:
                not_in.append(row)
            everyone.append({**row, "status": "not_in_yet" if working else "no_work_today", "time": ""})
    late.sort(key=lambda r: r["stamp"], reverse=True)
    for d in depts.values():
        d["pct"] = _pct(d["present"], d["total"])
    return {"working_day": working, "total": len(people), "present": present, "late": late,
            "not_in": not_in, "everyone": everyone,
            "departments": sorted(depts.values(), key=lambda d: d["name"].lower())}


def employee_month_rates(db, ids, today):
    """{employee_id: % of finished working days this month they were present}."""
    first, end = today.replace(day=1), today - timedelta(days=1)
    if not ids or end < first:
        return {}
    params = {"start": first.isoformat(), "end": end.isoformat(), "today": today.isoformat()}
    params.update({"i%d" % n: int(v) for n, v in enumerate(ids)})
    marks = ", ".join(":i%d" % n for n in range(len(ids)))
    rows = db.execute(_RECORDS + """SELECT r.employee_id, SUM(r.status != 'absent') AS present, COUNT(*) AS total
        FROM records r WHERE strftime('%w', r.work_date) NOT IN ('0', '6') AND r.employee_id IN (""" + marks +
        ") GROUP BY r.employee_id", params).fetchall()
    return {r["employee_id"]: _pct(r["present"], r["total"]) for r in rows}
