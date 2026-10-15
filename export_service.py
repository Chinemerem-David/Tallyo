"""Export everything as a ZIP of CSV files (opens in Excel). Passwords are never exported."""
import csv
import io
import zipfile

_QUERIES = {
    "employees.csv": ("SELECT employee_code, full_name, department, email, joined_on, is_active "
                      "FROM employees ORDER BY employee_code"),
    "cards.csv": ("SELECT c.uid AS card_id, e.employee_code, e.full_name, c.status, c.assigned_on "
                  "FROM cards c LEFT JOIN employees e ON e.id = c.employee_id ORDER BY c.id"),
    "attendance.csv": ("SELECT a.work_date, e.employee_code, e.full_name, a.check_in, a.check_out, "
                       "a.status, a.source, a.note FROM attendance a JOIN employees e ON e.id = a.employee_id "
                       "ORDER BY a.work_date, e.employee_code"),
    "tap_log.csv": ("SELECT t.tapped_at, t.card_uid, e.employee_code, t.result FROM tap_log t "
                    "LEFT JOIN employees e ON e.id = t.employee_id ORDER BY t.tapped_at, t.id"),
    "settings.csv": "SELECT key, value FROM settings ORDER BY key",
}


def safe_cell(value):
    """Spreadsheet programs run text that starts with = + - @ as a formula. Defuse it."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def export_zip(db):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename, sql in _QUERIES.items():
            cursor = db.execute(sql)
            out = io.StringIO(newline="")
            writer = csv.writer(out)
            writer.writerow([col[0] for col in cursor.description])
            for row in cursor:
                writer.writerow([safe_cell(v) for v in row])
            archive.writestr(filename, "\ufeff" + out.getvalue())     # BOM so Excel reads UTF-8 names
    return buffer.getvalue()
