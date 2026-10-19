"""Einspeise-Fahrplan bei dynamischer Vergütung, Minuspreis-Stunden."""
from datetime import date, timedelta

from . import zustand
from .archiv import archiv_aggregat
from .zustand import lock, state


FAHRPLAN_BATTERIE = {"kapazitaet_kwh": 5, "leistung_kw": 3, "wirkungsgrad": 0.9}   # wie Speicher-Simulation 5 kWh

def typische_last():
    """Mittlerer Verbrauch pro Stunde des Tages (kWh) aus den letzten 7 Tagen des Archivs."""
    heute = date.today()
    rows = archiv_aggregat(zustand.archiv.lesen(heute - timedelta(days=7), heute - timedelta(days=1)), "stunde")["zeilen"]
    pro_h = {}
    for z in rows:
        if z["sekunden"] >= 3000:
            pro_h.setdefault(int(z["zeit"][11:13]), []).append(z["verbrauch_kwh"] * 3600 / z["sekunden"])
    return {h: (sum(v) / len(v) if v else 0.3) for h, v in ((h, pro_h.get(h, [])) for h in range(24))}

def tag_fahrplan(tag_, stunden, preise, last, tarif_rp):
    """Fahrplan eines Tages: Überschuss, Minuspreise, einfaches vs. optimiertes Laden einer Batterie.
    Annahme dynamische Vergütung = Day-Ahead-Börsenpreis (auch negativ)."""
    B = FAHRPLAN_BATTERIE
    h_ = []
    for h in range(24):
        k = f"{tag_}T{h:02d}"
        pv = stunden.get(k)
        p = preise.get(k)
        if pv is None or p is None:
            continue
        l = last[h]
        h_.append({"stunde": h, "pv_kwh": round(pv, 2), "last_kwh": round(l, 2), "preis_rp": p,
                   "ueberschuss": max(pv - l, 0), "defizit": max(l - pv, 0)})
    if not h_:
        return None

    def bewerten(lade):                             # lade: {stunde: kWh in die Batterie}
        gespeichert = sum(lade.values()) * B["wirkungsgrad"]
        letzte_ladung = max(lade) if lade else -1
        rest, wert, aktion = gespeichert, 0.0, {}
        for x in h_:                                # Defizite nach dem Laden aus der Batterie decken (Wert = Bezugstarif)
            if x["stunde"] > letzte_ladung and rest > 0 and x["defizit"] > 0:
                d = min(rest, x["defizit"], B["leistung_kw"])
                rest -= d; wert += d * tarif_rp / 100; aktion[x["stunde"]] = f"aus Batterie {d:.1f} kWh"
        for x in sorted((x for x in h_ if x["stunde"] > letzte_ladung), key=lambda x: -x["preis_rp"]):
            if rest <= 0 or x["preis_rp"] <= 0:     # Rest zum besten Preis einspeisen
                break
            e = min(rest, B["leistung_kw"]); rest -= e; wert += e * x["preis_rp"] / 100
        for x in h_:                                # Überschuss, der nicht in die Batterie geht
            ex = x["ueberschuss"] - lade.get(x["stunde"], 0)
            x["_export"] = ex
        return wert, aktion

    # einfach: laden, sobald Überschuss da ist; alles andere einspeisen – auch bei Minuspreisen
    lade, frei = {}, B["kapazitaet_kwh"] / B["wirkungsgrad"]
    for x in h_:
        e = min(x["ueberschuss"], B["leistung_kw"], frei)
        if e > 0:
            lade[x["stunde"]] = e; frei -= e
    w_b, _ = bewerten(lade)
    einfach = w_b + sum((x["ueberschuss"] - lade.get(x["stunde"], 0)) * x["preis_rp"] / 100 for x in h_)
    # optimiert: zuerst in den billigsten (v. a. negativen) Stunden laden, bei Minuspreisen nicht einspeisen
    lade, frei = {}, B["kapazitaet_kwh"] / B["wirkungsgrad"]
    for x in sorted(h_, key=lambda x: x["preis_rp"]):
        e = min(x["ueberschuss"], B["leistung_kw"], frei)
        if e > 0:
            lade[x["stunde"]] = e; frei -= e
    w_b, aktion = bewerten(lade)
    optimiert = w_b + sum(max(x["ueberschuss"] - lade.get(x["stunde"], 0), 0) * x["preis_rp"] / 100
                          for x in h_ if x["preis_rp"] > 0)
    plan = []
    for x in h_:
        ex = x["ueberschuss"] - lade.get(x["stunde"], 0)
        if x["preis_rp"] < 0 and x["ueberschuss"] > 0.05:
            a = "Einspeisung stoppen" + (f", Batterie laden {lade[x['stunde']]:.1f} kWh" if x["stunde"] in lade else "")
        elif x["stunde"] in lade:
            a = f"Batterie laden {lade[x['stunde']]:.1f} kWh" + (f", {ex:.1f} kWh einspeisen" if ex > 0.05 else "")
        elif ex > 0.05:
            a = f"{ex:.1f} kWh einspeisen"
        else:
            a = aktion.get(x["stunde"], "Netzbezug" if x["defizit"] > 0.05 else "–")
        plan.append({k: v for k, v in x.items() if not k.startswith("_") and k not in ("ueberschuss", "defizit")} |
                    {"ueberschuss_kwh": round(x["ueberschuss"], 2), "aktion": a})
    neg = [x for x in h_ if x["preis_rp"] < 0]
    ueb_neg = sum(x["ueberschuss"] for x in neg)
    return {
        "datum": tag_, "stunden": plan,
        "minuspreis_stunden": [x["stunde"] for x in neg],
        "minuspreis_bloecke": bloecke([x["stunde"] for x in neg]),
        "ueberschuss_bei_minuspreis_kwh": round(ueb_neg, 1),
        "kosten_bei_minuspreis_chf": round(sum(x["ueberschuss"] * x["preis_rp"] / 100 for x in neg), 2),
        "einspeisung_dynamisch_chf": round(sum(x["ueberschuss"] * x["preis_rp"] / 100 for x in h_), 2),
        "batterie": {**B, "einfach_chf": round(einfach, 2), "optimiert_chf": round(optimiert, 2),
                     "mehrwert_chf": round(optimiert - einfach, 2)},
    }

def bloecke(stunden):
    """[11,12,13,16] -> ["11–14 Uhr", "16–17 Uhr"]"""
    out, start = [], None
    for i, h in enumerate(stunden):
        if start is None:
            start = h
        if i == len(stunden) - 1 or stunden[i + 1] != h + 1:
            out.append(f"{start:02d}–{h + 1:02d} Uhr"); start = None
    return out

def einspeise_plan():
    with lock:
        p, b, st = state["prognose"], state["boerse"], state["status"]
    if not p or not b:
        return {"startet": True}
    tarif = ((st or {}).get("tarif") or {})
    stunden = {x["zeit"][:13]: x["kwh"] for x in p["stunden"]}
    pro_h = {}                                     # Stunden- oder Viertelstundenpreise → Stundenmittel
    for x in b["preise"]:
        pro_h.setdefault(x["zeit"][:13], []).append(x["rp_kwh"])
    preise = {k: round(sum(v) / len(v), 2) for k, v in pro_h.items()}
    last = typische_last()
    heute = date.today()
    out = {"annahme": "Dynamische Einspeisevergütung = Day-Ahead-Börsenpreis Schweiz zum SNB-Tageskurs (Stundenmittel, "
                      "auch negativ); "
                      "Verbrauch = Mittel der letzten 7 Tage; Batterie virtuell (wie Speicher-Simulation).",
           "fix_verguetung_rp": round((tarif.get("rueckliefer_chf_kwh") or 0) * 100, 2),
           "bezug_rp": round((tarif.get("bezug_chf_kwh") or 0) * 100, 2)}
    for name, d in (("heute", heute), ("morgen", heute + timedelta(days=1))):
        out[name] = tag_fahrplan(d.isoformat(), stunden, preise, last, out["bezug_rp"])
        if out[name]:
            fix = sum(x["ueberschuss_kwh"] for x in out[name]["stunden"]) * out["fix_verguetung_rp"] / 100
            out[name]["einspeisung_fix_chf"] = round(fix, 2)
    return out
