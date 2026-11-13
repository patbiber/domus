#!/usr/bin/env python3
"""Sicherheits-Kachel der Retro-Webseite (home.biber.solar): wann war der letzte Abgleich, das letzte Backup, der letzte
GitHub-Upload – und ist etwas zu alt? Schreibt energie/data/sicherheit-status.json (jede Minute, User-Timer
domus-sicherheit-status, nur auf dem Hauptrechner). homi liest die Datei über /api/sicherheit."""
import glob
import json
import os
import subprocess
import time
from datetime import datetime

HOME = os.path.expanduser("~")
DOMUS = f"{HOME}/domus"
STATE = os.environ.get("XDG_STATE_HOME", f"{HOME}/.local/state") + "/domus"
AUS = f"{DOMUS}/energie/data/sicherheit-status.json"
H = 3600


def iso(t):
    return datetime.fromtimestamp(t).strftime("%Y-%m-%dT%H:%M:%S")


def lies(pfad):
    try:
        return open(pfad).read().strip()
    except OSError:
        return ""


def zeit_aus_iso(s):
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def punkt(name, t, grenze_h, detail="", ok=True):
    """t = Zeitpunkt (Sekunden) oder None; zu alt (> grenze_h) oder ok=False → Warnung."""
    alt = t is None or time.time() - t > grenze_h * H
    return {"name": name, "zeit": iso(t) if t else None, "ok": bool(ok and not alt), "detail": detail}


def git(pfad, *a):
    r = subprocess.run(["git", "-C", pfad, *a], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


punkte = []

# 1. Verschlüsseltes Gesamt-Backup des NUC (täglich 03:10, lokal + USB-Stick)
punkte.append(punkt("Backup NUC (verschlüsselt)", zeit_aus_iso(lies(f"{STATE}/backup-extern")), 30,
                    "täglich 03:10, geprüft, lokal und auf USB-Stick"))

# 2. Home-Assistant-Backup (mindestens wöchentlich, vor Image-Updates)
ha = sorted(glob.glob(f"{DOMUS}/backups/homeassistant-*.tar.gz"), key=os.path.getmtime)
punkte.append(punkt("Home-Assistant-Backup", os.path.getmtime(ha[-1]) if ha else None, 10 * 24, "bei jedem Update, spätestens wöchentlich"))

# 3. Abgleich NUC → Reserve (läuft, solange die Reserve an ist: täglich über Mittag)
fehler = lies(f"{STATE}/reserve-abgleich/fehler")
punkte.append(punkt("Abgleich NUC → Reserve", zeit_aus_iso(lies(f"{STATE}/reserve-abgleich/ok")), 30,
                    "Fehlversuche: " + fehler if fehler not in ("", "0") else "", ok=fehler in ("", "0")))

# 4. Backup auf der Reserve (6-TB-Platte, täglich 12:05)
try:
    t, erg = lies(f"{STATE}/reserve-abgleich/sicherung").split()
    sich = (int(t), erg == "ok")
except ValueError:
    sich = (None, True)
punkte.append(punkt("Backup auf der Reserve (6 TB)", sich[0], 30, "FEHLGESCHLAGEN" if not sich[1] else "", ok=sich[1]))

# 5. GitHub: letzter Upload (Timer github-abend) und was noch wartet (alle drei Repos)
wartend, aeltester = 0, None
for pfad, zweig in ((DOMUS, "main"), (f"{DOMUS}/biber-solar-test/site", "freigegeben"), (f"{DOMUS}/academy/repo", "main")):
    n = git(pfad, "rev-list", "--count", f"origin/main..{zweig}")
    if n.isdigit() and int(n) > 0:
        wartend += int(n)
        z = git(pfad, "log", "--format=%ct", f"origin/main..{zweig}").split()
        if z:
            aeltester = min(aeltester or 1e18, min(int(x) for x in z))
e = subprocess.run(["systemctl", "--user", "show", "github-abend.service", "-p", "Result", "-p", "ExecMainExitTimestamp"],
                   capture_output=True, text=True).stdout
w = dict(l.split("=", 1) for l in e.splitlines() if "=" in l)
try:
    letzte = datetime.strptime(w.get("ExecMainExitTimestamp", "").rsplit(" ", 1)[0], "%a %Y-%m-%d %H:%M:%S").timestamp()
except ValueError:
    letzte = None
detail = f"{wartend} Commit(s) warten auf den Abend" if wartend else "alles hochgeladen"
ok = w.get("Result") == "success" and (aeltester is None or time.time() - aeltester < 30 * H)
punkte.append(punkt("GitHub-Upload", letzte, 30, detail, ok=ok))

d = {"zeit": iso(time.time()), "punkte": punkte, "alles_ok": all(p["ok"] for p in punkte)}
tmp = AUS + ".neu"
with open(tmp, "w") as f:
    json.dump(d, f, ensure_ascii=False)
os.chmod(tmp, 0o644)
os.replace(tmp, AUS)
