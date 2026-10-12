"""Solarprognose (Open-Meteo, mit eigenen Messwerten geeicht)."""
import json
import os
import time
import urllib.request
from datetime import date, datetime, timedelta

from . import zustand
from .archiv import archiv_aggregat
from .konfig import ANLAGE_CFG, OPEN_METEO, PROGNOSE_LOG, PROGNOSE_REFRESH_S, load_json
from .zustand import lock, state


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
        zustand.archiv.lesen(heute - timedelta(days=14), heute - timedelta(days=1)), "stunde")["zeilen"] if z["sekunden"] >= 3000}
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
            z = archiv_aggregat(zustand.archiv.lesen(date.fromisoformat(tag_), date.fromisoformat(tag_)), "tag")["summe"]
            if z["sekunden"] > 0.9 * 86400:
                e["ist_kwh"] = round(z["pv_kwh"], 1)
    log = dict(sorted(log.items())[-400:])
    tmp = PROGNOSE_LOG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=1)
    os.replace(tmp, PROGNOSE_LOG)
    return [{"tag": k, **v} for k, v in list(log.items())[-14:]]

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
