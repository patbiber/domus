"""Energieberater: drei Tipps mit Begründung und Rechenweg, Batterie-Empfehlung."""
import json
import time
from datetime import date, datetime, timedelta

from . import zustand
from .amortisation import amort_cache, amortisation
from .einspeisung import einspeise_plan
from .konfig import ANLAGE_CFG, tsd
from .zustand import state


# ---------------------------------------------------------------- Energieberater
# Richtpreise für Batteriespeicher inkl. Anschluss (Stand Oktober 2026), überschreibbar in anlage.json "batterie_preise"
BATTERIE_PREISE = {
    "2": {"chf": 800, "beispiel": "Steckerspeicher, z. B. Marstek Venus E Mini oder EcoFlow Stream (max. 600 W)"},
    "5": {"chf": 2100, "beispiel": "z. B. Marstek Venus E 4.0 (CHF 1'709 bei Alltron) plus fester Anschluss"},
    "10": {"chf": 3800, "beispiel": "z. B. Marstek Venus E 4.0 mit Erweiterung oder Zendure SolarFlow 2400 AC"},
}

BATTERIE_LEBENSDAUER_J = 12

berater_cache = {"zeit": 0, "daten": None}

def berater():
    """Drei Tipps mit dem grössten Nutzen in CHF pro Jahr aus den Messwerten der letzten 14 Tage, dazu eine Batterie-
    Empfehlung (Kauf) bzw. Betriebstipps (vorhandene Batterie, anlage.json "haushalt.batterie").
    Jeder Tipp erklärt sich selbst: "warum" (was homi in den Daten sieht) und "rechnung" (Rechenschritte mit Zahlen).
    "geprueft" listet alle Kandidaten mit Ergebnis, auch die nicht gezeigten."""
    if time.time() - berater_cache["zeit"] < 3600 and berater_cache["daten"]:
        return berater_cache["daten"]
    with open(ANLAGE_CFG, encoding="utf-8") as f:
        cfg = json.load(f)
    haushalt = cfg.get("haushalt") or {}
    heute = date.today()
    rows = [r for r in zustand.archiv.lesen(heute - timedelta(days=14), heute - timedelta(days=1)) if r["sekunden"]]
    tage = sum(r["sekunden"] for r in rows) / 86400
    if tage < 3:
        return {"aktiv": False, "grund": "homi braucht mindestens 3 Tage Messwerte für Tipps."}
    t = (state.get("status") or {}).get("tarif") or {}
    bezug, rueck = t.get("bezug_chf_kwh") or 0.27, t.get("rueckliefer_chf_kwh") or 0.0
    diff = bezug - rueck
    rp = lambda x: f"{x * 100:.1f}".replace(".0", "") + " Rp."
    # Stundenprofil (Mittel pro Tag) und Grundlast (Median 01–05 Uhr)
    prof = [{"v": 0.0, "pv": 0.0, "b": 0.0, "e": 0.0} for _ in range(24)]
    nacht_w, gross = [], {}
    for r in rows:
        s = int(r["zeit"][11:13])
        for k, q in (("v", "verbrauch_kwh"), ("pv", "pv_kwh"), ("b", "bezug_kwh"), ("e", "einspeisung_kwh")):
            prof[s][k] += r[q] / tage
        w = r["verbrauch_kwh"] * 3.6e6 / r["sekunden"]
        if 1 <= s < 5 and r["sekunden"] >= 500:
            nacht_w.append(w)
        if (s >= 22 or s < 6) and w >= 600:          # grosser Verbraucher nachts (Boiler, Heizung …)
            gross[r["zeit"][:10]] = gross.get(r["zeit"][:10], 0) + r["verbrauch_kwh"]
    nacht_w.sort()
    grund_w = nacht_w[len(nacht_w) // 2] if nacht_w else 0
    std_ohne = sum(1 for p in prof if p["pv"] < p["v"])
    ohne_sonne = std_ohne / 24
    spitze = max(range(24), key=lambda s: prof[s]["b"])
    basis = f"Messwerte der letzten {round(tage)} Tage ({(heute - timedelta(days=14)).strftime('%d.%m.')}–{(heute - timedelta(days=1)).strftime('%d.%m.%Y')})"
    tipps, geprueft = [], []

    # 1) Grundlast
    if grund_w >= 50:
        ziel = max(40, round(grund_w * 0.7 / 5) * 5)
        kwh = (grund_w - ziel) * 8.76
        wert = ohne_sonne * bezug + (1 - ohne_sonne) * rueck
        tipps.append({"id": "grundlast", "titel": "Grundlast senken", "chf_jahr": round(kwh * wert), "kwh_jahr": round(kwh),
            "text": f"Dein Haus braucht auch nachts rund {grund_w:.0f} W – das sind {tsd(grund_w * 8.76)} kWh im Jahr nur für "
                    f"Dauerläufer und Geräte im Bereitschaftsmodus. Ziel: {ziel} W.",
            "warum": "Zwischen 1 und 5 Uhr schläft das Haus. Was dann noch Strom braucht, läuft rund um die Uhr – "
                     "jedes Watt Dauerlast kostet 8.8 kWh im Jahr, auch wenn niemand etwas davon hat. Nachts kommt "
                     f"dieser Strom aus dem Netz zu {rp(bezug)}, tagsüber verdrängt er Einspeisung zu {rp(rueck)}",
            "rechnung": [f"Grundlast = Median aller 10-Minuten-Werte zwischen 1 und 5 Uhr: {grund_w:.0f} W",
                         f"Ziel 30 % weniger: {ziel} W → {grund_w - ziel:.0f} W × 8'760 Stunden = {tsd(kwh)} kWh/Jahr",
                         f"{std_ohne} von 24 Stunden ohne Sonnenüberschuss zu {rp(bezug)}, {24 - std_ohne} Stunden zu {rp(rueck)} "
                         f"→ im Mittel {rp(wert)} pro kWh",
                         f"{tsd(kwh)} kWh × {rp(wert)} = CHF {round(kwh * wert)} pro Jahr"],
            "aktion": "Mit einem Steckdosen-Messgerät (ca. CHF 20) die grössten Dauerverbraucher suchen: alte Kühl- und "
                      "Gefriergeräte, Router und Netzwerk, Umwälzpumpe, TV und Spielkonsole im Standby. "
                      "Was nachts nicht laufen muss, mit Steckdosenleiste oder Zeitschaltuhr ausschalten."})
    else:
        geprueft.append({"titel": "Grundlast senken", "ergebnis": f"nicht nötig – nachts nur {grund_w:.0f} W"})

    # 2) Grosser Verbraucher nachts -> in die Mittagssonne
    kwh_nacht = sum(gross.values()) / tage if gross else 0
    ursache = ("typisch für Waschmaschine, Tumbler, Geschirrspüler oder eine Heizungspumpe"
               if haushalt.get("boiler_elektrisch") is False else "typisch für einen Boiler, eine Heizung oder Haushaltsgeräte")
    if kwh_nacht >= 0.3:
        tipps.append({"id": "nachtlast", "titel": "Nachtverbraucher in den Mittag legen",
            "chf_jahr": round(kwh_nacht * 365 * diff), "kwh_jahr": round(kwh_nacht * 365),
            "text": f"Zwischen 22 und 6 Uhr läuft an {len(gross)} von {round(tage)} Tagen ein grosser Verbraucher "
                    f"(über 600 W, im Mittel {kwh_nacht:.1f} kWh pro Nacht) – {ursache}.",
            "warum": f"Nachts kommt jede kWh aus dem Netz (zu {rp(bezug)}). Läuft derselbe Verbraucher mittags, deckt ihn "
                     f"deine Anlage – statt dass du den Strom für {rp(rueck)} einspeist.",
            "rechnung": [f"10-Minuten-Werte über 600 W zwischen 22 und 6 Uhr: an {len(gross)} von {round(tage)} Tagen",
                         f"im Mittel {kwh_nacht:.2f} kWh pro Nacht × 365 = {tsd(kwh_nacht * 365)} kWh/Jahr",
                         f"Vorteil pro verschobene kWh: {rp(bezug)} − {rp(rueck)} = {rp(diff)}",
                         f"{tsd(kwh_nacht * 365)} kWh × {rp(diff)} = CHF {round(kwh_nacht * 365 * diff)} pro Jahr"],
            "aktion": ("Prüfen, was nachts läuft: Geräte mit Startzeitvorwahl auf 11–15 Uhr stellen." if haushalt.get("boiler_elektrisch") is False
                       else "Prüfen, was nachts läuft. Ist es der Boiler: Ladezeit auf 11–15 Uhr umstellen lassen "
                            "(Zeitschaltuhr bzw. Rundsteuerung) – dann heizt ihn deine Sonne statt das Netz.")})
    else:
        geprueft.append({"titel": "Nachtverbraucher in den Mittag legen",
                         "ergebnis": "kein grosser Verbraucher (über 600 W) nachts gefunden"})

    # 3) Überschuss für Warmwasser / E-Auto / Wärmepumpe nutzen
    try:
        am = amortisation()
    except (OSError, ValueError, KeyError):
        am = amort_cache["daten"] or {}
    if am.get("aktiv"):
        ev = am.get("eigenverbrauch_pct", 0) / 100
        ueberschuss_jahr = am.get("jahresertrag_kwh", 0) * (1 - ev)
        quelle_u = (f"Jahresertrag {tsd(am['jahresertrag_kwh'])} kWh ({am.get('jahresertrag_quelle', 'Fronius')}) − "
                    f"Eigenverbrauch {am['eigenverbrauch_pct']:.1f} % = Überschuss {tsd(ueberschuss_jahr)} kWh/Jahr")
    else:
        ev = None
        ueberschuss_jahr = sum(p["e"] for p in prof) * 365
        quelle_u = f"Einspeisung im Mittel {sum(p['e'] for p in prof):.1f} kWh/Tag × 365 = {tsd(ueberschuss_jahr)} kWh/Jahr"
    kein_abnehmer = haushalt.get("boiler_elektrisch") is False and not haushalt.get("e_auto") and not haushalt.get("waermepumpe")
    if ueberschuss_jahr > 1000 and not kein_abnehmer:
        nutzbar = min(ueberschuss_jahr * 0.4, 2500)
        bedingung = None if haushalt.get("boiler_elektrisch") or haushalt.get("e_auto") or haushalt.get("waermepumpe") else \
            "falls dein Warmwasser elektrisch erzeugt wird oder du ein E-Auto hast"
        tipps.append({"id": "ueberschuss", "titel": "Sonnenüberschuss selbst nutzen",
            "chf_jahr": round(nutzbar * diff), "kwh_jahr": round(nutzbar), "bedingung": bedingung,
            "text": f"Du speist rund {tsd(ueberschuss_jahr)} kWh im Jahr für {rp(rueck)} ein. Selbst genutzt ist jede "
                    f"kWh {rp(bezug)} wert – {rp(diff)} mehr.",
            "warum": (f"Nur {ev * 100:.0f} % deines Solarstroms nutzt du selbst, der Rest geht ins Netz. " if ev is not None else "")
                     + "Mittags produziert die Anlage ein Vielfaches dessen, was das Haus gleichzeitig braucht. Ein "
                     "Verbraucher, der Energie speichern kann (Warmwasser, Auto-Akku, Gebäude), nimmt diesen Überschuss auf.",
            "rechnung": [quelle_u,
                         "Annahme: 40 % davon lassen sich nutzen, höchstens 2'500 kWh (etwa der Warmwasserbedarf eines "
                         f"4-Personen-Haushalts mit Elektroboiler) → {tsd(nutzbar)} kWh/Jahr",
                         f"Vorteil pro kWh: {rp(bezug)} − {rp(rueck)} = {rp(diff)}",
                         f"{tsd(nutzbar)} kWh × {rp(diff)} = CHF {round(nutzbar * diff)} pro Jahr"],
            "aktion": "Elektroboiler mit einem Überschuss-Regler (z. B. Fronius Ohmpilot, ca. CHF 900 inkl. Einbau) nur mit "
                      "Sonnenstrom heizen; E-Auto tagsüber laden; Wärmepumpe mittags vorheizen lassen."})
    else:
        geprueft.append({"titel": "Sonnenüberschuss selbst nutzen (Boiler, E-Auto)",
                         "ergebnis": "kaum Überschuss" if ueberschuss_jahr <= 1000 else
                         f"kein passender Verbraucher ({haushalt.get('warmwasser') + ' für Warmwasser, ' if haushalt.get('warmwasser') else ''}kein E-Auto)"})
    if kein_abnehmer and ueberschuss_jahr > 3000:
        verkauf, mehr = ueberschuss_jahr * 0.25, 0.03
        tipps.append({"id": "leg", "titel": "Überschuss an Nachbarn verkaufen (LEG)",
            "chf_jahr": round(verkauf * mehr), "kwh_jahr": round(verkauf), "bedingung": "falls Nachbarn mitmachen",
            "text": f"Du speist rund {tsd(ueberschuss_jahr)} kWh im Jahr für {rp(rueck)} ein und hast selbst keinen grossen "
                    "Abnehmer dafür. Seit 2026 darfst du Solarstrom in einer lokalen Elektrizitätsgemeinschaft (LEG) an "
                    "Nachbarn in deiner Gemeinde verkaufen.",
            "warum": "Ohne Elektroboiler, E-Auto oder Wärmepumpe lässt sich dein Überschuss im Haus kaum nutzen. In einer LEG "
                     "kauft ihn ein Nachbar zu einem Preis zwischen Rücklieferung und seinem Bezugstarif – beide profitieren. "
                     "Das neue Stromgesetz (in Kraft seit 1. Januar 2026) senkt dafür das Netzentgelt.",
            "rechnung": [quelle_u,
                         f"Annahme: ein Viertel davon geht an Nachbarn → {tsd(verkauf)} kWh/Jahr",
                         f"Annahme: Verkaufspreis {rp(rueck + mehr)} statt {rp(rueck)} Rücklieferung → {rp(mehr)} mehr pro kWh "
                         "(hängt vom vereinbarten Preis und vom Netzentgelt ab)",
                         f"{tsd(verkauf)} kWh × {rp(mehr)} = CHF {round(verkauf * mehr)} pro Jahr – grobe Schätzung"],
            "aktion": "Bei deinem Netzbetreiber nach den Bedingungen für eine LEG fragen und Nachbarn mit hohem Tagesverbrauch "
                      "ansprechen (Wärmepumpe, E-Auto, Gewerbe)."})

    # 4) Verbrauch verschieben (Waschen, Spülen, Trocknen)
    abend = sum(max(0.0, p["b"] - grund_w / 1000) for p in prof)
    kwh = abend * 0.4 * 365
    if kwh > 30:
        f = ((state.get("prognose") or {}).get("morgen") or {}).get("bestes_fenster")
        tipps.append({"id": "verschieben", "titel": "Grosse Geräte in die Sonnenstunden",
            "chf_jahr": round(kwh * diff), "kwh_jahr": round(kwh),
            "text": f"Am meisten Strom aus dem Netz brauchst du zwischen {spitze} und {spitze + 1} Uhr. Rund "
                    f"{abend:.1f} kWh pro Tag kommen über die Grundlast hinaus aus dem Netz.",
            "warum": "Abends und nachts kaufst du Strom ein, mittags speist du Überschuss ein. Geräte mit Startzeitvorwahl "
                     "lassen sich ohne Komfortverlust in die Sonnenstunden verschieben.",
            "rechnung": [f"Netzbezug pro Stunde, abzüglich Grundlast ({grund_w:.0f} W), Mittel der letzten {round(tage)} Tage: "
                         f"{abend:.2f} kWh pro Tag",
                         f"Grösster Netzbezug: {spitze}–{spitze + 1} Uhr mit {prof[spitze]['b']:.2f} kWh",
                         f"Annahme: 40 % davon sind verschiebbar (Waschen, Spülen, Trocknen) → {tsd(kwh)} kWh/Jahr",
                         f"{tsd(kwh)} kWh × {rp(diff)} Vorteil = CHF {round(kwh * diff)} pro Jahr"],
            "aktion": "Waschmaschine, Geschirrspüler und Tumbler mit Startzeitvorwahl über Mittag laufen lassen"
                      + (f" – morgen am besten {f['von']}–{f['bis']} Uhr." if f else ".")})
    else:
        geprueft.append({"titel": "Grosse Geräte in die Sonnenstunden", "ergebnis": "kaum Netzbezug über der Grundlast"})

    # 5) Akut: negative Preise in Sicht
    plan = einspeise_plan()
    tage_minus = [(n, d) for n, d in (("heute", plan.get("heute")), ("morgen", plan.get("morgen"))) if d and d.get("minuspreis_bloecke")]
    if tage_minus:
        n, d = tage_minus[0]
        tief = min(x["preis_rp"] for x in d["stunden"] if x["stunde"] in d["minuspreis_stunden"])
        tipps.insert(0, {"id": "minuspreis", "titel": f"{n.capitalize()} negative Strompreise nutzen", "akut": True,
            "chf_jahr": None, "kwh_jahr": None,
            "text": f"{n.capitalize()} {' und '.join(d['minuspreis_bloecke'])} liegt der Börsenpreis unter null.",
            "warum": "In diesen Stunden gibt es an der Börse mehr Strom als Abnehmer. Wer dann verbraucht, entlastet das "
                     "Netz; mit einem dynamischen Einspeisetarif kostet Einspeisen in dieser Zeit sogar Geld.",
            "rechnung": [f"Day-Ahead-Preise Schweiz (EPEX Spot via Energy-Charts) für {n}",
                         f"Stunden mit Preis unter null: {', '.join(d['minuspreis_bloecke'])}, tiefster Preis {tief:.1f} Rp./kWh",
                         f"Erwarteter Überschuss in dieser Zeit: {d.get('ueberschuss_bei_minuspreis_kwh', 0):.1f} kWh"],
            "aktion": "Grosse Verbraucher genau in diese Stunden legen. Mit dynamischem Einspeisetarif: Einspeisung drosseln."})
    else:
        geprueft.append({"titel": "Negative Strompreise", "ergebnis": "heute und morgen keine"})

    akut = [x for x in tipps if x.get("akut")]
    rest = sorted((x for x in tipps if not x.get("akut")), key=lambda x: -(x["chf_jahr"] or 0))
    drei = (akut + rest)[:3]
    for x in (akut + rest)[3:]:
        geprueft.insert(0, {"titel": x["titel"], "ergebnis": f"CHF {x['chf_jahr']}/Jahr – weniger als die drei gezeigten"})
    summe = sum(x["chf_jahr"] or 0 for x in drei)
    monate_frueher = None
    if am.get("aktiv") and am.get("pro_jahr_chf") and am.get("rest_chf"):
        monate_frueher = round(12 * (am["rest_chf"] / am["pro_jahr_chf"] - am["rest_chf"] / (am["pro_jahr_chf"] + summe)))
    auswahl = ("homi rechnet bis zu sechs mögliche Massnahmen mit deinen Messwerten und deinem Tarif durch und zeigt die drei mit "
               "dem grössten Nutzen in Franken pro Jahr. Hinweise auf negative Strompreise stehen immer zuoberst. "
               "Annahmen (z. B. wie viel sich verschieben lässt) sind bewusst vorsichtig und bei jedem Tipp genannt.")
    # Batterie
    preise = {**BATTERIE_PREISE, **(cfg.get("batterie_preise") or {})}
    vorhanden = haushalt.get("batterie")
    if vorhanden:
        batterie = {"modus": "optimieren", "titel": f"Deine Batterie ({vorhanden.get('kapazitaet_kwh', '?')} kWh) optimal nutzen",
            "warum": "Eine Batterie bringt nur Geld, wenn sie Sonnenstrom vom Mittag in den Abend verschiebt. Jede "
                     "Ladung aus dem Netz oder ungenutzte Kapazität schmälert den Nutzen.",
            "tipps": ["Nur mit Sonnenüberschuss laden, nie aus dem Netz – bei fixem Tarif kostet das nur Wirkungsgrad.",
                      "Notstrom-Reserve klein halten (10–20 %), sonst bleibt teure Kapazität ungenutzt.",
                      f"Die Batterie für den Abend aufsparen: Am meisten Netzstrom brauchst du um {spitze} Uhr.",
                      "Bei negativen Börsenpreisen (und dynamischem Tarif) gezielt aus dem Netz laden – homi warnt dich am Morgen."]}
    else:
        try:
            sim = zustand.speicher_sim.report()
        except Exception:                      # Simulation ist Beiwerk
            sim = {"speicher": []}
        optionen = []
        for s in sim.get("speicher", []):
            p = preise.get(str(int(s["kapazitaet_kwh"])))
            if not p or not s.get("ersparnis_pro_jahr_chf"):
                continue
            jahre = p["chf"] / s["ersparnis_pro_jahr_chf"]
            optionen.append({"kwh": s["kapazitaet_kwh"], "name": s["name"], "preis_chf": p["chf"], "beispiel": p["beispiel"],
                             "ersparnis_jahr_chf": round(s["ersparnis_pro_jahr_chf"]), "amortisation_jahre": round(jahre, 1),
                             "zyklen": s.get("zyklen"), "autarkie_plus_pct": s.get("autarkie_plus_pct")})
        beste = min(optionen, key=lambda o: o["amortisation_jahre"]) if optionen else None
        tage_sim = round((sim.get("speicher") or [{}])[0].get("tage_gemessen") or 0)
        if not beste:
            urteil, text = "noch offen", "Für eine Empfehlung braucht homi noch ein paar Tage Messwerte."
        elif beste["amortisation_jahre"] <= 8:
            urteil, text = "lohnt sich", (f"Eine {beste['kwh']}-kWh-Batterie spart dir rund CHF {beste['ersparnis_jahr_chf']} im Jahr "
                f"und ist nach etwa {beste['amortisation_jahre']:.0f} Jahren bezahlt. Beispiel: {beste['beispiel']}.")
        elif beste["amortisation_jahre"] <= BATTERIE_LEBENSDAUER_J:
            urteil, text = "knapp", (f"Am besten rechnet sich eine {beste['kwh']}-kWh-Batterie: rund CHF {beste['ersparnis_jahr_chf']} "
                f"im Jahr, bezahlt nach etwa {beste['amortisation_jahre']:.0f} Jahren – knapp innerhalb der Lebensdauer "
                f"(10–15 Jahre). Beispiel: {beste['beispiel']}. Vorher lohnen sich die Tipps oben mehr.")
        else:
            urteil, text = "lohnt sich nicht", (f"Eine Batterie würde dir höchstens rund CHF {beste['ersparnis_jahr_chf']} im Jahr "
                f"sparen ({beste['kwh']} kWh), kostet aber rund CHF {tsd(beste['preis_chf'])}. Bezahlt wäre sie erst nach "
                f"{beste['amortisation_jahre']:.0f} Jahren – länger, als sie hält. Neu rechnen lohnt sich mit einem "
                f"dynamischen Einspeisetarif, einem E-Auto oder wenn Speicher günstiger werden.")
        warum = (f"Seit {tage_sim} Tagen läuft in homi eine Simulation mit drei Batterien (2, 5 und 10 kWh) und deinen echten "
                 "Messwerten: Jeder Überschuss lädt sie, jeder Netzbezug entlädt sie. So zeigt sich, was eine Batterie bei "
                 "deinem Verbrauch wirklich bringen würde – nicht bei einem Durchschnittshaushalt.")
        if beste and (beste.get("zyklen") or 0) > 0 and beste["kwh"] < max(o["kwh"] for o in optionen):
            gross_o = max(optionen, key=lambda o: o["kwh"])
            warum += (f" Grössere Batterien sparen zwar etwas mehr ({gross_o['kwh']} kWh: CHF {gross_o['ersparnis_jahr_chf']} "
                      f"statt {beste['ersparnis_jahr_chf']}), kosten aber ein Vielfaches: Dein Haus braucht abends und "
                      "nachts nur wenig Strom, darum wird die zusätzliche Kapazität selten ganz genutzt.")
        rechnung = ["Ersparnis = vermiedener Netzbezug × Bezugstarif − entgangene Einspeisevergütung, mit 90 % Wirkungsgrad "
                    "(Laden und Entladen)",
                    "Hochrechnung aufs Jahr aus den letzten 7 simulierten Tagen",
                    "Bezahlt nach = Richtpreis inkl. Anschluss ÷ Ersparnis pro Jahr"]
        rechnung += [f"{o['kwh']} kWh: CHF {tsd(o['preis_chf'])} ÷ CHF {o['ersparnis_jahr_chf']}/Jahr = {o['amortisation_jahre']:.1f} Jahre"
                     for o in optionen]
        rechnung.append(f"Urteil: bis 8 Jahre «lohnt sich», bis {BATTERIE_LEBENSDAUER_J} Jahre «knapp» (Lebensdauer 10–15 "
                        "Jahre), darüber «lohnt sich nicht»")
        batterie = {"modus": "kaufen", "urteil": urteil, "text": text, "optionen": optionen, "warum": warum, "rechnung": rechnung,
                    "beste_kwh": beste["kwh"] if beste else None, "tage_gemessen": tage_sim,
                    "hinweis": "Richtpreise inkl. Anschluss, Stand Oktober 2026. Im Winter gibt es weniger Überschuss, "
                               "im Sommer mehr – die Empfehlung wird mit jedem Monat genauer."}
    daten = {"aktiv": True, "stand": datetime.now().isoformat(timespec="minutes"), "tage": round(tage, 1), "basis": basis,
             "auswahl": auswahl, "grundlast_w": round(grund_w), "spitze_stunde": spitze,
             "tarif": {"bezug_rp": round(bezug * 100, 2), "rueck_rp": round(rueck * 100, 2)},
             "tipps": drei, "geprueft": geprueft, "summe_chf_jahr": summe, "monate_frueher": monate_frueher, "batterie": batterie}
    berater_cache.update(zeit=time.time(), daten=daten)
    return daten
