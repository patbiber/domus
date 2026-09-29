"""Energie-Dienst für domus: Fronius-Livewerte + GWS-Tarif als JSON, dazu die Retro-Webseite.

Endpunkte:
  /api/status   aktuelle Leistung (PV, Verbrauch, Netz), Tarif, Kosten/Erlös pro Stunde
  /api/history  Minutenwerte der letzten 24 h (nur im Speicher, nach Neustart leer)
  /api/boerse   Day-Ahead-Börsenpreise Schweiz (heute/morgen) von Energy-Charts, in Rp/kWh via EZB-Kurs
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
            minute = now.strftime("%Y-%m-%dT%H:%M")
            if status["fronius_ok"] and minute != last_minute:
                last_minute = minute
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
        if path == "/api/history":
            with lock:
                return self.send_json(list(history))
        return super().do_GET()

    def list_directory(self, path):
        self.send_error(404)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=poll_boerse, daemon=True).start()
    threading.Thread(target=poll, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), partial(Handler, directory=WWW)).serve_forever()
