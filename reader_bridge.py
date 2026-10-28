"""Connects the Arduino card reader (USB serial) to Tallyo.

    python reader_bridge.py --list          show the serial ports on this computer
    python reader_bridge.py --port COM3     use this port  (Linux/Mac: /dev/ttyACM0)
    python reader_bridge.py                 find an Arduino by itself

Run it next to the web app (a second terminal). Both programs only share the database,
so either can be stopped and started again without the other.

What the Arduino sends (one line each, 115200 baud):
    UID A3 4F 91 0C        a card was tapped
    # hb ok                heartbeat every 2 seconds, the card reader module is answering
    # hb fault             heartbeat, but the card reader module is NOT answering (wiring)
    # ...                  any other line starting with # is information, shown in the log
What this program sends back:
    RESULT ok | again | denied     so the Arduino can light its LEDs / beep
"""
import argparse
import os
import sys
import time
from datetime import datetime

import db as dbm
import reader

BAUD = 115200
SILENT_AFTER = 6.0              # seconds without ANY line from the Arduino = something is wrong
BOOT_WAIT = 2.0                 # opening the port restarts an Uno; give it time to start
RETRY_WAIT = 2.0
ARDUINO_VENDORS = {0x2341, 0x2A03, 0x1A86, 0x0403, 0x10C4}     # Arduino, clones (CH340), FTDI, CP210x


def parse_line(line):
    """One line from the Arduino -> (kind, value). Kinds: uid, hb, info, junk."""
    text = line.strip()
    if text.startswith("UID "):
        return "uid", text[4:].strip()
    if text.startswith("# hb "):
        state = text[5:].strip()
        if state in ("ok", "fault"):
            return "hb", state
    if text.startswith("#"):
        return "info", text.lstrip("# ").strip()
    return "junk", text


def find_arduino_ports(ports):
    """ports: objects with .device, .vid, .description (what pyserial lists)."""
    return [p.device for p in ports
            if (p.vid in ARDUINO_VENDORS) or "arduino" in (p.description or "").lower()]


class Bridge:
    """Turns lines from the Arduino into database updates. No serial code in here."""

    def __init__(self, conn, port, log=print, now=datetime.now):
        self.conn, self.port, self.log, self.now = conn, port, log, now

    def handle_line(self, raw):
        """Returns the text to send back to the Arduino, or None."""
        kind, value = parse_line(raw)
        if kind == "hb":
            detail = None if value == "ok" else "The card reader module isn't answering. Check the wiring and the 3.3V supply."
            reader.heartbeat(self.conn, value, self.port, detail, self.now())
            return None
        if kind == "info":
            self.log("Arduino: " + value)
            return None
        if kind == "uid":
            out = reader.handle_uid(self.conn, value, self.now())
            reader.heartbeat(self.conn, "ok", self.port, None, self.now())      # a tap proves it is alive
            if out["mode"] == "invalid":
                self.log("Ignored an unreadable card ID: %r" % value)
            elif out["mode"] == "captured":
                self.log("Card %s captured for the admin page" % out["uid"])
            else:
                self.log("Tap %s -> %s" % (out["tap"]["uid"], out["tap"]["result"]))
            return "RESULT " + out["feedback"]
        if value:
            self.log("Ignored: %r" % value[:80])
        return None

    def mark(self, state, detail):
        reader.heartbeat(self.conn, state, self.port if state != "disconnected" else None, detail, self.now())


def serve(bridge, ser, should_stop=lambda: False, clock=time.monotonic):
    """Read lines until the port fails (raises) or should_stop() says so."""
    last_heard = clock()
    silent = False
    while not should_stop():
        raw = ser.readline()
        if not raw:
            if clock() - last_heard > SILENT_AFTER and not silent:
                silent = True
                bridge.mark("disconnected", "No data from the Arduino. Is the sketch uploaded and running?")
            continue
        last_heard, silent = clock(), False
        reply = bridge.handle_line(raw.decode("utf-8", "replace"))
        if reply:
            ser.write((reply + "\n").encode())


def open_port(port):
    try:
        import serial
    except ImportError:
        sys.exit("pyserial isn't installed. Run:  pip install pyserial")
    return serial.Serial(port, BAUD, timeout=1)


def list_ports():
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        sys.exit("pyserial isn't installed. Run:  pip install pyserial")
    return list(lp.comports())


def hint_for(error):
    text = str(error).lower()
    if "permission" in text or "denied" in text or "access" in text:
        return ("The port is busy or not allowed. Close the Arduino Serial Monitor. "
                "On Linux, add your user to the 'dialout' group and sign in again.")
    return "USB cable unplugged?"


def run_forever(db_path, port=None, opener=open_port, finder=None, sleep=time.sleep, log=print):
    """Keep trying: find the Arduino, read taps, and start over if the cable is pulled."""
    conn = dbm.connect(db_path)
    finder = finder or (lambda: find_arduino_ports(list_ports()))
    while True:
        chosen = port
        if not chosen:
            found = finder()
            chosen = found[0] if found else None
        bridge = Bridge(conn, chosen, log)
        if not chosen:
            bridge.mark("disconnected", "No Arduino found. Plug it in with the USB cable.")
            sleep(RETRY_WAIT)
            continue
        try:
            ser = opener(chosen)
        except OSError as err:                              # pyserial's errors are OSErrors too
            bridge.mark("disconnected", hint_for(err))
            log("Cannot open %s: %s" % (chosen, err))
            sleep(RETRY_WAIT)
            continue
        log("Listening on %s" % chosen)
        sleep(BOOT_WAIT)
        try:
            serve(bridge, ser)
        except OSError as err:
            bridge.mark("disconnected", hint_for(err))
            log("Lost the reader (%s). Trying again…" % err)
            sleep(RETRY_WAIT)
        finally:
            try:
                ser.close()
            except Exception:
                pass


def main(argv=None):
    parser = argparse.ArgumentParser(description="Connect the Arduino card reader to Tallyo.")
    parser.add_argument("--port", help="serial port, e.g. COM3 or /dev/ttyACM0 (default: find an Arduino)")
    parser.add_argument("--list", action="store_true", help="list serial ports and exit")
    parser.add_argument("--db", default=os.environ.get("TALLYO_DATABASE", str(dbm.DEFAULT_DB)))
    args = parser.parse_args(argv)
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    if args.list:
        ports = list_ports()
        for p in ports:
            mark = "  <- looks like an Arduino" if p.device in find_arduino_ports([p]) else ""
            print("%s  %s%s" % (p.device, p.description, mark))
        if not ports:
            print("No serial ports found.")
        return
    try:
        run_forever(args.db, args.port)
    except KeyboardInterrupt:
        conn = dbm.connect(args.db)
        reader.heartbeat(conn, "disconnected", None, "The reader program was stopped.")
        print("\nStopped.")


if __name__ == "__main__":
    main()
