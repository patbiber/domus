"""10-Minuten-Archiv (5 Jahre) und Zusammenfassungen."""
import csv
import gzip
import os
import shutil
from datetime import datetime, timedelta

from .konfig import ARCHIV_FELDER, ARCHIV_TAGE, MAX_DT_S, SLOT_S, SUMMEN


class Archiv:
    """10-Minuten-Archiv: eine CSV pro Tag unter <ARCHIV_DIR>/<Jahr>/<Datum>.csv.

    `sekunden` = wie viele Sekunden des Schritts gemessen wurden (600 = vollständig).
    Vergangene Tage werden gzip-komprimiert, Tage älter als 5 Jahre gelöscht.
    `quelle`: live = von homi gemessen, ha = einmalig aus Home Assistant übernommen.
    """

    def __init__(self, base):
        self.base = base
        self.slot = None
        self.acc = None
        self.last_ts = None
        self.tag = None

    def pfad(self, tag):
        return os.path.join(self.base, tag[:4], f"{tag}.csv")

    def schreiben(self, rows):
        for r in rows:
            p = self.pfad(r["zeit"][:10])
            os.makedirs(os.path.dirname(p), exist_ok=True)
            neu = not os.path.exists(p)
            with open(p, "a", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, ARCHIV_FELDER)
                if neu:
                    w.writeheader()
                w.writerow(r)

    def aufraeumen(self, heute):
        """Vergangene Tage komprimieren (an bestehende .gz anhängen), zu alte Tage löschen."""
        if not os.path.isdir(self.base):
            return
        grenze = (heute - timedelta(days=ARCHIV_TAGE)).isoformat()
        for jahr in os.listdir(self.base):
            d = os.path.join(self.base, jahr)
            for name in os.listdir(d):
                p = os.path.join(d, name)
                if name[:10] < grenze:
                    os.remove(p)
                elif name.endswith(".csv") and name[:10] < heute.isoformat():
                    with open(p, "rb") as src, gzip.open(p + ".gz", "ab") as dst:
                        shutil.copyfileobj(src, dst)
                    os.remove(p)
            if not os.listdir(d):
                os.rmdir(d)

    def lesen(self, von, bis):
        """Alle 10-Minuten-Zeilen von..bis (Datum), sortiert; doppelte Zeitpunkte: die vollständigere gewinnt."""
        rows = {}
        d = von
        while d <= bis:
            for p in (self.pfad(d.isoformat()) + ".gz", self.pfad(d.isoformat())):
                if not os.path.exists(p):
                    continue
                with (gzip.open if p.endswith(".gz") else open)(p, "rt", encoding="utf-8") as f:
                    for r in csv.DictReader(f):
                        if r["zeit"] == "zeit":          # Kopfzeile eines angehängten gzip-Teils
                            continue
                        alt = rows.get(r["zeit"])   # doppelt: die vollständigere Zeile gewinnt, bei Gleichstand live
                        if alt is None or (float(r["sekunden"] or 0), r["quelle"] == "live") > \
                                (float(alt["sekunden"] or 0), alt["quelle"] == "live"):
                            rows[r["zeit"]] = r
            d += timedelta(days=1)
        return [{"zeit": z, "quelle": rows[z]["quelle"],
                 **{k: (float(rows[z][k]) if rows[z][k] != "" else None)
                    for k in ARCHIV_FELDER if k not in ("zeit", "quelle")}} for z in sorted(rows)]

    def step(self, status, now):
        ts = now.timestamp()
        slot = int(ts // SLOT_S) * SLOT_S
        if slot != self.slot:
            self.flush()
            self.slot = slot
            self.acc = {"s": 0.0, "pv": 0.0, "vb": 0.0, "bz": 0.0, "es": 0.0, "k": 0.0, "e": 0.0, "bo": 0.0, "bo_s": 0.0}
            if self.tag != now.date():
                self.tag = now.date()
                self.aufraeumen(self.tag)
        dt = ts - self.last_ts if self.last_ts else None
        self.last_ts = ts
        if not status.get("fronius_ok") or dt is None or dt <= 0 or dt > MAX_DT_S:
            return
        a, h = self.acc, dt / 3600
        a["s"] += dt
        a["pv"] += status["pv_w"] * h / 1000
        a["vb"] += status["verbrauch_w"] * h / 1000
        a["bz"] += status["bezug_w"] * h / 1000
        a["es"] += status["einspeisung_w"] * h / 1000
        t = status.get("tarif")
        if t:
            a["k"] += status["bezug_w"] * h / 1000 * t["bezug_chf_kwh"]
            a["e"] += status["einspeisung_w"] * h / 1000 * (t["rueckliefer_chf_kwh"] or 0)
        b = status.get("boerse")
        if b and b.get("rp_kwh") is not None:
            a["bo"] += b["rp_kwh"] * dt
            a["bo_s"] += dt

    def flush(self):
        a = self.acc
        if not a or a["s"] < 1:
            return
        self.schreiben([{
            "zeit": datetime.fromtimestamp(self.slot).isoformat(timespec="minutes"),
            "sekunden": round(a["s"]), "pv_kwh": round(a["pv"], 5), "verbrauch_kwh": round(a["vb"], 5),
            "bezug_kwh": round(a["bz"], 5), "einspeisung_kwh": round(a["es"], 5),
            "kosten_chf": round(a["k"], 5), "erloes_chf": round(a["e"], 5),
            "boerse_rp": round(a["bo"] / a["bo_s"], 3) if a["bo_s"] else "", "quelle": "live"}])

def archiv_aggregat(rows, aufloesung):
    """Fasst 10-Minuten-Zeilen zu Stunden, Tagen, Monaten oder Jahren zusammen, plus Gesamtsumme."""
    laenge = {"10min": 16, "stunde": 13, "tag": 10, "monat": 7, "jahr": 4}[aufloesung]
    gruppen = {}
    for r in rows:
        g = gruppen.setdefault(r["zeit"][:laenge], {k: 0.0 for k in SUMMEN} | {"_bo": 0.0, "_bo_s": 0.0})
        for k in SUMMEN:
            g[k] += r[k] or 0
        if r["boerse_rp"] is not None and r["sekunden"]:
            g["_bo"] += r["boerse_rp"] * r["sekunden"]
            g["_bo_s"] += r["sekunden"]

    def fertig(g):
        out = {k: round(g[k], 4) for k in SUMMEN}
        out["sekunden"] = round(g["sekunden"])
        out["boerse_rp"] = round(g["_bo"] / g["_bo_s"], 2) if g["_bo_s"] else None
        out["saldo_chf"] = round(g["erloes_chf"] - g["kosten_chf"], 4)
        out["autarkie_pct"] = round(100 * (1 - g["bezug_kwh"] / g["verbrauch_kwh"]), 1) if g["verbrauch_kwh"] > 0.001 else None
        out["eigenverbrauch_pct"] = round(100 * (1 - g["einspeisung_kwh"] / g["pv_kwh"]), 1) if g["pv_kwh"] > 0.001 else None
        return out

    total = {k: 0.0 for k in SUMMEN} | {"_bo": 0.0, "_bo_s": 0.0}
    for g in gruppen.values():
        for k in total:
            total[k] += g[k]
    return {"zeilen": [{"zeit": z, **fertig(g)} for z, g in sorted(gruppen.items())], "summe": fertig(total)}
