"""Employee calendar logic, checked against an independent calculation."""
import os, sys, tempfile, unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_TMP = tempfile.TemporaryDirectory()
os.environ["TALLYO_DATABASE"] = os.path.join(_TMP.name, "portal_data.db")

import db as dbm        # noqa: E402
import queries          # noqa: E402
import seed as seeder   # noqa: E402

TODAY = date.today()
THIS_MONTH = TODAY.replace(day=1)
LAST_MONTH = (THIS_MONTH - timedelta(days=1)).replace(day=1)


class Calendar(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seeder.seed(os.environ["TALLYO_DATABASE"])
        cls.db = dbm.connect(os.environ["TALLYO_DATABASE"])

    def flat(self, cal):
        return [c for week in cal["weeks"] for c in week if c]

    def test_shape_weeks_start_on_monday_and_cover_every_day(self):
        for first in (LAST_MONTH, THIS_MONTH, date(2026, 2, 1), date(2024, 2, 1)):
            cal = queries.employee_calendar(self.db, 1, first, TODAY)
            self.assertTrue(all(len(w) == 7 for w in cal["weeks"]))
            days = self.flat(cal)
            last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            self.assertEqual([c["day"] for c in days], list(range(1, last.day + 1)))
            self.assertEqual(cal["weeks"][0].index(days[0]), first.weekday())      # day 1 sits in the right column

    def test_stats_and_states_match_the_database(self):
        for first in (LAST_MONTH, THIS_MONTH):
            cal = queries.employee_calendar(self.db, 1, first, TODAY)
            last = cal["last"]
            recs = self.db.execute("SELECT status, check_in, check_out FROM attendance WHERE employee_id = 1 "
                                   "AND work_date BETWEEN ? AND ?", (first.isoformat(), last.isoformat())).fetchall()
            self.assertEqual(cal["present"], len(recs))
            self.assertEqual(cal["late"], sum(r["status"] == "late" for r in recs))
            mins = sum(queries._minutes(r["check_in"], r["check_out"]) for r in recs if r["check_out"])
            self.assertEqual(cal["hours"], queries.hours_label(mins))
            states = {c["state"] for c in self.flat(cal)}
            self.assertLessEqual(states, {"on_time", "late", "absent", "weekend", "future", "pending", "before_join"})
            for c in self.flat(cal):
                if c["state"] == "absent":                       # an absence is a past working day with no record
                    d = date.fromisoformat(c["iso"])
                    self.assertTrue(d < TODAY and d.weekday() < 5)
                    self.assertIsNone(self.db.execute("SELECT 1 FROM attendance WHERE employee_id=1 AND work_date=?", (c["iso"],)).fetchone())

    def test_today_future_and_weekend_are_never_absent(self):
        cal = queries.employee_calendar(self.db, 1, THIS_MONTH, TODAY)
        for c in self.flat(cal):
            d = date.fromisoformat(c["iso"])
            if d > TODAY:
                self.assertEqual(c["state"], "future")
            if d == TODAY and not self.db.execute("SELECT 1 FROM attendance WHERE employee_id=1 AND work_date=?", (c["iso"],)).fetchone():
                self.assertEqual(c["state"], "pending")
            if d.weekday() >= 5 and d < TODAY and c["state"] not in ("on_time", "late"):
                self.assertEqual(c["state"], "weekend")

    def test_days_before_joining_are_blank_not_absent(self):
        mid = LAST_MONTH.replace(day=15)
        self.db.execute("INSERT INTO employees (employee_code, full_name, department, joined_on) VALUES ('EMP-9001','New Hire','Sales',?)", (mid.isoformat(),))
        new_id = self.db.execute("SELECT id FROM employees WHERE employee_code='EMP-9001'").fetchone()[0]
        cal = queries.employee_calendar(self.db, new_id, LAST_MONTH, TODAY)
        for c in self.flat(cal):
            d = date.fromisoformat(c["iso"])
            if d < mid and d.weekday() < 5:
                self.assertEqual(c["state"], "before_join")
            if d >= mid and d.weekday() < 5:
                self.assertEqual(c["state"], "absent")            # joined, nothing recorded

    def test_hours_label_and_month_options(self):
        self.assertEqual([queries.hours_label(m) for m in (0, 59, 60, 3260, 3240)], ["0h", "0h 59m", "1h", "54h 20m", "54h"])
        opts = queries.month_options(date(2026, 8, 1), date(2026, 10, 15))
        self.assertEqual(opts, [("2026-10", "October 2026"), ("2026-09", "September 2026"), ("2026-08", "August 2026")])
        self.assertEqual(len(queries.month_options(date(2000, 1, 1), date(2026, 10, 15))), 36)       # capped


if __name__ == "__main__":
    unittest.main()
