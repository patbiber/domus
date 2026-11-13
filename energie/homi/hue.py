"""Philips Hue (Bridge im Heimnetz, API v2, nur lesen): Lampen mit Zustand und Farbe, Räume, Bewegungssensor «Sensi».

Zugang: HUE_APP_KEY in .env (Anwendungsschlüssel der Bridge, nicht im Repo), Adresse in anlage.json → hue.host.
Die Bridge hat ein selbstsigniertes Zertifikat; im Heimnetz wird es nicht geprüft. homi schaltet nichts, es liest nur.
Der Bericht wird höchstens alle 10 s neu von der Bridge geholt (Cache), die Namenslisten alle 10 min."""
import json
import math
import os
import ssl
import threading
import time
import urllib.request

from .konfig import ANLAGE_CFG, load_json

CACHE_S = 10
NAMEN_S = 600
_lock = threading.Lock()
_cache = {"zeit": 0.0, "daten": None}
_namen = {"zeit": 0.0, "geraete": {}, "raeume": {}}
_ctx = ssl.create_default_context()
_ctx.check_hostname = False
_ctx.verify_mode = ssl.CERT_NONE


def _get(host, key, pfad):
    req = urllib.request.Request(f"https://{host}/clip/v2/resource/{pfad}", headers={"hue-application-key": key})
    with urllib.request.urlopen(req, timeout=5, context=_ctx) as r:
        return json.load(r).get("data", [])


def _xy_rgb(x, y, hell):
    """CIE-xy + Helligkeit (0–100) → «#rrggbb» (Wide-RGB-D65, wie in der Hue-Doku)."""
    if y <= 0:
        return "#ffd9a0"
    Y = max(0.2, hell / 100)
    X, Z = Y / y * x, Y / y * (1 - x - y)
    r, g, b = X * 1.656492 - Y * 0.354851 - Z * 0.255038, -X * 0.707196 + Y * 1.655397 + Z * 0.036152, X * 0.051713 - Y * 0.121364 + Z * 1.011530
    gam = lambda c: 12.92 * c if c <= 0.0031308 else 1.055 * (max(c, 0) ** (1 / 2.4)) - 0.055
    m = max(r, g, b, 1e-6)
    r, g, b = (gam(max(c, 0) / m if m > 1 else max(c, 0)) for c in (r, g, b))
    return "#%02x%02x%02x" % tuple(int(max(0, min(1, c)) * 255) for c in (r, g, b))


def _mirek_rgb(mirek, hell):
    """Weisstöne (Farbtemperatur in Mirek) → «#rrggbb» (Näherung nach Tanner Helland)."""
    k = 1e6 / max(153, min(500, mirek or 366)) / 100
    r = 255 if k <= 66 else max(0, min(255, 329.7 * (k - 60) ** -0.1332))
    g = max(0, min(255, 99.47 * math.log(k) - 161.12)) if k <= 66 else max(0, min(255, 288.12 * (k - 60) ** -0.0755))
    b = 255 if k >= 66 else 0 if k <= 19 else max(0, min(255, 138.52 * math.log(k - 10) - 305.04))
    f = 0.35 + 0.65 * (hell / 100)
    return "#%02x%02x%02x" % (int(r * f), int(g * f), int(b * f))


def _namen_laden(host, key):
    if time.time() - _namen["zeit"] < NAMEN_S and _namen["geraete"]:
        return
    geraete = {d["id"]: d for d in _get(host, key, "device")}
    raeume = {}
    for r in _get(host, key, "room"):
        for c in r.get("children", []):
            raeume[c["rid"]] = r["metadata"]["name"]
    _namen.update(zeit=time.time(), geraete=geraete, raeume=raeume)


def _neu(host, key, cfg):
    _namen_laden(host, key)
    gs, rs = _namen["geraete"], _namen["raeume"]
    verb = {z["owner"]["rid"]: z["status"] for z in _get(host, key, "zigbee_connectivity")}
    lampen = []
    for l in _get(host, key, "light"):
        dev = gs.get(l["owner"]["rid"], {})
        an = bool(l["on"]["on"])
        hell = (l.get("dimming") or {}).get("brightness", 100)
        xy = (l.get("color") or {}).get("xy")
        farbe = _xy_rgb(xy["x"], xy["y"], hell) if xy else _mirek_rgb((l.get("color_temperature") or {}).get("mirek"), hell)
        lampen.append({"id": l["id"], "name": (dev.get("metadata") or {}).get("name") or l["metadata"]["name"],
                       "raum": rs.get(l["owner"]["rid"], "ohne Raum"), "an": an, "helligkeit": round(hell),
                       "farbe": farbe, "erreichbar": verb.get(l["owner"]["rid"], "connected") == "connected",
                       "art": (dev.get("product_data") or {}).get("product_name", "")})
    lampen.sort(key=lambda x: (x["raum"], x["name"]))
    # Bewegungssensor (Temperatur, Helligkeit, Bewegung): je Gerät zusammenfassen
    sensoren = {}
    for art, feld in (("temperature", "temperature"), ("light_level", "light"), ("motion", "motion")):
        for s in _get(host, key, art):
            e = sensoren.setdefault(s["owner"]["rid"], {"aktiviert": True})
            e["aktiviert"] = e["aktiviert"] and bool(s.get("enabled"))
            w = s.get(feld) or {}
            if art == "temperature" and w.get("temperature_valid"):
                e["temp_c"] = round(w.get("temperature", w.get("temperature_report", {}).get("temperature")), 1)
            if art == "temperature" and "temp_c" not in e and (w.get("temperature_report") or {}).get("temperature") is not None:
                e["temp_c"] = round(w["temperature_report"]["temperature"], 1)
            if art == "light_level" and w.get("light_level_valid"):
                e["lux"] = round(10 ** ((w["light_level"] - 1) / 10000)) if w.get("light_level") else 0
            if art == "motion" and w.get("motion_valid"):
                e["bewegung"] = bool(w.get("motion"))
    leistung = {p["owner"]["rid"]: p["power_state"] for p in _get(host, key, "device_power")}
    out = []
    for rid, e in sensoren.items():
        dev = gs.get(rid, {})
        out.append({"name": (dev.get("metadata") or {}).get("name", "Sensor"), "raum": rs.get(rid, ""), **e,
                    "batterie": (leistung.get(rid) or {}).get("battery_level"),
                    "erreichbar": verb.get(rid, "connected") == "connected"})
    return {"aktiv": True, "zeit": time.strftime("%Y-%m-%dT%H:%M:%S"), "lampen": lampen, "sensoren": out,
            "an": sum(1 for x in lampen if x["an"] and x["erreichbar"]), "gesamt": len(lampen),
            "stockwerk": (cfg.get("stockwerk") or {})}


def bericht():
    cfg = (load_json(ANLAGE_CFG) or {}).get("hue") or {}
    key = os.environ.get("HUE_APP_KEY", "")
    if not cfg.get("host") or not key:
        return {"aktiv": False, "grund": "Hue nicht eingerichtet (hue.host in anlage.json, HUE_APP_KEY in .env)"}
    with _lock:
        if _cache["daten"] and time.time() - _cache["zeit"] < CACHE_S:
            return _cache["daten"]
        try:
            d = _neu(cfg["host"], key, cfg)
        except (OSError, ValueError, KeyError) as e:
            print("Hue:", e, flush=True)
            return _cache["daten"] and {**_cache["daten"], "veraltet": True} or {"aktiv": False, "grund": "Bridge antwortet nicht"}
        _cache.update(zeit=time.time(), daten=d)
        return d
