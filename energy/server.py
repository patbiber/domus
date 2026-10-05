"""homi-Verkaufsseite energy.biber.solar: liefert www/ aus und nimmt Anfragen entgegen.

POST /api/anfrage  JSON-Anfrage aus dem Formular -> /anfragen/neu/<zeit>-<zufall>.json
                   Der Host verschickt sie per Mail (energy/anfrage-mail.py, Pfad-Unit energy-anfrage.path),
                   damit dieser Container keine Mail-Zugangsdaten braucht.
Schutz: Grössenlimit, Pflichtfelder, Honigtopf-Feld, Mindestzeit auf der Seite; Rate-Limit macht nginx.
"""
import json
import os
import re
import secrets
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8080"))
WWW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "www")
ZIEL = os.environ.get("ANFRAGEN_DIR", "/anfragen/neu")
MAX_BYTES = 16 * 1024
OBJEKTE = {"Einfamilienhaus", "Mehrfamilienhaus / Wohnung", "Gewerbe", "Landwirtschaft", "ZEV / LEG / Gemeinschaft", "Anderes"}
PAKETE = {"Noch offen", "Einblick", "Kosten & Börse", "Steuerung", "Betrieb"}
VORHANDEN = {"Solaranlage", "Batterie", "Wärmepumpe", "E-Auto / Wallbox", "Smart Meter", "Noch nichts"}
EMAIL = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]{2,}$")


def text(d, k, n):
    v = d.get(k, "")
    return v.strip()[:n] if isinstance(v, str) else ""


class Handler(SimpleHTTPRequestHandler):
    server_version = "homi"
    sys_version = ""

    def antwort(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == "/api/health":
            return self.antwort(200, {"ok": True})
        return super().do_GET()

    def do_POST(self):
        if self.path.split("?")[0] != "/api/anfrage":
            return self.antwort(404, {"ok": False})
        laenge = int(self.headers.get("Content-Length") or 0)
        if laenge <= 0 or laenge > MAX_BYTES:
            return self.antwort(413, {"ok": False, "fehler": "zu gross"})
        try:
            d = json.loads(self.rfile.read(laenge))
            assert isinstance(d, dict)
        except (ValueError, AssertionError):
            return self.antwort(400, {"ok": False, "fehler": "ungültig"})
        if d.get("website") or not isinstance(d.get("t"), (int, float)) or d["t"] < 3000:
            return self.antwort(200, {"ok": True})            # Bot: freundlich ignorieren
        a = {
            "zeit": datetime.now().isoformat(timespec="seconds"),
            "name": text(d, "name", 100), "email": text(d, "email", 150), "telefon": text(d, "telefon", 40),
            "ort": text(d, "ort", 80), "wunsch": text(d, "wunsch", 3000),
            "objekt": d.get("objekt") if d.get("objekt") in OBJEKTE else "Anderes",
            "paket": d.get("paket") if d.get("paket") in PAKETE else "Noch offen",
            "vorhanden": [v for v in d.get("vorhanden", []) if v in VORHANDEN][:6] if isinstance(d.get("vorhanden"), list) else [],
            "demo": "betrieb" if d.get("demo") == "betrieb" else "heim",
        }
        if not a["name"] or not EMAIL.match(a["email"]) or d.get("einwilligung") is not True:
            return self.antwort(400, {"ok": False, "fehler": "Name, E-Mail und Einwilligung sind nötig"})
        name = f"{datetime.now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}.json"
        tmp = os.path.join(ZIEL, "." + name)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(a, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(ZIEL, name))
        return self.antwort(200, {"ok": True})

    def list_directory(self, path):
        self.send_error(404)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), partial(Handler, directory=WWW)).serve_forever()
