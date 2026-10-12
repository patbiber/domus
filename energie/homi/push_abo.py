"""Push-Abos der App (Versand macht push.py auf dem Host)."""
import json
import os

from .konfig import PUSH_DIR


def push_schluessel():
    """Öffentlicher VAPID-Schlüssel (von push.py erzeugt), None solange keiner da ist"""
    try:
        with open(os.path.join(PUSH_DIR, "vapid_public.txt"), encoding="ascii") as f:
            return f.read().strip() or None
    except OSError:
        return None

def push_lesen(datei):
    try:
        with open(datei, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

def push_schreiben(datei, inhalt):
    tmp = datei + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(inhalt, f, ensure_ascii=False)
    os.chmod(tmp, 0o644)
    os.replace(tmp, datei)
