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
  /             statische Webseite aus www/
"""

import csv
import gzip
import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.request
from collections import deque
from datetime import date, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

FRONIUS = os.environ.get("FRONIUS_HOST", "192.168.1.221")
TARIFE_JSON = os.environ.get("TARIFE_JSON", "/strompreise/latest/tarife.json")
TARIF_CFG = os.environ.get("TARIF_CFG", "/app/tarif.json")
PORT = int(os.environ.get("PORT", "8099"))
WWW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "www")
# Kunden-Oberfläche (modernes Layout) für Aufrufe über <name>.homi.solar; alle anderen Adressen bekommen www/ (Retro)
WWW_KUNDE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "www-kunde")
KUNDE_HOST = re.compile(r"^[a-z0-9-]+\.homi\.solar$")
POLL_S = 5
BOERSE_URL = "https://api.energy-charts.info/price?bzn=CH&start={start}&end={end}"
EZB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
BOERSE_REFRESH_S = 30 * 60
SPEICHER_FILE = os.environ.get("SPEICHER_FILE", "/data/speicher.json")
ARCHIV_DIR = os.environ.get("ARCHIV_DIR", "/data/archiv")
RAPL_DIR = os.environ.get("RAPL_DIR", "/host/rapl/intel-rapl:0")   # Energiezähler der CPU (Intel RAPL)
NUC_REST_W = float(os.environ.get("NUC_REST_W", "4.0"))          # geschätzt: Platine, SSD, Netzteilverluste
ANLAGE_CFG = os.environ.get("ANLAGE_CFG", "/app/anlage.json")
PROGNOSE_LOG = os.environ.get("PROGNOSE_LOG", "/data/prognose_log.json")
PROGNOSE_REFRESH_S = 3600
PUSH_DIR = os.environ.get("PUSH_DIR", "/data/push")     # Push-Abos der App; Versand macht push.py auf dem Host
PUSH_THEMEN = ["minuspreis", "prognose", "ausfall"]
# Nur Adressen der bekannten Push-Dienste annehmen (Chrome/Android, Firefox, Safari/iOS, Edge)
PUSH_HOSTS = re.compile(r"^(fcm\.googleapis\.com|[a-z0-9.-]+\.push\.services\.mozilla\.com|web\.push\.apple\.com"
                        r"|[a-z0-9.-]+\.notify\.windows\.com)$")
OPEN_METEO = ("https://api.open-meteo.com/v1/forecast?latitude={breite}&longitude={laenge}"
              "&hourly=global_tilted_irradiance,cloud_cover&tilt={neigung}&azimuth={azimut}"
              "&past_days=14&forecast_days=3&timezone=Europe%2FZurich")
ARCHIV_TAGE = 5 * 365 + 2          # 5 Jahre aufbewahren
SLOT_S = 600                       # 10-Minuten-Schritte
ARCHIV_FELDER = ["zeit", "sekunden", "pv_kwh", "verbrauch_kwh", "bezug_kwh", "einspeisung_kwh",
                 "kosten_chf", "erloes_chf", "boerse_rp", "quelle"]
SUMMEN = ["sekunden", "pv_kwh", "verbrauch_kwh", "bezug_kwh", "einspeisung_kwh", "kosten_chf", "erloes_chf"]
MAX_DT_S = 30                      # grössere Lücken (Neustart, Ausfall) werden nicht hochgerechnet
WIRKUNGSGRAD = 0.95                # je Richtung, also rund 90 % hin und zurück
SPEICHER = [                       # virtuelle Speicher: Kapazität nutzbar, Lade- und Entladeleistung
    {"id": "stecker-2kwh", "name": "Steckerspeicher 2 kWh", "kapazitaet_kwh": 2, "laden_w": 1200, "entladen_w": 600},
    {"id": "speicher-5kwh", "name": "Speicher 5 kWh", "kapazitaet_kwh": 5, "laden_w": 3000, "entladen_w": 3000},
    {"id": "speicher-10kwh", "name": "Speicher 10 kWh", "kapazitaet_kwh": 10, "laden_w": 5000, "entladen_w": 5000},
]
WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]

state = {"status": None, "boerse": None, "prognose": None}
history = deque(maxlen=24 * 60)
lock = threading.Lock()


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def energy_price(tariff, now):
    """Arbeitspreis (CHF/kWh) des Zeitfensters, in dem 'now' liegt."""
    day = WEEKDAYS[now.weekday()]
    t = now.hour * 60 + now.minute
    for e in tariff["prices"]["energy"]:
        if day not in e["weekdays"]:
            continue
        start, end = minutes(e["from"]), minutes(e["to"])
        if start == end or (start < end and start <= t < end) or (start > end and (t >= start or t < end)):
            return e["price"]
    raise ValueError(f"kein Zeitfenster für {tariff['tariffName']} um {now:%a %H:%M}")


def find_tariff(tariffs, name, today):
    for t in tariffs:
        if t["tariffName"] == name and t["startDate"] <= today.isoformat() <= t["endDate"]:
            return t
    raise ValueError(f"Tarif {name} für {today} nicht gefunden")


def year_value(table, year):
    """Wert für das Jahr oder – falls nicht vorhanden – das letzte bekannte Jahr davor."""
    years = sorted(int(y) for y in table if y.isdigit() and int(y) <= year)
    return table[str(years[-1])] if years else None


def current_tariff(now=None):
    now = now or datetime.now()
    cfg = load_json(TARIF_CFG)
    data = load_json(TARIFE_JSON)
    today = now.date()
    energie = energy_price(find_tariff(data["tariffs"], cfg["produkt"], today), now)
    netz = energy_price(find_tariff(data["tariffs"], cfg["netztarif"], today), now)
    exkl = energie + netz + cfg["gemeindeabgabe_exkl"]
    override = cfg.get("bezug_exkl_override", {}).get(str(today.year))
    if override:
        exkl = override
    rueck = year_value(cfg["rueckliefer"], today.year)
    sommer = 4 <= today.month <= 9
    return {
        "produkt": cfg["produkt"],
        "bezug_chf_kwh": round(exkl * (1 + cfg["mwst"]), 4),
        "bezug_exkl_chf_kwh": round(exkl, 4),
        "rueckliefer_chf_kwh": rueck["sommer" if sommer else "winter"] if rueck else None,
        "saison": "Sommer" if sommer else "Winter",
        "stand": os.path.basename(os.path.dirname(os.path.realpath(TARIFE_JSON))),
    }


def fetch_fronius():
    url = f"http://{FRONIUS}/solar_api/v1/GetPowerFlowRealtimeData.fcgi"
    with urllib.request.urlopen(url, timeout=4) as r:
        site = json.load(r)["Body"]["Data"]["Site"]
    pv = max(site.get("P_PV") or 0, 0)
    grid = site.get("P_Grid") or 0          # + Bezug, - Einspeisung
    load = -(site.get("P_Load") or 0)       # Fronius liefert Verbrauch negativ
    return {
        "pv_w": round(pv),
        "verbrauch_w": round(max(load, 0)),
        "netz_w": round(grid),
        "bezug_w": round(max(grid, 0)),
        "einspeisung_w": round(max(-grid, 0)),
        "pv_heute_kwh": round((site.get("E_Day") or 0) / 1000, 2),
        "autarkie_pct": site.get("rel_Autonomy"),
    }


def fetch_boerse():
    """Day-Ahead-Preise CH (EUR/MWh) für heute bis übermorgen und EUR/CHF-Kurs der EZB."""
    today = date.today()
    url = BOERSE_URL.format(start=today.isoformat(), end=(today + timedelta(days=2)).isoformat())
    req = urllib.request.Request(url, headers={"User-Agent": "domus-energie"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    with urllib.request.urlopen(EZB_URL, timeout=20) as r:
        kurs = float(re.search(r"currency='CHF' rate='([0-9.]+)'", r.read().decode()).group(1))
    ts, preise = data["unix_seconds"], data["price"]
    step = ts[1] - ts[0] if len(ts) > 1 else 3600
    return {
        "kurs_eur_chf": kurs,
        "intervall_s": step,
        "quelle": "EPEX Spot Day-Ahead CH via Energy-Charts (Fraunhofer ISE), " + data.get("license_info", ""),
        "preise": [
            {"zeit": datetime.fromtimestamp(t).isoformat(timespec="minutes"),
             "ts": t,
             "eur_mwh": p,
             "rp_kwh": round(p * kurs / 10, 2)}
            for t, p in zip(ts, preise) if p is not None
        ],
    }


def boerse_now(b, now_ts):
    """Aktueller Börsenpreis und Kennzahlen aus der Preisliste."""
    if not b or not b["preise"]:
        return None
    cur = next((p for p in b["preise"] if p["ts"] <= now_ts < p["ts"] + b["intervall_s"]), None)
    kommend = [p for p in b["preise"] if p["ts"] + b["intervall_s"] > now_ts]
    neg = next((p for p in kommend if p["eur_mwh"] < 0), None)
    return {
        "rp_kwh": cur["rp_kwh"] if cur else None,
        "eur_mwh": cur["eur_mwh"] if cur else None,
        "negativ": bool(cur and cur["eur_mwh"] < 0),
        "min_rp_kwh": min(p["rp_kwh"] for p in kommend) if kommend else None,
        "max_rp_kwh": max(p["rp_kwh"] for p in kommend) if kommend else None,
        "naechster_negativer": neg["zeit"] if neg else None,
        "kurs_eur_chf": b["kurs_eur_chf"],
    }


def poll_boerse():
    while True:
        try:
            b = fetch_boerse()
            with lock:
                state["boerse"] = b
            wait = BOERSE_REFRESH_S
        except Exception as e:  # Quelle nicht erreichbar: alte Werte behalten, bald erneut versuchen
            print("Börsenpreise nicht ladbar:", e, flush=True)
            wait = 300
        time.sleep(wait)


class SpeicherSimulation:
    """Rechnet mit dem echten Netzsaldo nach, was ein Speicher gespart hätte.

    Überschuss (Einspeisung) lädt den virtuellen Speicher, Netzbezug entlädt ihn. Die entgangene
    Rückliefervergütung wird mit der Energie im Speicher mitgeführt (wert_chf) und erst beim Entladen
    verrechnet: Ersparnis = vermiedener Bezug × Bezugspreis − anteilige entgangene Vergütung.
    """

    def __init__(self, path):
        self.path = path
        self.last_ts = None
        try:
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {"start": datetime.now().isoformat(timespec="seconds"), "sims": {}}
        for sp in SPEICHER:
            self.data["sims"].setdefault(sp["id"], {"soc_wh": 0.0, "wert_chf": 0.0, "tage": {}})

    def step(self, status, now):
        ts = now.timestamp()
        dt = ts - self.last_ts if self.last_ts else None
        self.last_ts = ts
        t = status.get("tarif")
        if not status.get("fronius_ok") or not t or dt is None or dt <= 0 or dt > MAX_DT_S:
            return
        h = dt / 3600
        netz = status["netz_w"]                     # + Bezug, - Einspeisung
        tag = now.date().isoformat()
        rueck = t["rueckliefer_chf_kwh"] or 0
        for sp in SPEICHER:
            sim = self.data["sims"][sp["id"]]
            cap = sp["kapazitaet_kwh"] * 1000
            d = sim["tage"].setdefault(tag, {"stunden": 0.0, "geladen_kwh": 0.0, "entladen_kwh": 0.0,
                                             "bezug_ohne_kwh": 0.0, "bezug_mit_kwh": 0.0, "ersparnis_chf": 0.0})
            laden = entladen = kosten = 0.0         # W am Netzanschluss, CHF
            if netz < 0:
                laden = min(-netz, sp["laden_w"], (cap - sim["soc_wh"]) / WIRKUNGSGRAD / h)
                sim["soc_wh"] += laden * h * WIRKUNGSGRAD
                sim["wert_chf"] += laden * h / 1000 * rueck
            elif netz > 0 and sim["soc_wh"] > 0:
                entladen = min(netz, sp["entladen_w"], sim["soc_wh"] * WIRKUNGSGRAD / h)
                anteil = min(entladen * h / WIRKUNGSGRAD / sim["soc_wh"], 1.0)
                kosten = sim["wert_chf"] * anteil
                sim["wert_chf"] -= kosten
                sim["soc_wh"] -= entladen * h / WIRKUNGSGRAD
            sim["soc_wh"] = min(max(sim["soc_wh"], 0.0), cap)
            d["stunden"] += h
            d["geladen_kwh"] += laden * h / 1000
            d["entladen_kwh"] += entladen * h / 1000
            d["bezug_ohne_kwh"] += max(netz, 0) * h / 1000
            d["bezug_mit_kwh"] += (max(netz, 0) - entladen) * h / 1000
            d["ersparnis_chf"] += entladen * h / 1000 * t["bezug_chf_kwh"] - kosten

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)

    def report(self):
        heute = date.today().isoformat()
        out = {"start": self.data["start"], "wirkungsgrad": WIRKUNGSGRAD ** 2, "speicher": []}
        for sp in SPEICHER:
            sim = self.data["sims"][sp["id"]]
            tage = sim["tage"]
            summe = {k: sum(d[k] for d in tage.values()) for k in
                     ("stunden", "geladen_kwh", "entladen_kwh", "bezug_ohne_kwh", "bezug_mit_kwh", "ersparnis_chf")}
            letzte7 = [d for k, d in sorted(tage.items())[-7:]]
            s7 = {k: sum(d[k] for d in letzte7) for k in ("stunden", "ersparnis_chf", "entladen_kwh")}
            r2 = lambda v, n=3: round(v, n)
            out["speicher"].append({
                **sp,
                "soc_pct": r2(sim["soc_wh"] / (sp["kapazitaet_kwh"] * 10), 1),
                "heute": {k: r2(v) for k, v in tage.get(heute, {}).items()},
                "summe": {k: r2(v) for k, v in summe.items()},
                "tage_gemessen": r2(summe["stunden"] / 24, 2),
                "zyklen": r2(summe["entladen_kwh"] / sp["kapazitaet_kwh"], 1),
                "autarkie_plus_pct": r2(100 * summe["entladen_kwh"] / summe["bezug_ohne_kwh"], 1) if summe["bezug_ohne_kwh"] else None,
                # Hochrechnung aus den letzten 7 Tagen – saisonal stark schwankend, nur als Richtwert
                "ersparnis_pro_jahr_chf": r2(s7["ersparnis_chf"] / s7["stunden"] * 24 * 365, 0) if s7["stunden"] >= 20 else None,
                "tage": [{"tag": k, **{kk: r2(vv) for kk, vv in d.items()}} for k, d in sorted(tage.items())[-60:]],
            })
        return out


speicher_sim = None


class ServerMesser:
    """Misst den homi-Server (NUC): CPU+RAM-Leistung über RAPL, CPU-Last, Temperatur, RAM, Uptime."""

    def __init__(self):
        self.last = None            # (zeit, energie_pkg_uj, energie_dram_uj, cpu_busy, cpu_total)
        self.werte = {}
        self.dienste = {}
        self.dienste_ts = 0

    @staticmethod
    def _lies(pfad, default=None):
        try:
            with open(pfad) as f:
                return f.read().strip()
        except OSError:
            return default

    def _rapl(self, sub):
        v = self._lies(os.path.join(RAPL_DIR, sub, "energy_uj") if sub else os.path.join(RAPL_DIR, "energy_uj"))
        return int(v) if v else None

    def _dram_dir(self):
        for d in ("intel-rapl:0:2", "intel-rapl:0:1", "intel-rapl:0:0"):
            if self._lies(os.path.join(RAPL_DIR, d, "name")) == "dram":
                return d
        return None

    def messen(self):
        t = time.time()
        pkg, dram = self._rapl(""), (self._rapl(self._dram_dir()) if self._dram_dir() else None)
        cpu = [int(x) for x in self._lies("/proc/stat", "cpu 0").splitlines()[0].split()[1:]]
        busy, total = sum(cpu) - cpu[3] - (cpu[4] if len(cpu) > 4 else 0), sum(cpu)
        w = {}
        if self.last:
            dt = t - self.last[0]
            wrap = int(self._lies(os.path.join(RAPL_DIR, "max_energy_range_uj"), "0") or 0)
            diff = lambda a, b: (a - b) if a >= b else (a + wrap - b)
            if pkg is not None and self.last[1] is not None and dt > 0:
                w["cpu_w"] = round(diff(pkg, self.last[1]) / dt / 1e6, 2)
            if dram is not None and self.last[2] is not None and dt > 0:
                w["ram_w"] = round(diff(dram, self.last[2]) / dt / 1e6, 2)
            if total > self.last[4]:
                w["cpu_pct"] = round(100 * (busy - self.last[3]) / (total - self.last[4]), 1)
        self.last = (t, pkg, dram, busy, total)
        if "cpu_w" in w:
            w["leistung_w"] = round(w["cpu_w"] + w.get("ram_w", 0) + NUC_REST_W, 1)
            w["rest_w_geschaetzt"] = NUC_REST_W
        temp = self._lies("/sys/class/hwmon/hwmon1/temp1_input") or self._lies("/sys/class/thermal/thermal_zone2/temp")
        w["temp_c"] = round(int(temp) / 1000, 1) if temp else None
        w["uptime_s"] = int(float(self._lies("/proc/uptime", "0 0").split()[0]))
        w["load1"] = float(self._lies("/proc/loadavg", "0").split()[0])
        mem = dict(l.split(":", 1) for l in self._lies("/proc/meminfo", "").splitlines() if ":" in l)
        try:
            w["ram_pct"] = round(100 * (1 - int(mem["MemAvailable"].split()[0]) / int(mem["MemTotal"].split()[0])), 1)
        except (KeyError, ValueError):
            pass
        try:
            st = os.statvfs("/data")
            w["disk_pct"] = round(100 * (1 - st.f_bavail / st.f_blocks), 1)
        except OSError:
            pass
        if t - self.dienste_ts > 60:        # Dienste nur jede Minute prüfen
            import socket
            for name, port in (("homeassistant", 8123), ("nginx", 443)):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=2).close()
                    self.dienste[name] = True
                except OSError:
                    self.dienste[name] = False
            self.dienste_ts = t
        w["dienste"] = dict(self.dienste, homi=True)
        self.werte = w
        return w


server_messer = ServerMesser()


class Archiv:
    """10-Minuten-Archiv: eine CSV pro Tag unter <ARCHIV_DIR>/<Jahr>/<Datum>.csv.

    `sekunden` = wie viele Sekunden des Schritts gemessen wurden (600 = vollständig).
    Vergangene Tage werden gzip-komprimiert, Tage älter als 5 Jahre gelöscht.
    `quelle`: live = von homi gemessen, ha = einmalig aus Home Assistant übernommen.
    """

    def __init__(self, base):
        self.base = base
        self.slot = None
        self.acc = None
        self.last_ts = None
        self.tag = None

    def pfad(self, tag):
        return os.path.join(self.base, tag[:4], f"{tag}.csv")

    def schreiben(self, rows):
        for r in rows:
            p = self.pfad(r["zeit"][:10])
            os.makedirs(os.path.dirname(p), exist_ok=True)
            neu = not os.path.exists(p)
            with open(p, "a", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, ARCHIV_FELDER)
                if neu:
                    w.writeheader()
                w.writerow(r)

    def aufraeumen(self, heute):
        """Vergangene Tage komprimieren (an bestehende .gz anhängen), zu alte Tage löschen."""
        if not os.path.isdir(self.base):
            return
        grenze = (heute - timedelta(days=ARCHIV_TAGE)).isoformat()
        for jahr in os.listdir(self.base):
            d = os.path.join(self.base, jahr)
            for name in os.listdir(d):
                p = os.path.join(d, name)
                if name[:10] < grenze:
                    os.remove(p)
                elif name.endswith(".csv") and name[:10] < heute.isoformat():
                    with open(p, "rb") as src, gzip.open(p + ".gz", "ab") as dst:
                        shutil.copyfileobj(src, dst)
                    os.remove(p)
            if not os.listdir(d):
                os.rmdir(d)

    def lesen(self, von, bis):
        """Alle 10-Minuten-Zeilen von..bis (Datum), sortiert; doppelte Zeitpunkte: die vollständigere gewinnt."""
        rows = {}
        d = von
        while d <= bis:
            for p in (self.pfad(d.isoformat()) + ".gz", self.pfad(d.isoformat())):
                if not os.path.exists(p):
                    continue
                with (gzip.open if p.endswith(".gz") else open)(p, "rt", encoding="utf-8") as f:
                    for r in csv.DictReader(f):
                        if r["zeit"] == "zeit":          # Kopfzeile eines angehängten gzip-Teils
                            continue
                        alt = rows.get(r["zeit"])   # doppelt: die vollständigere Zeile gewinnt, bei Gleichstand live
                        if alt is None or (float(r["sekunden"] or 0), r["quelle"] == "live") > \
                                (float(alt["sekunden"] or 0), alt["quelle"] == "live"):
                            rows[r["zeit"]] = r
            d += timedelta(days=1)
        return [{"zeit": z, "quelle": rows[z]["quelle"],
                 **{k: (float(rows[z][k]) if rows[z][k] != "" else None)
                    for k in ARCHIV_FELDER if k not in ("zeit", "quelle")}} for z in sorted(rows)]

    def step(self, status, now):
        ts = now.timestamp()
        slot = int(ts // SLOT_S) * SLOT_S
        if slot != self.slot:
            self.flush()
            self.slot = slot
            self.acc = {"s": 0.0, "pv": 0.0, "vb": 0.0, "bz": 0.0, "es": 0.0, "k": 0.0, "e": 0.0, "bo": 0.0, "bo_s": 0.0}
            if self.tag != now.date():
                self.tag = now.date()
                self.aufraeumen(self.tag)
        dt = ts - self.last_ts if self.last_ts else None
        self.last_ts = ts
        if not status.get("fronius_ok") or dt is None or dt <= 0 or dt > MAX_DT_S:
            return
        a, h = self.acc, dt / 3600
        a["s"] += dt
        a["pv"] += status["pv_w"] * h / 1000
        a["vb"] += status["verbrauch_w"] * h / 1000
        a["bz"] += status["bezug_w"] * h / 1000
        a["es"] += status["einspeisung_w"] * h / 1000
        t = status.get("tarif")
        if t:
            a["k"] += status["bezug_w"] * h / 1000 * t["bezug_chf_kwh"]
            a["e"] += status["einspeisung_w"] * h / 1000 * (t["rueckliefer_chf_kwh"] or 0)
        b = status.get("boerse")
        if b and b.get("rp_kwh") is not None:
            a["bo"] += b["rp_kwh"] * dt
            a["bo_s"] += dt

    def flush(self):
        a = self.acc
        if not a or a["s"] < 1:
            return
        self.schreiben([{
            "zeit": datetime.fromtimestamp(self.slot).isoformat(timespec="minutes"),
            "sekunden": round(a["s"]), "pv_kwh": round(a["pv"], 5), "verbrauch_kwh": round(a["vb"], 5),
            "bezug_kwh": round(a["bz"], 5), "einspeisung_kwh": round(a["es"], 5),
            "kosten_chf": round(a["k"], 5), "erloes_chf": round(a["e"], 5),
            "boerse_rp": round(a["bo"] / a["bo_s"], 3) if a["bo_s"] else "", "quelle": "live"}])


def archiv_aggregat(rows, aufloesung):
    """Fasst 10-Minuten-Zeilen zu Stunden, Tagen, Monaten oder Jahren zusammen, plus Gesamtsumme."""
    laenge = {"10min": 16, "stunde": 13, "tag": 10, "monat": 7, "jahr": 4}[aufloesung]
    gruppen = {}
    for r in rows:
        g = gruppen.setdefault(r["zeit"][:laenge], {k: 0.0 for k in SUMMEN} | {"_bo": 0.0, "_bo_s": 0.0})
        for k in SUMMEN:
            g[k] += r[k] or 0
        if r["boerse_rp"] is not None and r["sekunden"]:
            g["_bo"] += r["boerse_rp"] * r["sekunden"]
            g["_bo_s"] += r["sekunden"]

    def fertig(g):
        out = {k: round(g[k], 4) for k in SUMMEN}
        out["sekunden"] = round(g["sekunden"])
        out["boerse_rp"] = round(g["_bo"] / g["_bo_s"], 2) if g["_bo_s"] else None
        out["saldo_chf"] = round(g["erloes_chf"] - g["kosten_chf"], 4)
        out["autarkie_pct"] = round(100 * (1 - g["bezug_kwh"] / g["verbrauch_kwh"]), 1) if g["verbrauch_kwh"] > 0.001 else None
        out["eigenverbrauch_pct"] = round(100 * (1 - g["einspeisung_kwh"] / g["pv_kwh"]), 1) if g["pv_kwh"] > 0.001 else None
        return out

    total = {k: 0.0 for k in SUMMEN} | {"_bo": 0.0, "_bo_s": 0.0}
    for g in gruppen.values():
        for k in total:
            total[k] += g[k]
    return {"zeilen": [{"zeit": z, **fertig(g)} for z, g in sorted(gruppen.items())], "summe": fertig(total)}


archiv = None


def bestes_fenster(stunden, dauer):
    """Zusammenhängendes Fenster von `dauer` Stunden mit der höchsten erwarteten Produktion."""
    best = None
    for i in range(len(stunden) - dauer + 1):
        teil = stunden[i:i + dauer]
        if any(int(teil[j + 1]["zeit"][11:13]) != int(teil[j]["zeit"][11:13]) + 1 for j in range(dauer - 1)):
            continue
        summe = sum(h["kwh"] for h in teil)
        if best is None or summe > best[0]:
            best = (summe, teil[0]["zeit"][11:16], f"{int(teil[-1]['zeit'][11:13]) + 1:02d}:00")
    if not best or best[0] < 0.2:
        return None
    return {"von": best[1], "bis": best[2], "kwh": round(best[0], 2)}


def prognose_berechnen():
    """Open-Meteo-Einstrahlung auf die Modulebene × Eichfaktor aus den eigenen Messwerten der letzten 14 Tage."""
    cfg = load_json(ANLAGE_CFG)
    with urllib.request.urlopen(OPEN_METEO.format(**cfg), timeout=30) as r:
        d = json.load(r)
    h = d["hourly"]
    # Open-Meteo-Stundenwert gilt für die Stunde VOR dem Zeitstempel -> auf Stundenbeginn umrechnen
    gti, wolken = {}, {}
    for t, g, c in zip(h["time"], h["global_tilted_irradiance"], h["cloud_cover"]):
        beginn = (datetime.fromisoformat(t) - timedelta(hours=1)).isoformat(timespec="minutes")[:13]
        if g is not None:
            gti[beginn] = g
            wolken[beginn] = c
    heute = date.today()
    ist = {z["zeit"]: z["pv_kwh"] for z in archiv_aggregat(
        archiv.lesen(heute - timedelta(days=14), heute - timedelta(days=1)), "stunde")["zeilen"] if z["sekunden"] >= 3000}
    paare = [(gti[k] / 1000, v) for k, v in ist.items() if k in gti and gti[k] > 50]
    faktor = cfg["faktor_start"]
    if len(paare) >= 30 and sum(g for g, _ in paare) > 0:
        faktor = min(max(sum(v for _, v in paare) / sum(g for g, _ in paare), faktor * 0.5), faktor * 1.5)
    stunden = [{"zeit": k + ":00", "kwh": round(min(faktor * v / 1000, cfg["max_kw"]), 3), "wolken_pct": wolken.get(k)}
               for k, v in sorted(gti.items()) if k[:10] >= heute.isoformat()]

    def tag(d0, nur_ab=None):
        st = [x for x in stunden if x["zeit"][:10] == d0.isoformat()]
        rest = [x for x in st if nur_ab is None or x["zeit"][11:13] >= nur_ab]
        sonne = [x for x in st if x["kwh"] > 0.05]
        return {"datum": d0.isoformat(), "kwh": round(sum(x["kwh"] for x in st), 1),
                "rest_kwh": round(sum(x["kwh"] for x in rest), 1),
                "spitze_kw": round(max((x["kwh"] for x in st), default=0), 2),
                "wolken_pct": round(sum(x["wolken_pct"] or 0 for x in sonne) / len(sonne)) if sonne else None,
                "bestes_fenster": bestes_fenster(rest, cfg.get("geraet_stunden", 2))}

    jetzt = datetime.now()
    return {"aktualisiert": jetzt.isoformat(timespec="minutes"), "faktor": round(faktor, 3),
            "eichstunden": len(paare), "ausrichtung": {"neigung": cfg["neigung"], "azimut": cfg["azimut"]},
            "heute": tag(heute, f"{jetzt.hour:02d}"), "morgen": tag(heute + timedelta(days=1)),
            "uebermorgen": tag(heute + timedelta(days=2)), "stunden": stunden,
            "quelle": "Open-Meteo (CC BY 4.0), geeicht mit den Messwerten von homi"}


def prognose_log(p):
    """Merkt sich die Abendprognose für morgen und vergleicht vergangene Tage mit der Messung."""
    try:
        log = load_json(PROGNOSE_LOG)
    except (OSError, ValueError):
        log = {}
    if datetime.now().hour >= 18:
        log.setdefault(p["morgen"]["datum"], {"prognose_kwh": p["morgen"]["kwh"]})
    for tag_, e in log.items():
        if "ist_kwh" not in e and tag_ < date.today().isoformat():
            z = archiv_aggregat(archiv.lesen(date.fromisoformat(tag_), date.fromisoformat(tag_)), "tag")["summe"]
            if z["sekunden"] > 0.9 * 86400:
                e["ist_kwh"] = round(z["pv_kwh"], 1)
    log = dict(sorted(log.items())[-400:])
    tmp = PROGNOSE_LOG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=1)
    os.replace(tmp, PROGNOSE_LOG)
    return [{"tag": k, **v} for k, v in list(log.items())[-14:]]


FAHRPLAN_BATTERIE = {"kapazitaet_kwh": 5, "leistung_kw": 3, "wirkungsgrad": 0.9}   # wie Speicher-Simulation 5 kWh


def typische_last():
    """Mittlerer Verbrauch pro Stunde des Tages (kWh) aus den letzten 7 Tagen des Archivs."""
    heute = date.today()
    rows = archiv_aggregat(archiv.lesen(heute - timedelta(days=7), heute - timedelta(days=1)), "stunde")["zeilen"]
    pro_h = {}
    for z in rows:
        if z["sekunden"] >= 3000:
            pro_h.setdefault(int(z["zeit"][11:13]), []).append(z["verbrauch_kwh"] * 3600 / z["sekunden"])
    return {h: (sum(v) / len(v) if v else 0.3) for h, v in ((h, pro_h.get(h, [])) for h in range(24))}


def tag_fahrplan(tag_, stunden, preise, last, tarif_rp):
    """Fahrplan eines Tages: Überschuss, Minuspreise, einfaches vs. optimiertes Laden einer Batterie.
    Annahme dynamische Vergütung = Day-Ahead-Börsenpreis (auch negativ)."""
    B = FAHRPLAN_BATTERIE
    h_ = []
    for h in range(24):
        k = f"{tag_}T{h:02d}"
        pv = stunden.get(k)
        p = preise.get(k)
        if pv is None or p is None:
            continue
        l = last[h]
        h_.append({"stunde": h, "pv_kwh": round(pv, 2), "last_kwh": round(l, 2), "preis_rp": p,
                   "ueberschuss": max(pv - l, 0), "defizit": max(l - pv, 0)})
    if not h_:
        return None

    def bewerten(lade):                             # lade: {stunde: kWh in die Batterie}
        gespeichert = sum(lade.values()) * B["wirkungsgrad"]
        letzte_ladung = max(lade) if lade else -1
        rest, wert, aktion = gespeichert, 0.0, {}
        for x in h_:                                # Defizite nach dem Laden aus der Batterie decken (Wert = Bezugstarif)
            if x["stunde"] > letzte_ladung and rest > 0 and x["defizit"] > 0:
                d = min(rest, x["defizit"], B["leistung_kw"])
                rest -= d; wert += d * tarif_rp / 100; aktion[x["stunde"]] = f"aus Batterie {d:.1f} kWh"
        for x in sorted((x for x in h_ if x["stunde"] > letzte_ladung), key=lambda x: -x["preis_rp"]):
            if rest <= 0 or x["preis_rp"] <= 0:     # Rest zum besten Preis einspeisen
                break
            e = min(rest, B["leistung_kw"]); rest -= e; wert += e * x["preis_rp"] / 100
        for x in h_:                                # Überschuss, der nicht in die Batterie geht
            ex = x["ueberschuss"] - lade.get(x["stunde"], 0)
            x["_export"] = ex
        return wert, aktion

    # einfach: laden, sobald Überschuss da ist; alles andere einspeisen – auch bei Minuspreisen
    lade, frei = {}, B["kapazitaet_kwh"] / B["wirkungsgrad"]
    for x in h_:
        e = min(x["ueberschuss"], B["leistung_kw"], frei)
        if e > 0:
            lade[x["stunde"]] = e; frei -= e
    w_b, _ = bewerten(lade)
    einfach = w_b + sum((x["ueberschuss"] - lade.get(x["stunde"], 0)) * x["preis_rp"] / 100 for x in h_)
    # optimiert: zuerst in den billigsten (v. a. negativen) Stunden laden, bei Minuspreisen nicht einspeisen
    lade, frei = {}, B["kapazitaet_kwh"] / B["wirkungsgrad"]
    for x in sorted(h_, key=lambda x: x["preis_rp"]):
        e = min(x["ueberschuss"], B["leistung_kw"], frei)
        if e > 0:
            lade[x["stunde"]] = e; frei -= e
    w_b, aktion = bewerten(lade)
    optimiert = w_b + sum(max(x["ueberschuss"] - lade.get(x["stunde"], 0), 0) * x["preis_rp"] / 100
                          for x in h_ if x["preis_rp"] > 0)
    plan = []
    for x in h_:
        ex = x["ueberschuss"] - lade.get(x["stunde"], 0)
        if x["preis_rp"] < 0 and x["ueberschuss"] > 0.05:
            a = "Einspeisung stoppen" + (f", Batterie laden {lade[x['stunde']]:.1f} kWh" if x["stunde"] in lade else "")
        elif x["stunde"] in lade:
            a = f"Batterie laden {lade[x['stunde']]:.1f} kWh" + (f", {ex:.1f} kWh einspeisen" if ex > 0.05 else "")
        elif ex > 0.05:
            a = f"{ex:.1f} kWh einspeisen"
        else:
            a = aktion.get(x["stunde"], "Netzbezug" if x["defizit"] > 0.05 else "–")
        plan.append({k: v for k, v in x.items() if not k.startswith("_") and k not in ("ueberschuss", "defizit")} |
                    {"ueberschuss_kwh": round(x["ueberschuss"], 2), "aktion": a})
    neg = [x for x in h_ if x["preis_rp"] < 0]
    ueb_neg = sum(x["ueberschuss"] for x in neg)
    return {
        "datum": tag_, "stunden": plan,
        "minuspreis_stunden": [x["stunde"] for x in neg],
        "minuspreis_bloecke": bloecke([x["stunde"] for x in neg]),
        "ueberschuss_bei_minuspreis_kwh": round(ueb_neg, 1),
        "kosten_bei_minuspreis_chf": round(sum(x["ueberschuss"] * x["preis_rp"] / 100 for x in neg), 2),
        "einspeisung_dynamisch_chf": round(sum(x["ueberschuss"] * x["preis_rp"] / 100 for x in h_), 2),
        "batterie": {**B, "einfach_chf": round(einfach, 2), "optimiert_chf": round(optimiert, 2),
                     "mehrwert_chf": round(optimiert - einfach, 2)},
    }


def bloecke(stunden):
    """[11,12,13,16] -> ["11–14 Uhr", "16–17 Uhr"]"""
    out, start = [], None
    for i, h in enumerate(stunden):
        if start is None:
            start = h
        if i == len(stunden) - 1 or stunden[i + 1] != h + 1:
            out.append(f"{start:02d}–{h + 1:02d} Uhr"); start = None
    return out


def einspeise_plan():
    with lock:
        p, b, st = state["prognose"], state["boerse"], state["status"]
    if not p or not b:
        return {"startet": True}
    tarif = ((st or {}).get("tarif") or {})
    stunden = {x["zeit"][:13]: x["kwh"] for x in p["stunden"]}
    preise = {x["zeit"][:13]: x["rp_kwh"] for x in b["preise"]}
    last = typische_last()
    heute = date.today()
    out = {"annahme": "Dynamische Einspeisevergütung = Day-Ahead-Börsenpreis Schweiz (stündlich, auch negativ); "
                      "Verbrauch = Mittel der letzten 7 Tage; Batterie virtuell (wie Speicher-Simulation).",
           "fix_verguetung_rp": round((tarif.get("rueckliefer_chf_kwh") or 0) * 100, 2),
           "bezug_rp": round((tarif.get("bezug_chf_kwh") or 0) * 100, 2)}
    for name, d in (("heute", heute), ("morgen", heute + timedelta(days=1))):
        out[name] = tag_fahrplan(d.isoformat(), stunden, preise, last, out["bezug_rp"])
        if out[name]:
            fix = sum(x["ueberschuss_kwh"] for x in out[name]["stunden"]) * out["fix_verguetung_rp"] / 100
            out[name]["einspeisung_fix_chf"] = round(fix, 2)
    return out


def poll_prognose():
    time.sleep(20)                      # Archiv zuerst starten lassen
    while True:
        try:
            p = prognose_berechnen()
            p["treffer"] = prognose_log(p)
            with lock:
                state["prognose"] = p
            wait = PROGNOSE_REFRESH_S
        except Exception as e:  # Prognose ist Beiwerk
            print("Prognose nicht berechnet:", e, flush=True)
            wait = 600
        time.sleep(wait)


def poll():
    last_minute = None
    while True:
        now = datetime.now()
        status = {"zeit": now.isoformat(timespec="seconds"), "fronius_ok": False}
        try:
            status["tarif"] = current_tariff(now)
        except Exception as e:  # Tarifdaten fehlen/kaputt: Seite läuft trotzdem
            status["tarif"] = None
            status["tarif_fehler"] = str(e)
        try:
            status.update(fetch_fronius())
            status["fronius_ok"] = True
        except Exception as e:  # nachts ist der Wechselrichter aus
            status["fronius_fehler"] = type(e).__name__
        try:
            status["server"] = server_messer.messen()
            if status["tarif"] and status["server"].get("leistung_w"):
                kwh_jahr = status["server"]["leistung_w"] * 8.76
                status["server"]["kwh_jahr"] = round(kwh_jahr)
                status["server"]["kosten_jahr_chf"] = round(kwh_jahr * status["tarif"]["bezug_chf_kwh"], 2)
        except Exception as e:  # Messung ist Beiwerk, darf homi nie stören
            status["server"] = {"fehler": type(e).__name__}
        with lock:
            status["boerse"] = boerse_now(state["boerse"], now.timestamp())
            p = state["prognose"]
            status["prognose"] = {k: p[k] for k in ("heute", "morgen")} if p else None
        t = status["tarif"]
        if status["fronius_ok"] and t:
            status["kosten_chf_h"] = round(status["bezug_w"] / 1000 * t["bezug_chf_kwh"], 4)
            status["erloes_chf_h"] = round(status["einspeisung_w"] / 1000 * (t["rueckliefer_chf_kwh"] or 0), 4)
        with lock:
            state["status"] = status
            speicher_sim.step(status, now)
            try:
                archiv.step(status, now)
            except OSError as e:
                print("Archiv nicht geschrieben:", e, flush=True)
            minute = now.strftime("%Y-%m-%dT%H:%M")
            if status["fronius_ok"] and minute != last_minute:
                last_minute = minute
                try:
                    speicher_sim.save()
                except OSError as e:
                    print("Speicher-Simulation nicht gespeichert:", e, flush=True)
                history.append({k: status.get(k) for k in
                                ("zeit", "pv_w", "verbrauch_w", "netz_w", "kosten_chf_h", "erloes_chf_h")})
        time.sleep(POLL_S)


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


class Handler(SimpleHTTPRequestHandler):
    server_version = "domus"
    sys_version = ""
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".webmanifest": "application/manifest+json",
                      ".js": "text/javascript"}

    def end_headers(self):
        # Service Worker immer frisch prüfen, damit Änderungen an sw.js sofort ankommen
        if self.path.split("?")[0] == "/sw.js":
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
                return self.send_json(speicher_sim.report())
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
                   for r in archiv.lesen((jetzt - timedelta(hours=24)).date(), jetzt.date())
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
        rows = archiv.lesen(von, bis)
        erste = rows[0]["zeit"] if rows else None
        return self.send_json({"von": von.isoformat(), "bis": bis.isoformat(), "aufloesung": aufl,
                               "erste_daten": erste, **archiv_aggregat(rows, aufl)})

    def list_directory(self, path):
        self.send_error(404)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    speicher_sim = SpeicherSimulation(SPEICHER_FILE)
    archiv = Archiv(ARCHIV_DIR)
    threading.Thread(target=poll_boerse, daemon=True).start()
    threading.Thread(target=poll_prognose, daemon=True).start()
    threading.Thread(target=poll, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), partial(Handler, directory=WWW)).serve_forever()
