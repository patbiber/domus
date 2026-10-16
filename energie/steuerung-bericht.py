#!/usr/bin/env python3
"""Auswertung des Steuerungs-Testmodus (Minuspreis → Nulleinspeisung) per Mail an Patrick.
Daten: /api/steuerung. Einmalig am Ende der Testphase (homi-steuerung-bericht.timer).
Aufruf: steuerung-bericht.py [--nur-anzeigen]"""
import json
import os
import sys
import urllib.request
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "energy"))
from mailer import ABSENDER, senden  # noqa: E402

s = json.load(urllib.request.urlopen("http://127.0.0.1:8099/api/steuerung", timeout=20))
su, m = s["summe"], s["modbus"]
d = lambda x: date.fromisoformat(x[:10]).strftime("%d.%m.")
kosten, nutzen = su["verlust_fix_chf"], -su["boerse_chf"]

if su["minuspreis_h"] == 0:
    fazit = ("In den zwei Wochen gab es keine Stunde mit negativem Börsenpreis – homi hätte nie gedrosselt. "
             "Im Herbst und Winter ist das normal; Minuspreise kommen vor allem an sonnigen Wochenenden im "
             "Frühling und Sommer. Vorschlag: Testmodus weiterlaufen lassen und im Mai erneut auswerten.")
elif kosten > nutzen:
    fazit = (f"Mit deinem heutigen fixen Tarif hätte das Drosseln CHF {kosten:.2f} gekostet (entgangene Vergütung). "
             f"Bei Vergütung zum Börsenpreis hätte es CHF {nutzen:.2f} gebracht. Solange die GWS fix vergütet, "
             "lohnt sich das Scharfschalten nicht – der Testmodus läuft weiter.")
else:
    fazit = (f"Das Drosseln hätte mehr gebracht (CHF {nutzen:.2f}) als gekostet (CHF {kosten:.2f}). "
             "Wenn du willst, schalte ich die Regel scharf – sag einfach Bescheid.")

z = ["Hallo Patrick", "",
     f"Seit {d(s['seit'])} rechnet homi im TESTMODUS mit, was die Fronius-Steuerung getan hätte.",
     f"Regel: {s['regel']}", "",
     "ERGEBNIS",
     f"  Stunden mit negativem Börsenpreis:  {su['minuspreis_h']:.1f} h",
     f"  Einspeisung, die weggefallen wäre:  {su['abregeln_kwh']:.2f} kWh",
     f"  Kosten mit deinem fixen Tarif:      CHF {kosten:.2f}",
     f"  Nutzen bei Vergütung zur Börse:     CHF {nutzen:.2f}", "",
     "FAZIT", "  " + fazit, ""]
if s["ereignisse"]:
    z.append("MINUSPREIS-ZEITRÄUME (neueste zuerst)")
    for e in s["ereignisse"]:
        z.append(f"  {d(e['von'])} {e['von'][11:16]}–{e['bis'][11:16]}  bis {e['min_rp']:.1f} Rp./kWh, "
                 f"{e['kwh']:.2f} kWh, hätte auf {e['min_pct']:g} % gedrosselt")
    z.append("")
z += [f"Modbus: {'verbunden' if m.get('verbunden') else 'NICHT verbunden'}"
      + (f", Begrenzung {m['begrenzung_pct']} % ({'aktiv' if m['begrenzung_aktiv'] else 'aus'})" if m.get("verbunden") else ""),
      "", "Dein homi"]
text = "\n".join(z)
if "--nur-anzeigen" in sys.argv:
    print(text)
else:
    senden("root", f"homi: Fronius-Steuerung – Testergebnis nach {(date.today() - date.fromisoformat(s['seit'])).days} Tagen",
           text, absender=ABSENDER["homi"])
    print("Bericht gesendet")
