"""Reserve-PC über Mittag: Knopf «Reserve Server On» in der Retro-Webseite.

Der Dienst hat keinen Zugriff auf den Reserve-PC. Er legt nur den Wunsch ab (/data/reserve.json); das Skript
reserve/mittag.sh auf dem NUC (jede Minute) schaltet die Reserve ein/aus und schreibt den Zustand nach
/data/reserve-status.json."""
import json
import os
import time

from .konfig import RESERVE_FILE, RESERVE_STATUS_FILE, SICHERHEIT_FILE


def _lesen(pfad):
    try:
        with open(pfad) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def bericht():
    k, s = _lesen(RESERVE_FILE), _lesen(RESERVE_STATUS_FILE)
    try:
        alter = time.time() - time.mktime(time.strptime(s["zeit"], "%Y-%m-%dT%H:%M:%S"))
    except (KeyError, ValueError):
        alter = None
    frisch = alter is not None and alter < 240          # das Skript meldet sich jede Minute
    return {"dauerhaft": k.get("dauerhaft") is True, "bekannt": frisch,
            "an": bool(s.get("an")) if frisch else None, "rolle": s.get("rolle") if frisch else None,
            "fenster": s.get("fenster", "12:00–12:30"), "hinweis": s.get("hinweis") if frisch else None,
            "sicherung": s.get("sicherung")}


def setzen(dauerhaft, von):
    tmp = RESERVE_FILE + ".neu"
    with open(tmp, "w") as f:
        json.dump({"dauerhaft": bool(dauerhaft), "von": von, "zeit": time.strftime("%Y-%m-%dT%H:%M:%S")}, f)
    os.replace(tmp, RESERVE_FILE)
    return bericht()


def sicherheit():
    """Kachel «Datensicherheit»: Status der Sicherungen (schreibt system/sicherheit-status.py auf dem NUC jede Minute)."""
    d = _lesen(SICHERHEIT_FILE)
    try:
        alter = time.time() - time.mktime(time.strptime(d["zeit"], "%Y-%m-%dT%H:%M:%S"))
    except (KeyError, ValueError):
        return {"bekannt": False}
    if alter > 600:          # Meldung fehlt (z. B. NUC aus, Reserve hat übernommen): nichts Veraltetes als «sicher» zeigen
        return {"bekannt": False}
    return {"bekannt": True, "alles_ok": bool(d.get("alles_ok")), "punkte": d.get("punkte", [])}
