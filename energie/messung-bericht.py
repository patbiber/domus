#!/usr/bin/env python3
"""Bericht zu einer Messperiode aus energie/messungen.json (Daten: 10-Minuten-Archiv von homi), per Mail.
Aufruf: messung-bericht.py "<name>" [--nur-anzeigen]"""
import json
import statistics
import subprocess
import sys
import urllib.request
from datetime import datetime

API = "http://127.0.0.1:8099/api/"
name = sys.argv[1] if len(sys.argv) > 1 else "Grundlast Abwesenheit"
m = next(x for x in json.load(open(__file__.rsplit("/", 1)[0] + "/messungen.json", encoding="utf-8")) if x["name"] == name)


def zeilen(von, bis):
    d = json.load(urllib.request.urlopen(f"{API}archiv?von={von[:10]}&bis={bis[:10]}&aufloesung=10min", timeout=30))
    return [z for z in d["zeilen"] if von <= z["zeit"] < bis and z["sekunden"] >= 300]


def watt(z):
    return z["verbrauch_kwh"] * 3.6e6 / z["sekunden"]


def kennzahlen(rows):
    sek = sum(z["sekunden"] for z in rows)
    return {"mittel_w": sum(z["verbrauch_kwh"] for z in rows) * 3.6e6 / sek if sek else 0, "stunden": sek / 3600}


rows = zeilen(m["von"], m["bis"])
if not rows:
    sys.exit("keine Daten im Zeitraum")
k = kennzahlen(rows)
soll_h = (datetime.fromisoformat(min(m["bis"], datetime.now().isoformat(timespec="minutes"))) - datetime.fromisoformat(m["von"])).total_seconds() / 3600
nacht = [watt(z) for z in rows if "01" <= z["zeit"][11:13] < "05"]
alle = sorted(rows, key=watt)
st = json.load(urllib.request.urlopen(API + "status", timeout=20))
tarif = (st.get("tarif") or {}).get("bezug_chf_kwh", 0.2668)
server_w = (st.get("server") or {}).get("leistung_w")
jahr_kwh = k["mittel_w"] * 8.76

tage = {}
for z in rows:
    t = tage.setdefault(z["zeit"][:10], [0.0, 0.0, 0.0])
    t[0] += z["verbrauch_kwh"]; t[1] += z["sekunden"]; t[2] += z["bezug_kwh"]
profil = {}
for z in rows:
    profil.setdefault(int(z["zeit"][11:13]), []).append(watt(z))

v = m.get("vergleich")
vk = kennzahlen(zeilen(v["von"], v["bis"])) if v else None

WT = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
out = [f"homi misst den Grundbedarf des Hauses: {m['notiz']}", "",
       f"Zeitraum: {m['von'].replace('T', ' ')} bis {m['bis'].replace('T', ' ')}  "
       f"(gemessen {k['stunden']:.0f} von {soll_h:.0f} Stunden)", "",
       "ERGEBNIS",
       f"  Grundlast im Mittel:        {k['mittel_w']:.0f} W",
       f"  Nachts (01–05 Uhr, Median): {statistics.median(nacht):.0f} W" if nacht else "",
       f"  Tiefster 10-min-Wert:       {watt(alle[0]):.0f} W  ({alle[0]['zeit'].replace('T', ' ')})",
       f"  Pro Tag:                    {k['mittel_w'] * 24 / 1000:.1f} kWh",
       f"  Hochgerechnet aufs Jahr:    {jahr_kwh:.0f} kWh = CHF {jahr_kwh * tarif:.0f} (zu {tarif * 100:.2f} Rp./kWh)",
       f"  davon homi-Server:          ca. {server_w:.1f} W ({100 * server_w / k['mittel_w']:.0f} %)" if server_w and k["mittel_w"] else "",
       ""]
if vk and vk["stunden"]:
    out += [f"Zum Vergleich bewohnt ({v['von'][:10]} bis {v['bis'][:10]}): {vk['mittel_w']:.0f} W im Mittel",
            f"  -> Grundlast = {100 * k['mittel_w'] / vk['mittel_w']:.0f} % des normalen Verbrauchs.", ""]
out += ["PRO TAG                verbraucht   davon aus dem Netz"]
for t, (kwh, sek, bez) in sorted(tage.items()):
    out.append(f"  {WT[datetime.fromisoformat(t).weekday()]} {t[8:10]}.{t[5:7]}.  {kwh:6.2f} kWh    {bez:5.2f} kWh   ({sek / 3600:.0f} h gemessen)")
out += ["", "TAGESPROFIL (Mittel pro Stunde)"]
for h in range(0, 24, 3):
    teile = [f"{hh:02d}h {statistics.mean(profil[hh]):4.0f} W" for hh in range(h, h + 3) if hh in profil]
    out.append("  " + "   ".join(teile))
out += ["", "SPITZEN (höchste 10-min-Werte – was springt da an?)"]
for z in sorted(rows, key=watt, reverse=True)[:5]:
    out.append(f"  {WT[datetime.fromisoformat(z['zeit']).weekday()]} {z['zeit'][8:10]}.{z['zeit'][5:7]}. {z['zeit'][11:16]}  {watt(z):5.0f} W")
out += ["", "Dein homi · Logbuch: https://home.biber.solar/logbuch.html#woche"]
text = "\n".join(x for x in out if x is not None)
if "--nur-anzeigen" in sys.argv:
    print(text)
else:
    subprocess.run(["mail", "-s", f"homi: Grundbedarf des Hauses = {k['mittel_w']:.0f} W ({jahr_kwh:.0f} kWh/Jahr)", "root"],
                   input=text.encode(), check=True)
    print("Bericht gesendet")
