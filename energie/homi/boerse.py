"""Day-Ahead-Börsenpreise Schweiz (Energy-Charts) mit EZB-Kurs."""
import json
import re
import time
import urllib.request
from datetime import date, datetime, timedelta

from .konfig import BOERSE_REFRESH_S, BOERSE_URL, EZB_URL
from .zustand import lock, state


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
