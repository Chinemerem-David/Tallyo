"""Simulate a card tap with no hardware.

    python simulate_tap.py                          random active card, right now
    python simulate_tap.py "A3 4F 91 0C"            that card, right now
    python simulate_tap.py "A3 4F 91 0C" --at "2026-10-13 17:04"
"""
import argparse
import random
from datetime import datetime

import db as dbm
from services import record_tap

parser = argparse.ArgumentParser()
parser.add_argument("uid", nargs="?", help="card ID; omit to pick a random active card")
parser.add_argument("--at", help='pretend the tap happened then, e.g. "2026-10-13 08:42"')
args = parser.parse_args()

conn = dbm.connect()
uid = args.uid or random.choice(
    [r["uid"] for r in conn.execute("SELECT uid FROM cards WHERE status = 'active'")])
when = datetime.fromisoformat(args.at) if args.at else None
result = record_tap(conn, uid, when)

print("Card   :", result["uid"])
print("Result :", result["result"])
if result["employee"]:
    print("Person :", result["employee"]["name"], "(%s)" % result["employee"]["department"])
print("Time   :", result["time"])
for key in ("status", "hours", "checked_in_at", "left_early"):
    if key in result and result[key] is not None:
        print("%-7s:" % key.capitalize(), result[key])
