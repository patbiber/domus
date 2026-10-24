"""Verbraucher-Zuordnung: homi schätzt aus dem Stromzähler, welche Geräte wie viel Strom brauchen.

Grundlage: 10-Minuten-Archiv der letzten 14 Tage, Geräteliste in anlage.json → haushalt.geraete, exakt gemessene
Werte (homi-Server, Solarladestation) und bekannte Messungen (messungen.json, z. B. Waschgänge). Es ist eine Schätzung –
der Zähler sieht nur die Summe. Jede Zuordnung trägt deshalb eine Sicherheit: «gemessen», «sicher» (bekannte Messung),
«wahrscheinlich» (klares Muster) oder «vermutet» (passt, aber auch anderes möglich).

Vorgehen:
1. Grundlast = Median aller 10-Minuten-Werte zwischen 1 und 5 Uhr; aufgeteilt auf die Dauerläufer der Geräteliste
   (homi-Server gemessen, Router, Netzwerk, Foodcenter, Ventilator, Standby), der Rest ist «unbekannt».
2. Infrarotheizung: lange, gleichmässige Blöcke (≥ 90 min, 450–1300 W über der Grundlast, ≥ 60 % der Werte innerhalb
   ±20 % ihres Medians). Der Median ist die geschätzte Heizleistung; was darüber liegt, gehört anderen Geräten.
3. Übrige Ereignisse (über Grundlast + 250 W) nach Tageszeit, Dauer und Höhe: Waschmaschine (bekannte Messung),
   Kochen & Backen (11–14 und 17–21 Uhr, ≥ 1,2 kW), Spülmaschine (21:30–3 Uhr, 0,9–2,5 kW, ≤ 40 min), Frühstück
   (5–10 Uhr, kurz: Wasserkocher & Co.), Wärmepumpentrockner (0,5–1,1 kW gleichmässig, 1–3 h).
4. Abendlast über der Grundlast (17–24 Uhr ausserhalb der Ereignisse): Licht, Medien und Computer.
"""
import os
import statistics
import time
from datetime import date, datetime, timedelta

from . import zustand
from .konfig import ANLAGE_CFG, APP_DIR, load_json
from .zustand import lock, state

TAGE = 14
CACHE_S = 1800
SCHWELLE_W = 250
MESSUNGEN = os.path.join(APP_DIR, "messungen.json")
_cache = {"zeit": 0.0, "daten": None}


def _w(r):
    return r["verbrauch_kwh"] * 3.6e6 / r["sekunden"] if r["sekunden"] else 0.0


def _messungen():
    try:
        return [m for m in load_json(MESSUNGEN) if "wasch" in m["name"].lower() and m.get("von")]
    except (OSError, ValueError):
        return []


def _bloecke(rows, werte, bedingung, luecke=1):
    """Zusammenhängende Abschnitte (Indizes), in denen bedingung(x) gilt; kurze Lücken (≤ luecke Slots) werden überbrückt."""
    out, cur, miss = [], [], 0
    for i, x in enumerate(werte):
        if bedingung(x):
            if cur and (datetime.fromisoformat(rows[i]["zeit"]) - datetime.fromisoformat(rows[cur[-1]]["zeit"])) > timedelta(minutes=10 * (luecke + 1)):
                out.append(cur); cur = []
            cur.append(i); miss = 0
        elif cur:
            miss += 1
            if miss > luecke:
                out.append(cur); cur, miss = [], 0
    if cur:
        out.append(cur)
    return out


def berechnen():
    cfg = load_json(ANLAGE_CFG)
    geraete = {g["id"]: g for g in (cfg.get("haushalt") or {}).get("geraete", [])}
    heute = date.today()
    rows = [r for r in zustand.archiv.lesen(heute - timedelta(days=TAGE), heute - timedelta(days=1)) if r["sekunden"] >= 300]
    stunden = sum(r["sekunden"] for r in rows) / 3600
    tage = stunden / 24
    if tage < 3:
        return {"aktiv": False, "grund": "homi braucht mindestens 3 Tage Messwerte für die Zuordnung."}
    with lock:
        st = dict(state.get("status") or {})
        laden = (zustand.geraete.report()["geraete"] if zustand.geraete else [])
    tarif = (st.get("tarif") or {})
    bezug_rp, rueck_rp = (tarif.get("bezug_chf_kwh") or 0.27) * 100, (tarif.get("rueckliefer_chf_kwh") or 0) * 100

    w = [_w(r) for r in rows]
    nacht = sorted(x for r, x in zip(rows, w) if 1 <= int(r["zeit"][11:13]) < 5)
    g = statistics.median(nacht) if nacht else min(w)
    x = [max(0.0, v - g) for v in w]                       # über der Grundlast
    std = [r["sekunden"] / 3600 for r in rows]             # Stunden pro Zeile
    netzanteil = [min(1.0, r["bezug_kwh"] / r["verbrauch_kwh"]) if r["verbrauch_kwh"] > 0 else 0.0 for r in rows]
    teile = {}                                             # id → {"kwh", "netz_kwh", "ereignisse", "wann": [], "sicherheit"}

    def buchen(gid, i, watt, sicher, ereignis=None):
        t = teile.setdefault(gid, {"kwh": 0.0, "netz_kwh": 0.0, "ereignisse": set(), "stunden": {}, "sicherheit": sicher})
        kwh = watt * std[i] / 1000
        t["kwh"] += kwh
        t["netz_kwh"] += kwh * netzanteil[i]
        h = int(rows[i]["zeit"][11:13])
        t["stunden"][h] = t["stunden"].get(h, 0) + kwh
        if ereignis is not None:
            t["ereignisse"].add(ereignis)

    # 2) Infrarotheizung: lange, gleichmässige Blöcke
    rest = list(x)
    stufen = []
    wk = (geraete.get("infrarot") or {}).get("w_typ")      # gemessene Leistung (Ein-/Ausschalttest), sonst schätzen
    for b in _bloecke(rows, x, lambda v: v >= (0.75 * wk if wk else 450), luecke=1):
        if len(b) < 9:
            continue
        band = [x[i] for i in b if 450 <= x[i] <= 1300]
        if not band and not wk:
            continue
        L = wk or statistics.median(band)
        if sum(1 for i in b if abs(x[i] - L) <= 0.2 * L) / len(b) < 0.6:
            continue
        stufen.append(L)
        for i in b:
            teil = min(x[i], L)
            buchen("infrarot", i, teil, "wahrscheinlich", b[0])
            rest[i] = x[i] - teil
    infrarot_w = round(wk) if wk else round(statistics.median(stufen)) if stufen else None
    # 2b) Heizung auch in kürzeren Abschnitten (z. B. beim Einschalten am Abend, bevor gekocht wird): mindestens
    #     3 aufeinanderfolgende Werte auf der Heizstufe (±20 %) bzw. darüber
    if infrarot_w:
        L = infrarot_w
        for b in _bloecke(rows, rest, lambda v: v >= 0.8 * L, luecke=0):
            if len(b) >= 3 and sum(1 for i in b if rest[i] <= 1.25 * L) >= 3:
                for i in b:
                    teil = min(rest[i], L)
                    buchen("infrarot", i, teil, "wahrscheinlich", b[0])
                    rest[i] -= teil

    # 3) Übrige Ereignisse
    wasch = [(datetime.fromisoformat(m["von"]) - timedelta(minutes=10),
              datetime.fromisoformat(m["bis"]) if m.get("bis") else datetime.fromisoformat(m["von"]) + timedelta(hours=2))
             for m in _messungen()]
    im_abend = set()
    # 3a) Bekannte Waschgänge: alles über der Grundlast im Messfenster gehört der Waschmaschine
    for i, r in enumerate(rows):
        t = datetime.fromisoformat(r["zeit"])
        fenster = next((k for k, (v, bis) in enumerate(wasch) if v <= t <= bis), None)
        if rest[i] > 0 and fenster is not None:
            buchen("waschmaschine", i, rest[i], "sicher", f"w{fenster}")
            rest[i] = 0.0
            im_abend.add(i)
    for b in _bloecke(rows, rest, lambda v: v > SCHWELLE_W, luecke=0):
        t0 = datetime.fromisoformat(rows[b[0]]["zeit"])
        dauer = len(b) * 10
        spitze = max(rest[i] for i in b)
        mittel = statistics.mean(rest[i] for i in b)
        h = t0.hour + t0.minute / 60
        if (11 <= h < 14 or 17 <= h < 21) and spitze >= 1200:
            gid, sicher = "kochen", "wahrscheinlich"
        elif (h >= 21.5 or h < 3) and 900 <= spitze <= 2600 and dauer <= 40:
            gid, sicher = "spuelmaschine", "vermutet"
        elif 5 <= h < 10 and dauer <= 30:
            gid, sicher = "fruehstueck", "vermutet"
        elif 500 <= mittel <= 1100 and 60 <= dauer <= 180 and statistics.pstdev([rest[i] for i in b]) < 0.25 * mittel:
            gid, sicher = "tumbler", "vermutet"
        else:
            gid, sicher = "uebrig", "vermutet"
        for i in b:
            buchen(gid, i, rest[i], sicher, b[0])
            im_abend.add(i)

    # 4) Abendlast (Licht, Medien, Computer): Zeilen 17–24 Uhr ausserhalb der Ereignisse
    for i, r in enumerate(rows):
        if 17 <= int(r["zeit"][11:13]) and i not in im_abend and rest[i] > 0:
            buchen("abend", i, min(rest[i], SCHWELLE_W), "vermutet")

    # 1) Grundlast aufteilen
    srv = (st.get("server") or {}).get("leistung_w") or 0
    dauer_ids = [("nuc", srv), ("router", None), ("netzwerk", None), ("foodcenter", None), ("ventilator", None)]
    standby = sum((gd.get("w_standby") or 0) for gd in geraete.values() if gd.get("typ") in ("it", "licht"))
    verteilt = []
    for gid, wert in dauer_ids:
        if gid in geraete:
            verteilt.append((gid, wert if wert else (geraete[gid].get("w_mittel") or geraete[gid].get("w_typ") or 0)))
    verteilt.append(("standby", standby))
    summe = sum(v for _, v in verteilt)
    faktor = min(1.0, g / summe) if summe else 1.0          # mehr Bekanntes als Grundlast: anteilig kürzen
    for gid, wert in verteilt:
        teile[gid] = {"kwh": wert * faktor * stunden / 1000, "netz_kwh": 0.0, "ereignisse": set(), "stunden": {},
                      "sicherheit": "gemessen" if gid == "nuc" else "vermutet", "w": round(wert * faktor, 1)}
    unbekannt_w = max(0.0, g - summe)
    teile["grundlast_rest"] = {"kwh": unbekannt_w * stunden / 1000, "netz_kwh": 0.0, "ereignisse": set(), "stunden": {},
                               "sicherheit": "vermutet", "w": round(unbekannt_w)}
    grund_netz = sum(g * s / 1000 * n for s, n in zip(std, netzanteil))
    for gid in [k for k, _ in verteilt] + ["grundlast_rest"]:
        anteil = teile[gid]["kwh"] / (g * stunden / 1000) if g else 0
        teile[gid]["netz_kwh"] = grund_netz * anteil

    # Solarladestation (exakt gemessen, myStrom) – steckt in Abend/Grundlast, wird separat ausgewiesen
    for gd in laden:
        if gd.get("id") == "laden-mobil":
            teile["solarladestation"] = {"kwh": gd["woche"]["kwh"] * tage / 7, "netz_kwh": 0.0, "ereignisse": set(),
                                         "stunden": {}, "sicherheit": "gemessen"}

    gesamt = sum(r["verbrauch_kwh"] for r in rows)
    NAMEN = {
        "infrarot": "Infrarotheizung Dachstock", "kochen": "Kochen & Backen (Herd, Backofen)", "spuelmaschine": "Spülmaschine",
        "waschmaschine": "Waschmaschine", "tumbler": "Wärmepumpentrockner", "fruehstueck": "Frühstück (Wasserkocher & Co.)",
        "abend": "Licht, Medien & Computer (abends)", "uebrig": "Übrige Geräte", "nuc": "homi-Server",
        "router": "2 Router", "netzwerk": "Netzwerkgeräte", "foodcenter": "Foodcenter (Kühl-/Gefrierkombi)",
        "ventilator": "Abluftventilator Bad", "standby": "Standby (Boxen, Beamer, Computer, Hue)",
        "grundlast_rest": "Unbekannte Dauerlast", "solarladestation": "Solarladestation (Handy & Co.)",
    }
    HINWEIS = {
        "infrarot": (f"Leistung {infrarot_w} W gemessen (Ein-/Ausschalttest); " if (geraete.get("infrarot") or {}).get("w_typ") else "") + (f"läuft in langen, gleichmässigen Blöcken mit rund {infrarot_w} W" if infrarot_w else ""),
        "kochen": "Spitzen über Mittag und abends", "spuelmaschine": "Heizphase spät abends",
        "waschmaschine": "aus deinen Waschgängen bekannt", "abend": "Abendlast über der Grundlast",
        "grundlast_rest": "läuft rund um die Uhr, keinem Gerät der Liste zuzuordnen",
    }
    aus = []
    for gid, t in sorted(teile.items(), key=lambda kv: -kv[1]["kwh"]):
        if t["kwh"] < 0.05:
            continue
        haupt = sorted(t["stunden"].items(), key=lambda kv: -kv[1])[:3]
        aus.append({
            "id": gid, "name": NAMEN.get(gid, gd_name(geraete, gid)), "kwh": round(t["kwh"], 2),
            "kwh_tag": round(t["kwh"] / tage, 2), "kwh_jahr": round(t["kwh"] / tage * 365),
            "anteil_pct": round(100 * t["kwh"] / gesamt, 1) if gesamt else 0,
            "netz_kwh": round(t["netz_kwh"], 2), "netz_pct": round(100 * t["netz_kwh"] / t["kwh"]) if t["kwh"] else 0,
            "chf": round(t["netz_kwh"] * bezug_rp / 100 + (t["kwh"] - t["netz_kwh"]) * rueck_rp / 100, 2),
            "ereignisse": len(t["ereignisse"]), "haupt_stunden": [h for h, _ in sorted(haupt)],
            "sicherheit": t["sicherheit"], "w": t.get("w"), "hinweis": HINWEIS.get(gid, ""),
        })
    return {
        "aktiv": True, "tage": round(tage, 1),
        "zeitraum": f"{(heute - timedelta(days=TAGE)).strftime('%d.%m.')}–{(heute - timedelta(days=1)).strftime('%d.%m.%Y')}",
        "gesamt_kwh": round(gesamt, 2), "grundlast_w": round(g), "infrarot_w": infrarot_w,
        "bezug_rp": round(bezug_rp, 2), "rueck_rp": round(rueck_rp, 2),
        "geraete": aus, "jetzt": jetzt(g, infrarot_w, geraete),
        "hinweis": "Geschätzt aus dem Stromzähler: homi sieht nur die Summe und ordnet sie nach Mustern den Geräten zu. "
                   "«gemessen» = exakt (eigener Zähler), «sicher» = bekannte Messung, «wahrscheinlich» = klares Muster, "
                   "«vermutet» = passt, aber auch anderes möglich.",
    }


def gd_name(geraete, gid):
    return (geraete.get(gid) or {}).get("name", gid)


def jetzt(g, infrarot_w, geraete):
    """Was läuft vermutlich gerade? Aus den Minutenwerten der letzten 20 Minuten."""
    with lock:
        h = list(zustand.history)[-20:]
        st = dict(state.get("status") or {})
    if not h or not st.get("fronius_ok"):
        return []
    v = [p["verbrauch_w"] for p in h if p.get("verbrauch_w") is not None]
    if not v:
        return []
    akt = st.get("verbrauch_w") or v[-1]
    ueber = max(0, akt - g)
    aus = [{"id": "grundlast", "name": "Dauerläufer (Router, Foodcenter, Server …)", "w": round(min(akt, g))}]
    stunde = datetime.now().hour
    stabil = len(v) >= 10 and statistics.pstdev(v[-10:]) < 0.15 * max(1, statistics.mean(v[-10:]))
    if infrarot_w and ueber >= 0.75 * infrarot_w and stabil and ueber <= 1.4 * infrarot_w + 300:
        aus.append({"id": "infrarot", "name": "Infrarotheizung Dachstock", "w": round(min(ueber, infrarot_w))})
        ueber -= min(ueber, infrarot_w)
    if ueber >= 1200 and (11 <= stunde < 14 or 17 <= stunde < 21):
        aus.append({"id": "kochen", "name": "Kochen (Herd oder Backofen)", "w": round(ueber)}); ueber = 0
    elif ueber >= 900 and (stunde >= 21 or stunde < 3):
        aus.append({"id": "spuelmaschine", "name": "Spülmaschine (vermutet)", "w": round(ueber)}); ueber = 0
    elif ueber >= SCHWELLE_W:
        aus.append({"id": "uebrig", "name": "Ein grösseres Gerät", "w": round(ueber)}); ueber = 0
    if ueber > 20:
        aus.append({"id": "abend", "name": "Licht, Medien & Computer", "w": round(ueber)})
    return aus


def zuordnung():
    if time.time() - _cache["zeit"] < CACHE_S and _cache["daten"]:
        d = dict(_cache["daten"])
        d["jetzt"] = jetzt(d.get("grundlast_w") or 0, d.get("infrarot_w"), {})
        return d
    d = berechnen()
    _cache.update(zeit=time.time(), daten=d)
    return d
