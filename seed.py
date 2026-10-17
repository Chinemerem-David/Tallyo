"""Create the demo database with fake data.

    python seed.py        (WARNING: deletes and recreates instance/tallyo.db)

Demo logins:  admin / admin123   chiamaka / hr123   EMP-0142 / emp123
"""
import random
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path

from werkzeug.security import generate_password_hash

import db as dbm
from services import normalize_uid

# code, name, department, card ("RANDOM", a fixed UID, None = no card, "LOST")
NAMED = [
    ("EMP-0142", "Amara Okafor", "Operations", "A3 4F 91 0C"),
    ("EMP-0143", "Daniel Mensah", "Finance", "7B 22 C8 5E"),
    ("EMP-0147", "Grace Adeyemi", "Sales", None),
    ("EMP-0148", "Samuel Bright", "Support", "D1 09 6A 33"),
    ("EMP-0151", "Ngozi Eze", "Operations", "4E F7 20 B9"),
    ("EMP-0152", "Kofi Asante", "Support", "LOST"),
    ("EMP-0153", "Tunde Bakare", "Finance", "90 1C 5D A2"),
    ("EMP-0154", "Lena Fischer", "Operations", "RANDOM"),
    ("EMP-0155", "Chloe Martin", "Support", "RANDOM"),
    ("EMP-0156", "Ruth Mwangi", "Sales", "E8 33 AA 14"),
    ("EMP-0157", "Ibrahim Musa", "Sales", "RANDOM"),
    ("EMP-0159", "Peter Nwosu", "Finance", None),
]
DEPT_SIZE = {"Operations": 12, "Finance": 9, "Sales": 12, "Support": 15}
FIRST = ["Adaeze", "Emeka", "Fatima", "Yusuf", "Zainab", "Chidi", "Bola", "Kemi", "Sade",
         "Femi", "Hassan", "Ife", "Joy", "Kunle", "Lola", "Musa", "Nneka", "Obi", "Precious",
         "Rita", "Segun", "Tola", "Uche", "Vera", "Wale", "Yemi", "Zara", "Amina", "Bayo",
         "Dayo", "Efe", "Gift", "Halima", "Ikenna", "Jide", "Kayode"]
LAST = ["Adebayo", "Balogun", "Chukwu", "Danjuma", "Folarin", "Garba", "Igwe", "Johnson",
        "Kalu", "Lawal", "Mohammed", "Nwankwo", "Peters", "Quadri", "Rabiu", "Salami",
        "Taiwo", "Umeh", "Victor", "Williams", "Yakubu", "Zubair", "Okoro", "Bello", "Ojo"]

# Today's arrivals, chosen so the dashboards have something interesting to show.
TODAY_TIMES = {"EMP-0142": "08:42:10", "EMP-0143": "08:36:41", "EMP-0147": "09:14:05",
               "EMP-0148": "08:41:27", "EMP-0151": "08:22:50", "EMP-0153": "09:02:33",
               "EMP-0156": "09:08:19", "EMP-0159": "09:21:44"}
NOT_IN_TODAY = {"EMP-0152", "EMP-0154", "EMP-0155"}
LOST_UID = "B2 6D 05 F1"
UNREGISTERED_UID = "5D E1 02 9B"
SPARE_UIDS = ["C9 18 7E 40"]


def _people(rng):
    people = [{"code": c, "name": n, "dept": d, "card": k} for c, n, d, k in NAMED]
    names = {p["name"] for p in people}
    codes = {p["code"] for p in people}
    counts = Counter(p["dept"] for p in people)
    num = 101
    for dept, size in DEPT_SIZE.items():
        while counts[dept] < size:
            name = "%s %s" % (rng.choice(FIRST), rng.choice(LAST))
            if name in names:
                continue
            while "EMP-%04d" % num in codes:
                num += 1
            code = "EMP-%04d" % num
            names.add(name); codes.add(code); counts[dept] += 1
            people.append({"code": code, "name": name, "dept": dept, "card": "RANDOM"})
    return people


def _random_uid(rng, taken):
    while True:
        uid = normalize_uid(bytes(rng.randrange(256) for _ in range(4)).hex())
        if uid not in taken:
            taken.add(uid)
            return uid


def seed(path=dbm.DEFAULT_DB, today=None, rng_seed=42):
    today = today or date.today()
    rng = random.Random(rng_seed)
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)

    conn = dbm.connect(path)
    dbm.init_schema(conn)
    admin_h, hr_h, emp_h = (generate_password_hash(p) for p in ("admin123", "hr123", "emp123"))
    people = _people(rng)
    taken = {p["card"] for p in people if p["card"] not in (None, "RANDOM", "LOST")}
    taken |= {LOST_UID, UNREGISTERED_UID, *SPARE_UIDS}
    span = (today - timedelta(days=60) - date(2024, 1, 1)).days

    with dbm.transaction(conn):
        for p in people:                                     # employees, cards, accounts
            joined = (date(2024, 1, 1) + timedelta(days=rng.randrange(span))).isoformat()
            first, last = p["name"].lower().split(" ", 1)
            cur = conn.execute(
                "INSERT INTO employees (employee_code, full_name, department, email, joined_on)"
                " VALUES (?,?,?,?,?)",
                (p["code"], p["name"], p["dept"], "%s.%s@example.com" % (first, last), joined))
            p["id"], p["joined"], p["uid"] = cur.lastrowid, joined, None

            def add_card(uid, status):
                conn.execute("INSERT INTO cards (uid, employee_id, status, assigned_on)"
                             " VALUES (?,?,?,?)", (uid, p["id"], status, joined))
            if p["card"] == "LOST":
                add_card(LOST_UID, "lost")
                p["uid"] = _random_uid(rng, taken); add_card(p["uid"], "active")
            elif p["card"]:
                p["uid"] = _random_uid(rng, taken) if p["card"] == "RANDOM" else p["card"]
                add_card(p["uid"], "active")
            conn.execute("INSERT INTO users (username, full_name, role, password_hash, employee_id)"
                         " VALUES (?,?,?,?,?)", (p["code"], p["name"], "employee", emp_h, p["id"]))

        for uid in SPARE_UIDS + [_random_uid(rng, taken) for _ in range(2)]:
            conn.execute("INSERT INTO cards (uid, status) VALUES (?, 'available')", (uid,))
        conn.executemany("INSERT INTO users (username, full_name, role, password_hash)"
                         " VALUES (?,?,?,?)",
                         [("admin", "Admin", "admin", admin_h),
                          ("chiamaka", "Chiamaka Eze", "hr", hr_h),
                          ("tunde", "Tunde Bakare", "hr", hr_h)])

        for p in people:                                     # attendance history
            day = max(date.fromisoformat(p["joined"]), today - timedelta(days=45))
            while day <= today:
                if day.weekday() < 5:
                    if day == today:
                        if p["code"] in NOT_IN_TODAY:
                            day += timedelta(days=1); continue
                        t = TODAY_TIMES.get(p["code"]) or "08:%02d:%02d" % (rng.randrange(10, 59), rng.randrange(60))
                        check_in = datetime.combine(day, time.fromisoformat(t))
                        check_out = None
                    else:
                        if rng.random() < 0.05:              # a day off
                            day += timedelta(days=1); continue
                        minutes = min(max(int(rng.gauss(38, 16)), 5), 110)
                        check_in = datetime.combine(day, time(8)) + timedelta(minutes=minutes, seconds=rng.randrange(60))
                        check_out = datetime.combine(day, time(16, 40)) + timedelta(minutes=rng.randrange(65), seconds=rng.randrange(60))
                    status = "late" if check_in.time() > time(9, 0) else "on_time"
                    ci = check_in.strftime("%Y-%m-%d %H:%M:%S")
                    co = check_out.strftime("%Y-%m-%d %H:%M:%S") if check_out else None
                    conn.execute("INSERT INTO attendance (employee_id, work_date, check_in, check_out,"
                                 " status, source) VALUES (?,?,?,?,?,?)",
                                 (p["id"], day.isoformat(), ci, co, status, "card" if p["uid"] else "manual"))
                    if p["uid"]:
                        conn.execute("INSERT INTO tap_log (tapped_at, card_uid, employee_id, result) VALUES (?,?,?,?)",
                                     (ci, p["uid"], p["id"], "check_in"))
                        if co:
                            conn.execute("INSERT INTO tap_log (tapped_at, card_uid, employee_id, result) VALUES (?,?,?,?)",
                                         (co, p["uid"], p["id"], "check_out"))
                day += timedelta(days=1)

        # One stranger's card, on today if it is a working day, otherwise on the last Friday.
        stranger_day = today - timedelta(days=max(today.weekday() - 4, 0))
        conn.execute("INSERT INTO tap_log (tapped_at, card_uid, employee_id, result) VALUES (?,?,NULL,?)",
                     ("%s 08:51:12" % stranger_day.isoformat(), UNREGISTERED_UID, "unregistered"))

    summary = {t: conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
               for t in ("employees", "cards", "users", "attendance", "tap_log")}
    conn.close()
    return summary


if __name__ == "__main__":
    print("Seeded:", seed())
    print("Logins:  admin / admin123   chiamaka / hr123   EMP-0142 / emp123")
