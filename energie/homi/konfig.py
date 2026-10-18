"""Konstanten, Pfade und kleine Helfer."""
import json
import os
import re


APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # Ordner mit server.py
FRONIUS = os.environ.get("FRONIUS_HOST", "192.168.1.221")

TARIFE_JSON = os.environ.get("TARIFE_JSON", "/strompreise/latest/tarife.json")

TARIF_CFG = os.environ.get("TARIF_CFG", "/app/tarif.json")

PORT = int(os.environ.get("PORT", "8099"))

WWW = os.path.join(APP_DIR, "www")

# Kunden-Oberfläche (modernes Layout) für Aufrufe über <name>.homi.solar; alle anderen Adressen bekommen www/ (Retro)
WWW_KUNDE = os.path.join(APP_DIR, "www-kunde")

KUNDE_HOST = re.compile(r"^[a-z0-9-]+\.homi\.solar$")

POLL_S = 5

BOERSE_URL = "https://api.energy-charts.info/price?bzn=CH&start={start}&end={end}"

EZB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"

BOERSE_REFRESH_S = 30 * 60

SPEICHER_FILE = os.environ.get("SPEICHER_FILE", "/data/speicher.json")

ARCHIV_DIR = os.environ.get("ARCHIV_DIR", "/data/archiv")

RAPL_DIR = os.environ.get("RAPL_DIR", "/host/rapl/intel-rapl:0")   # Energiezähler der CPU (Intel RAPL)

NUC_REST_W = float(os.environ.get("NUC_REST_W", "4.0"))          # geschätzt: Platine, SSD, Netzteilverluste

GERAETE_FILE = os.environ.get("GERAETE_FILE", "/data/geraete.json")

STEUERUNG_FILE = os.environ.get("STEUERUNG_FILE", "/data/steuerung.json")

ANLAGE_CFG = os.environ.get("ANLAGE_CFG", "/app/anlage.json")

PROGNOSE_LOG = os.environ.get("PROGNOSE_LOG", "/data/prognose_log.json")

PROGNOSE_REFRESH_S = 3600

PUSH_DIR = os.environ.get("PUSH_DIR", "/data/push")     # Push-Abos der App; Versand macht push.py auf dem Host

PUSH_THEMEN = ["minuspreis", "prognose", "ausfall", "wochenbericht"]

# Nur Adressen der bekannten Push-Dienste annehmen (Chrome/Android, Firefox, Safari/iOS, Edge)
PUSH_HOSTS = re.compile(r"^(fcm\.googleapis\.com|[a-z0-9.-]+\.push\.services\.mozilla\.com|web\.push\.apple\.com"
                        r"|[a-z0-9.-]+\.notify\.windows\.com)$")

OPEN_METEO = ("https://api.open-meteo.com/v1/forecast?latitude={breite}&longitude={laenge}"
              "&hourly=global_tilted_irradiance,cloud_cover&tilt={neigung}&azimuth={azimut}"
              "&past_days=14&forecast_days=3&timezone=Europe%2FZurich")

ARCHIV_TAGE = 5 * 365 + 2          # 5 Jahre aufbewahren

SLOT_S = 600                       # 10-Minuten-Schritte

ARCHIV_FELDER = ["zeit", "sekunden", "pv_kwh", "verbrauch_kwh", "bezug_kwh", "einspeisung_kwh",
                 "kosten_chf", "erloes_chf", "boerse_rp", "quelle"]

SUMMEN = ["sekunden", "pv_kwh", "verbrauch_kwh", "bezug_kwh", "einspeisung_kwh", "kosten_chf", "erloes_chf"]

MAX_DT_S = 30                      # grössere Lücken (Neustart, Ausfall) werden nicht hochgerechnet

WIRKUNGSGRAD = 0.95                # je Richtung, also rund 90 % hin und zurück

SPEICHER = [                       # virtuelle Speicher: Kapazität nutzbar, Lade- und Entladeleistung
    {"id": "stecker-2kwh", "name": "Steckerspeicher 2 kWh", "kapazitaet_kwh": 2, "laden_w": 1200, "entladen_w": 600},
    {"id": "speicher-5kwh", "name": "Speicher 5 kWh", "kapazitaet_kwh": 5, "laden_w": 3000, "entladen_w": 3000},
    {"id": "speicher-10kwh", "name": "Speicher 10 kWh", "kapazitaet_kwh": 10, "laden_w": 5000, "entladen_w": 5000},
]

WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]

def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)

tsd = lambda x: f"{round(x):,}".replace(",", "'")      # 7'577 (Schweizer Tausendertrennung)
