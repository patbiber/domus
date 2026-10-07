#!/usr/bin/env python3
"""Verschickt Bestätigungs-Mails (Double-Opt-in) für die Gratis-Minuspreis-Warnung (energy/abos/versand/*.json).
Ausgelöst von der Pfad-Unit energy-warnung.path; fehlgeschlagene bleiben liegen (stündlicher Timer versucht erneut)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mailer import URL, senden  # noqa: E402

VERSAND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "abos", "versand")
fehler = 0
for name in sorted(f for f in os.listdir(VERSAND) if f.endswith(".json") and not f.startswith(".")):
    pfad = os.path.join(VERSAND, name)
    try:
        a = json.load(open(pfad, encoding="utf-8"))
        link = f"{URL}/api/warnung/bestaetigen?t={a['token']}"
        senden(a["email"], "Bitte bestätigen: Gratis-Warnung bei negativen Strompreisen", f"""Hallo

Du hast auf {URL} die Gratis-Warnung bei negativen Strompreisen bestellt.
Bitte bestätige mit einem Klick, dass du sie erhalten möchtest:

{link}

Danach bekommst du an Tagen mit negativen Börsenpreisen in der Schweiz um 6:45 Uhr eine kurze Mail:
wann die Preise unter null fallen und was du tun kannst. An allen anderen Tagen bekommst du nichts.

Warst du das nicht? Dann ignoriere diese Mail einfach – ohne Bestätigung passiert nichts.

Patrick Biber · homi von Biber Solar · {URL}
""")
        os.remove(pfad)
        print("Bestätigung verschickt:", name)
    except Exception as e:
        fehler += 1
        print("FEHLER", name, e, file=sys.stderr)
sys.exit(1 if fehler else 0)
