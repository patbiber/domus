"""Simulation virtueller Batteriespeicher mit den echten Netzwerten."""
import json
import os
from datetime import date, datetime

from .konfig import MAX_DT_S, SPEICHER, WIRKUNGSGRAD


class SpeicherSimulation:
    """Rechnet mit dem echten Netzsaldo nach, was ein Speicher gespart hätte.

    Überschuss (Einspeisung) lädt den virtuellen Speicher, Netzbezug entlädt ihn. Die entgangene
    Rückliefervergütung wird mit der Energie im Speicher mitgeführt (wert_chf) und erst beim Entladen
    verrechnet: Ersparnis = vermiedener Bezug × Bezugspreis − anteilige entgangene Vergütung.
    """

    def __init__(self, path):
        self.path = path
        self.last_ts = None
        try:
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {"start": datetime.now().isoformat(timespec="seconds"), "sims": {}}
        for sp in SPEICHER:
            self.data["sims"].setdefault(sp["id"], {"soc_wh": 0.0, "wert_chf": 0.0, "tage": {}})

    def step(self, status, now):
        ts = now.timestamp()
        dt = ts - self.last_ts if self.last_ts else None
        self.last_ts = ts
        t = status.get("tarif")
        if not status.get("fronius_ok") or not t or dt is None or dt <= 0 or dt > MAX_DT_S:
            return
        h = dt / 3600
        netz = status["netz_w"]                     # + Bezug, - Einspeisung
        tag = now.date().isoformat()
        rueck = t["rueckliefer_chf_kwh"] or 0
        for sp in SPEICHER:
            sim = self.data["sims"][sp["id"]]
            cap = sp["kapazitaet_kwh"] * 1000
            d = sim["tage"].setdefault(tag, {"stunden": 0.0, "geladen_kwh": 0.0, "entladen_kwh": 0.0,
                                             "bezug_ohne_kwh": 0.0, "bezug_mit_kwh": 0.0, "ersparnis_chf": 0.0})
            laden = entladen = kosten = 0.0         # W am Netzanschluss, CHF
            if netz < 0:
                laden = min(-netz, sp["laden_w"], (cap - sim["soc_wh"]) / WIRKUNGSGRAD / h)
                sim["soc_wh"] += laden * h * WIRKUNGSGRAD
                sim["wert_chf"] += laden * h / 1000 * rueck
            elif netz > 0 and sim["soc_wh"] > 0:
                entladen = min(netz, sp["entladen_w"], sim["soc_wh"] * WIRKUNGSGRAD / h)
                anteil = min(entladen * h / WIRKUNGSGRAD / sim["soc_wh"], 1.0)
                kosten = sim["wert_chf"] * anteil
                sim["wert_chf"] -= kosten
                sim["soc_wh"] -= entladen * h / WIRKUNGSGRAD
            sim["soc_wh"] = min(max(sim["soc_wh"], 0.0), cap)
            d["stunden"] += h
            d["geladen_kwh"] += laden * h / 1000
            d["entladen_kwh"] += entladen * h / 1000
            d["bezug_ohne_kwh"] += max(netz, 0) * h / 1000
            d["bezug_mit_kwh"] += (max(netz, 0) - entladen) * h / 1000
            d["ersparnis_chf"] += entladen * h / 1000 * t["bezug_chf_kwh"] - kosten

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        os.replace(tmp, self.path)

    def report(self):
        heute = date.today().isoformat()
        out = {"start": self.data["start"], "wirkungsgrad": WIRKUNGSGRAD ** 2, "speicher": []}
        for sp in SPEICHER:
            sim = self.data["sims"][sp["id"]]
            tage = sim["tage"]
            summe = {k: sum(d[k] for d in tage.values()) for k in
                     ("stunden", "geladen_kwh", "entladen_kwh", "bezug_ohne_kwh", "bezug_mit_kwh", "ersparnis_chf")}
            letzte7 = [d for k, d in sorted(tage.items())[-7:]]
            s7 = {k: sum(d[k] for d in letzte7) for k in ("stunden", "ersparnis_chf", "entladen_kwh")}
            r2 = lambda v, n=3: round(v, n)
            out["speicher"].append({
                **sp,
                "soc_pct": r2(sim["soc_wh"] / (sp["kapazitaet_kwh"] * 10), 1),
                "heute": {k: r2(v) for k, v in tage.get(heute, {}).items()},
                "summe": {k: r2(v) for k, v in summe.items()},
                "tage_gemessen": r2(summe["stunden"] / 24, 2),
                "zyklen": r2(summe["entladen_kwh"] / sp["kapazitaet_kwh"], 1),
                "autarkie_plus_pct": r2(100 * summe["entladen_kwh"] / summe["bezug_ohne_kwh"], 1) if summe["bezug_ohne_kwh"] else None,
                # Hochrechnung aus den letzten 7 Tagen – saisonal stark schwankend, nur als Richtwert
                "ersparnis_pro_jahr_chf": r2(s7["ersparnis_chf"] / s7["stunden"] * 24 * 365, 0) if s7["stunden"] >= 20 else None,
                "tage": [{"tag": k, **{kk: r2(vv) for kk, vv in d.items()}} for k, d in sorted(tage.items())[-60:]],
            })
        return out
