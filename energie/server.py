"""Energie-Dienst für domus: Fronius-Livewerte + GWS-Tarif als JSON, dazu die Retro-Webseite.

Endpunkte:
  /api/status   aktuelle Leistung (PV, Verbrauch, Netz), Tarif, Kosten/Erlös pro Stunde
  /api/history  Minutenwerte der letzten 24 h (nur im Speicher, nach Neustart leer)
  /api/boerse   Day-Ahead-Börsenpreise Schweiz (heute/morgen) von Energy-Charts, in Rp/kWh via EZB-Kurs
  /api/speicher Simulation virtueller Batteriespeicher mit den echten Netzwerten (Stand in /data/speicher.json)
  /api/archiv   10-Minuten-Archiv (5 Jahre), ?von=JJJJ-MM-TT&bis=JJJJ-MM-TT&aufloesung=10min|stunde|tag|monat|jahr
  /api/prognose Solarprognose heute/morgen (Open-Meteo, geeicht mit den eigenen Messwerten), bestes Zeitfenster
  /api/einspeisung  Einspeise-Fahrplan heute/morgen bei dynamischer Vergütung (Börsenpreis): Minuspreis-Stunden,
                Empfehlung pro Stunde, optimierter Batterie-Fahrplan (virtuelle Batterie) vs. einfaches Laden
  /api/amortisation  bisher erwirtschaftet, Break-even (Investition/Förderung in anlage.json)
  /api/berater  Energieberater: drei Tipps mit Begründung und Rechenweg, Batterie-Empfehlung
  /api/info     Anzeigename und Leistung der Anlage
  /api/wochenbericht  letzte ganze Woche (Mo–So) mit Vorwochen-Vergleich, ?woche=2026-W41
  /api/push     GET: öffentlicher VAPID-Schlüssel; POST: App-Push an-/abmelden, Testnachricht
  /             statische Webseite: www/ (Retro) bzw. www-kunde/ für <name>.homi.solar

Der Code liegt im Paket homi/ (ein Modul pro Thema); diese Datei enthält nur den HTTP-Server und den Start.
"""

import hashlib
import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from homi import zustand
from homi.amortisation import amort_cache, amortisation
from homi.archiv import Archiv, archiv_aggregat
from homi.berater import berater, berater_cache
from homi.boerse import boerse_now, poll_boerse
from homi.einspeisung import einspeise_plan
from homi.erfassung import poll
from homi.konfig import ANLAGE_CFG, ARCHIV_DIR, ARCHIV_TAGE, KUNDE_HOST, PORT, PUSH_DIR, PUSH_HOSTS, PUSH_THEMEN, SPEICHER_FILE, WWW, WWW_KUNDE
from homi.prognose import poll_prognose
from homi.push_abo import push_lesen, push_schluessel, push_schreiben
from homi.speicher import SpeicherSimulation
from homi.wochenbericht import wochenbericht
from homi.zustand import history, lock, state


class Handler(SimpleHTTPRequestHandler):
    server_version = "domus"
    sys_version = ""
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".webmanifest": "application/manifest+json",
                      ".js": "text/javascript"}

    def end_headers(self):
        # Seite, Programm, Design, Manifest und Service Worker bei jedem Öffnen neu prüfen (sonst zeigen Browser und
        # installierte Apps tagelang eine alte Version); unverändert antwortet der Server knapp mit 304
        p = self.path.split("?")[0]
        if p == "/" or p.endswith((".html", ".js", ".css", ".webmanifest")):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def send_json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def oberflaeche(self):
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        self.directory = WWW_KUNDE if KUNDE_HOST.match(host) and os.path.isdir(WWW_KUNDE) else WWW

    def do_HEAD(self):
        self.oberflaeche()
        return super().do_HEAD()

    def do_GET(self):
        self.oberflaeche()
        path = self.path.split("?")[0]
        if path == "/api/info":
            try:
                with open(ANLAGE_CFG, encoding="utf-8") as f:
                    a = json.load(f)
            except (OSError, ValueError):
                a = {}
            return self.send_json({"titel": a.get("titel") or "Mein Zuhause", "kwp": a.get("max_kw")})
        if path == "/api/status":
            with lock:
                return self.send_json(state["status"] or {"fronius_ok": False, "startet": True})
        if path == "/api/boerse":
            with lock:
                b = state["boerse"]
                return self.send_json({**b, "aktuell": boerse_now(b, time.time())} if b else {"preise": []})
        if path == "/api/speicher":
            with lock:
                return self.send_json(zustand.speicher_sim.report())
        if path == "/api/wochenbericht":
            q = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            try:
                return self.send_json(wochenbericht(q.get("woche")))
            except ValueError as e:
                return self.send_json({"aktiv": False, "grund": str(e) or "ungültige Woche (Format 2026-W41)"})
            except (OSError, KeyError, ZeroDivisionError) as e:
                print("Wochenbericht:", e, flush=True)
                return self.send_json({"aktiv": False, "grund": "Bericht gerade nicht verfügbar"})
        if path == "/api/berater":
            try:
                return self.send_json(berater())
            except (OSError, ValueError, KeyError, ZeroDivisionError) as e:
                print("Berater:", e, flush=True)
                return self.send_json(berater_cache["daten"] or {"aktiv": False, "grund": "Berechnung gerade nicht möglich"})
        if path == "/api/amortisation":
            try:
                return self.send_json(amortisation())
            except (OSError, ValueError, KeyError) as e:      # Fronius nachts aus o. Ä.: letzte Berechnung zeigen
                print("Amortisation:", e, flush=True)
                return self.send_json(amort_cache["daten"] or {"aktiv": False, "fehler": "Zähler nicht erreichbar"})
        if path == "/api/einspeisung":
            return self.send_json(einspeise_plan())
        if path == "/api/prognose":
            with lock:
                return self.send_json(state["prognose"] or {"startet": True})
        if path == "/api/archiv":
            return self.send_archiv()
        if path == "/api/history":
            with lock:
                h = list(history)
            # Lücke seit dem letzten Neustart mit 10-Minuten-Werten aus dem Archiv füllen
            jetzt = datetime.now()
            ab = (jetzt - timedelta(hours=24)).isoformat(timespec="minutes")
            bis = h[0]["zeit"][:16] if h else jetzt.isoformat(timespec="minutes")
            alt = [{"zeit": r["zeit"], "dauer_min": 10,
                    "pv_w": round(r["pv_kwh"] * 3.6e6 / r["sekunden"]),
                    "verbrauch_w": round(r["verbrauch_kwh"] * 3.6e6 / r["sekunden"]),
                    "netz_w": round((r["bezug_kwh"] - r["einspeisung_kwh"]) * 3.6e6 / r["sekunden"]),
                    "kosten_chf_h": round(r["kosten_chf"] * 3600 / r["sekunden"], 4),
                    "erloes_chf_h": round(r["erloes_chf"] * 3600 / r["sekunden"], 4)}
                   for r in zustand.archiv.lesen((jetzt - timedelta(hours=24)).date(), jetzt.date())
                   if ab <= r["zeit"] < bis and r["sekunden"] and r["sekunden"] >= 60]
            return self.send_json(alt + h)
        if path == "/api/push":
            return self.send_json({"schluessel": push_schluessel(), "themen": PUSH_THEMEN})
        return super().do_GET()

    def do_POST(self):
        """App-Push: {"aktion": "anmelden"|"abmelden"|"lesen"|"test", "abo": PushSubscription, "themen": [...]}"""
        if self.path != "/api/push":
            return self.send_error(405)
        try:
            laenge = int(self.headers.get("Content-Length", "0"))
            if not 0 < laenge <= 4096:
                raise ValueError
            d = json.loads(self.rfile.read(laenge))
            abo, aktion = d["abo"], d["aktion"]
            endpoint = abo["endpoint"]
            teile = urlsplit(endpoint)
            if teile.scheme != "https" or not PUSH_HOSTS.match(teile.hostname or "") or len(endpoint) > 1024:
                raise ValueError
            datei = os.path.join(PUSH_DIR, "abos", hashlib.sha256(endpoint.encode()).hexdigest()[:32] + ".json")
            if aktion == "anmelden":
                keys = {k: str(abo["keys"][k]) for k in ("p256dh", "auth")}
                if not all(re.fullmatch(r"[A-Za-z0-9_-]{16,200}={0,2}", v) for v in keys.values()):
                    raise ValueError
                themen = [t for t in d.get("themen", PUSH_THEMEN) if t in PUSH_THEMEN]
                alt = push_lesen(datei)
                neu = {"endpoint": endpoint, "keys": keys, "themen": themen,
                       "geraet": str(self.headers.get("User-Agent", ""))[:200],
                       "seit": alt.get("seit") or datetime.now().isoformat(timespec="seconds")}
                push_schreiben(datei, neu)
                return self.send_json({"ok": True, "themen": themen})
            if aktion == "abmelden":
                if os.path.exists(datei):
                    os.remove(datei)
                return self.send_json({"ok": True})
            if aktion == "lesen":
                return self.send_json({"angemeldet": os.path.exists(datei), "themen": push_lesen(datei).get("themen", [])})
            if aktion == "test" and os.path.exists(datei):
                # push.py (Pfad-Unit homi-push-test) schickt sofort eine Testnachricht an genau dieses Gerät
                push_schreiben(os.path.join(PUSH_DIR, "test", os.path.basename(datei)), {"abo": os.path.basename(datei)})
                return self.send_json({"ok": True})
            raise ValueError
        except (KeyError, TypeError, ValueError, AttributeError):
            return self.send_error(400)
        except OSError as e:
            print("Push-Abo:", e, flush=True)
            return self.send_error(503)

    def send_archiv(self):
        q = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
        aufl = q.get("aufloesung", "tag")
        grenzen = {"10min": 31, "stunde": 92, "tag": 800, "monat": ARCHIV_TAGE + 31, "jahr": ARCHIV_TAGE + 31}
        try:
            bis = date.fromisoformat(q.get("bis", date.today().isoformat()))
            von = date.fromisoformat(q.get("von", (bis - timedelta(days=6)).isoformat()))
            if aufl not in grenzen or von > bis or (bis - von).days > grenzen[aufl]:
                raise ValueError
        except ValueError:
            self.send_response(400)
            self.end_headers()
            return
        rows = zustand.archiv.lesen(von, bis)
        erste = rows[0]["zeit"] if rows else None
        return self.send_json({"von": von.isoformat(), "bis": bis.isoformat(), "aufloesung": aufl,
                               "erste_daten": erste, **archiv_aggregat(rows, aufl)})

    def list_directory(self, path):
        self.send_error(404)

    def log_message(self, *args):
        pass

if __name__ == "__main__":
    zustand.speicher_sim = SpeicherSimulation(SPEICHER_FILE)
    zustand.archiv = Archiv(ARCHIV_DIR)
    threading.Thread(target=poll_boerse, daemon=True).start()
    threading.Thread(target=poll_prognose, daemon=True).start()
    threading.Thread(target=poll, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), partial(Handler, directory=WWW)).serve_forever()
