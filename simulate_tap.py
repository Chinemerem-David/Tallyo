"""Simulate a card tap with no hardware.

    python simulate_tap.py                          random active card, right now
    python simulate_tap.py "A3 4F 91 0C"            that card, right now
    python simulate_tap.py "A3 4F 91 0C" --at "2026-10-13 17:04"
"""
import argparse
import os
import random
from datetime import datetime

try:                                  # use the same .env as the web app (database path etc.)
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import db as dbm
import reader

parser = argparse.ArgumentParser()
parser.add_argument("uid", nargs="?", help="card ID; omit to pick a random active card")
parser.add_argument("--at", help='pretend the tap happened then, e.g. "2026-10-13 08:42"')
args = parser.parse_args()

DB_PATH = os.environ.get("TALLYO_DATABASE", str(dbm.DEFAULT_DB))     # the same database the web app uses
conn = dbm.connect(DB_PATH)
uid = args.uid or random.choice(
    [r["uid"] for r in conn.execute("SELECT uid FROM cards WHERE status = 'active'")])
when = datetime.fromisoformat(args.at) if args.at else None
if when is not None and abs((datetime.now() - when).total_seconds()) > 20:
    print("NOTE: the kiosk screen only shows taps from the last 20 seconds, so this one won't appear there.")
    print("      Leave out --at to see it on the kiosk.\n")
outcome = reader.handle_uid(conn, uid, when)       # the same code the real reader uses
if outcome["mode"] == "captured":
    print("Captured for the admin page (an admin is waiting to register a card):", outcome["uid"])
    raise SystemExit
if outcome["mode"] == "invalid":
    raise SystemExit("Not a valid card ID: %r" % uid)
result = outcome["tap"]

print("Card   :", result["uid"])
print("Result :", result["result"])
if result["employee"]:
    print("Person :", result["employee"]["name"], "(%s)" % result["employee"]["department"])
print("Time   :", result["time"])
for key in ("status", "hours", "checked_in_at", "left_early"):
    if key in result and result[key] is not None:
        print("%-7s:" % key.capitalize(), result[key])
