"""Infrarotheizung (Tuya, lokal über homi/tuya.py): Zeitplan, Solarüberschuss/Minuspreis und Kuschel-Knopf.

Konfiguration in anlage.json → heizung: {name, dev_id, ip, mac, w, plan: [["05:15", 18], ["06:10", null], …],
kuscheln_c, kuscheln_h, solar: {temp_c, ein_ab_w, aus_unter_w, bei_minuspreis}}. Local Key in .env (TUYA_HEIZUNG_KEY).
Laufzeit-Zustand (Kuscheln bis, Protokoll) in /data/heizung.json.

Plan (Patrick, 05.11.2026): 05:15 18 °C, 06:10 aus, 17:00 18 °C, 21:00 15 °C. null = aus.
Vorrang: Kuscheln (22 °C für kuscheln_h Stunden) > Solarwärme > Plan.
Solarwärme: Fliesst mehr als ein_ab_w Überschuss ins Netz (2-Minuten-Mittel, die Heizung selbst herausgerechnet) oder
ist der Börsenpreis negativ, heizt homi auf solar.temp_c, sofern der Plan nicht schon mehr verlangt; aus unter
aus_unter_w. Zwischen zwei Wechseln mindestens SPERRE_S.
homi schreibt nur, wenn sich die Vorgabe ändert (und beim Start). Wer die Heizung von Hand oder in Smart Life
verstellt, behält das bis zum nächsten Wechsel. Fällt homi aus, bleibt die Heizung bei der letzten Einstellung.
Datenpunkte: 1 an/aus, 2 Soll (°C × 10), 3 Ist (°C × 10), 4 Modus («manual»).
Verbrauch: Die Heizung meldet keine Leistung. homi nimmt an, dass sie heizt, wenn sie an ist und der Raum unter der
Soll-Temperatur liegt (eingebauter Thermostat), und rechnet dann mit ihrer Nennleistung (w, am Zähler gemessen 650 W).
Daraus kWh und Solaranteil pro Tag (wie bei den Steckdosen) – eine Schätzung, keine Messung.
"""
import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timedelta

from .konfig import ANLAGE_CFG, load_json
from .tuya import TuyaGeraet
from .zustand import lock, state

TAKT_S = 30
SPERRE_S = 600                     # Solarwärme höchstens alle 10 min ein-/ausschalten (Wolken)
MITTEL_S = 120
FEHLER_MAX = 3
AKTIONEN_MAX = 100
ARP = "/proc/net/arp"
# Versuch (05.11.2026): Ist Datenpunkt 105 («worktime», laut Tuya-Modell Einheit «Stunden (vorläufig)») die Heizzeit?
# Dann könnte homi den Verbrauch messen statt schätzen. Bis zur Auswertung jede Abfrage mitschreiben.
BETRIEB_LOG = os.environ.get("HEIZUNG_BETRIEB_LOG", "/data/heizung-betrieb.csv")
BETRIEB_MAX = 5_000_000


def konfig():
    try:
        h = load_json(ANLAGE_CFG).get("heizung")
    except (OSError, ValueError):
        return None
    return h if h and h.get("dev_id") else None


def _min(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def plan_jetzt(plan, jetzt):
    """(Soll °C oder None, Beginn «HH:MM», nächster Wechsel (datetime, Soll)) für diesen Zeitpunkt."""
    p = sorted(plan, key=lambda x: _min(x[0]))
    m = jetzt.hour * 60 + jetzt.minute
    i = max((k for k, x in enumerate(p) if _min(x[0]) <= m), default=len(p) - 1)   # vor dem ersten: letzter vom Vortag
    n = p[(i + 1) % len(p)]
    naechst = jetzt.replace(hour=_min(n[0]) // 60, minute=_min(n[0]) % 60, second=0, microsecond=0)
    if naechst <= jetzt:
        naechst += timedelta(days=1)
    return p[i][1], p[i][0], (naechst, n[1])


def mac_ip(mac):
    """IP zu einer MAC aus der ARP-Tabelle des NUC (Host-Netz) – falls die Heizung per DHCP eine neue Adresse bekommt."""
    try:
        with open(ARP) as f:
            for z in f.read().splitlines()[1:]:
                t = z.split()
                if len(t) >= 4 and t[3].lower() == mac.lower():
                    return t[0]
    except OSError:
        pass
    return None


def text_soll(an, c):
    return f"{c:g} °C" if an else "aus"


class Heizung:
    def __init__(self, path):
        self.path = path
        try:
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}
        self.data.setdefault("aktionen", [])
        self.data.setdefault("kuscheln_bis", None)
        self.data.setdefault("tage", {})
        self.live = {"erreichbar": None}
        self.vorgabe = None                                       # (an, °C, Grund) – was homi gerade will
        self.solar = False
        self.wechsel = 0.0                                        # letzter Wechsel der Solarwärme
        self.wecken = threading.Event()                           # Kuschel-Knopf: sofort umsetzen, nicht erst im Takt

    def aktion(self, text, von=None):
        self.data["aktionen"] = (self.data["aktionen"] + [{"zeit": datetime.now().isoformat(timespec="seconds"),
                                                           "text": text, "von": von}])[-AKTIONEN_MAX:]

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)

    def kuscheln(self, an, von):
        """Kuschel-Knopf: an = kuscheln_c für kuscheln_h Stunden, aus = zurück zum Plan. Aufrufer hält den Lock."""
        h = konfig()
        if not h:
            raise ValueError
        if an:
            bis = datetime.now() + timedelta(hours=h.get("kuscheln_h", 2))
            self.data["kuscheln_bis"] = bis.isoformat(timespec="minutes")
            self.aktion(f"Kuscheln: {h.get('kuscheln_c', 22)} °C bis {bis:%H:%M}", von)
        elif self.data["kuscheln_bis"]:
            self.data["kuscheln_bis"] = None
            self.aktion("Kuscheln beendet", von)
        self.save()
        self.wecken.set()

    def soll(self, h, jetzt, ueberschuss, negativ):
        """(an, °C, Grund) nach Vorrang Kuscheln > Solarwärme > Plan."""
        kb = self.data.get("kuscheln_bis")
        if kb and datetime.fromisoformat(kb) > jetzt:
            return True, h.get("kuscheln_c", 22), f"Kuscheln bis {kb[11:16]}"
        if kb:
            with lock:
                self.data["kuscheln_bis"] = None
                self.aktion("Kuscheln abgelaufen", "homi")
        c, ab, _ = plan_jetzt(h["plan"], jetzt)
        s = h.get("solar") or {}
        if s:
            t = time.time()
            if negativ and s.get("bei_minuspreis", True):
                will, grund = True, "Börsenpreis negativ"
            elif ueberschuss is None:
                will, grund = self.solar, ""
            elif ueberschuss >= s.get("ein_ab_w", 800) or (self.solar and ueberschuss >= s.get("aus_unter_w", 200)):
                will, grund = True, f"Solarüberschuss {round(ueberschuss)} W"
            else:
                will, grund = False, ""
            if will != self.solar and (negativ or t - self.wechsel >= SPERRE_S):
                self.solar, self.wechsel = will, t
            if self.solar and (c is None or c < s.get("temp_c", 20)):
                return True, s.get("temp_c", 20), "Solarwärme: " + (grund or "Überschuss")
        return c is not None, c if c is not None else 0, f"Plan ab {ab}"

    def regeln(self):
        werte, fehler, geschrieben, letzt_ts = deque(), 0, None, None
        while True:
            h = konfig()
            key = os.environ.get("TUYA_HEIZUNG_KEY", "")
            if not h or not key:
                time.sleep(60)
                continue
            jetzt_ts = time.time()
            with lock:
                status = dict(state["status"] or {})
                ip = self.data.get("ip") or h["ip"]
            g = TuyaGeraet(h["dev_id"], ip, key)
            dps = None
            for _ in range(2):                                    # Tuya-Geräte trennen ab und zu kurz die Verbindung
                try:
                    dps = g.status()
                    break
                except (OSError, ValueError):
                    time.sleep(2)
            if dps is None:
                fehler += 1
                if fehler >= FEHLER_MAX:
                    with lock:
                        self.live = {"erreichbar": False, "ip": ip}
                    neu = h.get("mac") and mac_ip(h["mac"])
                    if neu and neu != ip:
                        with lock:
                            self.data["ip"] = neu
                            self.aktion(f"{h['name']}: neue Adresse {neu}", "homi")
                self.wecken.wait(TAKT_S)
                self.wecken.clear()
                continue
            fehler = 0
            an, soll_c, ist_c = bool(dps.get("1")), (dps.get("2") or 0) / 10, (dps.get("3") or 0) / 10
            heizt = an and ist_c < soll_c
            try:
                if not os.path.exists(BETRIEB_LOG) or os.path.getsize(BETRIEB_LOG) < BETRIEB_MAX:
                    neu_datei = not os.path.exists(BETRIEB_LOG)
                    with open(BETRIEB_LOG, "a", encoding="utf-8") as f:
                        if neu_datei:
                            f.write("zeit,worktime,an,soll_c,ist_c,heizt_schaetzung,verbrauch_w\n")
                        f.write(f"{datetime.now().isoformat(timespec='seconds')},{dps.get('105')},{int(an)},{soll_c},"
                                f"{ist_c},{int(heizt)},{status.get('verbrauch_w') if status.get('fronius_ok') else ''}\n")
            except OSError as e:
                print("Heizung: Betriebszeit nicht protokolliert:", e, flush=True)
            # Überschuss ohne die Heizung selbst (läuft sie wegen Solarwärme, zählt ihre Leistung dazu)
            if status.get("fronius_ok"):
                werte.append((jetzt_ts, -status["netz_w"] + (h.get("w", 650) if heizt else 0)))
            while werte and werte[0][0] < jetzt_ts - MITTEL_S:
                werte.popleft()
            ueberschuss = sum(x for _, x in werte) / len(werte) if len(werte) >= 3 else None
            negativ = bool((status.get("boerse") or {}).get("negativ"))
            jetzt = datetime.now()
            v_an, v_c, grund = self.soll(h, jetzt, ueberschuss, negativ)
            ziel = (v_an, v_c)
            if ziel != geschrieben:
                neu = {"1": v_an}
                if v_an:
                    neu["2"] = round(v_c * 10)
                    if dps.get("4") not in (None, "manual"):
                        neu["4"] = "manual"
                # nur senden, was sich ändert: die Heizung piepst bei jedem Befehl (Piepser lässt sich nicht abschalten)
                neu = {k: x for k, x in neu.items() if dps.get(k) != x}
                try:
                    if neu:
                        g.setzen(neu)
                    geschrieben = ziel
                    an, soll_c = v_an, v_c if v_an else soll_c
                    if neu:
                        with lock:
                            self.aktion(f"{h['name']}: {text_soll(v_an, v_c)} – {grund}", "homi")
                            self.save()
                except (OSError, ValueError) as e:
                    print("Heizung: Schalten fehlgeschlagen:", e, flush=True)
            heizt = an and ist_c < soll_c                        # nach einem Schaltbefehl neu beurteilen
            leistung = h.get("w", 650) if heizt else 0
            dt = jetzt_ts - letzt_ts if letzt_ts else 0
            letzt_ts = jetzt_ts
            with lock:
                if 0 < dt <= 120:                                 # kWh und Solaranteil aufsummieren (geschätzt)
                    tag = self.data["tage"].setdefault(jetzt.date().isoformat(), {"kwh": 0.0, "solar_kwh": 0.0, "heiz_h": 0.0})
                    kwh = leistung * dt / 3.6e6
                    bezug = max(status.get("netz_w") or 0, 0) if status.get("fronius_ok") else leistung
                    tag["kwh"] += kwh
                    tag["solar_kwh"] += kwh * (max(0.0, leistung - bezug) / leistung if leistung else 0)
                    tag["heiz_h"] += dt / 3600 if heizt else 0
                    for alt in sorted(self.data["tage"])[:-400]:
                        del self.data["tage"][alt]
                    if int(jetzt_ts) // 600 != int(jetzt_ts - dt) // 600:   # alle 10 Minuten sichern
                        try:
                            self.save()
                        except OSError as e:
                            print("Heizung nicht gespeichert:", e, flush=True)
                self.vorgabe = (v_an, v_c, grund)
                self.live = {"erreichbar": True, "ip": ip, "an": an, "soll_c": soll_c,
                             "ist_c": ist_c, "modus": dps.get("4"), "heizt": heizt, "leistung_w": leistung,
                             "ueberschuss_w": None if ueberschuss is None else round(ueberschuss),
                             "zeit": jetzt.isoformat(timespec="seconds")}
            self.wecken.wait(TAKT_S)
            self.wecken.clear()

    def report(self):
        """Aufrufer hält den Lock."""
        h = konfig()
        if not h:
            return {"aktiv": False}
        jetzt = datetime.now()
        _, ab, (n_zeit, n_c) = plan_jetzt(h["plan"], jetzt)
        kb = self.data.get("kuscheln_bis")
        s = h.get("solar") or {}
        plan = [{"ab": z, "soll_c": c} for z, c in sorted(h["plan"], key=lambda x: _min(x[0]))]
        regel = ("Zeitplan: " + ", ".join(f"{p['ab']} {text_soll(p['soll_c'] is not None, p['soll_c'] or 0)}" for p in plan)
                 + (f". Solarwärme: Fliessen mehr als {s.get('ein_ab_w', 800)} W Überschuss ins Netz (2-Minuten-Mittel) "
                    f"oder ist der Börsenpreis negativ, heizt homi auf {s.get('temp_c', 20)} °C, wenn der Plan weniger "
                    f"vorsieht; aus unter {s.get('aus_unter_w', 200)} W" if s else "")
                 + f". Kuscheln: {h.get('kuscheln_c', 22)} °C für {h.get('kuscheln_h', 2):g} Stunden, dann wieder nach Plan. "
                   "homi stellt die Heizung nur bei einem Wechsel um – von Hand Verstelltes bleibt bis zum nächsten Wechsel. "
                   f"Verbrauch geschätzt: Die Heizung meldet keine Leistung; liegt der Raum unter der Soll-Temperatur, "
                   f"rechnet homi mit {h.get('w', 650)} W.")
        v = self.vorgabe
        tage = sorted(self.data["tage"].items())
        rund = lambda t: {k: round(x, 3) for k, x in t.items()}
        return {"aktiv": True, "name": h["name"], "live": self.live, "w": h.get("w", 650),
                "heute": rund(self.data["tage"].get(jetzt.date().isoformat(), {"kwh": 0, "solar_kwh": 0, "heiz_h": 0})),
                "woche": rund({k: sum(t[k] for _, t in tage[-7:]) for k in ("kwh", "solar_kwh", "heiz_h")}),
                "vorgabe": {"an": v[0], "soll_c": v[1], "grund": v[2]} if v else None,
                "kuscheln": {"aktiv": bool(kb and datetime.fromisoformat(kb) > jetzt), "bis": kb,
                             "soll_c": h.get("kuscheln_c", 22), "stunden": h.get("kuscheln_h", 2)},
                "plan": plan, "plan_ab": ab,
                "naechster": {"zeit": n_zeit.isoformat(timespec="minutes"), "soll_c": n_c},
                "regel": regel, "aktionen": self.data["aktionen"][-10:][::-1]}


def starten(path):
    hz = Heizung(path)
    threading.Thread(target=hz.regeln, daemon=True).start()
    return hz
