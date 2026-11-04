"""Abregel-Übersicht (wie das Einspeise-Widget von Solar Manager): wann lohnt sich Einspeisen nicht, heute, morgen und
rückblickend. Gerechnet wird immer mit dem Modell ab 2027 (Börsenpreis + Ausgleich), auch wenn heute der feste Tarif
gilt – so sieht man, was ab 2027 passieren würde.

Stopp = Börsenpreis unter der Abregelschwelle des Quartals (verguetung.py). Heute und morgen aus Day-Ahead-Preisen und
Solarprognose (einspeisung.py), Vergangenheit aus dem 10-Minuten-Archiv (eingespeiste kWh in Stopp-Zeiten)."""
from datetime import date, timedelta

from . import zustand
from .einspeisung import bloecke, einspeise_plan
from .verguetung import ausgleich_eff, konfig as verg_konfig, quartal, verguetung

HISTORIE_TAGE = 366


def _schwellen():
    """{Quartal: (Schwelle, Ausgleich)} aus der Vergütungsrechnung; Quartale ohne Rechnung: 0 Rp., kein Ausgleich."""
    try:
        return {q["quartal"]: (q["schwelle_rp"], q["ausgleich_rp"]) for q in verguetung()["quartale"]}
    except (OSError, ValueError, KeyError, ZeroDivisionError):
        return {}


def _tag(plan, schwelle, ausgleich, k):
    if not plan:
        return None
    monat = int(plan["datum"][5:7])
    stunden = []
    for x in plan["stunden"]:
        p, e = x["preis_rp"], x["ueberschuss_kwh"]
        stopp = p < schwelle
        stunden.append({"stunde": x["stunde"], "preis_rp": p, "pv_kwh": x["pv_kwh"], "einspeisung_kwh": e,
                        "stopp": stopp})
    stopp_h = [x["stunde"] for x in stunden if x["stopp"]]
    betroffen = [x for x in stunden if x["stopp"] and x["einspeisung_kwh"] > 0.05]
    return {
        "datum": plan["datum"], "stunden": stunden,
        "stopps": bloecke(stopp_h), "stopp_h": len(stopp_h),
        "betroffen_kwh": round(sum(x["einspeisung_kwh"] for x in betroffen), 2),
        # was diese kWh ab 2027 kosten würden, wenn man sie trotzdem einspeist (negativ = Kosten)
        "ohne_stopp_chf": round(sum(x["einspeisung_kwh"] * (x["preis_rp"] + ausgleich_eff(x["preis_rp"], ausgleich, k, monat))
                                    for x in betroffen) / 100, 2),
        "einspeisung_kwh": round(sum(x["einspeisung_kwh"] for x in stunden), 2),
    }


def historie(schwellen):
    """Je Tag und Monat: eingespeiste kWh, Stunden und kWh unter der Schwelle (aus dem Archiv)."""
    heute = date.today()
    rows = zustand.archiv.lesen(heute - timedelta(days=HISTORIE_TAGE), heute)
    tage = {}
    for z in rows:
        tag = z["zeit"][:10]
        d = tage.setdefault(tag, {"tag": tag, "einspeisung_kwh": 0.0, "stopp_h": 0.0, "stopp_kwh": 0.0})
        e = z["einspeisung_kwh"] or 0
        d["einspeisung_kwh"] += e
        p = z["boerse_rp"]
        if p is not None and p < schwellen.get(quartal(date.fromisoformat(tag)), (0.0, 0.0))[0]:
            d["stopp_h"] += (z["sekunden"] or 0) / 3600
            d["stopp_kwh"] += e
    monate = {}
    for d in tage.values():
        m = monate.setdefault(d["tag"][:7], {"monat": d["tag"][:7], "einspeisung_kwh": 0.0, "stopp_h": 0.0,
                                              "stopp_kwh": 0.0, "stopp_tage": 0})
        for f in ("einspeisung_kwh", "stopp_h", "stopp_kwh"):
            m[f] += d[f]
        m["stopp_tage"] += d["stopp_h"] > 0
    runden = lambda d: {k: (round(v, 2) if isinstance(v, float) else v) for k, v in d.items()}
    return [runden(d) for d in sorted(tage.values(), key=lambda d: d["tag"])], \
           [runden(m) for m in sorted(monate.values(), key=lambda m: m["monat"])]


def abregelung():
    k = verg_konfig()
    schwellen = _schwellen()
    plan = einspeise_plan()
    if plan.get("startet"):
        return {"startet": True}
    heute = date.today()
    aus = {"modell": k["modell"], "abregeln_aktiv": k["modell"] == "boerse",
           "erklaerung": "Stopp = Börsenpreis unter der Abregelschwelle (Modell ab 2027: Börsenpreis + Ausgleich der "
                         "Minimalvergütung). Beim festen Tarif regelt homi nicht ab – die Übersicht zeigt, was ab 2027 "
                         "passieren würde."}
    for name, tag in (("heute", heute), ("morgen", heute + timedelta(days=1))):
        s, a = schwellen.get(quartal(tag), (0.0, 0.0))
        aus[name] = _tag(plan.get(name), s, a, k)
        if aus[name]:
            aus[name]["schwelle_rp"] = s
    aus["tage"], aus["monate"] = historie(schwellen)
    return aus
