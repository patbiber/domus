"""Energie-Dienst für domus: Fronius-Livewerte + GWS-Tarif als JSON, dazu die Retro-Webseite.

Endpunkte:
  /api/status   aktuelle Leistung (PV, Verbrauch, Netz), Tarif, Kosten/Erlös pro Stunde
  /api/history  Minutenwerte der letzten 24 h (nur im Speicher, nach Neustart leer)
  /api/boerse   Day-Ahead-Börsenpreise Schweiz (heute/morgen) von Energy-Charts, in Rp/kWh via EZB-Kurs
  /api/speicher Simulation virtueller Batteriespeicher mit den echten Netzwerten (Stand in /data/speicher.json)
  /             statische Webseite aus www/
"""

import json
import os
import re
import threading
import time
import urllib.request
from collections import deque
from datetime import date, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

FRONIUS = os.environ.get("FRONIUS_HOST", "192.168.1.221")
TARIFE_JSON = os.environ.get("TARIFE_JSON", "/strompreise/latest/tarife.json")
TARIF_CFG = os.environ.get("TARIF_CFG", "/app/tarif.json")
PORT = int(os.environ.get("PORT", "8099"))
WWW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "www")
POLL_S = 5
BOERSE_URL = "https://api.energy-charts.info/price?bzn=CH&start={start}&end={end}"
EZB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
BOERSE_REFRESH_S = 30 * 60
SPEICHER_FILE = os.environ.get("SPEICHER_FILE", "/data/speicher.json")
MAX_DT_S = 30                      # grössere Lücken (Neustart, Ausfall) werden nicht hochgerechnet
WIRKUNGSGRAD = 0.95                # je Richtung, also rund 90 % hin und zurück
SPEICHER = [                       # virtuelle Speicher: Kapazität nutzbar, Lade- und Entladeleistung
    {"id": "stecker-2kwh", "name": "Steckerspeicher 2 kWh", "kapazitaet_kwh": 2, "laden_w": 1200, "entladen_w": 600},
    {"id": "speicher-5kwh", "name": "Speicher 5 kWh", "kapazitaet_kwh": 5, "laden_w": 3000, "entladen_w": 3000},
    {"id": "speicher-10kwh", "name": "Speicher 10 kWh", "kapazitaet_kwh": 10, "laden_w": 5000, "entladen_w": 5000},
]
WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]

state = {"status": None, "boerse": None}
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
        with lock:
            status["boerse"] = boerse_now(state["boerse"], now.timestamp())
        t = status["tarif"]
        if status["fronius_ok"] and t:
            status["kosten_chf_h"] = round(status["bezug_w"] / 1000 * t["bezug_chf_kwh"], 4)
            status["erloes_chf_h"] = round(status["einspeisung_w"] / 1000 * (t["rueckliefer_chf_kwh"] or 0), 4)
        with lock:
            state["status"] = status
            speicher_sim.step(status, now)
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


class Handler(SimpleHTTPRequestHandler):
    server_version = "domus"
    sys_version = ""

    def send_json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
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
        if path == "/api/history":
            with lock:
                return self.send_json(list(history))
        return super().do_GET()

    def list_directory(self, path):
        self.send_error(404)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    speicher_sim = SpeicherSimulation(SPEICHER_FILE)
    threading.Thread(target=poll_boerse, daemon=True).start()
    threading.Thread(target=poll, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), partial(Handler, directory=WWW)).serve_forever()
