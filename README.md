# Tallyo: RFID attendance

Staff tap a card on a reader. Tallyo records check-in and check-out, and gives Admin, HR and each
employee their own view. Flask + SQLite, with an Arduino Uno and an RC522 reader.

## First run
```
python -m venv .venv
.venv\Scripts\activate            (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env            (Mac/Linux: cp .env.example .env)   then edit the secret key
python seed.py                    demo data: 48 employees, cards, a few weeks of attendance
python app.py                     open http://127.0.0.1:5000
```
Demo logins (pick the matching tab): `admin / admin123`, `chiamaka / hr123` (HR manager), `EMP-0142 / emp123` (Employee).
`seed.py` wipes the database. Never run it on real data.

## The pieces
| Piece | Where | Who sees it |
|---|---|---|
| Admin pages | `/admin` | Admin: employees, cards, users, settings, attendance, reports |
| HR pages | `/hr` | HR: today, attendance, reports, employees (view only, no card details) |
| Employee portal | `/portal` | Each employee: their own month |
| Kiosk screen | `/kiosk` | The screen next to the reader. No sign-in; see below |
| Reader program | `python reader_bridge.py` | Connects the Arduino to Tallyo |

## The kiosk
- Open `http://127.0.0.1:5000/kiosk` **on the computer that runs the app**. It opens in fullscreen with F11.
- To use it on another device (a tablet or phone): put `TALLYO_KIOSK_KEY=some-secret` and `TALLYO_HOST=0.0.0.0` in `.env`,
  restart, and open `http://<computer address>:5000/kiosk?key=some-secret` once on that device.
- It shows a result for 4 seconds after each tap. It only shows taps from the last 20 seconds.

## Trying everything without the hardware
In a second terminal, with the app running and `/kiosk` open:
```
python simulate_tap.py                     a random registered card, right now
python simulate_tap.py "A3 4F 91 0C"       a specific card
python simulate_tap.py "FF EE DD CC"       a card nobody owns: "Card not recognised"
```
Don't add `--at` with an old date if you want to see it on the kiosk: old taps are ignored there.

## If the kiosk doesn't react to a tap
1. Is `/kiosk` open **on the computer running the app** (or opened once with `?key=`)? See above.
2. The app, `simulate_tap.py` and `reader_bridge.py` must use the **same database**. Don't point them at different `TALLYO_DATABASE` files.
3. Don't use `--at` with an old date: the kiosk ignores taps older than 20 seconds.
4. Keep the project on a **local disk** (for example `C:\tallyo`), not on a network drive or in a cloud-synced folder such as OneDrive or Dropbox. SQLite doesn't share well across programs there.
5. A small "Reconnecting…" badge on the kiosk means it lost contact with the app.

## With the Arduino
See `arduino/README.md` (wiring, uploading the sketch, running `reader_bridge.py`).

## Tests
```
python -m unittest discover -s tests
```
The firmware tests need a C++ compiler (g++) and are skipped without one. A few tests are skipped on weekends
because the demo data has no attendance on weekends.

## Layout
```
app.py  auth.py  db.py  schema.sql  seed.py  simulate_tap.py  reader_bridge.py
admin.py  hr.py  portal.py  kiosk.py  account.py            the pages
services.py  reader.py  queries.py  *_service.py            the rules and database questions
templates/  static/  arduino/  tests/
instance/                                                    the database lives here (not in version control)
```
