"""State of the RFID reader. Nothing is connected until the Arduino step, so
this honestly says so. That step will replace status() with the real serial check."""


def status():
    return {"connected": False, "port": None, "label": "Reader not connected"}
