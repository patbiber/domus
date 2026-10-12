#!/usr/bin/env python3
"""Wochenbericht per Mail (Montag 08:00, homi-wochenbericht.service): die letzte Woche Mo–So mit Vorwochen-Vergleich."""
import json
import subprocess
import urllib.request

w = json.load(urllib.request.urlopen("http://127.0.0.1:8099/api/wochenbericht", timeout=60))
if not w.get("aktiv"):
    raise SystemExit(f"kein Wochenbericht: {w.get('grund')}")
chf = lambda x: f"{'+' if x >= 0 else '−'}CHF {abs(x):.2f}"
v = w.get("vergleich")
zeilen = ["Hallo Patrick, hier ist homi mit deiner Woche.", "", w["titel"].upper(), "", w["satz"], "",
          f"  Erzeugt:         {w['pv_kwh']:7.1f} kWh" + (f"   (Vorwoche {v['vorwoche_pv_kwh']:.1f} kWh, {v['pv_pct']:+d} %)".replace("-", "−") if v and v["pv_pct"] is not None else ""),
          f"  Verbraucht:      {w['verbrauch_kwh']:7.1f} kWh" + (f"   ({v['verbrauch_pct']:+d} %)".replace("-", "−") if v and v["verbrauch_pct"] is not None else ""),
          f"  Eingespeist:     {w['einspeisung_kwh']:7.1f} kWh   Erlös  CHF {w['erloes_chf']:.2f}",
          f"  Aus dem Netz:    {w['bezug_kwh']:7.1f} kWh   Kosten CHF {w['kosten_chf']:.2f}",
          f"  Bilanz:          {chf(w['saldo_chf'])}" + (f"   (Vorwoche {chf(v['vorwoche_saldo_chf'])})" if v else ""),
          f"  Autarkie:        {w['autarkie_pct']} %   Eigenverbrauch {w['eigenverbrauch_pct']} %" if w.get("autarkie_pct") is not None else "",
          "", "TAGE"]
zeilen += [f"  {t['name']:11} {t['pv_kwh']:5.1f} kWh Sonne  {t['verbrauch_kwh']:4.1f} kWh verbraucht"
           + ("   ← bester Tag" if t["tag"] == w["bester_tag"]["tag"] else "") for t in w["tage"]]
if w.get("minuspreis_stunden"):
    zeilen += ["", f"An der Börse gab es {w['minuspreis_stunden']} Stunden mit negativen Preisen."]
a = w.get("amortisation")
if a:
    zeilen += ["", "AMORTISATION",
               f"  Dein Solarstrom war diese Woche CHF {a['wert_chf']:.2f} wert (+{a['plus_pct']:.2f} %).",
               f"  Abbezahlt: {a['pct']:.1f} %, Break-even voraussichtlich {a['break_even'][8:10]}.{a['break_even'][5:7]}.{a['break_even'][:4]}."]
if w.get("batterie"):
    zeilen += ["", "BATTERIE-SIMULATION (was eine Batterie diese Woche gespart hätte)"]
    zeilen += [f"  {b['name']:22} CHF {b['ersparnis_chf']:.2f}" for b in w["batterie"]]
t = w.get("tipp")
if t:
    zeilen += ["", "TIPP DER WOCHE", f"  {t['titel']}" + (f" (rund CHF {t['chf_jahr']} im Jahr)" if t.get("chf_jahr") else ""),
               f"  {t['aktion']}", f"  Warum: {t['warum']}"]
if w.get("vollstaendig_pct", 100) < 95:
    zeilen += ["", f"Hinweis: Für {100 - w['vollstaendig_pct']} % der Woche fehlen Messwerte (z. B. Neustart)."]
zeilen += ["", "Dein homi · https://biber.homi.solar/#verlauf"]
betreff = f"homi: Deine Woche – {w['pv_kwh']:.0f} kWh Sonne, Bilanz {chf(w['saldo_chf'])}"
subprocess.run(["mail", "-s", betreff, "root"], input="\n".join(z for z in zeilen if z is not None).encode(), check=True)
print(betreff)
