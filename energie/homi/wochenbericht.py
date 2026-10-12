"""Wochenbericht: die letzte ganze Woche (Montag–Sonntag) im Überblick, mit Vergleich zur Vorwoche."""
import time
from datetime import date, datetime, timedelta

from . import zustand
from .amortisation import amort_cache, amortisation
from .berater import berater, berater_cache
from .zustand import state

WT = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
bericht_cache = {}


def _summe(rows):
    s = {k: sum(r[k] for r in rows) for k in ("sekunden", "pv_kwh", "verbrauch_kwh", "bezug_kwh", "einspeisung_kwh",
                                              "kosten_chf", "erloes_chf")}
    s["saldo_chf"] = s["erloes_chf"] - s["kosten_chf"]
    s["eigen_kwh"] = max(0.0, s["pv_kwh"] - s["einspeisung_kwh"])
    s["autarkie_pct"] = 100 * (1 - s["bezug_kwh"] / s["verbrauch_kwh"]) if s["verbrauch_kwh"] > 0 else None
    s["eigenverbrauch_pct"] = 100 * s["eigen_kwh"] / s["pv_kwh"] if s["pv_kwh"] > 0 else None
    return s


def _veraenderung(neu, alt):
    return round(100 * (neu - alt) / alt) if alt else None


def wochenbericht(woche=None):
    """woche: «2026-W41» (ISO-Woche) oder None = letzte abgeschlossene Woche."""
    heute = date.today()
    if woche:
        jahr, kw = woche.split("-W")
        montag = date.fromisocalendar(int(jahr), int(kw), 1)
    else:
        montag = heute - timedelta(days=heute.weekday() + 7)
    if montag + timedelta(days=6) >= heute:
        raise ValueError("Woche ist noch nicht abgeschlossen")
    schluessel = montag.isoformat()
    if schluessel in bericht_cache and time.time() - bericht_cache[schluessel]["zeit"] < 3600:
        return bericht_cache[schluessel]["daten"]
    sonntag = montag + timedelta(days=6)
    rows = [r for r in zustand.archiv.lesen(montag, sonntag) if r["sekunden"]]
    vor = [r for r in zustand.archiv.lesen(montag - timedelta(days=7), montag - timedelta(days=1)) if r["sekunden"]]
    kw = montag.isocalendar()
    titel = f"KW {kw.week} · {montag.day}.{montag.month}.–{sonntag.day}.{sonntag.month}.{sonntag.year}"
    if not rows:
        return {"aktiv": False, "woche": f"{kw.year}-W{kw.week:02d}", "titel": titel, "grund": "Für diese Woche gibt es keine Messwerte."}
    s, v = _summe(rows), (_summe(vor) if vor else None)
    tage = {}
    for r in rows:
        tage.setdefault(r["zeit"][:10], {"pv": 0.0, "verbrauch": 0.0})
        tage[r["zeit"][:10]]["pv"] += r["pv_kwh"]
        tage[r["zeit"][:10]]["verbrauch"] += r["verbrauch_kwh"]
    bester = max(tage.items(), key=lambda x: x[1]["pv"])
    schwach = min(tage.items(), key=lambda x: x[1]["pv"])
    # negative Börsenpreise: Stunden mit mindestens einem 10-Minuten-Wert unter null
    minus_std = len({r["zeit"][:13] for r in rows if r.get("boerse_rp") not in ("", None) and float(r["boerse_rp"]) < 0})
    # Wert des Solarstroms dieser Woche (wie in der Amortisation: selbst genutzt × Bezug + eingespeist × Rücklieferung)
    t = (state.get("status") or {}).get("tarif") or {}
    bezug, rueck = t.get("bezug_chf_kwh") or 0, t.get("rueckliefer_chf_kwh") or 0
    wert = s["eigen_kwh"] * bezug + s["einspeisung_kwh"] * rueck
    try:
        am = amortisation()
    except (OSError, ValueError, KeyError):
        am = amort_cache["daten"] or {}
    amort = None
    if am.get("aktiv") and am.get("netto_chf"):
        amort = {"pct": am["amortisiert_pct"], "plus_pct": round(100 * wert / am["netto_chf"], 2),
                 "break_even": am.get("break_even"), "wert_chf": round(wert, 2)}
    # Batterie-Simulation: Ersparnis dieser Woche je Grösse
    batterie = []
    try:
        for sp in zustand.speicher_sim.report().get("speicher", []):
            wtage = [d for d in sp.get("tage", []) if montag.isoformat() <= d["tag"] <= sonntag.isoformat()]
            if wtage:
                batterie.append({"name": sp["name"], "kwh": sp["kapazitaet_kwh"],
                                 "ersparnis_chf": round(sum(d["ersparnis_chf"] for d in wtage), 2)})
    except Exception:                            # Simulation ist Beiwerk
        pass
    # Tipp der Woche: die Berater-Tipps reihum, jede Woche ein anderer
    try:
        b = berater()
    except (OSError, ValueError, KeyError, ZeroDivisionError):
        b = berater_cache["daten"] or {}
    tipp = None
    if b.get("aktiv") and b.get("tipps"):
        x = b["tipps"][kw.week % len(b["tipps"])]
        tipp = {"titel": x["titel"], "chf_jahr": x.get("chf_jahr"), "aktion": x.get("aktion"), "warum": x.get("warum")}
    tag_name = lambda d: WT[date.fromisoformat(d).weekday()]
    vergleich = None
    if v and v["sekunden"] > 3 * 86400:
        vergleich = {"pv_pct": _veraenderung(s["pv_kwh"], v["pv_kwh"]), "verbrauch_pct": _veraenderung(s["verbrauch_kwh"], v["verbrauch_kwh"]),
                     "saldo_chf": round(s["saldo_chf"] - v["saldo_chf"], 2), "vorwoche_pv_kwh": round(v["pv_kwh"], 1),
                     "vorwoche_saldo_chf": round(v["saldo_chf"], 2)}
    satz = (f"Deine Anlage hat {s['pv_kwh']:.0f} kWh erzeugt"
            + (f" ({vergleich['pv_pct']:+d} % zur Vorwoche)".replace("-", "−") if vergleich and vergleich["pv_pct"] is not None else "")
            + f", dein Haus hat {s['verbrauch_kwh']:.0f} kWh gebraucht"
            + (f", {s['autarkie_pct']:.0f} % davon aus eigenem Strom" if s["autarkie_pct"] is not None else "")
            + f". Bilanz: {'+' if s['saldo_chf'] >= 0 else '−'}CHF {abs(s['saldo_chf']):.2f}.")
    daten = {
        "aktiv": True, "woche": f"{kw.year}-W{kw.week:02d}", "titel": titel, "von": montag.isoformat(), "bis": sonntag.isoformat(),
        "vollstaendig_pct": round(100 * s["sekunden"] / (7 * 86400)), "satz": satz,
        "pv_kwh": round(s["pv_kwh"], 1), "verbrauch_kwh": round(s["verbrauch_kwh"], 1), "bezug_kwh": round(s["bezug_kwh"], 1),
        "einspeisung_kwh": round(s["einspeisung_kwh"], 1), "eigen_kwh": round(s["eigen_kwh"], 1),
        "autarkie_pct": round(s["autarkie_pct"]) if s["autarkie_pct"] is not None else None,
        "eigenverbrauch_pct": round(s["eigenverbrauch_pct"]) if s["eigenverbrauch_pct"] is not None else None,
        "erloes_chf": round(s["erloes_chf"], 2), "kosten_chf": round(s["kosten_chf"], 2), "saldo_chf": round(s["saldo_chf"], 2),
        "bester_tag": {"tag": bester[0], "name": tag_name(bester[0]), "pv_kwh": round(bester[1]["pv"], 1)},
        "schwaechster_tag": {"tag": schwach[0], "name": tag_name(schwach[0]), "pv_kwh": round(schwach[1]["pv"], 1)},
        "tage": [{"tag": d, "name": tag_name(d), "pv_kwh": round(x["pv"], 1), "verbrauch_kwh": round(x["verbrauch"], 1)}
                 for d, x in sorted(tage.items())],
        "minuspreis_stunden": minus_std, "vergleich": vergleich, "amortisation": amort, "batterie": batterie, "tipp": tipp,
        "erstellt": datetime.now().isoformat(timespec="minutes"),
    }
    bericht_cache[schluessel] = {"zeit": time.time(), "daten": daten}
    return daten
