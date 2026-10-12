"""Stromtarif der GWS: Bezugs- und Rücklieferpreis zum aktuellen Zeitpunkt."""
import os
from datetime import datetime

from .konfig import TARIFE_JSON, TARIF_CFG, WEEKDAYS, load_json


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
