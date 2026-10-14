"""Part 3: assign, replace and retire cards."""
import os, re, sys, tempfile, unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_TMP = tempfile.TemporaryDirectory()
os.environ["TALLYO_DATABASE"] = os.path.join(_TMP.name, "cards.db")

import card_service as cs         # noqa: E402
import db as dbm                  # noqa: E402
import seed as seeder             # noqa: E402
from services import record_tap   # noqa: E402

AMARA_ID, GRACE_ID, DANIEL_ID = 1, 3, 2            # seeded employees (Grace has no card)
AMARA_CARD, DANIEL_CARD, KOFI_LOST = "A3 4F 91 0C", "7B 22 C8 5E", "B2 6D 05 F1"
NEW_CARD = "11 22 33 44"
DAY = datetime(2030, 1, 7, 8, 30)


class CardRules(unittest.TestCase):
    def setUp(self):
        seeder.seed(os.environ["TALLYO_DATABASE"])
        self.db = dbm.connect(os.environ["TALLYO_DATABASE"])

    def status(self, uid):
        row = self.db.execute("SELECT status FROM cards WHERE uid = ?", (uid,)).fetchone()
        return row["status"] if row else None

    def refused(self, *args, **kw):
        with self.assertRaises(cs.CardError) as cm:
            cs.assign_card(self.db, *args, **kw)
        return str(cm.exception)

    def test_assign_a_brand_new_card_then_it_works_at_the_reader(self):
        self.assertEqual(cs.assign_card(self.db, GRACE_ID, "11:22:33:44"), NEW_CARD)
        self.assertEqual(self.status(NEW_CARD), "active")
        r = record_tap(self.db, NEW_CARD, DAY)
        self.assertEqual((r["result"], r["employee"]["name"]), ("check_in", "Grace Adeyemi"))

    def test_assign_a_spare_card(self):
        spare = cs.available_cards(self.db)[0]["uid"]
        cs.assign_card(self.db, GRACE_ID, spare)
        self.assertEqual(self.status(spare), "active")
        self.assertEqual(cs.current_card(self.db, GRACE_ID)["uid"], spare)

    def test_a_card_belongs_to_one_person(self):
        self.assertIn("already belongs to Daniel Mensah", self.refused(GRACE_ID, DANIEL_CARD))

    def test_same_card_again_and_bad_ids_are_refused(self):
        self.assertIn("already assigned to Amara", self.refused(AMARA_ID, AMARA_CARD, replace_old_as="lost"))
        self.assertIn("doesn't look like a card ID", self.refused(GRACE_ID, "hello"))

    def test_lost_card_cannot_come_back(self):
        self.assertIn("marked lost", self.refused(GRACE_ID, KOFI_LOST))

    def test_second_card_needs_a_decision_about_the_first(self):
        self.assertIn("already has an active card", self.refused(AMARA_ID, NEW_CARD))
        self.assertIsNone(self.status(NEW_CARD))                       # nothing half-done

    def test_replacing_retires_the_old_card_and_activates_the_new(self):
        cs.assign_card(self.db, AMARA_ID, NEW_CARD, replace_old_as="lost")
        self.assertEqual((self.status(AMARA_CARD), self.status(NEW_CARD)), ("lost", "active"))
        active = self.db.execute("SELECT COUNT(*) FROM cards WHERE employee_id = ? AND status='active'", (AMARA_ID,)).fetchone()[0]
        self.assertEqual(active, 1)
        self.assertEqual(record_tap(self.db, AMARA_CARD, DAY)["result"], "inactive_card")    # old one dead
        self.assertEqual(record_tap(self.db, NEW_CARD, DAY)["result"], "check_in")           # new one works

    def test_inactive_employee_gets_no_card(self):
        self.db.execute("UPDATE employees SET is_active = 0 WHERE id = ?", (GRACE_ID,))
        self.assertIn("active employees", self.refused(GRACE_ID, NEW_CARD))

    def test_retire_card(self):
        self.assertEqual(cs.retire_card(self.db, AMARA_ID, "lost"), AMARA_CARD)
        self.assertEqual(self.status(AMARA_CARD), "lost")
        with self.assertRaises(cs.CardError):
            cs.retire_card(self.db, AMARA_ID, "lost")                  # nothing left to retire
        with self.assertRaises(cs.CardError):
            cs.retire_card(self.db, DANIEL_ID, "stolen")               # not an allowed status

    def test_unknown_card_taps_become_pickable_until_registered(self):
        record_tap(self.db, "AB CD EF 01", DAY)
        self.assertIn("AB CD EF 01", [r["card_uid"] for r in cs.recent_unregistered(self.db)])
        cs.assign_card(self.db, GRACE_ID, "AB CD EF 01")
        self.assertNotIn("AB CD EF 01", [r["card_uid"] for r in cs.recent_unregistered(self.db)])


class CardPages(unittest.TestCase):
    def setUp(self):
        from app import app
        app.config["TESTING"] = True
        self.app = app
        seeder.seed(os.environ["TALLYO_DATABASE"])

    def login(self, role="admin", user="admin", pw="admin123"):
        c = self.app.test_client()
        tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
        c.post("/login", data={"csrf_token": tok, "role": role, "username": user, "password": pw})
        return c

    def token(self, c, url):
        return re.search(r'name="csrf_token" value="([^"]+)"', c.get(url).get_data(as_text=True)).group(1)

    def test_page_shows_current_card_and_links_are_live(self):
        c = self.login()
        html = c.get("/admin/employees/1/card").get_data(as_text=True)
        self.assertIn(AMARA_CARD, html)
        self.assertIn("Replace card", html)
        listing = c.get("/admin/employees?q=grace").get_data(as_text=True)
        self.assertIn('href="/admin/employees/3/card"', listing)       # "Assign card" now works
        self.assertIn("Manage card", c.get("/admin/employees/1/edit").get_data(as_text=True))

    def test_assign_through_the_page(self):
        c = self.login(); url = "/admin/employees/3/card"
        r = c.post(url, data={"csrf_token": self.token(c, url), "uid_manual": "11 22 33 44"}, follow_redirects=True)
        self.assertIn("Card 11 22 33 44 assigned to Grace Adeyemi", r.get_data(as_text=True))

    def test_errors_keep_the_page_and_the_typed_value(self):
        c = self.login(); url = "/admin/employees/3/card"
        r = c.post(url, data={"csrf_token": self.token(c, url), "uid_manual": "7B 22 C8 5E"})
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 422)
        self.assertIn("already belongs to Daniel Mensah", html)
        self.assertIn('value="7B 22 C8 5E"', html)
        r = c.post(url, data={"csrf_token": self.token(c, url)})
        self.assertIn("Pick a card or type a card ID", r.get_data(as_text=True))

    def test_replace_and_retire_through_the_page(self):
        c = self.login(); url = "/admin/employees/1/card"
        r = c.post(url, data={"csrf_token": self.token(c, url), "uid_manual": NEW_CARD, "replace_old_as": "deactivated"},
                   follow_redirects=True)
        self.assertIn("assigned to Amara Okafor", r.get_data(as_text=True))
        r = c.post(url + "/retire", data={"csrf_token": self.token(c, url), "status": "lost"}, follow_redirects=True)
        html = r.get_data(as_text=True)
        self.assertIn("marked as lost", html)
        self.assertIn("No active card", html)
        self.assertIn("Earlier cards", html)

    def test_csrf_and_permissions(self):
        c = self.login()
        self.assertEqual(c.post("/admin/employees/3/card", data={"uid_manual": NEW_CARD}).status_code, 400)
        self.assertEqual(c.post("/admin/employees/1/card/retire", data={"status": "lost"}).status_code, 400)
        hr = self.login("hr", "chiamaka", "hr123")
        self.assertEqual(hr.get("/admin/employees/1/card").status_code, 403)
        self.assertEqual(hr.post("/admin/employees/1/card/retire", data={"status": "lost"}).status_code, 403)
        self.assertEqual(c.get("/admin/employees/9999/card").status_code, 404)


if __name__ == "__main__":
    unittest.main()
