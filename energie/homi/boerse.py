"""Day-Ahead-Börsenpreise Schweiz (Energy-Charts), umgerechnet zum Tageskurs der SNB (Rückfall: EZB).

Ab 2027 gilt für die Abnahmevergütung (Art. 12 Abs. 1 EnV): Spotpreis Day-Ahead Marktgebiet Schweiz zum
Tageswechselkurs der SNB; an Wochenenden und Feiertagen der Kurs des letzten Werktags. homi rechnet deshalb jeden
Preis mit dem SNB-Kurs seines Tages um (oder dem letzten davor). Für morgen gibt es noch keinen Kurs: dann gilt der
neueste. Das Preisraster (Stunde oder Viertelstunde) übernimmt homi so, wie die Börse es liefert.
"""
import json
import re
import time
import urllib.request
from datetime import date, datetime, timedelta

from .konfig import BOERSE_REFRESH_S, BOERSE_URL, EZB_URL, SNB_RSS
from .zustand import lock, state


def fetch_snb():
    """{Datum: CHF pro EUR} aus dem RSS der SNB (Tageskurse 11:00, einige Tage zurück)."""
    req = urllib.request.Request(SNB_RSS, headers={"User-Agent": "homi"})
    with urllib.request.urlopen(req, timeout=20) as r:
        text = r.read().decode("utf-8", "replace")
    kurse = {d: float(k.replace(",", ".")) for k, d in
             re.findall(r"<title>CH: ([0-9]+,[0-9]+) CHF = 1 EUR (\d{4}-\d{2}-\d{2}) T", text)}
    if not kurse:
        raise ValueError("kein EUR-Kurs im SNB-RSS")
    return kurse


def fetch_ezb():
    with urllib.request.urlopen(EZB_URL, timeout=20) as r:
        return float(re.search(r"currency='CHF' rate='([0-9.]+)'", r.read().decode()).group(1))


def kurs_fuer(kurse, tag):
    """SNB-Kurs des Tages oder des letzten Werktags davor; sonst der neueste."""
    frueher = [d for d in kurse if d <= tag]
    return kurse[max(frueher)] if frueher else kurse[max(kurse)]


def fetch_boerse():
    """Day-Ahead-Preise CH (EUR/MWh) für heute bis übermorgen, in Rp./kWh zum SNB-Tageskurs."""
    today = date.today()
    url = BOERSE_URL.format(start=today.isoformat(), end=(today + timedelta(days=2)).isoformat())
    req = urllib.request.Request(url, headers={"User-Agent": "domus-energie"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    try:
        kurse, quelle_kurs = fetch_snb(), "SNB Tageskurs 11:00"
    except (OSError, ValueError) as e:                   # SNB nicht erreichbar: EZB-Referenzkurs
        print("SNB-Kurs nicht ladbar, nehme EZB:", e, flush=True)
        kurse, quelle_kurs = {today.isoformat(): fetch_ezb()}, "EZB-Referenzkurs (SNB nicht erreichbar)"
    ts, preise = data["unix_seconds"], data["price"]
    step = ts[1] - ts[0] if len(ts) > 1 else 3600
    aus = []
    for t, p in zip(ts, preise):
        if p is None:
            continue
        zeit = datetime.fromtimestamp(t).isoformat(timespec="minutes")
        k = kurs_fuer(kurse, zeit[:10])
        aus.append({"zeit": zeit, "ts": t, "eur_mwh": p, "kurs": k, "rp_kwh": round(p * k / 10, 2)})
    return {
        "kurs_eur_chf": kurse[max(kurse)],
        "kurs_quelle": quelle_kurs,
        "kurs_datum": max(kurse),
        "intervall_s": step,
        "quelle": "EPEX Spot Day-Ahead CH via Energy-Charts (Fraunhofer ISE), " + data.get("license_info", ""),
        "preise": aus,
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
        "kurs_quelle": b.get("kurs_quelle"),
        "intervall_min": b["intervall_s"] // 60,
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
