"""Geräte über smarte Steckdosen schalten (zurzeit myStrom WiFi Switch): Solarladestation für Handy & Co.

Konfiguration in anlage.json → geraete: [{id, name, typ: "mystrom", mac, ip, ein_ab_w, aus_unter_w,
bei_minuspreis, max_w}]. Laufzeit-Zustand (Modus auto/ein/aus, Verbrauch pro Tag, Protokoll) in /data/geraete.json.

Regel im Modus «auto» nach SOLARÜBERSCHUSS (was ins Netz fliesst, plus der eigene Verbrauch des Geräts, damit es
sich nicht selbst wieder ausschaltet), 2-Minuten-Mittel: EIN ab ein_ab_w, AUS unter aus_unter_w; zusätzlich EIN bei
negativem Börsenpreis (bei_minuspreis). Zwischen zwei Schaltungen der Automatik mindestens SPERRE_S.
SICHERHEITSNETZ: homi schaltet nur über den Timer der Steckdose ein (/timer?mode=on&time=RUECKFALL_S) und erneuert ihn
jede Minute. Meldet sich homi nicht mehr, schaltet die Steckdose nach RUECKFALL_S selbst AUS (getestet 18.10.2026).
Ändert sich die IP (DHCP), sucht homi die Steckdose über ihre MAC-Adresse im Heimnetz.
"""
import json
import os
import threading
import time
import urllib.request
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .konfig import ANLAGE_CFG, load_json
from .zustand import lock, state

TAKT_S = 10
ERNEUERN_S = 60
RUECKFALL_S = 300                  # Steckdose schaltet selbst aus, wenn homi den Timer nicht erneuert
SPERRE_S = 300                     # mindestens 5 min zwischen zwei Schaltungen im Automatikbetrieb
MITTEL_S = 120                     # Überschuss über 2 min glätten (Wolken)
FEHLER_MAX = 3                     # erst nach 3 Fehlversuchen (30 s) als «nicht erreichbar» melden (WLAN wackelt)
NETZ = "192.168.1."
AKTIONEN_MAX = 100


def w(watt):
    """1850 → «1.9 kW», 200 → «200 W»"""
    return f"{watt / 1000:.1f} kW" if watt >= 1000 else f"{round(watt)} W"


def konfig():
    try:
        return load_json(ANLAGE_CFG).get("geraete") or []
    except (OSError, ValueError):
        return []


def http(url, timeout=3):
    req = urllib.request.Request(url, method="POST" if "/timer" in url else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        text = r.read().decode() or "{}"
    return json.loads(text) if text.strip().startswith("{") else {}


def mac_suchen(mac):
    """Heimnetz nach einer myStrom-Steckdose mit dieser MAC absuchen; IP oder None."""
    def probe(i):
        try:
            return f"{NETZ}{i}" if http(f"http://{NETZ}{i}/info", timeout=1.5).get("mac", "").upper() == mac.upper() else None
        except (OSError, ValueError):
            return None
    with ThreadPoolExecutor(64) as ex:
        return next((ip for ip in ex.map(probe, range(1, 255)) if ip), None)


class Geraete:
    def __init__(self, path):
        self.path = path
        try:
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("geraete", {})
        self.data.setdefault("aktionen", [])
        ids = {g["id"] for g in konfig()}
        for alt in [k for k in self.data["geraete"] if k not in ids]:   # aus anlage.json entfernte Geräte
            del self.data["geraete"][alt]
        self.live = {}                                           # id → letzte Messung der Steckdose

    def zustand(self, gid):
        return self.data["geraete"].setdefault(gid, {"modus": "auto", "tage": {}, "ip": None})

    def aktion(self, text, von=None):
        self.data["aktionen"] = (self.data["aktionen"] + [{"zeit": datetime.now().isoformat(timespec="seconds"),
                                                           "text": text, "von": von}])[-AKTIONEN_MAX:]

    def setzen(self, gid, modus, von):
        """Manuell: auto | ein | aus. Aufrufer hält den Lock."""
        g = next((g for g in konfig() if g["id"] == gid), None)
        if not g or modus not in ("auto", "ein", "aus"):
            raise ValueError
        z = self.zustand(gid)
        if z["modus"] != modus:
            z["modus"] = modus
            self.aktion(f"{g['name']}: Modus {modus.upper()}", von)
        self.save()

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)

    def regeln(self):
        intern = {}                                              # id → {"seit": letzte Schaltung, "erneuert": ts, "fehler": n}
        letzt_ts = None
        while True:
            jetzt = time.time()
            with lock:
                status = dict(state["status"] or {})
            b = status.get("boerse") or {}
            dt = jetzt - letzt_ts if letzt_ts else 0
            letzt_ts = jetzt
            for g in konfig():
                gid, i = g["id"], intern.setdefault(g["id"], {"seit": 0.0, "erneuert": 0.0, "fehler": 0,
                                                                   "werte": deque()})
                with lock:
                    z = self.zustand(gid)
                    modus, ip = z["modus"], z.get("ip") or g.get("ip")
                if i.get("modus") != modus:                      # Moduswechsel von Hand: sofort umsetzen, keine Sperre
                    i["modus"], i["seit"] = modus, 0.0
                try:
                    r = http(f"http://{ip}/report")
                    i["fehler"] = 0
                except (OSError, ValueError):
                    i["fehler"] += 1
                    if i["fehler"] >= FEHLER_MAX:
                        with lock:
                            self.live[gid] = {"erreichbar": False, "ip": ip}
                    if i["fehler"] in (3, 60) and g.get("mac"):          # nach 30 s und alle 10 min neu suchen
                        neu = mac_suchen(g["mac"])
                        if neu and neu != ip:
                            with lock:
                                z["ip"] = neu
                                self.aktion(f"{g['name']}: neue Adresse {neu}", "homi")
                    if i["fehler"] >= 60:
                        i["fehler"] = 3
                    continue
                an = bool(r.get("relay"))
                leistung = float(r.get("power") or 0)
                q = i["werte"]                                   # Überschuss ohne das Gerät selbst
                if status.get("fronius_ok"):
                    q.append((jetzt, -status["netz_w"] + (leistung if an else 0)))
                while q and q[0][0] < jetzt - MITTEL_S:
                    q.popleft()
                mittel = sum(x for _, x in q) / len(q) if q else 0.0
                grund = ""
                if modus == "ein":
                    soll, grund = True, "manuell ein"
                elif modus == "aus":
                    soll, grund = False, "manuell aus"
                elif g.get("bei_minuspreis") and b.get("negativ"):
                    soll, grund = True, f"Börsenpreis negativ ({b.get('rp_kwh')} Rp./kWh)"
                elif len(q) < 3:                                       # Neustart oder Fronius ohne Werte: nichts ändern
                    soll, grund = an, "misst den Überschuss …"
                elif mittel >= g["ein_ab_w"]:
                    soll, grund = True, f"Überschuss {w(mittel)} ≥ {w(g['ein_ab_w'])}"
                elif mittel < g["aus_unter_w"]:
                    soll, grund = False, f"Überschuss {w(max(mittel, 0))} < {w(g['aus_unter_w'])}"
                else:
                    soll, grund = an, f"Überschuss {w(mittel)} – bleibt wie es ist"
                gesperrt = modus == "auto" and soll != an and jetzt - i["seit"] < SPERRE_S
                try:
                    if soll and not gesperrt and (not an or jetzt - i["erneuert"] > ERNEUERN_S):
                        http(f"http://{ip}/timer?mode=on&time={RUECKFALL_S}")
                        i["erneuert"] = jetzt
                        if not an:
                            i["seit"] = jetzt
                            with lock:
                                self.aktion(f"{g['name']} EIN – {grund}", "Regel" if modus == "auto" else "manuell")
                    elif not soll and an and not gesperrt:
                        http(f"http://{ip}/relay?state=0")
                        i["seit"] = jetzt
                        with lock:
                            self.aktion(f"{g['name']} AUS – {grund}", "Regel" if modus == "auto" else "manuell")
                except (OSError, ValueError) as e:
                    print(f"Gerät {gid}: Schalten fehlgeschlagen:", e, flush=True)
                with lock:
                    if 0 < dt <= 60:                                     # Verbrauch und Solaranteil aufsummieren
                        tag = z["tage"].setdefault(datetime.now().date().isoformat(),
                                                   {"kwh": 0.0, "solar_kwh": 0.0, "laufzeit_h": 0.0})
                        kwh = leistung * dt / 3.6e6
                        bezug = max(status.get("netz_w") or 0, 0)        # was das Haus gerade aus dem Netz holt
                        anteil = max(0.0, leistung - bezug) / leistung if leistung > 0 else 0.0
                        tag["kwh"] += kwh
                        tag["solar_kwh"] += kwh * anteil
                        tag["laufzeit_h"] += dt / 3600 if an else 0
                        for alt in sorted(z["tage"])[:-400]:
                            del z["tage"][alt]
                    self.live[gid] = {"erreichbar": True, "ip": ip, "an": an, "leistung_w": round(leistung, 1),
                                      "temperatur_c": round(float(r.get("temperature") or 0), 1), "grund": grund,
                                      "ueberschuss_w": round(mittel),
                                      "gesperrt_s": round(SPERRE_S - (jetzt - i["seit"])) if gesperrt else 0,
                                      "zu_viel": leistung > g.get("max_w", 2300) * 0.95}
            if int(jetzt) // 60 != int(jetzt - TAKT_S) // 60:
                with lock:
                    try:
                        self.save()
                    except OSError as e:
                        print("Geräte nicht gespeichert:", e, flush=True)
            time.sleep(TAKT_S)

    def report(self):
        """Aufrufer hält den Lock."""
        heute = datetime.now().date().isoformat()
        aus = []
        for g in konfig():
            z = self.zustand(g["id"])
            tage = sorted(z["tage"].items())
            woche = tage[-7:]
            aus.append({
                "id": g["id"], "name": g["name"], "typ": g.get("typ"), "modus": z["modus"],
                "regel": f"Automatik: EIN, sobald mehr als {w(g['ein_ab_w'])} Solarüberschuss ins Netz fliessen "
                         f"(2-Minuten-Mittel), AUS unter {w(g['aus_unter_w'])}, mindestens {SPERRE_S // 60} Minuten zwischen zwei Schaltungen"
                         + (", zusätzlich EIN bei negativem Börsenpreis" if g.get("bei_minuspreis") else "")
                         + f". Sicherheitsnetz: Meldet sich homi nicht mehr, schaltet die Steckdose nach "
                           f"{RUECKFALL_S // 60} Minuten selbst aus.",
                "ein_ab_w": g["ein_ab_w"], "aus_unter_w": g["aus_unter_w"],
                "live": self.live.get(g["id"], {"erreichbar": None}),
                "heute": {k: round(v, 3) for k, v in z["tage"].get(heute, {"kwh": 0, "solar_kwh": 0, "laufzeit_h": 0}).items()},
                "woche": {k: round(sum(t[k] for _, t in woche), 3) for k in ("kwh", "solar_kwh", "laufzeit_h")},
            })
        return {"geraete": aus, "aktionen": self.data["aktionen"][-10:][::-1]}


def starten(path):
    g = Geraete(path)
    threading.Thread(target=g.regeln, daemon=True).start()
    return g
