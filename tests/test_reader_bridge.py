"""The serial bridge, tested with a stand-in for the USB port (no hardware needed)."""
import os, sys, tempfile, unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
_TMP = tempfile.TemporaryDirectory()
os.environ["TALLYO_DATABASE"] = os.path.join(_TMP.name, "bridge.db")

import db as dbm            # noqa: E402
import reader               # noqa: E402
import reader_bridge as rb  # noqa: E402
import seed as seeder       # noqa: E402

AMARA, NEW = "A3 4F 91 0C", "11 22 33 44"


class Unplugged(OSError):
    pass


class FakeSerial:
    """Hands out scripted lines; raises Unplugged (like a pulled cable) when the script ends."""
    def __init__(self, script):
        self.script, self.written, self.closed = list(script), [], False

    def readline(self):
        if not self.script:
            raise Unplugged("device disconnected")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def write(self, data):
        self.written.append(data.decode())

    def close(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.t = 0.0
    def __call__(self):
        return self.t


class Base(unittest.TestCase):
    def setUp(self):
        seeder.seed(os.environ["TALLYO_DATABASE"])
        self.db = dbm.connect(os.environ["TALLYO_DATABASE"])
        self.logs = []
        self.bridge = rb.Bridge(self.db, "COM3", self.logs.append)

    def taps(self):
        return self.db.execute("SELECT card_uid, result FROM tap_log ORDER BY id DESC LIMIT 3").fetchall()


class Parsing(unittest.TestCase):
    def test_lines(self):
        self.assertEqual(rb.parse_line("UID A3 4F 91 0C\r\n"), ("uid", "A3 4F 91 0C"))
        self.assertEqual(rb.parse_line("# hb ok"), ("hb", "ok"))
        self.assertEqual(rb.parse_line("# hb fault\n"), ("hb", "fault"))
        self.assertEqual(rb.parse_line("# tallyo-reader 1.0 ready"), ("info", "tallyo-reader 1.0 ready"))
        self.assertEqual(rb.parse_line("# hb weird")[0], "info")
        for junk in ("", "hello", "UI D", "\x00\xff"):
            self.assertEqual(rb.parse_line(junk)[0], "junk", repr(junk))

    def test_finding_an_arduino(self):
        ports = [SimpleNamespace(device="COM1", vid=None, description="Communications Port"),
                 SimpleNamespace(device="COM4", vid=0x2341, description="Arduino Uno"),
                 SimpleNamespace(device="COM7", vid=0x1A86, description="USB-SERIAL CH340"),
                 SimpleNamespace(device="/dev/ttyS0", vid=None, description="Arduino compatible")]
        self.assertEqual(rb.find_arduino_ports(ports), ["COM4", "COM7", "/dev/ttyS0"])
        self.assertEqual(rb.find_arduino_ports([]), [])


class Handling(Base):
    def test_heartbeats_drive_the_status(self):
        self.assertIsNone(self.bridge.handle_line("# hb ok\n"))
        self.assertEqual(reader.status(self.db)["label"], "Reader connected")
        self.bridge.handle_line("# hb fault\n")
        s = reader.status(self.db)
        self.assertEqual((s["state"], s["connected"]), ("fault", False))
        self.assertIn("wiring", s["detail"])

    def test_a_tap_is_recorded_and_the_arduino_is_told_the_outcome(self):
        self.assertEqual(self.bridge.handle_line("UID FF EE DD CC\n"), "RESULT denied")
        self.assertEqual(self.taps()[0]["result"], "unregistered")
        self.assertEqual(reader.status(self.db)["connected"], True)                           # a tap proves it is alive

    def test_new_employee_check_in_then_double_tap(self):
        cur = self.db.execute("INSERT INTO employees (employee_code, full_name, department, joined_on) VALUES ('EMP-9100','Test Person','Sales','2025-01-01')")
        self.db.execute("INSERT INTO cards (uid, employee_id, status, assigned_on) VALUES (?,?, 'active','2025-01-01')", (NEW, cur.lastrowid))
        self.assertEqual(self.bridge.handle_line("UID 11 22 33 44"), "RESULT ok")
        self.assertEqual(self.bridge.handle_line("UID 11:22:33:44"), "RESULT again")             # inside the 60 s window
        self.assertEqual([t["result"] for t in self.taps()][:2], ["duplicate", "check_in"])

    def test_garbage_changes_nothing_and_gets_no_reply(self):
        n = self.db.execute("SELECT COUNT(*) FROM tap_log").fetchone()[0]
        for junk in ("noise\n", "UID zz zz zz zz\n", "UID \n", "\x00\x01\n"):
            reply = self.bridge.handle_line(junk)
            self.assertIn(reply, (None, "RESULT denied"))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM tap_log").fetchone()[0], n)

    def test_waiting_for_a_card_captures_instead_of_recording(self):
        reader.start_listening(self.db, "register")
        self.assertEqual(self.bridge.handle_line("UID " + NEW), "RESULT ok")
        self.assertEqual(reader.listening(self.db)["uid"], NEW)
        self.assertTrue(any("captured" in line for line in self.logs))
        self.assertNotIn(NEW, [t["card_uid"] for t in self.taps()])


class Serving(Base):
    def test_a_stream_of_bytes_including_rubbish(self):
        ser = FakeSerial([b"# tallyo-reader 1.0 ready\r\n", b"# hb ok\r\n", b"\xff\xfe garbage\n",
                          b"UID FF EE DD CC\r\n", b"# hb ok\n"])
        with self.assertRaises(Unplugged):
            rb.serve(self.bridge, ser)
        self.assertEqual(ser.written, ["RESULT denied\n"])                       # exactly one reply
        self.assertEqual(self.taps()[0]["result"], "unregistered")
        self.assertIn("Arduino: tallyo-reader 1.0 ready", self.logs)

    def test_silence_marks_the_reader_disconnected_then_recovers(self):
        clock = Clock()
        class Quiet(FakeSerial):
            def readline(inner):
                clock.t += 2.0
                return super().readline() if inner.script else b""
        ser = Quiet([b"# hb ok\n"])
        steps = {"n": 0}
        def stop():
            steps["n"] += 1
            return steps["n"] > 6
        rb.serve(self.bridge, ser, should_stop=stop, clock=clock)
        s = reader.status(self.db)
        self.assertEqual(s["state"], "disconnected")
        self.assertIn("No data from the Arduino", s["detail"])
        ser.script.append(b"# hb ok\n"); steps["n"] = 0
        rb.serve(self.bridge, ser, should_stop=lambda: not ser.script, clock=clock)
        self.assertEqual(reader.status(self.db)["state"], "ok")


class Outer(Base):
    def test_no_arduino_then_found_then_unplugged_then_stopped(self):
        sleeps, opened = [], []
        finds = iter([[], ["COM9"], ["COM9"], ["COM9"]])
        sers = iter([FakeSerial([b"# hb ok\n", b"UID FF EE DD CC\n"]), None])
        def opener(port):
            opened.append(port)
            ser = next(sers)
            if ser is None:
                raise KeyboardInterrupt                                           # ends the loop for this test
            return ser
        states = []
        def fake_sleep(seconds):
            sleeps.append(seconds)
            states.append(reader.status(self.db)["detail"])
        with self.assertRaises(KeyboardInterrupt):
            rb.run_forever(os.environ["TALLYO_DATABASE"], None, opener=opener, finder=lambda: next(finds),
                           sleep=fake_sleep, log=self.logs.append)
        self.assertEqual(opened, ["COM9", "COM9"])
        self.assertIn("No Arduino found", states[0])                             # first lap: nothing plugged in
        self.assertEqual(sleeps[:2], [rb.RETRY_WAIT, rb.BOOT_WAIT])
        self.assertIn("USB cable unplugged?", states[-1])                          # after the cable was pulled
        self.assertIn("Listening on COM9", self.logs)

    def test_a_busy_port_gets_a_helpful_hint(self):
        self.assertIn("Serial Monitor", rb.hint_for(PermissionError("Access is denied")))
        self.assertIn("dialout", rb.hint_for(OSError("[Errno 13] Permission denied: '/dev/ttyACM0'")))
        self.assertEqual(rb.hint_for(OSError("gone")), "USB cable unplugged?")


if __name__ == "__main__":
    unittest.main()
