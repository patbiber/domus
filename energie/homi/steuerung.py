"""Wechselrichter-Steuerung über Modbus TCP (SunSpec) – zurzeit TESTMODUS.

homi liest den Fronius über Modbus (Leistung, Zustand, Leistungsbegrenzung) und rechnet bei jedem Messschritt nach,
was die Regel «bei negativem Börsenpreis auf Nulleinspeisung drosseln» getan hätte: welche Begrenzung (WMaxLimPct,
Prozent der Nennleistung = aktueller Hausverbrauch), wie viel Einspeisung dabei weggefallen wäre und was das
- bei der festen Vergütung des Versorgers (entgangene Rückliefervergütung) und
- bei dynamischer Vergütung zum Börsenpreis (vermiedene Kosten der Einspeisung)
ausgemacht hätte. Im Testmodus wird NICHTS auf den Wechselrichter geschrieben; scharf schalten nur auf Patricks Wunsch.
Freigabe am Fronius: «Wechselrichter-Steuerung über Modbus» EIN, «Steuerung einschränken» auf 192.168.1.23 (NUC).
"""
import json
import os
import socket
import struct
import time
from datetime import date, datetime, timedelta

from .konfig import ANLAGE_CFG, FRONIUS, MAX_DT_S, load_json
from .zustand import lock, state

MODBUS_PORT = 502
MODBUS_UNIT = 1                    # Wechselrichter (Smart Meter wäre 200)
MODBUS_POLL_S = 30
TEST_TAGE = 14
EREIGNISSE_MAX = 50

ZUSTAENDE = {1: "aus", 2: "schläft (Nacht)", 3: "startet", 4: "produziert", 5: "gedrosselt", 6: "fährt herunter",
             7: "Störung", 8: "Standby"}


class Modbus:
    """Minimaler Modbus-TCP-Client (nur Lesen, Funktion 3)."""

    def __init__(self, host, port=MODBUS_PORT, unit=MODBUS_UNIT):
        self.host, self.port, self.unit, self.tid = host, port, unit, 0

    def lesen(self, s, addr, anzahl):
        self.tid = (self.tid + 1) & 0xFFFF
        s.sendall(struct.pack(">HHHBBHH", self.tid, 0, 6, self.unit, 3, addr, anzahl))
        kopf = b""
        while len(kopf) < 9:
            teil = s.recv(9 - len(kopf))
            if not teil:
                raise ConnectionError("Verbindung geschlossen")
            kopf += teil
        _, _, _, _, fc, n = struct.unpack(">HHHBBB", kopf)
        if fc & 0x80:
            raise IOError(f"Modbus-Fehler {n} bei Register {addr}")
        daten = b""
        while len(daten) < n:
            teil = s.recv(n - len(daten))
            if not teil:
                raise ConnectionError("Verbindung geschlossen")
            daten += teil
        return daten

    def modelle(self, s):
        """SunSpec-Modelle ab 40000 durchgehen: {Modell-ID: Startadresse der Daten}."""
        if self.lesen(s, 40000, 2) != b"SunS":
            raise IOError("kein SunSpec-Gerät")
        adr, gefunden = 40002, {}
        for _ in range(30):
            mid, laenge = struct.unpack(">HH", self.lesen(s, adr, 2))
            if mid == 0xFFFF:
                break
            gefunden[mid] = adr + 2
            adr += 2 + laenge
        return gefunden

    def stand(self):
        """Leistung, Zustand und Leistungsbegrenzung des Wechselrichters."""
        with socket.create_connection((self.host, self.port), timeout=4) as s:
            m = self.modelle(s)
            if 113 not in m or 123 not in m:
                raise IOError("SunSpec-Modelle 113/123 fehlen (Datenausgabe auf «float» stellen)")
            d = self.lesen(s, m[113], 48)                         # Wechselrichter, Gleitkomma
            f = lambda o: struct.unpack(">f", d[o * 2:o * 2 + 4])[0]
            st = struct.unpack(">H", d[92:94])[0]
            c = struct.unpack(">24h", self.lesen(s, m[123], 24))   # Immediate Controls
            nenn = None
            if 120 in m:
                w, sf = struct.unpack(">Hh", self.lesen(s, m[120] + 1, 2))
                nenn = w * 10 ** sf
        skal = 10 ** c[21]
        return {
            "verbunden": True,
            "leistung_w": round(f(20)),
            "zustand": ZUSTAENDE.get(st, f"Code {st}"),
            "eingeschaltet": c[2] == 1,                          # Conn: 1 = verbunden, 0 = Standby
            "begrenzung_pct": round(c[3] * skal, 1),
            "begrenzung_aktiv": c[7] == 1,                       # WMaxLim_Ena
            "rueckfall_s": c[5],                                 # WMaxLimPct_RvrtTms
            "nennleistung_w": nenn,
            "gelesen": datetime.now().isoformat(timespec="seconds"),
        }


def poll_modbus():
    mb = Modbus(FRONIUS)
    while True:
        try:
            st = mb.stand()
        except (OSError, struct.error) as e:                     # nachts/Neustart: als nicht verbunden melden
            st = {"verbunden": False, "fehler": str(e) or type(e).__name__,
                  "gelesen": datetime.now().isoformat(timespec="seconds")}
        with lock:
            state["modbus"] = st
        time.sleep(MODBUS_POLL_S)


def konfig():
    try:
        s = load_json(ANLAGE_CFG).get("steuerung") or {}
    except (OSError, ValueError):
        s = {}
    return {"modus": s.get("modus", "aus"), "seit": s.get("seit")}


class SteuerungTest:
    """Rechnet die Regel «Minuspreis → Nulleinspeisung» mit den echten Messwerten nach, ohne zu schreiben."""

    def __init__(self, path):
        self.path = path
        self.last_ts = None
        try:
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {"start": datetime.now().isoformat(timespec="seconds"), "tage": {}, "ereignisse": []}
        self.offen = None                                        # laufendes Ereignis (Minuspreis-Zeitraum)

    def step(self, status, now):
        ts = now.timestamp()
        dt = ts - self.last_ts if self.last_ts else None
        self.last_ts = ts
        b, t = status.get("boerse"), status.get("tarif")
        if konfig()["modus"] != "test" or not status.get("fronius_ok") or not b or not t \
                or dt is None or dt <= 0 or dt > MAX_DT_S:
            return
        if not b.get("negativ"):
            self.offen = None
            return
        h = dt / 3600
        kwh = status["einspeisung_w"] * h / 1000                 # fiele bei Nulleinspeisung weg
        verlust = kwh * (t["rueckliefer_chf_kwh"] or 0)          # feste Vergütung: entgangen
        boerse = kwh * b["rp_kwh"] / 100                         # dynamisch: negativ = Einspeisen kostet
        nenn = (state.get("modbus") or {}).get("nennleistung_w") or 8200
        pct = min(100.0, round(status["verbrauch_w"] / nenn * 100, 1))
        d = self.data["tage"].setdefault(now.date().isoformat(),
                                         {"minuspreis_h": 0.0, "abregeln_kwh": 0.0, "verlust_fix_chf": 0.0,
                                          "boerse_chf": 0.0})
        d["minuspreis_h"] += h
        d["abregeln_kwh"] += kwh
        d["verlust_fix_chf"] += verlust
        d["boerse_chf"] += boerse
        if self.offen is None:
            self.offen = {"von": now.isoformat(timespec="minutes"), "kwh": 0.0, "verlust_fix_chf": 0.0,
                          "boerse_chf": 0.0, "min_rp": b["rp_kwh"], "min_pct": pct}
            self.data["ereignisse"] = (self.data["ereignisse"] + [self.offen])[-EREIGNISSE_MAX:]
        e = self.offen
        e["bis"] = now.isoformat(timespec="minutes")
        e["kwh"] += kwh
        e["verlust_fix_chf"] += verlust
        e["boerse_chf"] += boerse
        e["min_rp"] = min(e["min_rp"], b["rp_kwh"])
        e["min_pct"] = min(e["min_pct"], pct)

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)

    def report(self, status, modbus):
        k = konfig()
        seit = k["seit"] or self.data["start"][:10]
        summe = {f: round(sum(d[f] for d in self.data["tage"].values()), 4)
                 for f in ("minuspreis_h", "abregeln_kwh", "verlust_fix_chf", "boerse_chf")}
        jetzt = {"wuerde": "nichts tun", "grund": "Börsenpreis nicht negativ"}
        b = (status or {}).get("boerse")
        if not (status or {}).get("fronius_ok"):
            jetzt = {"wuerde": "nichts tun", "grund": "Wechselrichter liefert gerade keine Werte"}
        elif b and b.get("negativ"):
            nenn = (modbus or {}).get("nennleistung_w") or 8200
            pct = min(100.0, round(status["verbrauch_w"] / nenn * 100, 1))
            jetzt = {"wuerde": f"auf {pct:g} % drosseln", "grund":
                     f"Börsenpreis {b['rp_kwh']:.1f} Rp./kWh – Leistung auf den Hausverbrauch "
                     f"({status['verbrauch_w']} W) begrenzen, Einspeisung {status['einspeisung_w']} W fiele weg"}
        return {
            "modus": k["modus"],
            "regel": "Bei negativem Börsenpreis die Leistung auf den Hausverbrauch begrenzen (Nulleinspeisung). "
                     "Jede Begrenzung hat eine Ablaufzeit: meldet sich homi nicht mehr, hebt der Wechselrichter sie "
                     "selbst auf. Die Rundsteuerung des Netzbetreibers hat immer Vorrang.",
            "seit": seit,
            "auswertung_am": (date.fromisoformat(seit) + timedelta(days=TEST_TAGE)).isoformat(),
            "modbus": modbus or {"verbunden": False, "fehler": "noch nicht gelesen"},
            "jetzt": jetzt,
            "summe": summe,
            "tage": [{"tag": t, **{f: round(v, 4) for f, v in d.items()}}
                     for t, d in sorted(self.data["tage"].items())[-TEST_TAGE:]],
            "ereignisse": [{f: (round(v, 4) if isinstance(v, float) else v) for f, v in e.items()}
                           for e in self.data["ereignisse"][-10:]][::-1],
        }
