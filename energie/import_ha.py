"""Einmalige Übernahme der 5-Minuten-Werte aus Home Assistant ins 10-Minuten-Archiv von homi.

Läuft im energie-Container (nutzt die Tariflogik aus server.py):
  1. Export aus HA nach energie/data/ha_import.json (siehe README)
  2. docker exec -i energie python - < import_ha.py
Übernimmt nur Schritte vor dem ersten Live-Schritt; Zeilen bekommen quelle=ha.
"""
import json
import sys
from datetime import date, datetime

sys.path.insert(0, "/app")
import server  # noqa: E402

daten = json.load(open("/data/ha_import.json"))
reihe = {name: dict(werte) for name, werte in daten["sensoren"].items()}   # name -> {start_ts: mittel_W}
archiv = server.Archiv(server.ARCHIV_DIR)
heute = date.today()
live = [r["zeit"] for r in archiv.lesen(date.fromisoformat(daten["von"][:10]), heute) if r["quelle"] == "live"]
grenze = min(live) if live else datetime.now().isoformat(timespec="minutes")

slots = sorted({int(ts // server.SLOT_S) * server.SLOT_S for ts in reihe["verbrauch"]})
zeilen = []
for slot in slots:
    zeit = datetime.fromtimestamp(slot).isoformat(timespec="minutes")
    if zeit >= grenze:
        continue
    teile = [ts for ts in (slot, slot + 300) if ts in reihe["verbrauch"]]
    kwh = lambda name: sum(reihe[name].get(ts, 0.0) or 0.0 for ts in teile) * 300 / 3.6e6
    tarif = server.current_tariff(datetime.fromtimestamp(slot))
    bezug, einsp = kwh("netzbezug"), kwh("netzeinspeisung")
    boerse = [reihe["boerse"][ts] for ts in teile if ts in reihe.get("boerse", {})]
    zeilen.append({
        "zeit": zeit, "sekunden": 300 * len(teile),
        "pv_kwh": round(kwh("pv"), 5), "verbrauch_kwh": round(kwh("verbrauch"), 5),
        "bezug_kwh": round(bezug, 5), "einspeisung_kwh": round(einsp, 5),
        "kosten_chf": round(bezug * tarif["bezug_chf_kwh"], 5),
        "erloes_chf": round(einsp * (tarif["rueckliefer_chf_kwh"] or 0), 5),
        "boerse_rp": round(sum(boerse) / len(boerse), 3) if boerse else "",
        "quelle": "ha"})
archiv.schreiben(zeilen)
archiv.aufraeumen(heute)
print(f"{len(zeilen)} Zehn-Minuten-Schritte übernommen ({zeilen[0]['zeit']} bis {zeilen[-1]['zeit']}), Grenze {grenze}")
