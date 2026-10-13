"""Gemeinsamer Mailversand für homi (msmtp über Proton SMTP).

homi-Mails (Login-Codes, Wochenbericht, Prognose, Minuspreis-Warnung, Anfragen): Konto «homi» in /etc/msmtprc,
Absender support@homi.solar mit einem Namen je Mailart (ABSENDER). Proton erlaubt pro SMTP-Token nur die eigene Adresse.
Systemmails des NUC (Updates, IP-Wechsel, Neustart, Tarifabruf) laufen weiter über domus@biber.solar (mail-Befehl).
"""
import subprocess
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

URL = "https://homi.solar"
HOMI = "support@homi.solar"
ABSENDER = {
    "homi": f"homi <{HOMI}>",                          # Prognose, Minuspreis-Warnung, Bestätigungen
    "anmeldung": f"homi Anmeldung <{HOMI}>",            # Login-Codes
    "wochenbericht": f"homi Wochenbericht <{HOMI}>",    # Wochenbericht mit Tipp der Woche
    "anfragen": f"homi Anfragen <{HOMI}>",              # Anfragen von homi.solar an Patrick
}


def senden(an, betreff, text, absender=ABSENDER["homi"], reply_to=HOMI, header=None, konto="homi"):
    m = EmailMessage()
    m["From"] = absender
    m["To"] = an
    m["Reply-To"] = reply_to
    m["Subject"] = betreff
    m["Date"] = formatdate(localtime=True)
    m["Message-ID"] = make_msgid(domain="homi.solar")
    for k, v in (header or {}).items():
        m[k] = v
    m.set_content(text)
    subprocess.run(["/usr/bin/msmtp", "-a", konto, "-t"], input=m.as_bytes(), check=True)
