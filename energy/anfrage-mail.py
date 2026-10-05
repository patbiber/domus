#!/usr/bin/env python3
"""Verschickt neue Anfragen von energy.biber.solar (energy/anfragen/neu/*.json) als Mail an root (-> Patrick).
Reply-To = Interessent, damit «Antworten» direkt an ihn geht. Danach nach anfragen/versendet/ verschoben.
Ausgelöst von der Pfad-Unit energy-anfrage.path (sofort) und stündlich vom Timer energy-anfrage.timer (Nachzügler)."""
import json
import os
import subprocess
import sys
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

BASIS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "anfragen")
NEU, FERTIG = os.path.join(BASIS, "neu"), os.path.join(BASIS, "versendet")
os.makedirs(FERTIG, exist_ok=True)
fehler = 0
for name in sorted(f for f in os.listdir(NEU) if f.endswith(".json") and not f.startswith(".")):
    pfad = os.path.join(NEU, name)
    try:
        a = json.load(open(pfad, encoding="utf-8"))
        zeilen = [
            "Neue Anfrage über https://energy.biber.solar", "",
            f"Name:       {a['name']}", f"E-Mail:     {a['email']}", f"Telefon:    {a.get('telefon') or '–'}",
            f"Ort:        {a.get('ort') or '–'}", f"Objekt:     {a.get('objekt')}", f"Interesse:  {a.get('paket')}",
            f"Vorhanden:  {', '.join(a.get('vorhanden') or []) or '–'}",
            f"Demo:       {'Gewerbebetrieb' if a.get('demo') == 'betrieb' else 'Einfamilienhaus'} angesehen",
            "", "Wunsch:", a.get("wunsch") or "–", "",
            f"Eingegangen: {a['zeit'].replace('T', ' ')}", "",
            "Mit «Antworten» schreibst du direkt an die anfragende Person.",
        ]
        m = EmailMessage()
        m["From"] = "homi Anfragen <domus@biber.solar>"
        m["To"] = "root"
        m["Reply-To"] = formataddr((a["name"], a["email"]))
        m["Subject"] = f"homi-Anfrage: {a['name']}" + (f", {a['ort']}" if a.get("ort") else "") + f" ({a.get('objekt')}, {a.get('paket')})"
        m["Date"] = formatdate(localtime=True)
        m["Message-ID"] = make_msgid(domain="biber.solar")
        m.set_content("\n".join(zeilen))
        subprocess.run(["/usr/bin/msmtp", "-t"], input=m.as_bytes(), check=True)
        os.replace(pfad, os.path.join(FERTIG, name))
        print("verschickt:", name)
    except Exception as e:
        fehler += 1
        print("FEHLER bei", name, e, file=sys.stderr)
sys.exit(1 if fehler else 0)
