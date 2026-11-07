#!/usr/bin/env python3
"""Mails der EnergyHub Academy verschicken: data/outbox/*.json ({to, subject, text}) → msmtp (Konto academy).
Absender academy@biber.solar (eigenes msmtp-Konto «academy», Proton-SMTP-Token nur für diese Adresse).
Gestartet von academy-mail.path, sobald eine Datei im Ausgang liegt. Fehlgeschlagene Mails landen in outbox/failed/."""
import glob
import json
import os
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "energy"))
from mailer import senden  # noqa: E402

AUSGANG = os.path.join(HIER, "data", "outbox")
ABSENDER = "EnergyHub Academy <academy@biber.solar>"
ANTWORT = "academy@biber.solar"

for datei in sorted(glob.glob(os.path.join(AUSGANG, "*.json"))):
    try:
        with open(datei, encoding="utf-8") as f:
            m = json.load(f)
        senden(m["to"], m["subject"], m["text"], absender=ABSENDER, reply_to=ANTWORT, konto="academy")
        os.remove(datei)
        print(f"gesendet an {m['to']}: {m['subject'][:60]}")
    except Exception as e:                                   # nicht endlos neu versuchen
        os.makedirs(os.path.join(AUSGANG, "failed"), exist_ok=True)
        os.replace(datei, os.path.join(AUSGANG, "failed", os.path.basename(datei)))
        print(f"FEHLER {os.path.basename(datei)}: {e}", file=sys.stderr)
