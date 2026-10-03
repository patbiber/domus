#!/usr/bin/env python3
"""Abendmail (Timer homi-prognose, 19:00): Solarprognose für morgen, bestes Zeitfenster, Treffer von heute."""
import json
import subprocess
import urllib.request
from datetime import date

API = "http://127.0.0.1:8099/api/"
p = json.load(urllib.request.urlopen(API + "prognose", timeout=20))
st = json.load(urllib.request.urlopen(API + "status", timeout=20))
if "morgen" not in p:
    raise SystemExit("Prognose noch nicht verfügbar")
m, ue = p["morgen"], p["uebermorgen"]
WT = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
tag = lambda d: f"{WT[date.fromisoformat(d).weekday()]}, {int(d[8:])}.{int(d[5:7])}."
symbol = "☀️" if m["kwh"] >= 20 else "⛅" if m["kwh"] >= 8 else "☁️"
f = m["bestes_fenster"]
betreff = f"homi: Morgen {symbol} {m['kwh']:.0f} kWh" + (f" – beste Zeit {f['von'][:2]}–{f['bis'][:2]} Uhr" if f else "")

t = st.get("tarif") or {}
vorteil = (t.get("bezug_chf_kwh", 0) - (t.get("rueckliefer_chf_kwh") or 0)) * 100
spruch = ("Die Panels haben morgen Hochbetrieb. Ich poliere schon mal das Sparschwein."
          if m["kwh"] >= 20 else "Gemischtes Wetter. Die Panels geben ihr Bestes."
          if m["kwh"] >= 8 else "Morgen eher grau. Die Panels machen einen Ruhetag, ich nicht.")
zeilen = [
    f"Hallo Patrick, hier ist homi mit der Solarprognose.", "",
    f"MORGEN, {tag(m['datum'])}",
    f"  erwartet:      {m['kwh']:.1f} kWh  (Spitze {m['spitze_kw']:.1f} kW, Bewölkung ca. {m['wolken_pct'] or 0} %)",
]
if f:
    zeilen += [f"  beste Zeit:    {f['von']}–{f['bis']} Uhr (ca. {f['kwh']:.1f} kWh in 2 Stunden)",
               f"                -> ideal für Waschmaschine, Tumbler, Geschirrspüler oder E-Bike.",
               f"                   Jede selbst genutzte kWh bringt {vorteil:.1f} Rp. mehr als Einspeisen."]
zeilen += ["", f"Übermorgen, {tag(ue['datum'])}: {ue['kwh']:.1f} kWh erwartet", ""]
gestern = [x for x in p.get("treffer", []) if "ist_kwh" in x]
heute_ist = st.get("pv_heute_kwh")
heute_prog = next((x["prognose_kwh"] for x in p.get("treffer", []) if x["tag"] == date.today().isoformat()), None)
if heute_ist is not None:
    zeilen.append(f"Heute erzeugt: {heute_ist:.1f} kWh" + (f" (Prognose von gestern Abend: {heute_prog:.1f} kWh)" if heute_prog else ""))
if len(gestern) >= 3:
    fehler = sum(abs(x["prognose_kwh"] - x["ist_kwh"]) / x["ist_kwh"] for x in gestern if x["ist_kwh"]) / len(gestern)
    zeilen.append(f"Treffsicherheit der letzten {len(gestern)} Tage: im Mittel ±{100 * fehler:.0f} %")
zeilen += ["", spruch, "", "Dein homi · https://home.biber.solar",
           "Prognose: Open-Meteo (CC BY 4.0), geeicht mit deinen eigenen Messwerten."]
subprocess.run(["mail", "-s", betreff, "root"], input="\n".join(zeilen).encode(), check=True)
print(betreff)
