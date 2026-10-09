#!/usr/bin/env python3
"""Mittags-Screenshots der Kunden-Oberfläche für homi.solar (Abschnitt «So sieht dein homi aus»).

Timer homi-screenshots (11:30, 12:30, 13:30, 14:30): Liefert die Anlage gerade genug Sonnenstrom (Solar ≥ 2 kW und
Einspeisung ≥ 0.3 kW), nimmt es die drei Handy-Ansichten auf, ersetzt energy/www/img/app-*.png, passt den Text zur
Aufnahme an, committet und pusht, mailt Patrick und schaltet den Timer ab. Sonst wartet es auf den nächsten Termin.
Bis ENDE keine Sonne: Mail und Timer aus.

Aufruf: handy-screenshots.py            (Timer)
        handy-screenshots.py --probe DIR  nur aufnehmen nach DIR, ohne Sonnen-Prüfung, nichts ändern
"""
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import date, datetime

HIER = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HIER)
IMG = os.path.join(HIER, "www", "img")
INDEX = os.path.join(HIER, "www", "index.html")
DATEIEN = ["app-uebersicht.png", "app-verlauf.png", "app-preise.png"]
ENDE = date(2026, 10, 17)
MIN_PV_W, MIN_EIN_W = 2000, 300
WT = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MON = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"]


class Durchreichen(http.server.BaseHTTPRequestHandler):
    """localhost:18099 -> homi :8099 mit Host biber.homi.solar (Kunden-Oberfläche, ohne nginx/Passwort)"""
    def do_GET(self):
        req = urllib.request.Request("http://127.0.0.1:8099" + self.path, headers={"Host": "biber.homi.solar"})
        try:
            with urllib.request.urlopen(req, timeout=30) as a:
                body, code, kopf = a.read(), a.status, a.getheaders()
        except urllib.error.HTTPError as e:
            body, code, kopf = e.read(), e.code, []
        self.send_response(code)
        for k, v in kopf:
            if k.lower() not in ("transfer-encoding", "connection", "content-length"):
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def aufnehmen(ziel):
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 18099), Durchreichen)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        os.chmod(ziel, 0o777)                     # der Container schreibt als anderer Benutzer
        subprocess.run(["docker", "run", "--rm", "--network", "host", "-v", f"{ziel}:/w",
                        "-v", f"{HIER}/handy-screenshots.mjs:/home/pptruser/s.mjs:ro", "-w", "/home/pptruser",
                        "ghcr.io/puppeteer/puppeteer", "node", "s.mjs"], check=True, timeout=300)
    finally:
        server.shutdown()
    for f in DATEIEN:
        p = os.path.join(ziel, f)
        if not os.path.exists(p) or os.path.getsize(p) < 50_000:
            raise SystemExit(f"Screenshot fehlt oder ist leer: {f}")


def mail(betreff, text):
    subprocess.run(["mail", "-s", betreff, "root"], input=text.encode(), check=True)


def timer_aus():
    subprocess.run(["systemctl", "--user", "disable", "--now", "homi-screenshots.timer"], check=False)


def main():
    if sys.argv[1:2] == ["--probe"] and len(sys.argv) == 3:
        os.makedirs(sys.argv[2], exist_ok=True)
        aufnehmen(sys.argv[2])
        print("Probe-Aufnahmen in", sys.argv[2])
        return
    if date.today() > ENDE:
        mail("homi.solar: keine Sonne für die Screenshots",
             f"Hallo Patrick\n\nBis {ENDE.day}.{ENDE.month}. gab es mittags nie genug Sonne (Solar ≥ {MIN_PV_W / 1000:.0f} kW).\n"
             "Die Screenshots auf homi.solar bleiben die vom Abend. Neu versuchen: Claude fragen oder\n"
             "systemctl --user enable --now homi-screenshots.timer (ENDE in energy/handy-screenshots.py anpassen).\n\nDein homi\n")
        timer_aus()
        return
    with urllib.request.urlopen("http://127.0.0.1:8099/api/status", timeout=20) as r:
        s = json.load(r)
    pv, ein, haus = s.get("pv_w") or 0, s.get("einspeisung_w") or 0, s.get("verbrauch_w") or 0
    if not s.get("fronius_ok") or pv < MIN_PV_W or ein < MIN_EIN_W:
        print(f"Zu wenig Sonne (Solar {pv} W, Einspeisung {ein} W) – nächster Versuch beim nächsten Termin")
        return
    jetzt = datetime.now()
    with tempfile.TemporaryDirectory() as tmp:
        aufnehmen(tmp)
        for f in DATEIEN:
            shutil.copyfile(os.path.join(tmp, f), os.path.join(IMG, f))
    text = (f"Echte Werte aus Uerikon, aufgenommen am {WT[jetzt.weekday()]}, {jetzt.day}. {MON[jetzt.month - 1]} "
            f"{jetzt.year} um {jetzt:%H:%M} Uhr: Die Anlage liefert gerade {pv / 1000:.1f} kW, das Haus braucht "
            f"{haus / 1000:.1f} kW, der Rest fliesst ins Netz.")
    with open(INDEX, encoding="utf-8") as f:
        html = f.read()
    html, n = re.subn(r'(<section id="ansicht">.*?<p class="sub">)[^<]*(</p>)', lambda m: m.group(1) + text + m.group(2),
                      html, count=1, flags=re.S)
    if n != 1:
        raise SystemExit("Text im Abschnitt #ansicht nicht gefunden")
    with open(INDEX, "w", encoding="utf-8") as f:
        f.write(html)
    git = lambda *a: subprocess.run(["git", "-C", REPO, *a], check=True)
    git("add", INDEX, *[os.path.join(IMG, f) for f in DATEIEN])
    git("commit", "-q", "-m", f"homi.solar: Handy-Ansichten mit Mittagssonne ({jetzt:%d.%m.%Y %H:%M}, Solar {pv / 1000:.1f} kW)\n\n"
        "Automatisch aufgenommen von energy/handy-screenshots.py\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>",
        "--", INDEX, *[os.path.join(IMG, f) for f in DATEIEN])        # nur diese Dateien, nichts anderes Halbfertiges
    git("push", "-q", "origin", "main")
    mail("homi.solar: neue Handy-Ansichten mit Sonne sind online",
         f"Hallo Patrick\n\nHeute um {jetzt:%H:%M} Uhr schien die Sonne: Solar {pv / 1000:.1f} kW, Haus {haus / 1000:.1f} kW, "
         f"ins Netz {ein / 1000:.1f} kW.\nDie drei Handy-Ansichten auf https://homi.solar/#ansicht sind ersetzt und ins Repo "
         "gepusht.\n\nDer Timer hat sich danach selbst abgeschaltet.\n\nDein homi\n")
    timer_aus()
    print("Screenshots ersetzt, committet, gepusht, Mail verschickt")


if __name__ == "__main__":
    main()
