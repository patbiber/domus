"""Wechselrichter-Steuerung über Modbus TCP (SunSpec): Regel «Minuspreis → Nulleinspeisung» und Schalter Ein/Aus.

Modus in anlage.json → steuerung.modus:
  aus     nichts lesen, nichts schreiben
  test    nur nachrechnen, was die Regel getan hätte (nichts schreiben)
  scharf  homi schreibt auf den Wechselrichter (seit 16.10.2026 auf Patricks Wunsch)

Im Modus «scharf» prüft regeln() alle 10 s:
  - Schalter «aus» (manuell, App/Retro/HA): Standby (Conn = 0) mit Ablaufzeit, alle 60 s erneuert.
  - Regel an und Börsenpreis unter der Abregelschwelle des Vergütungsmodells (verguetung.py): Leistung auf den
    Hausverbrauch begrenzen (WMaxLimPct, Ena = 1) mit Ablaufzeit. Beim festen Tarif und beim Quartalsdurchschnitt
    lohnt sich Abregeln nie (Schwelle None); ab 2027 mit Börsenpreis erst unter −Ausgleich der Minimalvergütung.
  - sonst: Begrenzung aufheben, Standby beenden.
SICHERHEITSNETZ: Jeder Befehl hat eine Ablaufzeit (RUECKFALL_S). Meldet sich homi nicht mehr (Absturz, NUC aus,
Netzwerk weg), hebt der Fronius Begrenzung und Standby von selbst auf und produziert normal weiter. Getestet am
16.10.2026: Begrenzung 10 % wirkt sofort und fällt nach der Ablaufzeit weg; Standby wirkt sofort, nach dem
Wiedereinschalten speist der Fronius nach rund 2 Minuten wieder ein (Netz-Zuschaltbedingungen).
Die Rundsteuerung des Netzbetreibers (IO-Steuerung) hat am Fronius Vorrang vor Modbus.
Freigabe am Fronius: «Wechselrichter-Steuerung über Modbus» EIN, «Steuerung einschränken» auf 192.168.1.23 (NUC).

Die Abrechnung (tage/ereignisse) läuft in «test» und «scharf»: Minuspreis-Stunden, Einspeisung, die wegfällt
(scharf: geschätzt aus der Solarprognose, weil die Anlage dann ja gedrosselt ist), Kosten zum fixen Tarif
(entgangene Rückliefervergütung) und Nutzen bei Vergütung zum Börsenpreis.
"""
import json
import math
import os
import socket
import struct
import time
from datetime import date, datetime, timedelta

from .konfig import ANLAGE_CFG, FRONIUS, MAX_DT_S, load_json
from .verguetung import ausgleich_eff, konfig as verg_konfig, schwelle_jetzt
from . import zustand
from .zustand import lock, state

MODBUS_PORT = 502
MODBUS_UNIT = 1                    # Wechselrichter (Smart Meter wäre 200)
TAKT_S = 10                        # Prüfen und ggf. Schreiben
ERNEUERN_S = 60                    # aktive Befehle so oft erneuern
RUECKFALL_S = 300                  # Ablaufzeit jedes Befehls: danach gibt der Fronius von selbst frei
RESERVE_W = 50                     # Begrenzung etwas über dem Verbrauch, damit kein Netzbezug entsteht
TEST_TAGE = 14
EREIGNISSE_MAX = 50
AKTIONEN_MAX = 100

ZUSTAENDE = {1: "aus", 2: "schläft (Nacht)", 3: "startet", 4: "produziert", 5: "gedrosselt", 6: "fährt herunter",
             7: "Störung", 8: "Standby"}


class Modbus:
    """Minimaler Modbus-TCP-Client (Funktion 3 lesen, 16 schreiben) für SunSpec-Wechselrichter."""

    def __init__(self, host, port=MODBUS_PORT, unit=MODBUS_UNIT):
        self.host, self.port, self.unit, self.tid = host, port, unit, 0
        self.m = None                                            # {Modell-ID: Startadresse der Daten}

    def _senden(self, s, pdu):
        self.tid = (self.tid + 1) & 0xFFFF
        s.sendall(struct.pack(">HHHB", self.tid, 0, len(pdu) + 1, self.unit) + pdu)
        kopf = self._genau(s, 8)
        _, _, laenge, _, fc = struct.unpack(">HHHBB", kopf)
        rest = self._genau(s, laenge - 2)
        if fc & 0x80:
            raise IOError(f"Modbus-Fehler {rest[0]}")
        return rest

    @staticmethod
    def _genau(s, n):
        d = b""
        while len(d) < n:
            teil = s.recv(n - len(d))
            if not teil:
                raise ConnectionError("Verbindung geschlossen")
            d += teil
        return d

    def lesen(self, s, addr, anzahl):
        return self._senden(s, struct.pack(">BHH", 3, addr, anzahl))[1:]

    def schreiben(self, s, addr, werte):
        self._senden(s, struct.pack(">BHHB", 16, addr, len(werte), 2 * len(werte)) + struct.pack(f">{len(werte)}h", *werte))

    def modelle(self, s):
        if self.m:
            return self.m
        if self.lesen(s, 40000, 2) != b"SunS":
            raise IOError("kein SunSpec-Gerät")
        adr, gefunden = 40002, {}
        for _ in range(30):
            mid, laenge = struct.unpack(">HH", self.lesen(s, adr, 2))
            if mid == 0xFFFF:
                break
            gefunden[mid] = adr + 2
            adr += 2 + laenge
        if 113 not in gefunden or 123 not in gefunden:
            raise IOError("SunSpec-Modelle 113/123 fehlen (Datenausgabe auf «float» stellen)")
        self.m = gefunden
        return gefunden

    def stand(self, s):
        """Leistung, Zustand und Steuer-Register des Wechselrichters."""
        m = self.modelle(s)
        d = self.lesen(s, m[113], 48)                             # Wechselrichter, Gleitkomma
        st = struct.unpack(">H", d[92:94])[0]
        c = struct.unpack(">24h", self.lesen(s, m[123], 24))       # Immediate Controls
        nenn = None
        if 120 in m:
            w, sf = struct.unpack(">Hh", self.lesen(s, m[120] + 1, 2))
            nenn = w * 10 ** sf
        return {
            "verbunden": True,
            "leistung_w": round(struct.unpack(">f", d[40:44])[0]),
            "zustand": ZUSTAENDE.get(st, f"Code {st}"),
            "eingeschaltet": c[2] == 1,                          # Conn: 1 = am Netz, 0 = Standby
            "begrenzung_pct": round(c[3] * 10 ** c[21], 1),
            "begrenzung_aktiv": c[7] == 1,                       # WMaxLim_Ena
            "rueckfall_s": c[5],                                 # WMaxLimPct_RvrtTms
            "nennleistung_w": nenn,
            "gelesen": datetime.now().isoformat(timespec="seconds"),
        }

    def standby(self, s, an):
        """Conn_WinTms, Conn_RvrtTms, Conn: Standby mit Ablaufzeit bzw. wieder ans Netz."""
        self.schreiben(s, self.m[123], [0, RUECKFALL_S, 0] if an else [0, 0, 1])

    def begrenzen(self, s, pct):
        """WMaxLimPct (SF -2), WinTms, RvrtTms, RmpTms, WMaxLim_Ena; pct None = Begrenzung aufheben."""
        if pct is None:
            self.schreiben(s, self.m[123] + 7, [0])
        else:
            self.schreiben(s, self.m[123] + 3, [round(pct * 100), 0, RUECKFALL_S, 0, 1])


def konfig():
    try:
        s = load_json(ANLAGE_CFG).get("steuerung") or {}
    except (OSError, ValueError):
        s = {}
    return {"modus": s.get("modus", "aus"), "seit": s.get("seit"), "scharf_seit": s.get("scharf_seit")}


def soll_pct(verbrauch_w, nenn):
    """Begrenzung in % der Nennleistung, damit die Anlage nur den Hausverbrauch deckt."""
    return min(100.0, max(0.0, math.ceil((verbrauch_w + RESERVE_W) / nenn * 1000) / 10))


def protokoll(text, von=None):
    with lock:
        zustand.steuerung.aktion(text, von)


def regeln():
    """Hauptschleife: lesen, entscheiden, im Modus «scharf» schreiben."""
    mb = Modbus(FRONIUS)
    letzte = {"was": None, "zeit": 0.0, "pct": None}           # zuletzt geschriebener Befehl
    while True:
        try:
            with socket.create_connection((mb.host, mb.port), timeout=4) as s:
                st = mb.stand(s)
                with lock:
                    status, sw = state["status"] or {}, dict(zustand.steuerung.data["schalter"])
                modus, jetzt = konfig()["modus"], None
                if modus == "scharf":
                    jetzt, nenn = time.time(), st["nennleistung_w"] or 8200
                    b = status.get("boerse") or {}
                    alt = jetzt - letzte["zeit"] > ERNEUERN_S
                    if sw["wr"] == "aus":
                        if st["eingeschaltet"] or letzte["was"] != "aus" or alt:
                            mb.standby(s, True)
                            if letzte["was"] != "aus":
                                protokoll("Wechselrichter AUS (Standby)", sw.get("von"))
                            letzte.update(was="aus", zeit=jetzt, pct=None)
                    else:
                        if not st["eingeschaltet"]:
                            mb.standby(s, False)
                            protokoll("Wechselrichter wieder EIN", sw.get("von"))
                            letzte.update(was=None, zeit=jetzt, pct=None)
                        aktiv_schwelle = schwelle_jetzt()[0]
                        if sw["regel"] and aktiv_schwelle is not None and b.get("rp_kwh") is not None \
                                and b["rp_kwh"] < aktiv_schwelle and status.get("fronius_ok"):
                            pct = soll_pct(status["verbrauch_w"], nenn)
                            if not st["begrenzung_aktiv"] or alt or letzte["pct"] is None or abs(pct - letzte["pct"]) >= 2:
                                mb.begrenzen(s, pct)
                                if letzte["was"] != "drosseln":
                                    protokoll(f"Börsenpreis {b.get('rp_kwh')} Rp./kWh unter Schwelle "
                                              f"{aktiv_schwelle:g}: gedrosselt auf {pct:g} %", "Regel")
                                letzte.update(was="drosseln", zeit=jetzt, pct=pct)
                        elif st["begrenzung_aktiv"]:
                            mb.begrenzen(s, None)
                            protokoll("Begrenzung aufgehoben – volle Leistung", "Regel")
                            letzte.update(was=None, zeit=jetzt, pct=None)
                if jetzt and letzte["zeit"] == jetzt:              # nach dem Schreiben frisch lesen
                    st = mb.stand(s)
        except (OSError, struct.error) as e:                      # nachts/Neustart: als nicht verbunden melden
            mb.m = None
            st = {"verbunden": False, "fehler": str(e) or type(e).__name__,
                  "gelesen": datetime.now().isoformat(timespec="seconds")}
        with lock:
            state["modbus"] = st
        time.sleep(TAKT_S)


class Steuerung:
    """Schalter-Zustand (bleibt über Neustarts erhalten), Protokoll und Abrechnung der Regel."""

    def __init__(self, path):
        self.path = path
        self.last_ts = None
        try:
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {"start": datetime.now().isoformat(timespec="seconds"), "tage": {}, "ereignisse": []}
        self.data.setdefault("schalter", {"wr": "ein", "regel": True})
        self.data.setdefault("aktionen", [])
        self.offen = None                                        # laufendes Ereignis (Minuspreis-Zeitraum)

    def schalten(self, wr=None, regel=None, von=None):
        """Manueller Schalter (App, Retro, HA). Aufrufer hält den Lock."""
        sw = self.data["schalter"]
        if wr in ("ein", "aus"):
            sw["wr"] = wr
        if isinstance(regel, bool) and regel != sw["regel"]:
            sw["regel"] = regel
            self.aktion(f"Regel «Minuspreis → Nulleinspeisung» {'EIN' if regel else 'AUS'}", von)
        sw["geaendert"], sw["von"] = datetime.now().isoformat(timespec="seconds"), von
        self.save()

    def aktion(self, text, von=None):
        self.data["aktionen"] = (self.data["aktionen"] + [{"zeit": datetime.now().isoformat(timespec="seconds"),
                                                           "text": text, "von": von}])[-AKTIONEN_MAX:]

    def step(self, status, now):
        ts = now.timestamp()
        dt = ts - self.last_ts if self.last_ts else None
        self.last_ts = ts
        b, t, modus = status.get("boerse"), status.get("tarif"), konfig()["modus"]
        if modus not in ("test", "scharf") or not status.get("fronius_ok") or not b or not t \
                or dt is None or dt <= 0 or dt > MAX_DT_S:
            return
        _, schwelle, ausgleich = schwelle_jetzt()                # gerechnet wird immer mit dem Modell ab 2027
        if b.get("rp_kwh") is None or b["rp_kwh"] >= schwelle or not self.data["schalter"]["regel"]:
            self.offen = None
            return
        h = dt / 3600
        if (state.get("modbus") or {}).get("begrenzung_aktiv"):  # gedrosselt: was wäre ohne Drosselung eingespeist worden?
            moeglich_w = max(status["pv_w"], prognose_w(now))
            kwh = max(0.0, moeglich_w - status["pv_w"]) * h / 1000 + status["einspeisung_w"] * h / 1000
        else:
            kwh = status["einspeisung_w"] * h / 1000             # fiele bei Nulleinspeisung weg
        verlust = kwh * (t["rueckliefer_chf_kwh"] or 0)          # feste Vergütung: entgangen
        p = b["rp_kwh"]                                          # ab 2027: Spotpreis + Ausgleich; < 0 = Einspeisen kostet
        boerse = kwh * (p + ausgleich_eff(p, ausgleich, verg_konfig())) / 100
        nenn = (state.get("modbus") or {}).get("nennleistung_w") or 8200
        pct = soll_pct(status["verbrauch_w"], nenn)
        d = self.data["tage"].setdefault(now.date().isoformat(),
                                         {"minuspreis_h": 0.0, "abregeln_kwh": 0.0, "verlust_fix_chf": 0.0,
                                          "boerse_chf": 0.0})
        d["minuspreis_h"] += h
        d["abregeln_kwh"] += kwh
        d["verlust_fix_chf"] += verlust
        d["boerse_chf"] += boerse
        if self.offen is None:
            self.offen = {"von": now.isoformat(timespec="minutes"), "kwh": 0.0, "verlust_fix_chf": 0.0,
                          "boerse_chf": 0.0, "min_rp": b["rp_kwh"], "min_pct": pct, "modus": modus}
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
        scharf = k["modus"] == "scharf"
        sw = self.data["schalter"]
        summe = {f: round(sum(d[f] for d in self.data["tage"].values()), 4)
                 for f in ("minuspreis_h", "abregeln_kwh", "verlust_fix_chf", "boerse_chf")}
        status, b = status or {}, (status or {}).get("boerse")
        aktiv, schwelle_2027, ausgleich = schwelle_jetzt()
        vk = verg_konfig()
        if sw["wr"] == "aus":
            jetzt = {"wuerde": "AUS (Standby)", "grund": f"manuell ausgeschaltet ({sw.get('von') or 'unbekannt'})"}
        elif not sw["regel"]:
            jetzt = {"wuerde": "volle Leistung", "grund": "Regel ausgeschaltet"}
        elif not status.get("fronius_ok"):
            jetzt = {"wuerde": "nichts tun", "grund": "Wechselrichter liefert gerade keine Werte"}
        elif b and b.get("rp_kwh") is not None and aktiv is not None and b["rp_kwh"] < aktiv:
            pct = soll_pct(status["verbrauch_w"], (modbus or {}).get("nennleistung_w") or 8200)
            jetzt = {"wuerde": f"{'drosselt' if scharf else 'würde drosseln'} auf {pct:g} %", "grund":
                     f"Börsenpreis {b['rp_kwh']:.1f} Rp./kWh unter der Schwelle von {aktiv:g} Rp. – Leistung auf den "
                     f"Hausverbrauch ({status['verbrauch_w']} W) begrenzen"}
        elif aktiv is None:
            jetzt = {"wuerde": "volle Leistung", "grund": f"Mit deinem Vergütungsmodell lohnt sich Abregeln nie "
                     f"(ab 2027 mit Börsenpreis: unter {schwelle_2027:g} Rp./kWh)"}
        else:
            jetzt = {"wuerde": "volle Leistung" if scharf else "nichts tun",
                     "grund": f"Börsenpreis über der Schwelle von {aktiv:g} Rp./kWh"}
        return {
            "modus": k["modus"],
            "scharf_seit": k["scharf_seit"],
            "schalter": sw,
            "verguetung": {"modell": vk["modell"], "schwelle_rp": aktiv, "schwelle_2027_rp": schwelle_2027,
                           "ausgleich_rp": ausgleich, "minimal_rp": vk["minimal_rp"]},
            "regel": "Fällt der Börsenpreis unter die Abregelschwelle, begrenzt homi die Leistung auf den Hausverbrauch "
                     "(Nulleinspeisung). Die Schwelle hängt vom Vergütungsmodell ab: beim festen Tarif lohnt sich "
                     "Abregeln nie, ab 2027 mit Börsenpreis erst unter minus dem Ausgleich der Minimalvergütung. "
                     f"Jeder Befehl hat eine Ablaufzeit von {RUECKFALL_S // 60} Minuten: meldet sich homi nicht mehr, "
                     "hebt der Wechselrichter Begrenzung und Standby selbst auf. Die Rundsteuerung des "
                     "Netzbetreibers hat immer Vorrang.",
            "seit": seit,
            "auswertung_am": (date.fromisoformat(seit) + timedelta(days=TEST_TAGE)).isoformat(),
            "modbus": modbus or {"verbunden": False, "fehler": "noch nicht gelesen"},
            "jetzt": jetzt,
            "summe": summe,
            "tage": [{"tag": t, **{f: round(v, 4) for f, v in d.items()}}
                     for t, d in sorted(self.data["tage"].items())[-TEST_TAGE:]],
            "ereignisse": [{f: (round(v, 4) if isinstance(v, float) else v) for f, v in e.items()}
                           for e in self.data["ereignisse"][-10:]][::-1],
            "aktionen": self.data["aktionen"][-10:][::-1],
        }


def prognose_w(now):
    """Erwartete Solarleistung dieser Stunde (Prognose, kWh pro Stunde = mittlere kW)."""
    p = state.get("prognose") or {}
    stunde = now.strftime("%Y-%m-%dT%H:00")
    return next((x["kwh"] * 1000 for x in p.get("stunden", []) if x["zeit"] == stunde), 0.0)
