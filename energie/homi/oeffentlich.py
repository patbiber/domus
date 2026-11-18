"""Öffentliche Kachel «Live vom Dach» auf biber.solar (/api/sonne).

Nur Werte, die nichts über den Haushalt verraten: Solarleistung, Wetter, Day-Ahead-Börsenpreis und was die Anlage
verdienen würde, wenn aller Solarstrom zum Börsenpreis eingespeist würde (Vergütung nach Börsenpreis ab 2028).
Verbrauch, Bezug, Einspeisung und Eigenverbrauch kommen hier bewusst NICHT vor (Datenschutz) – auch nicht indirekt.
"""
import threading
import time
from datetime import date, datetime, timedelta

from . import zustand
from .boerse import boerse_now
from .konfig import ANLAGE_CFG, load_json
from .zustand import lock, state

_cache = {"ts": 0, "daten": None, "tage_ts": 0, "tage": None}
_cache_lock = threading.Lock()


def _verdienst(rows):
    """Σ Solarertrag × Börsenpreis (Rp/kWh → CHF) über Archivzeilen; Zeilen ohne Preis zählen nicht."""
    kwh = chf = 0.0
    for r in rows:
        if r.get("pv_kwh") and r.get("boerse_rp") is not None:
            kwh += r["pv_kwh"]
            chf += r["pv_kwh"] * r["boerse_rp"] / 100
    return kwh, chf


def _tage():
    """Gestern und Durchschnitt der letzten 30 ganzen Tage (stündlich neu gerechnet)."""
    if _cache["tage"] and time.time() - _cache["tage_ts"] < 3600:
        return _cache["tage"]
    heute = date.today()
    rows = zustand.archiv.lesen(heute - timedelta(days=30), heute - timedelta(days=1))
    je_tag = {}
    for r in rows:
        je_tag.setdefault(r["zeit"][:10], []).append(r)
    tage = []
    for t, rs in sorted(je_tag.items()):
        if sum(r.get("sekunden") or 0 for r in rs) < 0.9 * 86400:
            continue                                  # lückenhafte Tage verfälschen den Schnitt
        kwh, chf = _verdienst(rs)
        tage.append({"tag": t, "kwh": round(kwh, 2), "chf": round(chf, 2)})
    gestern = next((x for x in tage if x["tag"] == (heute - timedelta(days=1)).isoformat()), None)
    n = len(tage)
    sk, sc = sum(x["kwh"] for x in tage), sum(x["chf"] for x in tage)
    erg = {"gestern": gestern,
           "schnitt_30": {"tage": n, "kwh_tag": round(sk / n, 1), "chf_tag": round(sc / n, 2),
                          "rp_kwh": round(100 * sc / sk, 1) if sk else None} if n else None}
    _cache.update(tage=erg, tage_ts=time.time())
    return erg


def _berechnen():
    jetzt = datetime.now()
    heute = date.today()
    try:
        kwp = load_json(ANLAGE_CFG).get("max_kw")
    except (OSError, ValueError):
        kwp = None
    with lock:
        st = dict(state["status"] or {})
        b = state["boerse"]
        p = state["prognose"] or {}
    akt = boerse_now(b, time.time()) if b else None
    preise_heute = [{"zeit": x["zeit"][11:16], "rp": x["rp_kwh"]} for x in (b or {}).get("preise", [])
                    if x["zeit"][:10] == heute.isoformat()]

    # Solarertrag heute je Stunde (gemessen) und Rest des Tages (Prognose)
    rows = zustand.archiv.lesen(heute, heute)
    std = {}
    for r in rows:
        std[int(r["zeit"][11:13])] = std.get(int(r["zeit"][11:13]), 0) + (r.get("pv_kwh") or 0)
    kwh_bisher, chf_bisher = _verdienst(rows)

    def preis_stunde(h):
        v = [x["rp"] for x in preise_heute if int(x["zeit"][:2]) == h]
        return sum(v) / len(v) if v else None

    prog = [x for x in p.get("stunden", []) if x["zeit"][:10] == heute.isoformat()]
    chf_rest = kwh_rest = 0.0
    for x in prog:
        h = int(x["zeit"][11:13])
        anteil = 1.0 if h > jetzt.hour else (1 - jetzt.minute / 60 if h == jetzt.hour else 0)
        rp = preis_stunde(h)
        if anteil and rp is not None:
            kwh_rest += x["kwh"] * anteil
            chf_rest += x["kwh"] * anteil * rp / 100
    kurve = [{"h": h, "ist_kwh": round(std[h], 3) if h in std and h <= jetzt.hour else None,
              "prog_kwh": next((x["kwh"] for x in prog if int(x["zeit"][11:13]) == h), None)} for h in range(24)]

    w = p.get("wetter") or {}
    return {
        "zeit": jetzt.isoformat(timespec="seconds"),
        "ort": "Uerikon am Zürichsee",
        "kwp": kwp,
        "online": bool(st.get("fronius_ok")),
        # auf 10 W gerundet: genug für die Anzeige
        "pv_w": int(round((st.get("pv_w") or 0) / 10) * 10) if st.get("fronius_ok") else None,
        "pv_heute_kwh": round(kwh_bisher, 2),
        "prognose": {"heute_kwh": (p.get("heute") or {}).get("kwh"), "morgen_kwh": (p.get("morgen") or {}).get("kwh")},
        "wetter": w,
        "boerse": {"rp_kwh": akt and akt["rp_kwh"], "min_rp": min((x["rp"] for x in preise_heute), default=None),
                   "max_rp": max((x["rp"] for x in preise_heute), default=None), "preise_heute": preise_heute,
                   "kurs_eur_chf": b and b.get("kurs_eur_chf")},
        "kurve": kurve,
        "wenn_alles_eingespeist": {
            "bisher_chf": round(chf_bisher, 2), "bisher_kwh": round(kwh_bisher, 2),
            "bisher_rp_kwh": round(100 * chf_bisher / kwh_bisher, 1) if kwh_bisher > 0.05 else None,
            "erwartet_chf": round(chf_bisher + chf_rest, 2), "erwartet_kwh": round(kwh_bisher + kwh_rest, 1),
            **_tage()},
        "quellen": "Börsenpreis: EPEX Spot Day-Ahead CH via Energy-Charts (Fraunhofer ISE), CC BY 4.0, "
                   "umgerechnet mit dem SNB-Tageskurs · Wetter: Open-Meteo, CC BY 4.0 · Solar: eigene Messung",
    }


def sonne():
    """Zwischengespeichert für 30 s, damit viele Besucher den Server nicht belasten."""
    with _cache_lock:
        if not _cache["daten"] or time.time() - _cache["ts"] > 30:
            _cache.update(daten=_berechnen(), ts=time.time())
        return _cache["daten"]
