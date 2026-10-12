"""Erfassungsschleife: alle 5 s Fronius lesen, Tarif, Archiv, Speicher, Verlauf."""
import time
from datetime import datetime

from . import zustand
from .boerse import boerse_now
from .fronius import fetch_fronius
from .konfig import POLL_S
from .servermessung import server_messer
from .tarif import current_tariff
from .zustand import history, lock, state


def poll():
    last_minute = None
    while True:
        now = datetime.now()
        status = {"zeit": now.isoformat(timespec="seconds"), "fronius_ok": False}
        try:
            status["tarif"] = current_tariff(now)
        except Exception as e:  # Tarifdaten fehlen/kaputt: Seite läuft trotzdem
            status["tarif"] = None
            status["tarif_fehler"] = str(e)
        try:
            status.update(fetch_fronius())
            status["fronius_ok"] = True
        except Exception as e:  # nachts ist der Wechselrichter aus
            status["fronius_fehler"] = type(e).__name__
        try:
            status["server"] = server_messer.messen()
            if status["tarif"] and status["server"].get("leistung_w"):
                kwh_jahr = status["server"]["leistung_w"] * 8.76
                status["server"]["kwh_jahr"] = round(kwh_jahr)
                status["server"]["kosten_jahr_chf"] = round(kwh_jahr * status["tarif"]["bezug_chf_kwh"], 2)
        except Exception as e:  # Messung ist Beiwerk, darf homi nie stören
            status["server"] = {"fehler": type(e).__name__}
        with lock:
            status["boerse"] = boerse_now(state["boerse"], now.timestamp())
            p = state["prognose"]
            status["prognose"] = {k: p[k] for k in ("heute", "morgen")} if p else None
        t = status["tarif"]
        if status["fronius_ok"] and t:
            status["kosten_chf_h"] = round(status["bezug_w"] / 1000 * t["bezug_chf_kwh"], 4)
            status["erloes_chf_h"] = round(status["einspeisung_w"] / 1000 * (t["rueckliefer_chf_kwh"] or 0), 4)
        with lock:
            state["status"] = status
            zustand.speicher_sim.step(status, now)
            try:
                zustand.archiv.step(status, now)
            except OSError as e:
                print("Archiv nicht geschrieben:", e, flush=True)
            minute = now.strftime("%Y-%m-%dT%H:%M")
            if status["fronius_ok"] and minute != last_minute:
                last_minute = minute
                try:
                    zustand.speicher_sim.save()
                except OSError as e:
                    print("Speicher-Simulation nicht gespeichert:", e, flush=True)
                history.append({k: status.get(k) for k in
                                ("zeit", "pv_w", "verbrauch_w", "netz_w", "kosten_chf_h", "erloes_chf_h")})
        time.sleep(POLL_S)
