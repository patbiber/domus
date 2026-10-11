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
    rows = archiv.lesen(heute - timedelta(days=ARCHIV_TAGE), heute)
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


# ---------------------------------------------------------------- Energieberater
# Richtpreise für Batteriespeicher inkl. Anschluss (Stand Oktober 2026), überschreibbar in anlage.json "batterie_preise"
BATTERIE_PREISE = {
    "2": {"chf": 800, "beispiel": "Steckerspeicher, z. B. Marstek Venus E Mini oder EcoFlow Stream (max. 600 W)"},
    "5": {"chf": 2100, "beispiel": "z. B. Marstek Venus E 4.0 (CHF 1'709 bei Alltron) plus fester Anschluss"},
    "10": {"chf": 3800, "beispiel": "z. B. Marstek Venus E 4.0 mit Erweiterung oder Zendure SolarFlow 2400 AC"},
}
BATTERIE_LEBENSDAUER_J = 12
tsd = lambda x: f"{round(x):,}".replace(",", "'")      # 7'577 (Schweizer Tausendertrennung)
berater_cache = {"zeit": 0, "daten": None}


def berater():
    """Drei Tipps mit dem grössten Nutzen in CHF pro Jahr aus den Messwerten der letzten 14 Tage, dazu eine Batterie-
    Empfehlung (Kauf) bzw. Betriebstipps (vorhandene Batterie, anlage.json "haushalt.batterie").
    Jeder Tipp erklärt sich selbst: "warum" (was homi in den Daten sieht) und "rechnung" (Rechenschritte mit Zahlen).
    "geprueft" listet alle Kandidaten mit Ergebnis, auch die nicht gezeigten."""
    if time.time() - berater_cache["zeit"] < 3600 and berater_cache["daten"]:
        return berater_cache["daten"]
    with open(ANLAGE_CFG, encoding="utf-8") as f:
        cfg = json.load(f)
    haushalt = cfg.get("haushalt") or {}
    heute = date.today()
    rows = [r for r in archiv.lesen(heute - timedelta(days=14), heute - timedelta(days=1)) if r["sekunden"]]
    tage = sum(r["sekunden"] for r in rows) / 86400
    if tage < 3:
        return {"aktiv": False, "grund": "homi braucht mindestens 3 Tage Messwerte für Tipps."}
    t = (state.get("status") or {}).get("tarif") or {}
    bezug, rueck = t.get("bezug_chf_kwh") or 0.27, t.get("rueckliefer_chf_kwh") or 0.0
    diff = bezug - rueck
    rp = lambda x: f"{x * 100:.1f}".replace(".0", "") + " Rp."
    # Stundenprofil (Mittel pro Tag) und Grundlast (Median 01–05 Uhr)
    prof = [{"v": 0.0, "pv": 0.0, "b": 0.0, "e": 0.0} for _ in range(24)]
    nacht_w, gross = [], {}
    for r in rows:
        s = int(r["zeit"][11:13])
        for k, q in (("v", "verbrauch_kwh"), ("pv", "pv_kwh"), ("b", "bezug_kwh"), ("e", "einspeisung_kwh")):
            prof[s][k] += r[q] / tage
        w = r["verbrauch_kwh"] * 3.6e6 / r["sekunden"]
        if 1 <= s < 5 and r["sekunden"] >= 500:
            nacht_w.append(w)
        if (s >= 22 or s < 6) and w >= 600:          # grosser Verbraucher nachts (Boiler, Heizung …)
            gross[r["zeit"][:10]] = gross.get(r["zeit"][:10], 0) + r["verbrauch_kwh"]
    nacht_w.sort()
    grund_w = nacht_w[len(nacht_w) // 2] if nacht_w else 0
    std_ohne = sum(1 for p in prof if p["pv"] < p["v"])
    ohne_sonne = std_ohne / 24
    spitze = max(range(24), key=lambda s: prof[s]["b"])
    basis = f"Messwerte der letzten {round(tage)} Tage ({(heute - timedelta(days=14)).strftime('%d.%m.')}–{(heute - timedelta(days=1)).strftime('%d.%m.%Y')})"
    tipps, geprueft = [], []

    # 1) Grundlast
    if grund_w >= 50:
        ziel = max(40, round(grund_w * 0.7 / 5) * 5)
        kwh = (grund_w - ziel) * 8.76
        wert = ohne_sonne * bezug + (1 - ohne_sonne) * rueck
        tipps.append({"id": "grundlast", "titel": "Grundlast senken", "chf_jahr": round(kwh * wert), "kwh_jahr": round(kwh),
            "text": f"Dein Haus braucht auch nachts rund {grund_w:.0f} W – das sind {tsd(grund_w * 8.76)} kWh im Jahr nur für "
                    f"Dauerläufer und Geräte im Bereitschaftsmodus. Ziel: {ziel} W.",
            "warum": "Zwischen 1 und 5 Uhr schläft das Haus. Was dann noch Strom braucht, läuft rund um die Uhr – "
                     "jedes Watt Dauerlast kostet 8.8 kWh im Jahr, auch wenn niemand etwas davon hat. Nachts kommt "
                     f"dieser Strom aus dem Netz zu {rp(bezug)}, tagsüber verdrängt er Einspeisung zu {rp(rueck)}",
            "rechnung": [f"Grundlast = Median aller 10-Minuten-Werte zwischen 1 und 5 Uhr: {grund_w:.0f} W",
                         f"Ziel 30 % weniger: {ziel} W → {grund_w - ziel:.0f} W × 8'760 Stunden = {tsd(kwh)} kWh/Jahr",
                         f"{std_ohne} von 24 Stunden ohne Sonnenüberschuss zu {rp(bezug)}, {24 - std_ohne} Stunden zu {rp(rueck)} "
                         f"→ im Mittel {rp(wert)} pro kWh",
                         f"{tsd(kwh)} kWh × {rp(wert)} = CHF {round(kwh * wert)} pro Jahr"],
            "aktion": "Mit einem Steckdosen-Messgerät (ca. CHF 20) die grössten Dauerverbraucher suchen: alte Kühl- und "
                      "Gefriergeräte, Router und Netzwerk, Umwälzpumpe, TV und Spielkonsole im Standby. "
                      "Was nachts nicht laufen muss, mit Steckdosenleiste oder Zeitschaltuhr ausschalten."})
    else:
        geprueft.append({"titel": "Grundlast senken", "ergebnis": f"nicht nötig – nachts nur {grund_w:.0f} W"})

    # 2) Grosser Verbraucher nachts -> in die Mittagssonne
    kwh_nacht = sum(gross.values()) / tage if gross else 0
    if kwh_nacht >= 0.3 and haushalt.get("boiler_elektrisch") is not False:
        tipps.append({"id": "nachtlast", "titel": "Nachtverbraucher in den Mittag legen",
            "chf_jahr": round(kwh_nacht * 365 * diff), "kwh_jahr": round(kwh_nacht * 365),
            "text": f"Zwischen 22 und 6 Uhr läuft an {len(gross)} von {round(tage)} Tagen ein grosser Verbraucher "
                    f"(über 600 W, im Mittel {kwh_nacht:.1f} kWh pro Nacht) – typisch für einen Boiler oder eine Heizung.",
            "warum": f"Nachts kommt jede kWh aus dem Netz zu {rp(bezug)}. Läuft derselbe Verbraucher mittags, deckt ihn "
                     f"deine Anlage – statt dass du den Strom für {rp(rueck)} einspeist.",
            "rechnung": [f"10-Minuten-Werte über 600 W zwischen 22 und 6 Uhr: an {len(gross)} von {round(tage)} Tagen",
                         f"im Mittel {kwh_nacht:.2f} kWh pro Nacht × 365 = {tsd(kwh_nacht * 365)} kWh/Jahr",
                         f"Vorteil pro verschobene kWh: {rp(bezug)} − {rp(rueck)} = {rp(diff)}",
                         f"{tsd(kwh_nacht * 365)} kWh × {rp(diff)} = CHF {round(kwh_nacht * 365 * diff)} pro Jahr"],
            "aktion": "Prüfen, was nachts läuft. Ist es der Boiler: Ladezeit auf 11–15 Uhr umstellen lassen "
                      "(Zeitschaltuhr bzw. Rundsteuerung) – dann heizt ihn deine Sonne statt das Netz."})
    else:
        geprueft.append({"titel": "Nachtverbraucher in den Mittag legen",
                         "ergebnis": "kein grosser Verbraucher (über 600 W) nachts gefunden" if kwh_nacht < 0.3 else "laut Angabe kein Elektroboiler"})

    # 3) Überschuss für Warmwasser / E-Auto / Wärmepumpe nutzen
    try:
        am = amortisation()
    except (OSError, ValueError, KeyError):
        am = amort_cache["daten"] or {}
    if am.get("aktiv"):
        ev = am.get("eigenverbrauch_pct", 0) / 100
        ueberschuss_jahr = am.get("jahresertrag_kwh", 0) * (1 - ev)
        quelle_u = (f"Jahresertrag {tsd(am['jahresertrag_kwh'])} kWh ({am.get('jahresertrag_quelle', 'Fronius')}) − "
                    f"Eigenverbrauch {am['eigenverbrauch_pct']:.1f} % = Überschuss {tsd(ueberschuss_jahr)} kWh/Jahr")
    else:
        ev = None
        ueberschuss_jahr = sum(p["e"] for p in prof) * 365
        quelle_u = f"Einspeisung im Mittel {sum(p['e'] for p in prof):.1f} kWh/Tag × 365 = {tsd(ueberschuss_jahr)} kWh/Jahr"
    kein_abnehmer = haushalt.get("boiler_elektrisch") is False and not haushalt.get("e_auto") and not haushalt.get("waermepumpe")
    if ueberschuss_jahr > 1000 and not kein_abnehmer:
        nutzbar = min(ueberschuss_jahr * 0.4, 2500)
        bedingung = None if haushalt.get("boiler_elektrisch") or haushalt.get("e_auto") or haushalt.get("waermepumpe") else \
            "falls dein Warmwasser elektrisch erzeugt wird oder du ein E-Auto hast"
        tipps.append({"id": "ueberschuss", "titel": "Sonnenüberschuss selbst nutzen",
            "chf_jahr": round(nutzbar * diff), "kwh_jahr": round(nutzbar), "bedingung": bedingung,
            "text": f"Du speist rund {tsd(ueberschuss_jahr)} kWh im Jahr für {rp(rueck)} ein. Selbst genutzt ist jede "
                    f"kWh {rp(bezug)} wert – {rp(diff)} mehr.",
            "warum": (f"Nur {ev * 100:.0f} % deines Solarstroms nutzt du selbst, der Rest geht ins Netz. " if ev is not None else "")
                     + "Mittags produziert die Anlage ein Vielfaches dessen, was das Haus gleichzeitig braucht. Ein "
                     "Verbraucher, der Energie speichern kann (Warmwasser, Auto-Akku, Gebäude), nimmt diesen Überschuss auf.",
            "rechnung": [quelle_u,
                         "Annahme: 40 % davon lassen sich nutzen, höchstens 2'500 kWh (etwa der Warmwasserbedarf eines "
                         f"4-Personen-Haushalts mit Elektroboiler) → {tsd(nutzbar)} kWh/Jahr",
                         f"Vorteil pro kWh: {rp(bezug)} − {rp(rueck)} = {rp(diff)}",
                         f"{tsd(nutzbar)} kWh × {rp(diff)} = CHF {round(nutzbar * diff)} pro Jahr"],
            "aktion": "Elektroboiler mit einem Überschuss-Regler (z. B. Fronius Ohmpilot, ca. CHF 900 inkl. Einbau) nur mit "
                      "Sonnenstrom heizen; E-Auto tagsüber laden; Wärmepumpe mittags vorheizen lassen."})
    else:
        geprueft.append({"titel": "Sonnenüberschuss selbst nutzen",
                         "ergebnis": "kaum Überschuss" if ueberschuss_jahr <= 1000 else "laut Angabe kein passender Verbraucher"})

    # 4) Verbrauch verschieben (Waschen, Spülen, Trocknen)
    abend = sum(max(0.0, p["b"] - grund_w / 1000) for p in prof)
    kwh = abend * 0.4 * 365
    if kwh > 30:
        f = ((state.get("prognose") or {}).get("morgen") or {}).get("bestes_fenster")
        tipps.append({"id": "verschieben", "titel": "Grosse Geräte in die Sonnenstunden",
            "chf_jahr": round(kwh * diff), "kwh_jahr": round(kwh),
            "text": f"Am meisten Strom aus dem Netz brauchst du zwischen {spitze} und {spitze + 1} Uhr. Rund "
                    f"{abend:.1f} kWh pro Tag kommen über die Grundlast hinaus aus dem Netz.",
            "warum": "Abends und nachts kaufst du Strom ein, mittags speist du Überschuss ein. Geräte mit Startzeitvorwahl "
                     "lassen sich ohne Komfortverlust in die Sonnenstunden verschieben.",
            "rechnung": [f"Netzbezug pro Stunde, abzüglich Grundlast ({grund_w:.0f} W), Mittel der letzten {round(tage)} Tage: "
                         f"{abend:.2f} kWh pro Tag",
                         f"Grösster Netzbezug: {spitze}–{spitze + 1} Uhr mit {prof[spitze]['b']:.2f} kWh",
                         f"Annahme: 40 % davon sind verschiebbar (Waschen, Spülen, Trocknen) → {tsd(kwh)} kWh/Jahr",
                         f"{tsd(kwh)} kWh × {rp(diff)} Vorteil = CHF {round(kwh * diff)} pro Jahr"],
            "aktion": "Waschmaschine, Geschirrspüler und Tumbler mit Startzeitvorwahl über Mittag laufen lassen"
                      + (f" – morgen am besten {f['von']}–{f['bis']} Uhr." if f else ".")})
    else:
        geprueft.append({"titel": "Grosse Geräte in die Sonnenstunden", "ergebnis": "kaum Netzbezug über der Grundlast"})

    # 5) Akut: negative Preise in Sicht
    plan = einspeise_plan()
    tage_minus = [(n, d) for n, d in (("heute", plan.get("heute")), ("morgen", plan.get("morgen"))) if d and d.get("minuspreis_bloecke")]
    if tage_minus:
        n, d = tage_minus[0]
        tief = min(x["preis_rp"] for x in d["stunden"] if x["stunde"] in d["minuspreis_stunden"])
        tipps.insert(0, {"id": "minuspreis", "titel": f"{n.capitalize()} negative Strompreise nutzen", "akut": True,
            "chf_jahr": None, "kwh_jahr": None,
            "text": f"{n.capitalize()} {' und '.join(d['minuspreis_bloecke'])} liegt der Börsenpreis unter null.",
            "warum": "In diesen Stunden gibt es an der Börse mehr Strom als Abnehmer. Wer dann verbraucht, entlastet das "
                     "Netz; mit einem dynamischen Einspeisetarif kostet Einspeisen in dieser Zeit sogar Geld.",
            "rechnung": [f"Day-Ahead-Preise Schweiz (EPEX Spot via Energy-Charts) für {n}",
                         f"Stunden mit Preis unter null: {', '.join(d['minuspreis_bloecke'])}, tiefster Preis {tief:.1f} Rp./kWh",
                         f"Erwarteter Überschuss in dieser Zeit: {d.get('ueberschuss_bei_minuspreis_kwh', 0):.1f} kWh"],
            "aktion": "Grosse Verbraucher genau in diese Stunden legen. Mit dynamischem Einspeisetarif: Einspeisung drosseln."})
    else:
        geprueft.append({"titel": "Negative Strompreise", "ergebnis": "heute und morgen keine"})

    akut = [x for x in tipps if x.get("akut")]
    rest = sorted((x for x in tipps if not x.get("akut")), key=lambda x: -(x["chf_jahr"] or 0))
    drei = (akut + rest)[:3]
    for x in (akut + rest)[3:]:
        geprueft.insert(0, {"titel": x["titel"], "ergebnis": f"CHF {x['chf_jahr']}/Jahr – weniger als die drei gezeigten"})
    summe = sum(x["chf_jahr"] or 0 for x in drei)
    monate_frueher = None
    if am.get("aktiv") and am.get("pro_jahr_chf") and am.get("rest_chf"):
        monate_frueher = round(12 * (am["rest_chf"] / am["pro_jahr_chf"] - am["rest_chf"] / (am["pro_jahr_chf"] + summe)))
    auswahl = ("homi rechnet fünf mögliche Massnahmen mit deinen Messwerten und deinem Tarif durch und zeigt die drei mit "
               "dem grössten Nutzen in Franken pro Jahr. Hinweise auf negative Strompreise stehen immer zuoberst. "
               "Annahmen (z. B. wie viel sich verschieben lässt) sind bewusst vorsichtig und bei jedem Tipp genannt.")
    # Batterie
    preise = {**BATTERIE_PREISE, **(cfg.get("batterie_preise") or {})}
    vorhanden = haushalt.get("batterie")
    if vorhanden:
        batterie = {"modus": "optimieren", "titel": f"Deine Batterie ({vorhanden.get('kapazitaet_kwh', '?')} kWh) optimal nutzen",
            "warum": "Eine Batterie bringt nur Geld, wenn sie Sonnenstrom vom Mittag in den Abend verschiebt. Jede "
                     "Ladung aus dem Netz oder ungenutzte Kapazität schmälert den Nutzen.",
            "tipps": ["Nur mit Sonnenüberschuss laden, nie aus dem Netz – bei fixem Tarif kostet das nur Wirkungsgrad.",
                      "Notstrom-Reserve klein halten (10–20 %), sonst bleibt teure Kapazität ungenutzt.",
                      f"Die Batterie für den Abend aufsparen: Am meisten Netzstrom brauchst du um {spitze} Uhr.",
                      "Bei negativen Börsenpreisen (und dynamischem Tarif) gezielt aus dem Netz laden – homi warnt dich am Morgen."]}
    else:
        try:
            sim = speicher_sim.report()
        except Exception:                      # Simulation ist Beiwerk
            sim = {"speicher": []}
        optionen = []
        for s in sim.get("speicher", []):
            p = preise.get(str(int(s["kapazitaet_kwh"])))
            if not p or not s.get("ersparnis_pro_jahr_chf"):
                continue
            jahre = p["chf"] / s["ersparnis_pro_jahr_chf"]
            optionen.append({"kwh": s["kapazitaet_kwh"], "name": s["name"], "preis_chf": p["chf"], "beispiel": p["beispiel"],
                             "ersparnis_jahr_chf": round(s["ersparnis_pro_jahr_chf"]), "amortisation_jahre": round(jahre, 1),
                             "zyklen": s.get("zyklen"), "autarkie_plus_pct": s.get("autarkie_plus_pct")})
        beste = min(optionen, key=lambda o: o["amortisation_jahre"]) if optionen else None
        tage_sim = round((sim.get("speicher") or [{}])[0].get("tage_gemessen") or 0)
        if not beste:
            urteil, text = "noch offen", "Für eine Empfehlung braucht homi noch ein paar Tage Messwerte."
        elif beste["amortisation_jahre"] <= 8:
            urteil, text = "lohnt sich", (f"Eine {beste['kwh']}-kWh-Batterie spart dir rund CHF {beste['ersparnis_jahr_chf']} im Jahr "
                f"und ist nach etwa {beste['amortisation_jahre']:.0f} Jahren bezahlt. Beispiel: {beste['beispiel']}.")
        elif beste["amortisation_jahre"] <= BATTERIE_LEBENSDAUER_J:
            urteil, text = "knapp", (f"Am besten rechnet sich eine {beste['kwh']}-kWh-Batterie: rund CHF {beste['ersparnis_jahr_chf']} "
                f"im Jahr, bezahlt nach etwa {beste['amortisation_jahre']:.0f} Jahren – knapp innerhalb der Lebensdauer "
                f"(10–15 Jahre). Beispiel: {beste['beispiel']}. Vorher lohnen sich die Tipps oben mehr.")
        else:
            urteil, text = "lohnt sich nicht", (f"Eine Batterie würde dir höchstens rund CHF {beste['ersparnis_jahr_chf']} im Jahr "
                f"sparen ({beste['kwh']} kWh), kostet aber rund CHF {tsd(beste['preis_chf'])}. Bezahlt wäre sie erst nach "
                f"{beste['amortisation_jahre']:.0f} Jahren – länger, als sie hält. Neu rechnen lohnt sich mit einem "
                f"dynamischen Einspeisetarif, einem E-Auto oder wenn Speicher günstiger werden.")
        warum = (f"Seit {tage_sim} Tagen läuft in homi eine Simulation mit drei Batterien (2, 5 und 10 kWh) und deinen echten "
                 "Messwerten: Jeder Überschuss lädt sie, jeder Netzbezug entlädt sie. So zeigt sich, was eine Batterie bei "
                 "deinem Verbrauch wirklich bringen würde – nicht bei einem Durchschnittshaushalt.")
        if beste and (beste.get("zyklen") or 0) > 0 and beste["kwh"] < max(o["kwh"] for o in optionen):
            gross_o = max(optionen, key=lambda o: o["kwh"])
            warum += (f" Grössere Batterien sparen zwar etwas mehr ({gross_o['kwh']} kWh: CHF {gross_o['ersparnis_jahr_chf']} "
                      f"statt {beste['ersparnis_jahr_chf']}), kosten aber ein Vielfaches: Dein Haus braucht abends und "
                      "nachts nur wenig Strom, darum wird die zusätzliche Kapazität selten ganz genutzt.")
        rechnung = ["Ersparnis = vermiedener Netzbezug × Bezugstarif − entgangene Einspeisevergütung, mit 90 % Wirkungsgrad "
                    "(Laden und Entladen)",
                    "Hochrechnung aufs Jahr aus den letzten 7 simulierten Tagen",
                    "Bezahlt nach = Richtpreis inkl. Anschluss ÷ Ersparnis pro Jahr"]
        rechnung += [f"{o['kwh']} kWh: CHF {tsd(o['preis_chf'])} ÷ CHF {o['ersparnis_jahr_chf']}/Jahr = {o['amortisation_jahre']:.1f} Jahre"
                     for o in optionen]
        rechnung.append(f"Urteil: bis 8 Jahre «lohnt sich», bis {BATTERIE_LEBENSDAUER_J} Jahre «knapp» (Lebensdauer 10–15 "
                        "Jahre), darüber «lohnt sich nicht»")
        batterie = {"modus": "kaufen", "urteil": urteil, "text": text, "optionen": optionen, "warum": warum, "rechnung": rechnung,
                    "beste_kwh": beste["kwh"] if beste else None, "tage_gemessen": tage_sim,
                    "hinweis": f"Richtpreise inkl. Anschluss, Stand Oktober 2026. Im Winter gibt es weniger Überschuss, "
                               f"im Sommer mehr – die Empfehlung wird mit jedem Monat genauer."}
    daten = {"aktiv": True, "stand": datetime.now().isoformat(timespec="minutes"), "tage": round(tage, 1), "basis": basis,
             "auswahl": auswahl, "grundlast_w": round(grund_w), "spitze_stunde": spitze,
             "tarif": {"bezug_rp": round(bezug * 100, 2), "rueck_rp": round(rueck * 100, 2)},
             "tipps": drei, "geprueft": geprueft, "summe_chf_jahr": summe, "monate_frueher": monate_frueher, "batterie": batterie}
    berater_cache.update(zeit=time.time(), daten=daten)
    return daten


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
        if path == "/api/berater":
            try:
                return self.send_json(berater())
            except (OSError, ValueError, KeyError, ZeroDivisionError) as e:
                print("Berater:", e, flush=True)
                return self.send_json(berater_cache["daten"] or {"aktiv": False, "grund": "Berechnung gerade nicht möglich"})
        if path == "/api/amortisation":
            try:
                return self.send_json(amortisation())
            except (OSError, ValueError, KeyError) as e:      # Fronius nachts aus o. Ä.: letzte Berechnung zeigen
                print("Amortisation:", e, flush=True)
                return self.send_json(amort_cache["daten"] or {"aktiv": False, "fehler": "Zähler nicht erreichbar"})
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
