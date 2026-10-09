"""homi-Verkaufsseite homi.solar (früher energy.biber.solar): liefert www/ aus und nimmt Anfragen entgegen.

POST /api/anfrage  JSON-Anfrage aus dem Formular -> /anfragen/neu/<zeit>-<zufall>.json
                   Der Host verschickt sie per Mail (energy/anfrage-mail.py, Pfad-Unit energy-anfrage.path),
                   damit dieser Container keine Mail-Zugangsdaten braucht.
POST /api/warnung  Gratis-Minuspreis-Warnung abonnieren (Double-Opt-in):
                   /abos/ausstehend/<token>.json + Versandauftrag /abos/versand/<token>.json (Host mailt den Link)
GET  /api/warnung/bestaetigen?t=<token>  -> /abos/aktiv/<abmelde-token>.json
GET  /api/warnung/abmelden?t=<abmelde-token>  -> Abo gelöscht
Schutz: Grössenlimit, Pflichtfelder, Honigtopf-Feld, Mindestzeit auf der Seite; Rate-Limit macht nginx.
"""
import json
import os
import re
import secrets
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8080"))
WWW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "www")
ZIEL = os.environ.get("ANFRAGEN_DIR", "/anfragen/neu")
ABOS = os.environ.get("ABOS_DIR", "/abos")
TOKEN = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
MAX_BYTES = 16 * 1024
OBJEKTE = {"Einfamilienhaus", "Mehrfamilienhaus / Wohnung", "Gewerbe", "Landwirtschaft", "ZEV / LEG / Gemeinschaft", "Anderes"}
PAKETE = {"Noch offen", "Einblick", "Kosten & Börse", "Steuerung", "Betrieb"}
VORHANDEN = {"Solaranlage", "Batterie", "Wärmepumpe", "E-Auto / Wallbox", "Smart Meter", "Noch nichts"}
EMAIL = re.compile(r"^[^\s@<>]+@[^\s@<>]+\.[^\s@<>]{2,}$")


def text(d, k, n):
    v = d.get(k, "")
    return v.strip()[:n] if isinstance(v, str) else ""


def schreiben(pfad, daten):
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    tmp = os.path.join(os.path.dirname(pfad), "." + os.path.basename(pfad))
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(daten, f, ensure_ascii=False, indent=1)
    os.replace(tmp, pfad)


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

    def weiter(self, ziel):
        self.send_response(303)
        self.send_header("Location", ziel)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        pfad = self.path.split("?")[0]
        if pfad == "/api/health":
            return self.antwort(200, {"ok": True})
        if pfad in ("/api/warnung/bestaetigen", "/api/warnung/abmelden"):
            t = parse_qs(urlsplit(self.path).query).get("t", [""])[0]
            if not TOKEN.match(t):
                return self.weiter("/?warnung=ungueltig#einspeisung")
            if pfad.endswith("bestaetigen"):
                quelle = os.path.join(ABOS, "ausstehend", t + ".json")
                if not os.path.exists(quelle):
                    return self.weiter("/?warnung=ungueltig#einspeisung")
                d = json.load(open(quelle, encoding="utf-8"))
                if not any(json.load(open(os.path.join(ABOS, "aktiv", f), encoding="utf-8"))["email"] == d["email"]
                           for f in os.listdir(os.path.join(ABOS, "aktiv")) if f.endswith(".json")):
                    ab = secrets.token_urlsafe(24)
                    schreiben(os.path.join(ABOS, "aktiv", ab + ".json"),
                              {"email": d["email"], "seit": datetime.now().isoformat(timespec="seconds"), "abmelden": ab})
                os.remove(quelle)
                return self.weiter("/?warnung=aktiv#einspeisung")
            ziel = os.path.join(ABOS, "aktiv", t + ".json")
            if os.path.exists(ziel):
                os.remove(ziel)
            return self.weiter("/?warnung=abgemeldet#einspeisung")
        return super().do_GET()

    def do_POST(self):
        pfad = self.path.split("?")[0]
        if pfad not in ("/api/anfrage", "/api/warnung"):
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
        if pfad == "/api/warnung":
            email = text(d, "email", 150)
            if not EMAIL.match(email) or d.get("einwilligung") is not True:
                return self.antwort(400, {"ok": False, "fehler": "E-Mail und Einwilligung sind nötig"})
            t = secrets.token_urlsafe(24)
            jetzt = datetime.now().isoformat(timespec="seconds")
            schreiben(os.path.join(ABOS, "ausstehend", t + ".json"), {"email": email, "zeit": jetzt})
            schreiben(os.path.join(ABOS, "versand", t + ".json"), {"typ": "bestaetigung", "email": email, "token": t})
            return self.antwort(200, {"ok": True})
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
