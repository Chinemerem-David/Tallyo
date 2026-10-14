"""Admin Cards page: list, search, filters, register, assign a spare."""
import os, re, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_TMP = tempfile.TemporaryDirectory()
os.environ["TALLYO_DATABASE"] = os.path.join(_TMP.name, "cardspage.db")

import card_service as cs         # noqa: E402
import db as dbm                  # noqa: E402
import queries                    # noqa: E402
import seed as seeder             # noqa: E402


class CardQueries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seeder.seed(os.environ["TALLYO_DATABASE"])
        cls.db = dbm.connect(os.environ["TALLYO_DATABASE"])

    def test_counts_match_the_table(self):
        counts = queries.card_counts(self.db)
        self.assertEqual(counts["all"], self.db.execute("SELECT COUNT(*) FROM cards").fetchone()[0])
        self.assertEqual((counts["available"], counts["lost"], counts["deactivated"]), (3, 1, 0))

    def test_filter_by_status(self):
        self.assertEqual(queries.list_cards(self.db, status="available")["total"], 3)
        lost = queries.list_cards(self.db, status="lost")["rows"]
        self.assertEqual((lost[0]["uid"], lost[0]["owner"]), ("B2 6D 05 F1", "Kofi Asante"))

    def test_search_card_id_ignores_spaces_case_and_colons(self):
        for text in ("a3 4f 91", "A34F91", "a3:4f:91:0c"):
            rows = queries.list_cards(self.db, q=text)["rows"]
            self.assertEqual([r["owner"] for r in rows], ["Amara Okafor"], text)

    def test_search_by_owner_name_and_wildcards(self):
        self.assertEqual([r["owner"] for r in queries.list_cards(self.db, q="daniel")["rows"]], ["Daniel Mensah"])
        self.assertEqual(queries.list_cards(self.db, q="%")["total"], 0)

    def test_last_tap_and_pagination(self):
        amara = queries.list_cards(self.db, q="A3 4F 91 0C")["rows"][0]
        self.assertTrue(amara["last_tap"])
        first = queries.list_cards(self.db)
        self.assertEqual((len(first["rows"]), first["pages"]), (10, 5))
        self.assertIsNotNone(first["rows"][0]["owner"])                  # assigned cards are listed first
        last = queries.list_cards(self.db, page=5)["rows"]
        self.assertIsNone(last[-1]["owner"])                             # spares sit at the end

    def test_friendly_time(self):
        from shell import friendly_when
        self.assertEqual(friendly_when("2026-10-13 08:42:10", "2026-10-13"), "Today 08:42")
        self.assertEqual(friendly_when("2026-10-09 17:04:00", "2026-10-13"), "9 Oct 17:04")
        self.assertEqual(friendly_when(None), "Never")


class RegisterRules(unittest.TestCase):
    def setUp(self):
        seeder.seed(os.environ["TALLYO_DATABASE"])
        self.db = dbm.connect(os.environ["TALLYO_DATABASE"])

    def test_register_makes_an_available_card(self):
        self.assertEqual(cs.register_card(self.db, "aa:bb:cc:dd"), "AA BB CC DD")
        self.assertEqual(self.db.execute("SELECT status, employee_id FROM cards WHERE uid='AA BB CC DD'").fetchone()[:], ("available", None))

    def test_duplicates_and_junk_are_refused(self):
        with self.assertRaisesRegex(cs.CardError, "already registered"):
            cs.register_card(self.db, "A3 4F 91 0C")
        with self.assertRaises(cs.CardError):
            cs.register_card(self.db, "nope")

    def test_employees_needing_a_card(self):
        names = [p["full_name"] for p in cs.employees_needing_card(self.db)]
        self.assertEqual(names, ["Grace Adeyemi", "Peter Nwosu"])


class CardsPage(unittest.TestCase):
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

    def token(self, c, url="/admin/cards"):
        return re.search(r'name="csrf_token" value="([^"]+)"', c.get(url).get_data(as_text=True)).group(1)

    def spare_id(self):
        return dbm.connect(os.environ["TALLYO_DATABASE"]).execute("SELECT id FROM cards WHERE status='available'").fetchone()[0]

    def test_page_lists_cards_and_offers_actions(self):
        html = self.login().get("/admin/cards").get_data(as_text=True)
        self.assertIn("Register a new card", html)
        self.assertIn("A3 4F 91 0C", html)
        self.assertIn("Available 3", html)
        self.assertIn("5D E1 02 9B", html)                 # the stranger's tap is offered for one-click register
        self.assertIn("Search card ID", html)

    def test_filter_and_search_through_the_url(self):
        c = self.login()
        html = c.get("/admin/cards?status=lost").get_data(as_text=True)
        self.assertIn("B2 6D 05 F1", html)
        self.assertNotIn("Amara Okafor", html)             # (the placeholder text also contains that ID)
        self.assertIn("No cards match", c.get("/admin/cards?q=zzzz").get_data(as_text=True))

    def test_register_through_the_page_and_one_click_from_a_recent_tap(self):
        c = self.login()
        r = c.post("/admin/cards/register", data={"csrf_token": self.token(c), "uid": "AA BB CC DD"}, follow_redirects=True)
        self.assertIn("Card AA BB CC DD registered", r.get_data(as_text=True))
        r = c.post("/admin/cards/register", data={"csrf_token": self.token(c), "uid": "5D E1 02 9B"}, follow_redirects=True)
        html = r.get_data(as_text=True)
        self.assertIn("Card 5D E1 02 9B registered", html)
        self.assertNotIn("+ 5D E1 02 9B", html)            # no longer offered as unregistered
        r = c.post("/admin/cards/register", data={"csrf_token": self.token(c), "uid": "AA BB CC DD"}, follow_redirects=True)
        self.assertIn("already registered", r.get_data(as_text=True))

    def test_assign_a_spare_card_to_an_employee(self):
        c = self.login(); url = "/admin/cards/%d/assign" % self.spare_id()
        html = c.get(url).get_data(as_text=True)
        self.assertIn("Grace Adeyemi", html)
        self.assertNotIn("Amara Okafor", html)             # already has a card, so not offered
        r = c.post(url, data={"csrf_token": self.token(c, url), "employee_id": "3"}, follow_redirects=True)
        self.assertIn("assigned to Grace Adeyemi", r.get_data(as_text=True))
        r = c.post(url, data={"csrf_token": self.token(c)})           # the card is no longer a spare
        self.assertEqual(r.status_code, 302)

    def test_assign_requires_a_choice_and_only_spare_cards(self):
        c = self.login(); url = "/admin/cards/%d/assign" % self.spare_id()
        r = c.post(url, data={"csrf_token": self.token(c, url), "employee_id": ""})
        self.assertEqual(r.status_code, 422)
        self.assertIn("Choose an employee", r.get_data(as_text=True))
        r = c.get("/admin/cards/1/assign", follow_redirects=True)       # card 1 is active, not a spare
        self.assertIn("Only spare cards", r.get_data(as_text=True))

    def test_overview_links_are_live_and_permissions_hold(self):
        c = self.login()
        html = c.get("/admin").get_data(as_text=True)
        self.assertIn('href="/admin/employees?status=no_card"', html)
        self.assertIn('href="/admin/cards"', html)
        self.assertEqual(c.post("/admin/cards/register", data={"uid": "AA BB CC DD"}).status_code, 400)
        hr = self.login("hr", "chiamaka", "hr123")
        self.assertEqual(hr.get("/admin/cards").status_code, 403)
        self.assertEqual(hr.post("/admin/cards/register", data={"uid": "AA BB CC DD"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
