"""'Waiting for a card…', Test reader, and the live reader status on the admin pages."""
import os, re, sys, tempfile, unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_TMP = tempfile.TemporaryDirectory()
os.environ["TALLYO_DATABASE"] = os.path.join(_TMP.name, "readerpages.db")

import db as dbm        # noqa: E402
import reader           # noqa: E402
import seed as seeder   # noqa: E402

NEW, SPARE = "11 22 33 44", "C9 18 7E 40"


class Pages(unittest.TestCase):
    def setUp(self):
        from app import app
        app.config["TESTING"] = True
        self.app = app
        seeder.seed(os.environ["TALLYO_DATABASE"])
        self.db = dbm.connect(os.environ["TALLYO_DATABASE"])
        self.admin = self.login("admin", "admin", "admin123")

    def login(self, role, user, pw):
        c = self.app.test_client()
        c.post("/login", data={"csrf_token": self.token(c, "/login"), "role": role, "username": user, "password": pw})
        return c

    def token(self, c, url):
        return re.search(r'name="csrf_token" value="([^"]+)"', c.get(url).get_data(as_text=True)).group(1)

    def post(self, url, **data):
        return self.admin.post(url, data={"csrf_token": self.token(self.admin, "/admin/cards"), **data})

    def test_listen_capture_and_status_round_trip(self):
        self.assertEqual(self.admin.get("/admin/reader/listen/status").get_json()["waiting"], False)
        self.assertEqual(self.post("/admin/reader/listen", purpose="register").get_json(), {"ok": True})
        waiting = self.admin.get("/admin/reader/listen/status")
        self.assertEqual(waiting.headers["Cache-Control"], "no-store")
        self.assertTrue(waiting.get_json()["waiting"])
        reader.handle_uid(self.db, NEW)                                          # the reader program sees a card
        got = self.admin.get("/admin/reader/listen/status").get_json()
        self.assertEqual((got["waiting"], got["uid"]), (False, NEW))
        self.assertIn("new card", got["note"])
        reader.start_listening(self.db, "register")
        reader.handle_uid(self.db, SPARE)
        self.assertIn("spare", self.admin.get("/admin/reader/listen/status").get_json()["note"])
        self.assertEqual(self.post("/admin/reader/listen/cancel").get_json(), {"ok": True})
        self.assertEqual(self.admin.get("/admin/reader/listen/status").get_json()["uid"], None)

    def test_captured_card_can_be_registered_and_assigned_with_the_existing_forms(self):
        self.post("/admin/reader/listen", purpose="register")
        reader.handle_uid(self.db, NEW)
        uid = self.admin.get("/admin/reader/listen/status").get_json()["uid"]
        url = "/admin/employees/3/card"                                           # Grace, who has no card
        r = self.admin.post(url, data={"csrf_token": self.token(self.admin, url), "uid_manual": uid}, follow_redirects=True)
        self.assertIn("Card %s assigned to Grace Adeyemi" % NEW, r.get_data(as_text=True))
        self.post("/admin/reader/listen", purpose="register")
        reader.handle_uid(self.db, "AA BB CC DD")
        r = self.admin.post("/admin/cards/register", data={"csrf_token": self.token(self.admin, "/admin/cards"),
                            "uid": self.admin.get("/admin/reader/listen/status").get_json()["uid"]}, follow_redirects=True)
        self.assertIn("Card AA BB CC DD registered", r.get_data(as_text=True))

    def test_the_boxes_are_on_the_right_pages(self):
        card = self.admin.get("/admin/employees/3/card").get_data(as_text=True)
        self.assertIn("data-listen", card)
        self.assertIn('data-target="#uid_manual"', card)
        self.assertIn('id="uid_manual"', card)
        self.assertIn("js/listen.js", card)
        cards = self.admin.get("/admin/cards").get_data(as_text=True)
        self.assertIn('data-target="#register_uid"', cards)
        self.assertIn('id="register_uid"', cards)
        settings = self.admin.get("/admin/settings").get_data(as_text=True)
        self.assertIn('data-purpose="test"', settings)
        self.assertNotIn("disabled>Test reader", settings)
        for html in (card, cards, settings):
            self.assertIn('type="button">Start listening', html)               # never submits the form around it
            self.assertIn("simulate_tap.py", html)                              # no reader yet: explains how to try it

    def test_status_everywhere_follows_the_reader(self):
        page = lambda: self.admin.get("/admin/settings").get_data(as_text=True)
        self.assertIn("Not connected", page())
        reader.heartbeat(self.db, "ok", "COM3")
        html = page()
        self.assertIn("Connected", html)
        self.assertIn("COM3", html)
        self.assertIn("Reader connected", self.admin.get("/admin/cards").get_data(as_text=True))      # sidebar
        self.assertNotIn("simulate_tap.py", self.admin.get("/admin/cards").get_data(as_text=True))     # reader is there: no hint
        reader.heartbeat(self.db, "fault", "COM3")
        self.assertIn("Fault", page())
        self.assertIn("Reader fault: check wiring", self.admin.get("/admin/cards").get_data(as_text=True))
        self.assertIn("isn&#39;t answering", page())                                                 # the explanation for a fault

    def test_only_admins_with_a_token_may_use_it(self):
        hr = self.login("hr", "chiamaka", "hr123")
        emp = self.login("employee", "EMP-0142", "emp123")
        for c in (hr, emp):
            self.assertEqual(c.get("/admin/reader/listen/status").status_code, 403)
            self.assertEqual(c.post("/admin/reader/listen", data={"purpose": "register"}).status_code, 403)
        self.assertEqual(self.app.test_client().get("/admin/reader/listen/status").status_code, 302)
        self.assertEqual(self.admin.post("/admin/reader/listen", data={"purpose": "register"}).status_code, 400)      # no token
        self.assertEqual(self.post("/admin/reader/listen", purpose="shutdown").status_code, 400)                       # bad purpose
        self.assertEqual(self.admin.post("/admin/reader/listen/cancel", data={}).status_code, 400)

    def test_listening_never_records_attendance(self):
        n = self.db.execute("SELECT COUNT(*) FROM attendance").fetchone()[0]
        self.post("/admin/reader/listen", purpose="test")
        reader.handle_uid(self.db, "A3 4F 91 0C")                                  # an employee's card, tapped while listening
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM attendance").fetchone()[0], n)


if __name__ == "__main__":
    unittest.main()
