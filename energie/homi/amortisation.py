"""Amortisation der Anlage: bisher erwirtschaftet und Break-even."""
import json
import time
import urllib.request
from datetime import date, timedelta

from . import zustand
from .konfig import ANLAGE_CFG, ARCHIV_TAGE, FRONIUS, tsd
from .zustand import state


# Typischer Anteil am Jahresertrag pro Monat (Schweizer Mittelland), um den Jahresertrag aus dem bisherigen Jahr hochzurechnen
PV_MONATSANTEIL = [.03, .05, .08, .11, .12, .13, .135, .12, .09, .06, .035, .025]

amort_cache = {"zeit": 0, "daten": None}

def amortisation():
    """Break-even der Anlage: bisher erwirtschaftet (Zähler seit Inbetriebnahme) und Hochrechnung bis zur Amortisation.
    Wert einer kWh: selbst verbraucht = Bezugstarif (gesparter Einkauf), eingespeist = Rücklieferung. Heutige Tarife,
    ohne Alterung der Module und ohne Preisänderungen. Einstellungen in anlage.json (investition_chf, foerderung_chf,
    inbetriebnahme, jahresertrag_kwh)."""
    if time.time() - amort_cache["zeit"] < 600 and amort_cache["daten"]:
        return amort_cache["daten"]
    with open(ANLAGE_CFG, encoding="utf-8") as f:
        cfg = json.load(f)
    if not cfg.get("investition_chf"):
        return {"aktiv": False}
    base = f"http://{FRONIUS}/solar_api/v1/"
    with urllib.request.urlopen(base + "GetInverterRealtimeData.cgi?Scope=Device&DeviceId=1&DataCollection=CommonInverterData", timeout=6) as r:
        wr = json.load(r)["Body"]["Data"]
    with urllib.request.urlopen(base + "GetMeterRealtimeData.cgi?Scope=System", timeout=6) as r:
        zaehler = list(json.load(r)["Body"]["Data"].values())
    prod_kwh = wr["TOTAL_ENERGY"]["Value"] / 1000
    jahr_kwh = wr["YEAR_ENERGY"]["Value"] / 1000
    verbrauch_kwh = sum(z.get("EnergyReal_WAC_Sum_Consumed") or 0 for z in zaehler if z.get("Meter_Location_Current") == 1) / 1000
    # Autarkie aus dem homi-Archiv (Anteil des Verbrauchs, den die Anlage direkt deckt)
    heute = date.today()
    rows = zustand.archiv.lesen(heute - timedelta(days=ARCHIV_TAGE), heute)
    v = sum(r["verbrauch_kwh"] for r in rows)
    b = sum(r["bezug_kwh"] for r in rows)
    autarkie = max(0.0, min(1.0, 1 - b / v)) if v > 1 else 0.3
    eigen_kwh = min(prod_kwh, verbrauch_kwh * autarkie)
    eigen_anteil = eigen_kwh / prod_kwh if prod_kwh else 0
    t = (state.get("status") or {}).get("tarif") or {}
    bezug, rueck = t.get("bezug_chf_kwh") or 0, t.get("rueckliefer_chf_kwh") or 0
    wert_kwh = eigen_anteil * bezug + (1 - eigen_anteil) * rueck
    # Jahresertrag: Angabe > aus Inbetriebnahme > Hochrechnung des laufenden Jahres > 1000 kWh pro kWp
    inbetrieb = cfg.get("inbetriebnahme")
    if cfg.get("jahresertrag_kwh"):
        jahr_ertrag, quelle = cfg["jahresertrag_kwh"], "Angabe"
    elif inbetrieb:
        jahre = (heute - date.fromisoformat(inbetrieb)).days / 365.25
        jahr_ertrag, quelle = prod_kwh / jahre, "Zähler seit Inbetriebnahme"
    else:
        anteil = sum(PV_MONATSANTEIL[:heute.month - 1]) + PV_MONATSANTEIL[heute.month - 1] * heute.day / 31
        if anteil >= .3:
            jahr_ertrag, quelle = jahr_kwh / anteil, f"hochgerechnet aus {tsd(jahr_kwh)} kWh seit 1. Januar"
        else:
            jahr_ertrag, quelle = (cfg.get("max_kw") or 0) * 1000, "1000 kWh pro kWp"
    if not inbetrieb and jahr_ertrag:
        inbetrieb = (heute - timedelta(days=round(prod_kwh / jahr_ertrag * 365.25))).isoformat()
        inbetrieb_geschaetzt = True
    else:
        inbetrieb_geschaetzt = False
    netto = cfg["investition_chf"] - (cfg.get("foerderung_chf") or 0)
    bisher = prod_kwh * wert_kwh
    pro_jahr = jahr_ertrag * wert_kwh
    rest = max(0.0, netto - bisher)
    rest_jahre = rest / pro_jahr if pro_jahr else None
    break_even = (heute + timedelta(days=round(rest_jahre * 365.25))).isoformat() if rest_jahre is not None else None
    gesamt = ((date.fromisoformat(break_even) - date.fromisoformat(inbetrieb)).days / 365.25) if break_even and inbetrieb else None
    daten = {
        "aktiv": True, "investition_chf": cfg["investition_chf"], "foerderung_chf": cfg.get("foerderung_chf") or 0,
        "netto_chf": netto, "produktion_kwh": round(prod_kwh), "verbrauch_kwh": round(verbrauch_kwh),
        "eigenverbrauch_kwh": round(eigen_kwh), "eigenverbrauch_pct": round(100 * eigen_anteil, 1),
        "autarkie_pct": round(100 * autarkie, 1), "wert_rp_kwh": round(100 * wert_kwh, 2),
        "bezug_rp_kwh": round(100 * bezug, 2), "rueck_rp_kwh": round(100 * rueck, 2),
        "bisher_chf": round(bisher), "pro_jahr_chf": round(pro_jahr), "jahresertrag_kwh": round(jahr_ertrag),
        "jahresertrag_quelle": quelle, "inbetriebnahme": inbetrieb, "inbetriebnahme_geschaetzt": inbetrieb_geschaetzt,
        "amortisiert_pct": round(min(100.0, 100 * bisher / netto), 1) if netto else 100.0, "rest_chf": round(rest),
        "rest_jahre": round(rest_jahre, 1) if rest_jahre is not None else None, "break_even": break_even,
        "gesamtdauer_jahre": round(gesamt, 1) if gesamt else None,
    }
    amort_cache.update(zeit=time.time(), daten=daten)
    return daten
