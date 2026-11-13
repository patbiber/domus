"""Lautsprecher (Google Cast: Chromecast Audio, Chromecast Ultra) – nur Status anzeigen.

Jedes Gerät antwortet im Heimnetz auf http://<ip>:8008/setup/eureka_info (ohne Anmeldung, nur lesen).
Konfiguration in anlage.json → lautsprecher: [{name, ort, ip}]. homi spielt nichts ab; die Ansage bei negativen
Strompreisen macht Home Assistant (homeassistant/config/configuration.yaml, Automation homi_minuspreis_ansage).
Der Bericht wird höchstens alle 15 s neu geholt."""
import json
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from .konfig import ANLAGE_CFG, load_json

CACHE_S = 15
_lock = threading.Lock()
_cache = {"zeit": 0.0, "daten": None}


def _eins(g):
    erg = {"name": g.get("name"), "ort": g.get("ort"), "ip": g["ip"], "online": False}
    try:
        url = f"http://{g['ip']}:8008/setup/eureka_info?params=name,ssid,signal_level,uptime,has_update,version"
        with urllib.request.urlopen(url, timeout=3) as r:
            d = json.load(r)
        erg.update(online=True, geraet=d.get("name") or g.get("name"), ssid=d.get("ssid"),
                   signal_dbm=d.get("signal_level"), laeuft_s=int(d.get("uptime") or 0))
    except Exception:
        pass
    return erg


def bericht():
    cfg = (load_json(ANLAGE_CFG) or {}).get("lautsprecher") or []
    if not cfg:
        return {"aktiv": False}
    with _lock:
        if _cache["daten"] and time.time() - _cache["zeit"] < CACHE_S:
            return _cache["daten"]
        with ThreadPoolExecutor(len(cfg)) as ex:
            liste = list(ex.map(_eins, cfg))
        daten = {"aktiv": True, "geraete": liste, "online": sum(1 for g in liste if g["online"]), "gesamt": len(liste)}
        _cache.update(zeit=time.time(), daten=daten)
        return daten
