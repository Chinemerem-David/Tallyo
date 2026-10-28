"""The Arduino sketch, run on a normal computer against fake hardware, and checked against
the real Python bridge. This does NOT replace trying it on the real board, but it proves the
logic and the text protocol are right before you plug anything in."""
import os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ARDUINO = ROOT / "arduino"
sys.path.insert(0, str(ROOT))
_TMP = tempfile.TemporaryDirectory()
os.environ["TALLYO_DATABASE"] = os.path.join(_TMP.name, "fw.db")

import db as dbm            # noqa: E402
import reader_bridge as rb  # noqa: E402
import seed as seeder       # noqa: E402


@unittest.skipUnless(shutil.which("g++"), "needs a C++ compiler (g++) to run the sketch on a computer")
class Firmware(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = os.path.join(_TMP.name, "fw_test")
        cls.build = subprocess.run(
            ["g++", "-std=c++11", "-Wall", "-Wextra", "-I", str(ARDUINO / "tests" / "stubs"), "-include", "Arduino.h",
             str(ARDUINO / "tests" / "firmware_test.cpp"), "-o", cls.exe], capture_output=True, text=True)
        cls.run_out = subprocess.run([cls.exe], capture_output=True, text=True) if cls.build.returncode == 0 else None

    def test_the_sketch_compiles_cleanly(self):
        self.assertEqual(self.build.returncode, 0, self.build.stderr[:600])
        ours = [l for l in self.build.stderr.splitlines() if ("tallyo_reader" in l or "uid_format" in l) and "warning" in l]
        self.assertEqual(ours, [])                                          # no warnings in OUR code

    def test_all_the_firmware_logic_checks_pass(self):
        self.assertEqual(self.run_out.returncode, 0, self.run_out.stderr)
        self.assertIn("firmware logic: all checks passed", self.run_out.stdout)

    def test_the_real_bridge_understands_everything_the_sketch_says(self):
        text = self.run_out.stdout.split("TRANSCRIPT_BEGIN\n")[1].split("TRANSCRIPT_END")[0]
        lines = text.splitlines()
        self.assertEqual(lines[0], "# tallyo-reader 1.0 ready")
        seeder.seed(os.environ["TALLYO_DATABASE"])
        conn = dbm.connect(os.environ["TALLYO_DATABASE"])
        bridge = rb.Bridge(conn, "COM3", lambda *_: None)
        replies = {}
        for line in lines:
            kind, value = rb.parse_line(line)
            self.assertIn(kind, ("uid", "hb", "info"), line)               # nothing the sketch says is junk
            reply = bridge.handle_line(line + "\n")
            if kind == "uid":
                replies[value] = reply
        self.assertEqual(sorted(replies), ["A3 4F 91 0C", "DE AD BE EF"])
        self.assertEqual(replies["DE AD BE EF"], "RESULT denied")           # not a registered card
        self.assertIn(replies["A3 4F 91 0C"], ("RESULT ok", "RESULT again"))
        last = conn.execute("SELECT card_uid, result FROM tap_log ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual((last["card_uid"], last["result"]), ("DE AD BE EF", "unregistered"))
        import reader
        self.assertEqual(reader.status(conn)["label"], "Reader connected")


if __name__ == "__main__":
    unittest.main()
