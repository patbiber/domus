#!/usr/bin/env python3
"""Auswertung der Fronius-Steuerung (Abregeln unter der Schwelle → Nulleinspeisung) per Mail an Patrick.
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
    fazit = ("In den zwei Wochen fiel der Börsenpreis nie unter die Abregelschwelle – homi hätte nie gedrosselt. "
             "Im Herbst und Winter ist das normal; Minuspreise kommen vor allem an sonnigen Wochenenden im "
             "Frühling und Sommer. Die Regel bleibt aktiv; spannend wird es im Frühling.")
elif kosten > nutzen:
    fazit = (f"Mit deinem heutigen fixen Tarif hätte das Drosseln CHF {kosten:.2f} gekostet (entgangene Vergütung). "
             f"Bei Vergütung zum Börsenpreis hätte es CHF {nutzen:.2f} gebracht. Solange die GWS fix vergütet, "
             "kostet dich die Regel Geld. Du kannst sie in der App, auf home.biber.solar oder in Home Assistant "
             "ausschalten (Schalter «Automatik bei tiefem Börsenpreis»). Im Modell «fixer Tarif» drosselt homi ohnehin nie.")
else:
    fazit = (f"Das Drosseln hätte mehr gebracht (CHF {nutzen:.2f}) als gekostet (CHF {kosten:.2f}). "
             "Die Regel lohnt sich.")

z = ["Hallo Patrick", "",
     (f"Seit {d(s['seit'])} rechnet homi mit, seit {d(s['scharf_seit'])} steuert homi den Fronius SCHARF "
      "(Einspeisung bei Minuspreis geschätzt aus der Solarprognose)." if s["modus"] == "scharf" else
      f"Seit {d(s['seit'])} rechnet homi im TESTMODUS mit, was die Fronius-Steuerung getan hätte."),
     f"Regel: {s['regel']}", "",
     "ERGEBNIS",
     f"  Stunden unter der Schwelle (2027):  {su['minuspreis_h']:.1f} h",
     f"  Einspeisung, die weggefallen wäre:  {su['abregeln_kwh']:.2f} kWh",
     f"  Kosten mit deinem fixen Tarif:      CHF {kosten:.2f}",
     f"  Nutzen ab 2027 (Börse + Ausgleich): CHF {nutzen:.2f}", "",
     f"Vergütungsmodell heute: {s['verguetung']['modell']} · Abregeln ab 2027 unter {s['verguetung']['schwelle_2027_rp']} Rp./kWh "
     f"(Minimalvergütung {s['verguetung']['minimal_rp']} Rp., Ausgleich bisher {s['verguetung']['ausgleich_rp']} Rp.)", "",
     "FAZIT", "  " + fazit, ""]
if s["ereignisse"]:
    z.append("ZEITRÄUME UNTER DER SCHWELLE (neueste zuerst)")
    for e in s["ereignisse"]:
        z.append(f"  {d(e['von'])} {e['von'][11:16]}–{e['bis'][11:16]}  bis {e['min_rp']:.1f} Rp./kWh, "
                 f"{e['kwh']:.2f} kWh, hätte auf {e['min_pct']:g} % gedrosselt")
    z.append("")
z += [f"Letzte Aktionen: " + "; ".join(f"{d(a['zeit'])} {a['zeit'][11:16]} {a['text']}" for a in s.get("aktionen", [])[:5]), "",
      f"Modbus: {'verbunden' if m.get('verbunden') else 'NICHT verbunden'}"
      + (f", Begrenzung {m['begrenzung_pct']} % ({'aktiv' if m['begrenzung_aktiv'] else 'aus'})" if m.get("verbunden") else ""),
      "", "Dein homi"]
text = "\n".join(z)
if "--nur-anzeigen" in sys.argv:
    print(text)
else:
    senden("root", f"homi: Fronius-Steuerung – Ergebnis nach {(date.today() - date.fromisoformat(s['seit'])).days} Tagen",
           text, absender=ABSENDER["homi"])
    print("Bericht gesendet")
