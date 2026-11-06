#!/usr/bin/env python3
"""Auswertung: Ist die Betriebszeit der Heizung (Tuya-Datenpunkt 105) die echte Heizzeit? Mail an Patrick.
Daten: data/heizung-betrieb.csv (homi/heizung.py schreibt alle 30 s Zähler, Zustand und Hausverbrauch mit).
Prüft über die letzten TAGE Tage: steigt der Zähler, wenn die Heizung aus ist? Steigt er gleichmässig, solange sie an
ist (= zählt nur «eingeschaltet»)? Oder nur dann, wenn der Hausverbrauch um ihre Leistung höher ist (= echte Heizzeit)?
Einmalig am 08.11.2026 (homi-heizung-bericht.timer). Aufruf: heizung-betrieb-bericht.py [--nur-anzeigen]"""
import csv
import os
import statistics
import sys
from datetime import datetime, timedelta

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HIER, "..", "energy"))
from mailer import ABSENDER, senden  # noqa: E402

TAGE = 3
W_HEIZUNG = 650
ab = datetime.now() - timedelta(days=TAGE)

zeilen = []
with open(os.path.join(HIER, "data", "heizung-betrieb.csv"), encoding="utf-8") as f:
    for r in csv.DictReader(f):
        try:
            z = datetime.fromisoformat(r["zeit"])
            if z >= ab and r["worktime"] not in ("", "None"):
                zeilen.append((z, int(r["worktime"]), r["an"] == "1", float(r["soll_c"]), float(r["ist_c"]),
                               r["heizt_schaetzung"] == "1", float(r["verbrauch_w"]) if r["verbrauch_w"] else None))
        except (KeyError, ValueError):
            continue

# Abschnitte zwischen zwei Abfragen (Lücken über 5 Minuten auslassen)
ab_an, ab_aus = [], []                                         # (Minuten, Zuwachs, Verbrauch)
for (t1, w1, an1, *_), (t2, w2, an2, s2, i2, h2, v2) in zip(zeilen, zeilen[1:]):
    dt = (t2 - t1).total_seconds() / 60
    if 0 < dt <= 5 and an1 == an2 and w2 >= w1:
        (ab_an if an2 else ab_aus).append((dt, w2 - w1, v2))

def rate(a):
    m = sum(x[0] for x in a)
    return (sum(x[1] for x in a) / m if m else 0.0), m

r_an, m_an = rate(ab_an)
r_aus, m_aus = rate(ab_aus)
# Hausverbrauch, während der Zähler steigt, gegenüber dann, wenn er steht (nur Zeit «an»)
v_steigt = [v for _, d, v in ab_an if d > 0 and v is not None]
v_steht = [v for _, d, v in ab_an if d == 0 and v is not None]
diff = statistics.median(v_steigt) - statistics.median(v_steht) if len(v_steigt) >= 10 and len(v_steht) >= 10 else None

if not zeilen or m_an < 60:
    fazit, taugt = "Zu wenig Daten (weniger als eine Stunde mit eingeschalteter Heizung) – nichts entschieden.", None
elif m_aus >= 60 and r_aus > 0.05:
    fazit, taugt = ("Der Zähler steigt auch, wenn die Heizung ausgeschaltet ist. Er misst also nicht die Heizzeit. "
                    "homi bleibt bei der Schätzung."), False
elif diff is not None and diff >= W_HEIZUNG * 0.6:
    fazit, taugt = (f"Der Zähler steigt nur, wenn im Haus rund {diff:.0f} W mehr gebraucht werden – das ist die Heizung "
                    f"({W_HEIZUNG} W). Er misst die echte Heizzeit. Vorschlag: homi rechnet den Verbrauch künftig "
                    "aus dem Zähler statt zu schätzen."), True
elif v_steht and len(v_steht) < len(v_steigt) * 0.1:
    fazit, taugt = ("Der Zähler steigt praktisch die ganze Zeit, solange die Heizung eingeschaltet ist – auch wenn sie "
                    "wegen des Thermostats gar nicht heizt. Er zählt also nur «eingeschaltet», nicht «heizt». homi bleibt "
                    "bei der Schätzung; besser wäre, das Heizen am Sprung im Hausverbrauch zu erkennen."), False
else:
    fazit, taugt = ("Kein klares Bild: Der Zähler steigt nicht nur beim Heizen, aber auch nicht immer. homi bleibt "
                    "vorerst bei der Schätzung."), None

geschaetzt_h = sum((t2 - t1).total_seconds() / 3600 for (t1, *_), (t2, _, _, _, _, h2, _) in zip(zeilen, zeilen[1:])
                   if h2 and (t2 - t1).total_seconds() <= 300)
z = ["Hallo Patrick", "",
     f"Auswertung der Heizung (Dachstock) über die letzten {TAGE} Tage: Ist der Betriebszeit-Zähler die echte Heizzeit?", "",
     "ERGEBNIS", "  " + fazit, "",
     "ZAHLEN",
     f"  Abfragen:                       {len(zeilen)} (alle 30 s)",
     f"  Heizung eingeschaltet:          {m_an / 60:.1f} h, Zähler +{r_an * m_an:.0f} ({r_an:.2f} pro Minute)",
     f"  Heizung ausgeschaltet:          {m_aus / 60:.1f} h, Zähler +{r_aus * m_aus:.0f} ({r_aus:.2f} pro Minute)",
     f"  Hausverbrauch, Zähler steigt:   {statistics.median(v_steigt):.0f} W (Median)" if v_steigt else "  Hausverbrauch, Zähler steigt:   –",
     f"  Hausverbrauch, Zähler steht:    {statistics.median(v_steht):.0f} W (Median)" if v_steht else "  Hausverbrauch, Zähler steht:    –",
     f"  Geschätzte Heizzeit von homi:   {geschaetzt_h:.1f} h (Raum unter Soll-Temperatur)", "",
     ("Antworte mir (Claude) mit «Betriebszeit umstellen», dann rechnet homi künftig mit dem Zähler." if taugt else
      "Wenn du willst, baue ich die Erkennung über den Hausverbrauch ein – sag einfach «Heizung über Verbrauch erkennen»."
      if taugt is False else "Sag mir, ob ich länger messen soll."),
     "", "Dein homi"]
text = "\n".join(z)
if "--nur-anzeigen" in sys.argv:
    print(text)
else:
    senden("root", f"homi: Heizung – Auswertung Betriebszeit ({TAGE} Tage)", text, absender=ABSENDER["homi"])
    print("Bericht gesendet")
