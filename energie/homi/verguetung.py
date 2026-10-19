"""Abnahmevergütung ab 2027: Vergütungsmodelle, Minimalvergütung, Referenz-Marktpreis und Abregelschwelle.

Rechtslage (Energieverordnung, Revision Mai 2026, in Kraft 1.1.2027; Art. 15 Abs. 1bis EnG):
- Einigen sich Netzbetreiber und Produzent nicht, wird zum Day-Ahead-Spotpreis Schweiz zum Zeitpunkt der Einspeisung
  vergütet (stündlich, später viertelstündlich), zum SNB-Tageskurs (Art. 12 Abs. 1 EnV).
- Anlagen < 150 kW haben eine Minimalvergütung (Art. 12a EnV): bis 30 kW 6 Rp./kWh, darüber 180 / kW Rp. (150 kW:
  1.2 Rp.). Sie wird NICHT direkt pro kWh bezahlt: Lag der Referenz-Marktpreis des Quartals (Art. 12 Abs. 2 EnV,
  vierteljährlich gemittelt) darunter, gibt es nachträglich die Differenz pro eingespeister kWh («Ausgleich»).
- Ohne intelligentes Messsystem bis Ende 2027 Abrechnung zum Referenz-Marktpreis bzw. zur Minimalvergütung.
Folge für die Steuerung: Eine eingespeiste kWh bringt Spotpreis + Ausgleich. Abregeln lohnt sich erst, wenn der
Spotpreis unter −Ausgleich fällt. Ob der Ausgleich auch für kWh bei negativen Preisen gilt, ist politisch noch offen
(Swissolar 2025: «bei negativen Preisen sollen Vergütungen wegfallen») → einstellbar (ausgleich_bei_minuspreis).

Modelle in anlage.json → verguetung.modell:
  fix            fester Rückliefertarif des Netzbetreibers (heute GWS) – Abregeln lohnt sich nie
  referenzmarkt  Quartalsdurchschnitt bzw. Minimalvergütung (ohne intelligenten Zähler) – Abregeln lohnt sich nie
  boerse         Spotpreis + Ausgleich (ab 2027 mit intelligentem Zähler) – abregeln unter der Schwelle
Den Referenz-Marktpreis schätzt homi aus dem eigenen Archiv (Spotpreis gewichtet mit der eigenen Solarproduktion,
wie der Referenz-Marktpreis Photovoltaik mit der Schweizer PV-Produktion gewichtet ist). Veröffentlichte offizielle
Werte gehen vor (verguetung.rmp_offiziell, z. B. {"2026-Q3": 4.1}).
"""
import time
from datetime import date

from . import zustand
from .konfig import ANLAGE_CFG, load_json

MODELLE = {
    "fix": "Fester Rückliefertarif",
    "referenzmarkt": "Quartalsdurchschnitt / Minimalvergütung",
    "boerse": "Börsenpreis + Ausgleich (ab 2027)",
}
CACHE_S = 600
_cache = {"zeit": 0.0, "daten": None}


def konfig():
    try:
        a = load_json(ANLAGE_CFG)
    except (OSError, ValueError):
        a = {}
    v = a.get("verguetung") or {}
    kw = a.get("max_kw") or 0
    auto = 6.0 if kw <= 30 else (round(180 / kw, 2) if kw < 150 else 0.0)
    return {"modell": v.get("modell", "fix") if v.get("modell") in MODELLE else "fix",
            "minimal_rp": v.get("minimal_rp") if v.get("minimal_rp") is not None else auto,
            "ausgleich_bei_minuspreis": v.get("ausgleich_bei_minuspreis", True),
            "rmp_offiziell": v.get("rmp_offiziell") or {}, "kw": kw}


def quartal(tag):
    return f"{tag.year}-Q{(tag.month - 1) // 3 + 1}"


def quartal_start(q):
    jahr, n = int(q[:4]), int(q[-1])
    return date(jahr, 3 * (n - 1) + 1, 1)


def ausgleich_eff(p_rp, ausgleich, k):
    """Ausgleich, der auf eine kWh zum Spotpreis p tatsächlich anfällt."""
    return ausgleich if (p_rp >= 0 or k["ausgleich_bei_minuspreis"]) else 0.0


def schwelle_rp(ausgleich, k):
    """Unter diesem Spotpreis bringt Einspeisen weniger als nichts (Modell «boerse»)."""
    return round(0.0 - ausgleich, 2) if k["ausgleich_bei_minuspreis"] and ausgleich > 0 else 0.0


def berechnen():
    """Quartale seit Beginn des Archivs: Referenz-Marktpreis, Ausgleich, Wert der Einspeisung je Modell."""
    k = konfig()
    heute = date.today()
    q_jetzt = quartal(heute)
    qs = [q_jetzt]
    for _ in range(3):                                           # bis zu drei Quartale zurück
        s = quartal_start(qs[-1])
        qs.append(quartal(date(s.year - 1, 12, 1) if s.month == 1 else date(s.year, s.month - 3, 1)))
    rows = zustand.archiv.lesen(quartal_start(qs[-1]), heute)
    aus = []
    for q in reversed(qs):
        r = [z for z in rows if quartal(date.fromisoformat(z["zeit"][:10])) == q]
        if not r:
            continue
        mit_preis = [z for z in r if z["boerse_rp"] is not None]
        pv = sum(z["pv_kwh"] or 0 for z in mit_preis)
        rmp_schaetz = sum((z["pv_kwh"] or 0) * z["boerse_rp"] for z in mit_preis) / pv if pv > 0 else None
        rmp = k["rmp_offiziell"].get(q, rmp_schaetz)
        ausgleich = max(0.0, k["minimal_rp"] - rmp) if rmp is not None else 0.0
        schwelle = schwelle_rp(ausgleich, k)
        e_alle = sum(z["einspeisung_kwh"] or 0 for z in r)
        e_preis = sum(z["einspeisung_kwh"] or 0 for z in mit_preis)
        # alle Modelle auf derselben Basis: nur Zeiten, für die ein Börsenpreis gespeichert ist
        fix = sum(z["erloes_chf"] or 0 for z in mit_preis)
        ref = e_preis * max(rmp or 0, k["minimal_rp"]) / 100
        ohne = mit = minus_kwh = minus_h = 0.0
        for z in mit_preis:
            e, p = z["einspeisung_kwh"] or 0, z["boerse_rp"]
            wert = e * (p + ausgleich_eff(p, ausgleich, k)) / 100
            ohne += wert
            if p < schwelle:
                minus_kwh += e
                minus_h += (z["sekunden"] or 0) / 3600
            else:
                mit += wert
        tage = len({z["zeit"][:10] for z in r})
        tage_preis = len({z["zeit"][:10] for z in mit_preis})
        aus.append({
            "quartal": q, "laufend": q == q_jetzt, "tage": tage, "tage_mit_boersenpreis": tage_preis,
            "einspeisung_kwh": round(e_alle, 2), "einspeisung_mit_preis_kwh": round(e_preis, 2),
            "rmp_rp": round(rmp, 2) if rmp is not None else None,
            "rmp_quelle": "offiziell" if q in k["rmp_offiziell"] else "Schätzung homi (eigene Solarproduktion)",
            "ausgleich_rp": round(ausgleich, 2), "schwelle_rp": round(schwelle, 2),
            "abregeln_h": round(minus_h, 2), "abregeln_kwh": round(minus_kwh, 2),
            "wert_chf": {"fix": round(fix, 2), "referenzmarkt": round(ref, 2),
                         "boerse": round(ohne, 2), "boerse_abgeregelt": round(mit, 2)},
            "vergleich_basis": "eingespeiste kWh mit gespeichertem Börsenpreis (einspeisung_mit_preis_kwh)",
        })
    return {
        "modell": k["modell"], "modell_name": MODELLE[k["modell"]], "modelle": MODELLE,
        "minimal_rp": k["minimal_rp"], "anlage_kw": k["kw"],
        "ausgleich_bei_minuspreis": k["ausgleich_bei_minuspreis"],
        "quartale": aus,
        "erklaerung": (
            f"Ab 2027 wird Solarstrom ohne andere Abmachung zum Börsenpreis der Stunde vergütet (SNB-Tageskurs). "
            f"Für Anlagen unter 150 kW gilt eine Minimalvergütung, für deine Anlage ({k['kw']:g} kW) "
            f"{k['minimal_rp']:g} Rp./kWh: Lag der Quartalsdurchschnitt (Referenz-Marktpreis) darunter, gibt es die "
            "Differenz nachträglich auf jede eingespeiste kWh. Abregeln lohnt sich deshalb erst, wenn der Börsenpreis "
            "unter minus diesem Ausgleich fällt. Ohne intelligenten Zähler gilt bis Ende 2027 der Quartalsdurchschnitt."),
    }


def verguetung():
    if time.time() - _cache["zeit"] < CACHE_S and _cache["daten"]:
        return _cache["daten"]
    d = berechnen()
    _cache.update(zeit=time.time(), daten=d)
    return d


def schwelle_jetzt():
    """(Schwelle für das aktive Modell oder None = abregeln lohnt sich nie, Schwelle im Modell «boerse», Ausgleich)."""
    d = _cache["daten"]
    if d is None:
        return (None, 0.0, 0.0) if konfig()["modell"] != "boerse" else (0.0, 0.0, 0.0)
    q = next((x for x in d["quartale"] if x["laufend"]), None)
    s, a = (q["schwelle_rp"], q["ausgleich_rp"]) if q else (0.0, 0.0)
    return (s if d["modell"] == "boerse" else None), s, a


def poll_verguetung():
    """Hält den Cache warm (die Steuerung braucht die Schwelle ohne Wartezeit)."""
    while True:
        try:
            verguetung()
        except (OSError, ValueError, KeyError, ZeroDivisionError) as e:
            print("Vergütung:", e, flush=True)
        time.sleep(CACHE_S)

