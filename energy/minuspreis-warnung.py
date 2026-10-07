#!/usr/bin/env python3
"""Morgen-Mail (Timer energy-minuspreis, 06:45) an Tagen mit negativen Day-Ahead-Preisen in der Schweiz:
 - an Patrick (root) persönlich, mit seinem Einspeise-Fahrplan aus homi (/api/einspeisung),
 - an alle bestätigten Abonnenten der Gratis-Warnung (energy/abos/aktiv/), mit Abmeldelink.
An Tagen ohne negative Preise wird nichts verschickt. Räumt unbestätigte Anmeldungen nach 7 Tagen weg.
Aufruf: minuspreis-warnung.py [--test]   (--test: erfundener Minuspreis-Tag, nur an Patrick, Betreff mit TEST)"""
import json
import os
import sys
import time
import urllib.request
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mailer import URL, senden  # noqa: E402

TEST = "--test" in sys.argv
ABOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "abos")
WT = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

# unbestätigte Anmeldungen nach 7 Tagen löschen
for f in os.listdir(os.path.join(ABOS, "ausstehend")):
    p = os.path.join(ABOS, "ausstehend", f)
    if time.time() - os.path.getmtime(p) > 7 * 86400:
        os.remove(p)

e = json.load(urllib.request.urlopen("http://127.0.0.1:8099/api/einspeisung", timeout=30))
h = e.get("heute")
if TEST:
    h = {"datum": date.today().isoformat(), "minuspreis_bloecke": ["11–15 Uhr"], "minuspreis_stunden": [11, 12, 13, 14],
         "ueberschuss_bei_minuspreis_kwh": 18.4, "kosten_bei_minuspreis_chf": -0.98,
         "stunden": [{"stunde": s, "preis_rp": p} for s, p in ((11, -2), (12, -6), (13, -8), (14, -5))],
         "batterie": {"kapazitaet_kwh": 5, "einfach_chf": 0.52, "optimiert_chf": 1.79, "mehrwert_chf": 1.26}}
if not h or not h.get("minuspreis_stunden"):
    print("heute keine negativen Preise – keine Mail")
    sys.exit(0)

tag_ = date.fromisoformat(h["datum"])
wann = " und ".join(h["minuspreis_bloecke"])
tief = min(x["preis_rp"] for x in h["stunden"] if x["stunde"] in h["minuspreis_stunden"])
datum_txt = f"{WT[tag_.weekday()]}, {tag_.day}.{tag_.month}."
betreff = ("TEST – " if TEST else "") + f"⚠️ Heute {wann} negative Strompreise (bis {tief:.1f} Rp/kWh)"
tun = f"""WAS DU TUN KANNST
  1. Strom selbst verbrauchen: Waschmaschine, Tumbler, Geschirrspüler, Boiler oder E-Auto genau
     in diesen Stunden laufen lassen.
  2. Einspeisung stoppen: Den Wechselrichter in dieser Zeit über seine App oder sein Display auf
     0 % Einspeiseleistung begrenzen bzw. ausschalten und danach wieder einschalten.
     Bitte nichts an der Elektroinstallation selbst schalten."""

# --- an Patrick, mit seinen eigenen Zahlen aus homi
b = h["batterie"]
senden("root", betreff, f"""Hallo Patrick, hier ist homi.

{datum_txt}: Die Day-Ahead-Börsenpreise in der Schweiz liegen heute {wann} unter null, tiefster Wert {tief:.1f} Rp/kWh.

DEINE ANLAGE IN DIESER ZEIT
  erwarteter Überschuss: {h['ueberschuss_bei_minuspreis_kwh']:.1f} kWh
  mit dynamischer Vergütung würde das Einspeisen kosten: CHF {-h['kosten_bei_minuspreis_chf']:.2f}
  Batterie ({b['kapazitaet_kwh']} kWh, Simulation): optimiert CHF {b['optimiert_chf']:.2f} statt {b['einfach_chf']:.2f} bei einfachem Laden

{tun}

Bei deiner heutigen fixen Vergütung der GWS kostet dich das Einspeisen noch nichts – die Warnung zeigt,
was mit einem dynamischen Einspeisetarif auf dem Spiel stünde. Fahrplan Stunde für Stunde: https://home.biber.solar

Dein homi
""")
print("Mail an Patrick:", betreff)

# --- an die Abonnenten der Gratis-Warnung
if not TEST:
    aktiv = os.path.join(ABOS, "aktiv")
    for f in sorted(x for x in os.listdir(aktiv) if x.endswith(".json")):
        a = json.load(open(os.path.join(aktiv, f), encoding="utf-8"))
        ab = f"{URL}/api/warnung/abmelden?t={a['abmelden']}"
        try:
            senden(a["email"], betreff, f"""Guten Morgen

{datum_txt}: Die Strompreise an der Börse liegen in der Schweiz heute {wann} unter null,
tiefster Wert {tief:.1f} Rp/kWh. Wird deine Einspeisung dynamisch (nach Börsenpreis) vergütet,
kostet dich jede eingespeiste Kilowattstunde in dieser Zeit Geld.

{tun}

Lieber automatisch? Mit homi und einem kleinen Gateway stoppt die Einspeisung von selbst, die Batterie lädt
genau im günstigsten Moment und Boiler, Wärmepumpe oder E-Auto springen ein: {URL}/#einspeisung

Patrick Biber · homi von Biber Solar
Du bekommst diese Mail nur an Tagen mit negativen Preisen. Abmelden: {ab}
""", header={"List-Unsubscribe": f"<{ab}>"})
            print("Abonnent:", f)
        except Exception as ex:
            print("FEHLER Abonnent", f, ex, file=sys.stderr)
