"""Monthly report numbers, checked against an independent Python calculation."""
import os, sys, tempfile, unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_TMP = tempfile.TemporaryDirectory()
os.environ["TALLYO_DATABASE"] = os.path.join(_TMP.name, "rep.db")

import db as dbm        # noqa: E402
import queries          # noqa: E402
import seed as seeder   # noqa: E402

TODAY = date.today()
THIS_MONTH = TODAY.replace(day=1)
LAST_MONTH = (THIS_MONTH - timedelta(days=1)).replace(day=1)


class Navigation(unittest.TestCase):
    def test_month_parsing(self):
        r = lambda t: queries.resolve_month(t, TODAY)
        self.assertEqual(r("2026-03"), date(2026, 3, 1))
        for bad in (None, "", "junk", "2026-13", "2026-00", "2026-3", "<script>"):
            self.assertEqual(r(bad), THIS_MONTH, bad)
        self.assertEqual(r("2099-01"), THIS_MONTH)                     # no future months
        self.assertEqual(r("1900-01"), date(2000, 1, 1))

    def test_previous_and_next(self):
        self.assertEqual(queries.month_neighbours(date(2026, 1, 1), date(2026, 10, 15)), (date(2025, 12, 1), date(2026, 2, 1)))
        self.assertEqual(queries.month_neighbours(date(2026, 10, 1), date(2026, 10, 15))[1], None)     # current month: no next


class Numbers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seeder.seed(os.environ["TALLYO_DATABASE"])
        cls.db = dbm.connect(os.environ["TALLYO_DATABASE"])

    def independent(self, first):
        """Slow, obvious version of the same rules."""
        last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        end = min(last, TODAY - timedelta(days=1))
        records = {(r[0], r[1]): (r[2], r[3]) for r in self.db.execute("SELECT employee_id, work_date, status, check_in FROM attendance")}
        people = self.db.execute("SELECT id, joined_on FROM employees WHERE is_active = 1").fetchall()
        present = late = total = 0
        per_day, late_count, minutes = {}, {}, []
        d = first
        while d <= end:
            if d.weekday() < 5:
                n_present = n_total = 0
                for emp_id, joined in people:
                    if joined > d.isoformat():
                        continue
                    n_total += 1
                    rec = records.get((emp_id, d.isoformat()))
                    if rec:
                        n_present += 1
                        if rec[0] == "late":
                            late += 1; late_count[emp_id] = late_count.get(emp_id, 0) + 1
                        h, m, s = (int(x) for x in rec[1][11:19].split(":"))
                        minutes.append(h * 60 + m + s / 60)
                present += n_present; total += n_total
                if n_total:
                    per_day[d.isoformat()] = (n_present, n_total)
            d += timedelta(days=1)
        return present, total, late, per_day, late_count, minutes

    def test_last_full_month_matches_the_independent_calculation(self):
        rep = queries.report_month(self.db, LAST_MONTH, TODAY)
        present, total, late, per_day, late_count, minutes = self.independent(LAST_MONTH)
        t = rep["totals"]
        self.assertEqual((t["present"], t["total"], t["late"], t["absent"]), (present, total, late, total - present))
        self.assertEqual(t["rate"], round(100 * present / total))
        self.assertEqual({d["date"]: (d["present"], d["total"]) for d in rep["days"]}, per_day)
        mean = round(sum(minutes) / len(minutes))
        self.assertEqual(rep["avg_arrival"], "%02d:%02d" % divmod(mean, 60))
        top = rep["top_late"]
        self.assertLessEqual(len(top), 5)
        self.assertEqual([x["times"] for x in top], sorted(late_count.values(), reverse=True)[:len(top)])

    def test_current_month_ignores_today_and_weekends(self):
        rep = queries.report_month(self.db, THIS_MONTH, TODAY)
        if TODAY.day > 1:
            self.assertNotIn(TODAY.isoformat(), [d["date"] for d in rep["days"]])
        present, total, late, per_day, *_ = self.independent(THIS_MONTH)
        if rep["totals"]:
            self.assertEqual((rep["totals"]["present"], rep["totals"]["total"]), (present, total))
        # a weekend shift entered by hand must not change the report
        saturday = TODAY - timedelta(days=(TODAY.weekday() - 5) % 7 or 7)
        self.db.execute("INSERT INTO attendance (employee_id, work_date, check_in, status, source, note) "
                        "VALUES (1, ?, ?, 'on_time', 'manual', 'weekend')", (saturday.isoformat(), saturday.isoformat() + " 09:00:00"))
        after = queries.report_month(self.db, saturday.replace(day=1), TODAY)
        self.assertNotIn(saturday.isoformat(), [d["date"] for d in after["days"]])

    def test_empty_cases(self):
        self.assertIsNone(queries.report_month(self.db, date(2020, 1, 1), TODAY)["totals"])        # no data that month
        self.assertIsNone(queries.report_month(self.db, date(2026, 10, 1), date(2026, 10, 1))["totals"])   # nothing finished yet

    def test_departments_and_days_are_consistent(self):
        rep = queries.report_month(self.db, LAST_MONTH, TODAY)
        self.assertEqual([d["name"] for d in rep["departments"]], ["Finance", "Operations", "Sales", "Support"])
        self.assertTrue(all(0 <= d["rate"] <= 100 for d in rep["days"]))
        self.assertEqual(sum(d["late"] for d in rep["days"]), rep["totals"]["late"])


if __name__ == "__main__":
    unittest.main()
