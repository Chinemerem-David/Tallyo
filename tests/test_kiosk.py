"""Kiosk: what it shows, who may open it, and that it never leaks private data."""
import json, os, re, sys, tempfile, unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_TMP = tempfile.TemporaryDirectory()
os.environ["TALLYO_DATABASE"] = os.path.join(_TMP.name, "kiosk.db")

import db as dbm                  # noqa: E402
import kiosk_service as ks        # noqa: E402
import seed as seeder             # noqa: E402
from services import record_tap   # noqa: E402

AMARA, KOFI_LOST, STRANGER = "A3 4F 91 0C", "B2 6D 05 F1", "FF EE DD CC"
T0 = datetime(2030, 1, 7, 8, 40, 0)


class Events(unittest.TestCase):
    def setUp(self):
        seeder.seed(os.environ["TALLYO_DATABASE"])
        self.db = dbm.connect(os.environ["TALLYO_DATABASE"])
        self.start = ks.latest_id(self.db)

    def new(self, now, **kw):
        last, events = ks.events_after(self.db, self.start, now=now, **kw)
        return events

    def test_the_four_kinds_with_exactly_the_right_details(self):
        record_tap(self.db, STRANGER, T0)
        record_tap(self.db, AMARA, T0 + timedelta(seconds=5))                       # check in
        record_tap(self.db, AMARA, T0 + timedelta(seconds=25))                      # double tap
        record_tap(self.db, AMARA, T0 + timedelta(hours=8, minutes=22))             # check out
        record_tap(self.db, AMARA, T0 + timedelta(hours=8, minutes=50))             # day already complete
        record_tap(self.db, KOFI_LOST, T0 + timedelta(hours=8, minutes=51))        # lost card
        ev = self.new(T0 + timedelta(hours=9), max_age=10 ** 6)
        self.assertEqual([e["kind"] for e in ev], ["denied", "check_in", "already", "check_out", "already", "denied"])
        self.assertEqual(ev[1]["name"], "Amara")
        self.assertEqual(ev[1]["time"], "08:40")
        self.assertEqual(ev[2]["checked_in_at"], "08:40")
        self.assertEqual((ev[3]["hours"], ev[3]["time"]), ("8h 21m", "17:02"))
        self.assertEqual(ev[4]["checked_out_at"], "17:02")

    def test_a_public_screen_gets_no_private_data(self):
        record_tap(self.db, STRANGER, T0)
        record_tap(self.db, KOFI_LOST, T0 + timedelta(seconds=1))
        record_tap(self.db, AMARA, T0 + timedelta(seconds=2))
        ev = self.new(T0 + timedelta(seconds=3))
        self.assertNotIn("name", ev[0])                                  # unknown card: nothing personal
        self.assertNotIn("name", ev[1])                                  # lost card: not even the owner's name
        text = json.dumps(ev)
        for secret in (AMARA, KOFI_LOST, STRANGER, "Okafor", "Operations", "EMP-0142", "Asante"):
            self.assertNotIn(secret, text)
        self.assertEqual(set(ev[2]), {"id", "kind", "time", "name"})     # exactly these fields

    def test_old_taps_are_skipped_but_the_position_still_moves_on(self):
        record_tap(self.db, AMARA, T0)
        last, events = ks.events_after(self.db, self.start, now=T0 + timedelta(minutes=5))      # 5 min later
        self.assertEqual(events, [])
        self.assertEqual(last, ks.latest_id(self.db))

    def test_limit_and_first_name_only(self):
        for i in range(5):
            record_tap(self.db, "AA BB CC %02X" % i, T0 + timedelta(seconds=i))
        last, events = ks.events_after(self.db, self.start, now=T0 + timedelta(seconds=6), limit=3)
        self.assertEqual(len(events), 3)
        self.assertEqual(ks.events_after(self.db, last, now=T0 + timedelta(seconds=6))[1].__len__(), 2)   # the rest next time
        self.db.execute("UPDATE employees SET full_name = 'Mary Jane Watson' WHERE id = 1")
        record_tap(self.db, AMARA, T0 + timedelta(seconds=30))
        self.assertEqual(self.new(T0 + timedelta(seconds=31))[-1]["name"], "Mary")


class Access(unittest.TestCase):
    def setUp(self):
        from app import app
        app.config["TESTING"] = True
        self.app = app
        self.saved_key = app.config["KIOSK_KEY"]
        app.config["KIOSK_KEY"] = ""
        seeder.seed(os.environ["TALLYO_DATABASE"])

    def tearDown(self):
        self.app.config["KIOSK_KEY"] = self.saved_key

    def remote(self, c, url="/kiosk", ip="10.0.0.5"):
        return c.get(url, environ_overrides={"REMOTE_ADDR": ip})

    def login(self, role, user, pw):
        c = self.app.test_client()
        tok = re.search(r'name="csrf_token" value="([^"]+)"', c.get("/login").get_data(as_text=True)).group(1)
        c.post("/login", data={"csrf_token": tok, "role": role, "username": user, "password": pw})
        return c

    def test_without_a_key_only_this_computer_may_open_it(self):
        c = self.app.test_client()
        self.assertEqual(c.get("/kiosk").status_code, 200)                       # localhost
        self.assertEqual(c.get("/kiosk/events").status_code, 200)
        for url in ("/kiosk", "/kiosk/events"):
            self.assertEqual(self.remote(c, url).status_code, 403, url)           # another device
        self.assertEqual(self.remote(c, "/kiosk?key=guess").status_code, 403)     # no key is configured

    def test_with_a_key_other_devices_need_it_once(self):
        self.app.config["KIOSK_KEY"] = "s3cret-key"
        c = self.app.test_client()
        self.assertEqual(self.remote(c).status_code, 403)
        self.assertEqual(self.remote(c, "/kiosk?key=wrong").status_code, 403)
        r = self.remote(c, "/kiosk?key=s3cret-key")
        self.assertEqual(r.status_code, 302)
        self.assertNotIn("key=", r.headers["Location"])                          # the key is removed from the address
        self.assertEqual(self.remote(c).status_code, 200)                         # remembered by this browser
        self.assertEqual(self.remote(c, "/kiosk/events").status_code, 200)
        self.assertEqual(self.remote(self.app.test_client()).status_code, 403)    # a different browser is still refused
        self.assertEqual(self.app.test_client().get("/kiosk").status_code, 403)   # a key means localhost is not enough

    def test_signed_in_admin_may_open_it_but_hr_and_employees_may_not(self):
        self.assertEqual(self.remote(self.login("admin", "admin", "admin123")).status_code, 200)
        self.assertEqual(self.remote(self.login("hr", "chiamaka", "hr123")).status_code, 403)
        self.assertEqual(self.remote(self.login("employee", "EMP-0142", "emp123")).status_code, 403)

    def test_page_and_feed(self):
        c = self.app.test_client()
        page = c.get("/kiosk")
        html = page.get_data(as_text=True)
        self.assertIn("Tap your", html)
        self.assertIn('data-events="/kiosk/events"', html)
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        self.assertNotIn("Okafor", html)
        first = c.get("/kiosk/events").get_json()
        self.assertEqual(first["events"], [])
        conn = dbm.connect(os.environ["TALLYO_DATABASE"])
        record_tap(conn, STRANGER)                                              # a real tap, happening now
        record_tap(conn, "7B 22 C8 5E", datetime.now() - timedelta(minutes=3))  # an old one
        feed = c.get("/kiosk/events?after=%d" % first["last_id"])
        data = feed.get_json()
        self.assertEqual(feed.headers["Cache-Control"], "no-store")
        self.assertEqual([e["kind"] for e in data["events"]], ["denied"])        # the 3-minute-old tap is not replayed
        self.assertGreater(data["last_id"], first["last_id"])
        for junk in ("abc", "-5", "999999999", ""):
            r = c.get("/kiosk/events?after=" + junk)
            self.assertEqual((r.status_code, r.get_json()["events"]), (200, []), junk)


if __name__ == "__main__":
    unittest.main()
