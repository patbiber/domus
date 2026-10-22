#!/usr/bin/env python3
"""Verschickt neue Anfragen von homi.solar (energy/anfragen/neu/*.json) als Mail an root (-> Patrick).
Reply-To = Interessent, damit «Antworten» direkt an ihn geht. Danach nach anfragen/versendet/ verschoben.
Ausgelöst von der Pfad-Unit energy-anfrage.path (sofort) und stündlich vom Timer energy-anfrage.timer (Nachzügler)."""
import json
import os
import sys
from email.utils import formataddr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mailer import ABSENDER, senden  # noqa: E402
from server import SYSTEME  # noqa: E402  (Bezeichnungen der Systeme, gleiche Quelle wie das Formular)

BASIS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "anfragen")
NEU, FERTIG = os.path.join(BASIS, "neu"), os.path.join(BASIS, "versendet")
os.makedirs(FERTIG, exist_ok=True)
fehler = 0
for name in sorted(f for f in os.listdir(NEU) if f.endswith(".json") and not f.startswith(".")):
    pfad = os.path.join(NEU, name)
    try:
        a = json.load(open(pfad, encoding="utf-8"))
        systeme = a.get("systeme") or {}
        zeilen = [
            f"Neue Anmeldung über https://homi.solar – {a.get('rolle', 'Privat')}", "",
            f"Name:       {a['name']}", f"E-Mail:     {a['email']}", f"Telefon:    {a.get('telefon') or '–'}",
            f"Ort:        {a.get('ort') or '–'}", f"Ich bin:    {a.get('rolle') or '–'}", f"Objekt:     {a.get('objekt')}",
            f"Sprache:    {'Englisch (homi.solar/en/) – auf Englisch antworten' if a.get('sprache') == 'en' else 'Deutsch'}",
            "", "SYSTEME",
        ] + ([f"  {SYSTEME.get(k, k) + ':':32} {v}" for k, v in systeme.items()] or ["  (keine Angaben)"]) + [
            "", "Was ist wichtig:", a.get("wunsch") or "–", "",
            f"Eingegangen: {a['zeit'].replace('T', ' ')}", "",
            "Mit «Antworten» schreibst du direkt an die anfragende Person.",
        ]
        betreff = f"homi-Anmeldung ({a.get('rolle', 'Privat')}): {a['name']}" + (f", {a['ort']}" if a.get("ort") else "") + f" – {a.get('objekt')}"
        senden("root", " ".join(betreff.split()),       # Betreff immer einzeilig (ältere Anfragen konnten Umbrüche enthalten)
               "\n".join(zeilen), absender=ABSENDER["anfragen"], reply_to=formataddr((a["name"], a["email"])))
        os.replace(pfad, os.path.join(FERTIG, name))
        print("verschickt:", name)
    except Exception as e:
        fehler += 1
        print("FEHLER bei", name, e, file=sys.stderr)
sys.exit(1 if fehler else 0)
