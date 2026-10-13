"""Read-only questions the dashboards ask the database."""


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


def attention_items(db, today):
    """Things only an admin needs to act on. Empty list = all clear."""
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
                          "action": action, "href": "#"})

    names = [r[0] for r in db.execute("""
        SELECT full_name FROM employees e WHERE is_active = 1 AND NOT EXISTS
          (SELECT 1 FROM cards c WHERE c.employee_id = e.id AND c.status = 'active')
        ORDER BY full_name""")]
    if names:
        items.append({"title": "1 employee has no card" if len(names) == 1
                               else "%d employees have no card" % len(names),
                      "detail": _names(names) + ".", "action": "Assign cards", "href": "#"})
    return items
