#!/usr/bin/env python3
"""Erzeugt die Autoinstall-Dateien für den Reserve-PC und schickt sie Patrick per Mail.

- SSH-Schlüssel ~/.ssh/domus_reserve (NUC → Reserve, für Abgleich und Übernahme), falls noch nicht vorhanden
- zufälliges Passwort für den Benutzer claude (nur für die Anmeldung am Bildschirm; SSH nur mit Schlüssel)
- schreibt backups/reserve-usb/user-data, meta-data, ANLEITUNG.txt (nicht im Repo) und mailt sie als Anhang an root
Aufruf: reserve/usb-erzeugen.py [--ohne-mail]
"""
import os
import secrets
import subprocess
import sys
from email.message import EmailMessage
from email.utils import formatdate

import yaml

HIER = os.path.dirname(os.path.abspath(__file__))
ZIEL = os.path.join(HIER, "..", "backups", "reserve-usb")
SCHLUESSEL = os.path.expanduser("~/.ssh/domus_reserve")
DOCKER_KEY = "/etc/apt/keyrings/docker.asc"            # derselbe Schlüssel wie auf dem NUC

ANLEITUNG = """Reserve-PC «domus-reserve» installieren
=======================================

Du brauchst zwei USB-Sticks:
  1. Ubuntu Server 26.04 LTS (amd64), z. B. mit balenaEtcher oder Rufus geschrieben:
     https://ubuntu.com/download/server
  2. Einen kleinen Stick mit FAT32 und dem Namen CIDATA (genau so geschrieben).
     Darauf die beiden Dateien aus dieser Mail kopieren: «user-data» und «meta-data» (ohne Endung!).

Installation:
  - Reserve-PC per Netzwerkkabel ans gleiche Netz wie der NUC (gleicher Router/Switch).
  - Beide Sticks einstecken, vom Ubuntu-Stick starten (Boot-Menü meist F12, F11 oder Esc).
  - Der Installer fragt «Continue with autoinstall?» – mit «yes» bestätigen.
  - Danach fragt er nur noch nach dem DATENTRÄGER: die Platte fürs System wählen (am besten die SSD).
    Die grosse Daten-Platte NICHT fürs System nehmen, falls es eine separate SSD gibt – die richten wir danach
    gemeinsam ein. Alles, was auf dem gewählten Datenträger ist, wird gelöscht.
  - Alles andere läuft automatisch (ca. 10–20 Minuten), am Ende startet der PC neu.
  - Den CIDATA-Stick danach abziehen.

Der PC heisst dann «domus-reserve», hat die feste Adresse 192.168.1.24 (dazu eine per DHCP) und lässt sich vom
NUC aus per SSH erreichen. Am Bildschirm anmelden: Benutzer «claude», Passwort siehe Mail.
Sag Claude Bescheid, wenn er läuft – dann richtet der NUC Abgleich und automatische Übernahme ein.
"""


def main():
    os.makedirs(ZIEL, mode=0o700, exist_ok=True)
    if not os.path.exists(SCHLUESSEL):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "domus→domus-reserve", "-f", SCHLUESSEL], check=True)
    ssh_nuc = open(SCHLUESSEL + ".pub", encoding="utf-8").read().strip()
    passwort = secrets.token_urlsafe(12)
    passwort_hash = subprocess.run(["openssl", "passwd", "-6", "-stdin"], input=passwort, capture_output=True, text=True,
                                   check=True).stdout.strip()
    docker_key = "\n".join("          " + z for z in open(DOCKER_KEY, encoding="utf-8").read().strip().splitlines())
    text = open(os.path.join(HIER, "autoinstall-vorlage.yaml"), encoding="utf-8").read()
    text = text.replace("{{PASSWORT_HASH}}", passwort_hash).replace("{{SSH_NUC}}", ssh_nuc).replace("{{DOCKER_KEY}}", docker_key)
    daten = yaml.safe_load(text)                       # prüft die Syntax
    a = daten["autoinstall"]
    assert a["identity"]["password"].startswith("$6$") and a["ssh"]["authorized-keys"][0].startswith("ssh-ed25519")
    assert "BEGIN PGP PUBLIC KEY BLOCK" in a["apt"]["sources"]["docker.list"]["key"]
    for name, inhalt in (("user-data", text), ("meta-data", "instance-id: domus-reserve\n"), ("ANLEITUNG.txt", ANLEITUNG)):
        p = os.path.join(ZIEL, name)
        with open(os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
            f.write(inhalt)
    print("geschrieben:", os.path.realpath(ZIEL))
    if "--ohne-mail" in sys.argv:
        print("Passwort (nur einmal angezeigt):", passwort)
        return
    m = EmailMessage()
    m["From"] = "domus <domus@biber.solar>"
    m["To"] = "root"
    m["Subject"] = "domus: Installations-Dateien für den Reserve-PC"
    m["Date"] = formatdate(localtime=True)
    m.set_content(f"""Hallo Patrick

Im Anhang die Dateien für den Reserve-PC «domus-reserve». Die Anleitung steht in ANLEITUNG.txt (auch unten).

Anmeldung am Bildschirm des Reserve-PCs:
    Benutzer: claude
    Passwort: {passwort}
Bitte im Passwortmanager speichern. Per SSH kommt nur der NUC hinein (mit Schlüssel, ohne Passwort).

{ANLEITUNG}
Dein NUC
""")
    for name in ("user-data", "meta-data", "ANLEITUNG.txt"):
        m.add_attachment(open(os.path.join(ZIEL, name), "rb").read(), maintype="text", subtype="plain", filename=name)
    subprocess.run(["/usr/bin/msmtp", "-t"], input=m.as_bytes(), check=True)
    print("Mail mit Anhängen an Patrick verschickt")


if __name__ == "__main__":
    main()
