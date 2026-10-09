# Domus

Home-System auf einem Intel NUC mit Ubuntu Server und Docker.

Ausführliche Projektdokumentation: [docs/PROJEKT.md](docs/PROJEKT.md)



Jeder Dienst hat einen eigenen Ordner mit eigener `compose.yml`:

| Ordner | Dienst | Status |
|---|---|---|
| `homeassistant/` | Home Assistant (Container, `network_mode: host`) – https://domus.biber.solar | läuft |
| `mosquitto/` | MQTT-Broker | geplant |
| `zigbee2mqtt/` | Zigbee-Geräte (Zigbee-Stick folgt) | geplant |
| – | Hue über die Hue Bridge (Integration in Home Assistant) | geplant |
| `proxy/` | nginx (Reverse Proxy für Home Assistant) + certbot (Let's Encrypt) | läuft |
| `claude-remote/` | Dauerhafte Claude-Code-Session mit Remote Control (systemd-User-Dienst, kein Docker) | läuft |
| `biber-solar/` | Hauptseite https://biber.solar aus github.com/patbiber/biber-solar, main (nginx-Container, Veröffentlichung alle 5 min) | läuft (seit 03.10.2026) |
| `energy/` | https://homi.solar (früher energy.biber.solar): homi-Verkaufsseite mit simulierter Live-Demo, KI-Demo und Anfrageformular | läuft (seit 05.10.2026) |
| `training/` | https://training.biber.solar: MkDocs-Material-Seite aus github.com/patbiber/solartech (Build bei neuem Commit, nginx-Container) | läuft (seit 03.10.2026) |
| `biber-solar-test/` | Vorschau https://test.biber.solar = Arbeitskopie von biber-solar (Änderungen ansehen, dann pushen) | läuft |
| `system/` | Neustart-Bericht, wöchentliches Docker-Image-Update mit Prüfung und Zurückrollen, Überwachung der öffentlichen IP (systemd-User-Dienste) | läuft |
| `energie/` | Energie-API (Fronius live + GWS-Tarif) und Retro-Webseite https://home.biber.solar | läuft |
| `strompreise/` | Monatliches Archiv der GWS-Stromtarife (Skript + systemd-User-Timer, kein Docker) | läuft |

## Einrichtung

```bash
cp .env.example .env   # Werte anpassen
cd homeassistant && docker compose up -d
```

Home Assistant: https://domus.biber.solar (Internet) bzw. http://192.168.178.121:8123 (LAN)

### Webseite / Zertifikat (`proxy/`)

Voraussetzung: Fritzbox leitet TCP 80 und 443 an `192.168.178.121` weiter, `domus.biber.solar` zeigt (A-Record bei OVH) auf die öffentliche IP.

```bash
cd proxy
./init-cert.sh --staging   # Testlauf gegen Let's-Encrypt-Staging
./init-cert.sh             # echtes Zertifikat
```

Erneuerung läuft automatisch (certbot prüft alle 12h mit `certbot renew` ohne `--webroot`: jedes Zertifikat nutzt seine
gespeicherte Methode, webroot bzw. DNS-Hook für `*.homi.solar`; nginx lädt alle 6h neu). Port 80 leitet auf 443 weiter.
Neue Seiten: weitere Datei in `proxy/conf.d/` anlegen.

`domus.biber.solar` leitet auf Home Assistant weiter (`host.docker.internal:8123`, WebSockets aktiv).
Die Proxy-Einstellungen von Home Assistant stehen seit 2026.x **nicht** mehr in `configuration.yaml` (`http:` wird ignoriert),
sondern in `homeassistant/config/.storage/http` (nur bei gestopptem HA bearbeiten):

- `use_x_forwarded_for: true`, `trusted_proxies: [172.16.0.0/12]` (Docker-Netze)
- `ip_ban_enabled: true`, `login_attempts_threshold: 5` – Sperren landen in `config/ip_bans.yaml`

Für jeden Benutzer 2-Faktor-Login (TOTP) im HA-Profil aktivieren.

### Claude Remote Control (`claude-remote/`)

Dauerhafte Claude-Code-Session, steuerbar über https://claude.ai/code oder die Claude-App (Umgebung „domus“).
Läuft als systemd-User-Dienst in tmux, startet nach Absturz/Neustart automatisch (Linger aktiv).

```bash
mkdir -p ~/.config/systemd/user
ln -sf ~/domus/claude-remote/claude-remote.service ~/.config/systemd/user/
sudo loginctl enable-linger $USER
systemctl --user daemon-reload && systemctl --user enable --now claude-remote

systemctl --user status claude-remote   # Status
tmux attach -t claude-remote            # zuschauen (verlassen: Ctrl-b d)
```

Rechte: `--permission-mode default` – Befehle werden in der App bestätigt.

### Photovoltaik (Fronius)

| Gerät | IP | Details |
|---|---|---|
| Fronius-Wechselrichter (Datamanager, Solar API v1) | `192.168.1.221` | Seriennr. 33185466 |
| Fronius Smart Meter TS 65A-3 | über den Wechselrichter | Seriennr. 96535978, Einbauort **Verbrauchszweig** (`Meter_Location: load`) |

Der Smart Meter ist kein eigenes Netzwerkgerät, sondern hängt am Wechselrichter und wird über dessen Solar API ausgelesen.
Home Assistant: Integration **Fronius** mit Host `192.168.1.221` (liefert Wechselrichter, Smart Meter und Powerflow).
Nachts ist der Wechselrichter nicht erreichbar, die Einrichtung muss also tagsüber passieren.

Test: `curl -s http://192.168.1.221/solar_api/v1/GetPowerFlowRealtimeData.fcgi`

### Stromtarife GWS (`strompreise/`)

`fetch-tarife.sh` lädt monatlich (1. des Monats, 06:17) von https://gws.ch/strom-tarife-produkte/:
- die maschinenlesbaren Tarife (`tarife.json`, verlinkt auf strompreisvergleich.ch) – enthält aktuelles und kommendes Tarifjahr,
- alle Tarif-PDFs der Seite (Privat/Gewerbe, Grosskunden, Messtarife, Haushalttarife, Stromprodukte).

Ablage in `strompreise/data/<JJJJ-MM-TT>/` (nicht im Repo), `data/latest` zeigt auf den neuesten Stand, dazu `SHA256SUMS` und `quellen.txt`.
Bei geänderten Dateien oder einem Fehler kommt eine Mail an `root` (→ patrick@biber.solar).

```bash
ln -sf ~/domus/strompreise/strompreise.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now strompreise.timer

systemctl --user list-timers strompreise.timer   # nächster Lauf
systemctl --user start strompreise.service       # sofort abrufen
journalctl --user -u strompreise                 # Log
```

### Energie-Dienst und home.biber.solar (`energie/`)

Kleiner Python-Container (`network_mode: host`, Port 8099, nur im LAN offen):
- fragt alle 5 s den Fronius ab (`GetPowerFlowRealtimeData`) – nachts ist der Wechselrichter aus, dann `fronius_ok: false`,
- berechnet den aktuellen Strompreis aus `strompreise/data/latest/tarife.json` + `energie/tarif.json`
  (Produkt ÖkoStrom, Netztarif, Gemeindeabgabe, MWST, Rückliefervergütung Sommer/Winter, Korrekturen gemäss PDF),
- `/api/status` (live) und `/api/history` (Minutenwerte der letzten 24 h, nur im Speicher),
- `/api/boerse`: Day-Ahead-Börsenpreise Schweiz (EPEX Spot, stündlich, heute/morgen) von
  https://api.energy-charts.info (Fraunhofer ISE, CC BY 4.0, ohne Token), umgerechnet in Rp/kWh zum EZB-Tageskurs;
  wird alle 30 min aktualisiert,
- `/api/speicher`: **Speicher-Simulation** – drei virtuelle Speicher (Stecker 2 kWh/600 W, 5 kWh/3 kW, 10 kWh/5 kW,
  je 95 % Wirkungsgrad pro Richtung) werden alle 5 s mit dem echten Netzsaldo geladen/entladen; Ersparnis =
  vermiedener Bezug × Bezugspreis − beim Entladen anteilig verrechnete entgangene Rückliefervergütung.
  Stand in `energie/data/speicher.json` (nicht im Repo, übersteht Neustarts). Zurücksetzen: Dienst stoppen, Datei löschen.
- `/api/archiv`: **10-Minuten-Archiv**, 5 Jahre: pro Tag eine CSV unter `energie/data/archiv/<Jahr>/<Datum>.csv`
  (vergangene Tage gzip, ~3 KB/Tag, ~5 MB für 5 Jahre; ältere Tage löscht homi selbst). Spalten: Zeit, gemessene
  Sekunden, PV/Verbrauch/Bezug/Einspeisung in kWh, Kosten/Erlös in CHF, Börsenpreis, Quelle (`live` | `ha`).
  Abfrage `?von=&bis=&aufloesung=10min|stunde|tag|monat|jahr`. Die 24-h-Diagramme füllen Lücken nach Neustarts daraus.
  Werte 27.09.–01.10.2026 einmalig aus den 5-min-Statistiken von Home Assistant übernommen (`energie/import_ha.py`).
- **homi-Server** (der NUC selbst) in `/api/status` → `server`: Leistung = CPU+RAM gemessen über Intel RAPL
  (`/sys/devices/virtual/powercap/intel-rapl`, read-only gemountet) + 4 W geschätzt für Platine/SSD/Netzteil
  (`NUC_REST_W`), CPU-Last, Temperatur, RAM, Uptime, Erreichbarkeit HA/nginx, kWh und CHF pro Jahr.
  Auf der Seite: Server im Schrank unter der Treppe (fest im Inventar, eigene Antworten auf alle Verben) und eigene Kachel.
- **Einspeise-Fahrplan** `/api/einspeisung` (Vorbereitung auf dynamische Einspeisetarife ab 2027): pro Stunde heute/morgen
  Börsenpreis, PV-Prognose, typischer Verbrauch (Mittel 7 Tage), Empfehlung (einspeisen / Batterie laden /
  **Einspeisung stoppen** bei Minuspreisen); Wert der Einspeisung dynamisch vs. fix (GWS) und **optimierter
  Batterie-Fahrplan** (virtuelle 5 kWh: lädt in den billigsten bzw. negativen Stunden) vs. einfaches Laden.
  Auf homi: rot blinkendes Warnband bei Minuspreisen heute/morgen, Kachel „Einspeisung dynamisch heute“, Sprüche.
- **Solarprognose** `/api/prognose`: Open-Meteo-Einstrahlung auf die Modulebene (`energie/anlage.json`: Stäfa,
  25° Neigung, Azimut +30° = Süd-Südwest, aus den Messdaten bestimmt) × Eichfaktor, der stündlich aus den eigenen
  Messwerten der letzten 14 Tage nachgeführt wird. Heute/morgen/übermorgen in kWh, Spitze, Bewölkung und bestes
  2-Stunden-Fenster. Abendprognose und Messung werden in `energie/data/prognose_log.json` verglichen (Treffsicherheit).
  Rückblick 27.09.–03.10.2026: sonnige Tage ±6 %, trübe Tage bis +23 %.
  Mail jeden Abend um 19:00 (`energie/prognose-mail.py`, Timer `homi-prognose`). HA: `sensor.pv_prognose_heute`,
  `sensor.pv_prognose_morgen`, `sensor.pv_prognose_bestes_zeitfenster_morgen`. Auf homi: Kachel und Sprüche.
- **Messperioden** `energie/messungen.json` (z. B. „Grundlast Abwesenheit“ 04.–09.10.2026, niemand zu Hause):
  `energie/messung-bericht.py "<name>"` wertet das 10-Minuten-Archiv aus (Mittel, Nacht-Median, Minimum, Tage,
  Tagesprofil, Spitzen, Hochrechnung pro Jahr, Vergleich mit bewohnten Tagen) und mailt den Bericht;
  `--nur-anzeigen` gibt ihn nur aus. Einmal-Timer `homi-messung` am 09.10.2026 12:15.
- **App (PWA) mit Push-Nachrichten**: homi ist installierbar (Android: Menü → «App installieren», iPhone: Teilen →
  «Zum Home-Bildschirm», Push ab iOS 16.4). `www/manifest.webmanifest`, `www/sw.js` (Push anzeigen, Offline-Seite
  `www/offline.html`, keine Live-Daten im Cache), Icons `www/icons/` (erzeugt mit `energie/icons.py`, Pixel-Art).
  Abschnitt «homi als App» unten auf der Seite: Push ein/aus, Themen (Minuspreise, Prognose, Störungen), Testnachricht.
  - Anmelden: `POST /api/push` (`anmelden`/`abmelden`/`lesen`/`test`; nur Endpunkte der bekannten Push-Dienste
    Google, Mozilla, Apple, Microsoft) → `energie/data/push/abos/<hash>.json` (nicht im Repo).
  - Versand auf dem Host: `energie/push.py` (Web Push mit VAPID, RFC 8291/8292, `python3-cryptography`), Unit-Vorlage
    `homi-push@.service`; Timer `homi-push-minuspreis` (06:45), `homi-push-prognose` (19:00), `homi-push-ausfall`
    (alle 5 min: homi weg oder Dienst aus ≥ 15 min, Fronius tagsüber 9–16 Uhr ≥ 30 min; meldet auch die Erholung),
    Pfad-Unit `homi-push-test.path` (Testknopf). Abgelaufene Abos (HTTP 404/410) werden automatisch gelöscht.
  - VAPID-Schlüssel: `energie/data/push/vapid_private.pem` (Secret, nicht im Repo; einmalig `./push.py schluessel`).
    Geht er verloren, müssen alle Geräte Push neu einschalten. `./push.py liste` zeigt die angemeldeten Geräte,
    `./push.py minuspreis --test` schickt einen Test-Alarm.

  ```bash
  cd ~/.config/systemd/user && for u in 'homi-push@.service' homi-push-{minuspreis,prognose,ausfall}.timer homi-push-test.path; do ln -sf ~/domus/energie/$u $u; done
  systemctl --user daemon-reload && systemctl --user enable --now homi-push-{minuspreis,prognose,ausfall}.timer homi-push-test.path
  ```
- **Logbuch** `logbuch.html` (Link im Kassenbuch): Tag (10 min), Woche, Monat, Jahr, Alles; Ansicht per `#tag`, `#monat` …
- liefert die Webseite `energie/www/index.html` aus (Retro-Adventure-Look, zufällige Geräte passend zum Smart-Meter-Verbrauch).
  Tag/Nacht richtet sich nach der PV-Leistung: nachts Mond, Sterne, beleuchtete Räume, Nachtstrom-Sprüche;
  der Schlafmodus erscheint nur noch, wenn der Fronius keine Daten liefert.

https://home.biber.solar – nginx (`proxy/conf.d/home.biber.solar.conf`) leitet an `host.docker.internal:8099`,
nur GET/HEAD (Ausnahme: `POST /api/push`, max. 4 kB, eigenes Rate-Limit `home_push`), API mit Rate-Limit.
Manifest, Service Worker, Icons und Offline-Seite sind ohne Passwort abrufbar (keine Daten, nötig für Installation/Push). Zertifikat einmalig geholt mit
`docker exec certbot certbot certonly --webroot -w /var/www/certbot -d home.biber.solar --email … --agree-tos -n`
(Erneuerung automatisch).

**Passwortschutz** (HTTP Basic Auth, Benutzer `home`): Hash in `proxy/conf.d/home.biber.solar.htpasswd` (nicht im Repo).
Home Assistant liest intern über `127.0.0.1:8099` und ist nicht betroffen. Passwort ändern:

```bash
cd ~/domus/proxy
printf 'home:%s\n' "$(openssl passwd -apr1 'NEUES-PASSWORT')" > conf.d/home.biber.solar.htpasswd
docker exec nginx nginx -s reload
```

Home Assistant liest per `rest` (in `configuration.yaml`): `sensor.strompreis_bezug`, `sensor.ruckliefervergutung`
(beide CHF/kWh, fürs Energie-Dashboard), `sensor.stromkosten_aktuell`, `sensor.einspeiseerlos_aktuell` (CHF/h).
Börse: `sensor.borsenpreis_day_ahead` (Rp/kWh, Attribute min/max/nächster negativer Preis),
`binary_sensor.borsenpreis_negativ`, `sensor.borsenpreise_prognose` (Attribut `preise` mit allen Stundenwerten).
Dazu `sensor.borsenpreis_day_ahead_chf` (CHF/kWh) für den direkten Vergleich mit den GWS-Tarifen.
Speicher-Simulation: `sensor.speicher_simulation_<grösse>_ersparnis` (CHF seit Start, Attribute heute/Zyklen/
Autarkie/Hochrechnung) und `sensor.speicher_simulation_<grösse>_ladestand` (%); Karten im Dashboard „Energie & Börse“.

**Dashboard „Energie & Börse“** (`/energie-boerse`, in `config/.storage/lovelace.energie_boerse`): Energie-Karten wie im
eingebauten Energie-Dashboard (das sich nicht erweitern lässt) plus aktuelle Preise, Verlauf Börse vs. GWS (7 Tage)
und Tabelle der kommenden Börsenpreise (🟩 unter GWS-Vergütung, 🟨 darüber, 🟥 negativ).

**Energie-Dashboard** (`homeassistant/config/.storage/energy`, nur bei gestopptem HA bearbeiten):
- Netz „GWS“: Bezug `sensor.netzbezug_energie`, Einspeisung `sensor.netzeinspeisung_energie` – kWh-Zähler per
  `integration`-Sensor (in `configuration.yaml`) aus den Fronius-Leistungen `sensor.solarnet_leistung_netzbezug/-einspeisung`,
  weil der Smart Meter im Verbrauchszweig sitzt und selbst keine Netzzähler liefert.
- Preise: `sensor.strompreis_bezug` / `sensor.ruckliefervergutung`, Grundgebühren (Netznutzung + Messtarif,
  CHF 11.35/Monat inkl. MWST) als `cost_adjustment_day: 0.373`.
- Solar: `sensor.symo_8_2_3_m_1_energie_gesamt` (Wechselrichter-Zähler).
- Nachtmodus am Wechselrichter ist aktiv (seit 30.09.2026): Der Fronius misst auch nachts, der Netzbezug wird durchgehend erfasst.

Neue Tarife: Wenn das JSON vom PDF abweicht, `bezug_exkl_override` in `energie/tarif.json` setzen;
neue Rückliefervergütung pro Jahr unter `rueckliefer` eintragen. Danach `cd energie && docker compose restart`.

### E-Mail-Versand (Proton SMTP)

Absender `domus@biber.solar` über `smtp.protonmail.ch:587` (STARTTLS) mit einem Proton-SMTP-Token.

- **Home Assistant:** Integration **SMTP** („domus_mail“, eingerichtet per einmaligem YAML-Import), Entität
  `notify.domus_mail_patrick_biber_solar`. Zugangsdaten zusätzlich als `smtp_username` / `smtp_password` in
  `homeassistant/config/secrets.yaml` (nicht im Repo). Token ändern: Geräte & Dienste → SMTP → Neu konfigurieren.
- **Systemmails:** `/etc/aliases` leitet `root` und alle anderen lokalen Empfänger an `patrick@biber.solar`.
- **Host (`mail`-Befehl):** `msmtp` + `msmtp-mta` (stellt `sendmail` bereit) + `bsd-mailx` (stellt `mail` bereit).
  Konfiguration in `/etc/msmtprc` (`root:msmtp`, `640`, enthält den Token), `msmtp` ist per
  `dpkg-statoverride` setgid `msmtp` – so kann jeder Benutzer senden, aber niemand das Passwort lesen.

```bash
echo "Testinhalt" | mail -s "Betreff" empfaenger@example.com
msmtp --serverinfo            # Verbindung zu Proton prüfen
```

## Betrieb

```bash
# Update eines Dienstes (vorher Backup!)
cd <dienst> && docker compose pull && docker compose up -d

# Logs
docker compose logs -f
```

### Backup Home Assistant

```bash
~/domus/homeassistant/backup.sh   # sichert config/ nach backups/, behält die letzten 10 (KEEP=20 ./backup.sh für mehr)
```

### Webseite biber.solar (`biber-solar/`, `biber-solar-test/`)

Statische Webseite aus https://github.com/patbiber/biber-solar (kein Build-Schritt). Zwei nginx-Container im Netz
`proxy_default`, ohne eigene Ports; der Proxy leitet weiter und hält die Let's-Encrypt-Zertifikate.

| | Hauptseite `biber-solar/` | Vorschau `biber-solar-test/` |
|---|---|---|
| Adresse | https://biber.solar (www → 301 auf ohne www) | https://test.biber.solar (`noindex`) |
| Inhalt | Klon von `main` in `site/`, Timer `biber-solar` alle 5 min (`update.sh`) | **Arbeitskopie** `site/`, Timer `biber-solar-test` holt neue Commits nur, wenn nichts in Arbeit ist |
| Zweck | was veröffentlicht ist | Änderungen von Claude sofort ansehen, erst danach `git push` |

Ändern von hier: in `biber-solar-test/site` bearbeiten → auf test.biber.solar prüfen → commit → `git -C biber-solar-test/site push`
(Push über eigenen Deploy-Key `~/.ssh/github_biber_solar`, Host-Alias `github-biber-solar` in `~/.ssh/config`;
der öffentliche Schlüssel muss in GitHub unter biber-solar → Settings → Deploy keys mit **Allow write access** eingetragen sein).
Höchstens 5 Minuten nach dem Push ist die Änderung auf biber.solar.

**Umzug von OVH:** seit 03.10.2026 live auf dem NUC (`proxy/conf.d/biber.solar.conf`, Zertifikat für biber.solar +
www.biber.solar, Erneuerung automatisch). Bei OVH zeigen nur die **A-Einträge** von `biber.solar` und `www` auf den NUC;
MX, SPF, DKIM, DMARC und die Proton-TXT-Einträge bleiben unverändert. `training.biber.solar` ist ebenfalls umgezogen (siehe unten).

```bash
ln -sf ~/domus/biber-solar/biber-solar.{service,timer} ~/domus/biber-solar-test/biber-solar-test.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now biber-solar.timer biber-solar-test.timer
```

### Verkaufsseite homi.solar (`energy/`)

Hauptadresse seit 09.10.2026: **https://homi.solar**. `www.homi.solar` leitet mit 301, die frühere Adresse
`energy.biber.solar` dauerhaft mit **308** weiter (Pfad, Parameter und Methode bleiben – Bestätigungs-/Abmeldelinks in
alten Mails und Formular-POSTs alter Seiten funktionieren weiter). Mail-Links verwenden `URL` in `energy/mailer.py`.


homi als Angebot für Haus & Betrieb – Start des Geschäfts. Statische Seite `energy/www/` (index.html, style.css, app.js,
keine externen Ressourcen, kein Tracking) und kleiner Python-Server `energy/server.py` (Container `energy`, Port 8080
nur im Netz `proxy_default`).
- **Simulierte Live-Demo** (app.js): echte Zürcher Uhrzeit und Sonnenstand für Stäfa, Wetter/Verbrauch pro Tag
  reproduzierbar simuliert; Profile Einfamilienhaus (9.8 kWp, 10 kWh Batterie, WP, E-Auto) und Gewerbebetrieb (60 kWp).
  Energiefluss, Tagesverlauf mit Prognose, Börsenpreise, Verbraucher, Meldungen, Zeitraffer „Ein Tag in 30 Sekunden“.
- **KI-Demo**: Wunsch-Chips bauen sichtbar neue Kacheln; freier Wunsch wird ins Formular übernommen.
- **Preise** aus dem Businessplan (Pilotpreise, anpassen in index.html, Abschnitt `#preise`).
- **Anfrageformular** → `POST /api/anfrage` (Pflichtfelder, Honigtopf, Mindestzeit; nginx: max. 2/min pro IP) →
  `energy/anfragen/neu/*.json` (nicht im Repo) → Pfad-Unit `energy-anfrage.path` ruft sofort `energy/anfrage-mail.py`
  auf: Mail an root (→ patrick@biber.solar) mit Reply-To des Interessenten, Datei nach `anfragen/versendet/`.
  Timer `energy-anfrage.timer` holt stündlich fehlgeschlagene Sendungen nach. Der Container braucht keine Mail-Zugangsdaten.
- **Eigene Adresse `<name>.homi.solar`**: erwähnt in «So geht's» (Schritt 3), in der App-Liste mit Link auf
  https://beispiel.homi.solar und in der FAQ «Wie erreiche ich mein homi?».
- **Abschnitt «So sieht dein homi aus»** (`#ansicht`): drei echte Handy-Screenshots der Kunden-Oberfläche
  (`img/app-uebersicht.png`, `app-verlauf.png`, `app-preise.png`), auf dem Handy wischbar. Neu aufnehmen mit
  `energy/handy-screenshots.py` (Timer `homi-screenshots`, 11:30–14:30: nur bei Solar ≥ 2 kW, ersetzt Bilder und Text,
  committet, pusht, mailt und schaltet sich ab; bis 17.10.2026). `--probe <ordner>` nimmt nur auf, ohne etwas zu ändern.
- **Abschnitt «homi als App»** (`#app`): Handy-Rahmen mit echtem homi-Screenshot (`img/app-uebersicht.png`) und
  eingeblendeter Beispiel-Push-Nachricht.
- Proxy `proxy/conf.d/homi.solar.conf`: Let's Encrypt, strenge CSP, nur GET bzw. POST fürs Formular
  (`energy.biber.solar.conf` enthält nur noch die Weiterleitung).

```bash
cd ~/domus/energy && docker compose up -d
ln -sf ~/domus/energy/energy-anfrage.{service,path,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now energy-anfrage.path energy-anfrage.timer
```

**Minuspreise (homi.solar `#einspeisung`, prominent nach dem Kopfbereich):** Erklärung dynamische Einspeisetarife,
Beispieltag mit Rechnung (−CHF 0.98 ohne Steuerung, +CHF 1.26 mit homi), zwei Angebote:
- **Gratis-Warnung:** Anmeldung `POST /api/warnung` → Double-Opt-in (`energy/abos/ausstehend/`, Versandauftrag
  `abos/versand/` → Pfad-Unit `energy-warnung.path` → `energy/warnung-versand.py` mailt den Bestätigungslink) →
  `abos/aktiv/<abmelde-token>.json`. Abmelden per Link in jeder Mail (`/api/warnung/abmelden?t=…`, List-Unsubscribe).
  Unbestätigte Anmeldungen werden nach 7 Tagen gelöscht. `energy/abos/` ist nicht im Repo.
- **Morgen-Mail** `energy/minuspreis-warnung.py` (Timer `energy-minuspreis`, 06:45): nur an Tagen mit negativen
  Day-Ahead-Preisen; an Patrick mit eigenem Fahrplan aus homi, an alle Abonnenten allgemein mit Handlungstipps
  (Verbrauch verschieben, Wechselrichter per App/Display drosseln). Test: `minuspreis-warnung.py --test` (nur Patrick).
- **Automatisch im Abo** (Paket «Steuerung», Gateway): Einspeisestopp und Batterie nach Börsenpreis.

```bash
ln -sf ~/domus/energy/energy-{warnung.service,warnung.path,minuspreis.service,minuspreis.timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now energy-warnung.path energy-minuspreis.timer
```

### Kunden-Domain homi.solar (`kunden/`)

`homi.solar` ist die Basis-Domain für alle Kunden-Instanzen: **`<name>.homi.solar`** (z. B. `sonnenhof.homi.solar`).
- DNS bei OVH: A-Einträge `homi.solar`, `www` und Wildcard `*` → NUC. Neue Kunden brauchen keinen DNS-Eintrag.
- `homi.solar` ist zugleich die Adresse der Verkaufsseite (siehe oben), `www.homi.solar` leitet dorthin weiter.
- Nicht eingerichtete Namen lehnt `proxy/conf.d/00-default.conf` ab (HTTP 444, HTTPS `ssl_reject_handshake`).
- **`kunden/kunde.py`** richtet eine Instanz in einem Schritt ein: DNS prüfen, Zertifikat (HTTP-01 über den
  Standardserver), Passwort (Benutzer `homi`, wird einmal angezeigt), nginx-Konfiguration aus `kunden/kunde.conf.vorlage`
  (wie home.biber.solar: Basic Auth, CSP, nur GET/HEAD + Push-Anmeldung, App-Hülle ohne Passwort), `nginx -t` mit
  Zurückrollen bei Fehler, Reload.
  Erzeugt `proxy/conf.d/kunde-<name>.conf` und `.htpasswd` – **nicht im Repo** (Kundennamen = Personendaten).

```bash
kunden/kunde.py neu sonnenhof                    # Platzhalterseite, Passwort wird erzeugt
kunden/kunde.py ziel sonnenhof host:8099         # homi-Dienst auf dem NUC (Port)
kunden/kunde.py ziel sonnenhof http://homi-sonnenhof:8099   # Container im Netz proxy_default / WireGuard-Adresse
kunden/kunde.py passwort sonnenhof               # neues Passwort
kunden/kunde.py liste
kunden/kunde.py entfernen sonnenhof --ja         # Konfiguration, Passwort, Zertifikat löschen
```

- **Kunden-Oberfläche** `energie/www-kunde/` (modernes, marktfähiges Layout im Stil von homi.solar, hell/dunkel,
  Handy mit Tab-Leiste unten): `energie/server.py` liefert sie für jeden Aufruf über `<name>.homi.solar` aus (Host-Header),
  alle anderen Adressen (home.biber.solar, LAN) bekommen weiterhin die Retro-Seite `www/`. Gleiche API, gleiche Push-Abos.
  Bereiche: Übersicht (Energiefluss live, Kennzahlen heute, Sonne morgen, Strompreis, 24 h), Verlauf (Woche/Monat/Jahr
  aus dem Archiv), Strompreis (Börse heute/morgen, Tarif, Wert der Einspeisung, Fahrplan der Überschuss-Stunden),
  Mehr (App/Push, Batterie-Simulation, Anlage, Kontakt). Diagramme als eigenes SVG mit Werten beim Antippen, keine
  Fremdbibliothek. Anzeigename aus `energie/anlage.json` (`titel`) über `/api/info`.
- **Erste Instanz: https://biber.homi.solar** = Patricks eigenes homi (Ziel `host:8099`, seit 09.10.2026; home.biber.solar
  läuft parallel weiter). Push-Abos und Daten teilen sich beide Adressen, die App muss pro Adresse installiert werden.
- Der Platzhalter (`kunden/platzhalter.html`, «Hier wohnt bald ein homi») ist immer öffentlich; das Passwort gilt,
  sobald ein Ziel gesetzt ist. `beispiel.homi.solar` ist die öffentliche Musterseite (ohne Passwort).
- **Wildcard-Zertifikat `*.homi.solar`** (seit 09.10.2026, Name `wildcard.homi.solar`): alle Kunden-Instanzen nutzen
  es, `kunde.py` holt dann kein Einzelzertifikat mehr → Kundennamen erscheinen nicht in den öffentlichen
  Certificate-Transparency-Logs, neue Instanzen sind in unter einer Sekunde eingerichtet.
  Geholt mit `proxy/wildcard-cert.sh` per DNS-01: `proxy/ovh-dns-hook.py` setzt den TXT-Eintrag `_acme-challenge`
  über die OVH-API, wartet 90 s und löscht ihn danach wieder (das offizielle Plugin certbot-dns-ovh bräuchte zusätzlich
  das Recht, alle Zonen aufzulisten). Hook und Zugangsdaten liegen in `proxy/certbot/conf/` (`ovh.ini`, 600, nicht im
  Repo); der certbot-Container erneuert es mit denselben Hooks. Nach einem OVH-Schlüsselwechsel `wildcard-cert.sh`
  erneut aufrufen (schreibt `ovh.ini` neu, holt nur bei Bedarf ein neues Zertifikat).
- DNS bleibt öffentlich: Wer den Namen kennt, kann ihn auflösen → trotzdem neutrale Namen empfehlen.
- IP-Nachführung: `system/ovh-dns.py` führt beide Zonen nach (biber.solar und homi.solar mit `""`, `www`, `*`;
  TTL 300 s). Fehlen für eine Zone Rechte, meldet es sie als «NICHT möglich» (Exit-Code 2), die anderen laufen weiter.

### Trainingsseite training.biber.solar (`training/`)

Solar- & Haustechnik-Wissen, MkDocs Material, Quelle https://github.com/patbiber/solartech (`docs/`, `mkdocs.yml`).
- `update.sh` (Timer `training`, alle 15 min): holt neue Commits nach `training/src/`, baut mit dem Image
  `squidfunk/mkdocs-material:9` nach `training/ausgabe/build-<commit>/` und schaltet erst danach den Link
  `ausgabe/aktuell` um (nie eine halbfertige Seite). Erzwingen: `training/update.sh --neu`.
- Die Bilder in `docs/` sind im Repo als **absolute Symlinks auf den alten OVH-Pfad**
  `/var/www/html/training.biber.solar/solartech/…` angelegt; der Build hängt den Klon deshalb zusätzlich unter
  diesem Pfad ein. Wer die Links im Repo relativ macht (`../Bild.png`), braucht das nicht mehr.
- Container `training` (nginx, read-only) im Netz `proxy_default`; Proxy `proxy/conf.d/training.biber.solar.conf`,
  Let's Encrypt. Seit 03.10.2026 auf dem NUC (vorher OVH), Inhalt beim Umzug mit der OVH-Seite verglichen (identisch).

```bash
ln -sf ~/domus/training/training.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now training.timer
```

### Updates und Neustarts (`system/`)

- **Ubuntu** (`unattended-upgrades`, Ergänzung in `/etc/apt/apt.conf.d/52domus-unattended`): täglich ~06:00
  Sicherheitsupdates, Fehlerbehebungen (`-updates`) und Docker CE. Neustart **nur wenn nötig**, dann um **03:30**.
  Bericht per Mail an root (→ patrick@biber.solar), wenn sich etwas geändert hat. Release-Upgrades (z. B. 28.04) nie automatisch.
- **Nach jedem Neustart** schickt `system/boot-report.sh` (User-Dienst `domus-boot-report`, 3 min nach dem Start) eine Mail:
  Kernel, Grund, Home Assistant, homi/Fronius, nginx, Container.
- **Docker-Images** (Home Assistant, nginx, certbot, Python): `system/image-update.sh` (Timer `domus-image-update`,
  Montag 04:15) holt neue Images, macht vor einem Home-Assistant-Update automatisch ein Backup, merkt das alte Image
  als `domus-rollback/<dienst>:letzte`, erstellt nur geänderte Container neu und prüft danach (bis 5 min):
  HA antwortet (200), homi liefert `/api/status`, domus.biber.solar 200, home.biber.solar 401, die beiden Webseiten-Container antworten im Proxy-Netz, certbot läuft.
  Schlägt die Prüfung fehl, wird automatisch die alte Version wiederhergestellt. Mail an root nur bei Änderung/Fehler.
  Test des Zurückrollens: `NO_PULL=1 PRUEF_VERSUCHE=6 system/image-update.sh` mit absichtlich falsch getaggtem Image.

```bash
ln -sf ~/domus/system/domus-boot-report.service ~/domus/system/domus-image-update.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable domus-boot-report.service && systemctl --user enable --now domus-image-update.timer
systemctl --user start domus-image-update.service   # Update sofort ausführen
cat /var/run/reboot-required 2>/dev/null   # Neustart nötig?
```

### Überwachung der öffentlichen IP (`system/ip-check.sh`)

Timer `domus-ip-check` alle 5 min: ermittelt die öffentliche IPv4 (mindestens zwei von ipify, icanhazip, ifconfig.me
müssen übereinstimmen) und prüft, ob biber.solar, www, training, domus, home, test und energy darauf zeigen.
- IP geändert → **sofort** Mail „ÖFFENTLICHE IP GEÄNDERT -> <neue IP>“ mit allen anzupassenden A-Einträgen (OVH).
- DNS zeigt nicht auf die IP → Mail, danach Erinnerung höchstens alle 6 h; wieder in Ordnung → Entwarnung.
- Zustand in `~/.local/state/domus/` (`public_ip`, `ip_meldung`).
- **Automatische DNS-Nachführung:** Stimmt ein A-Eintrag nicht, setzt `system/ovh-dns.py --setzen <ip>` die Einträge
  über die OVH-API neu (nur A-Einträge von biber.solar, www, training, domus, home, test, energy, `*` sowie homi.solar, www, `*`;
  MX/TXT bleiben unberührt) und
  lädt die Zone neu. Zugangsdaten `OVH_*` in `.env` (Vorlage `.env.example`, Token-Rechte dort beschrieben).
  Ohne Zugangsdaten oder bei Fehler: Mail mit Anleitung für die manuelle Änderung. `system/ovh-dns.py` ohne
  Argument zeigt die aktuellen A-Einträge laut OVH.
- Wildcard `*.biber.solar` (A, seit 07.10.2026, TTL 300 s): neue Subdomains brauchen keinen DNS-Eintrag mehr,
  nur eine nginx-Datei mit Zertifikat. Unbekannte Namen lehnt `proxy/conf.d/00-default.conf` ab (HTTP 444, HTTPS
  `ssl_reject_handshake`), damit nie das Zertifikat einer anderen Seite erscheint.
- TTL der A-Einträge: 300 s (home.biber.solar 60 s), gesetzt am 03.10.2026 – nach einem IP-Wechsel sind die Seiten
  so nach spätestens ca. 10 Minuten wieder erreichbar.

```bash
ln -sf ~/domus/system/domus-ip-check.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now domus-ip-check.timer
```

### Aufbewahrung von Daten und Logs

| Was | Wo | Wie lange |
|---|---|---|
| Livewerte, 24-h-Diagramm, Börsenpreise | Arbeitsspeicher Energie-Dienst | bis zum nächsten Abruf bzw. 24 h, nach Neustart leer |
| Speicher-Simulation | `energie/data/speicher.json` | unbegrenzt (ein Eintrag pro Tag) |
| homi-Archiv (10 min) | `energie/data/archiv/` | 5 Jahre, danach automatisch gelöscht |
| HA-Zustände und 5-min-Werte | `home-assistant_v2.db` | 10 Tage (HA-Standard) |
| HA-Stundenstatistik (Energie, Kosten) | `home-assistant_v2.db` | unbegrenzt |
| Tarifarchiv GWS | `strompreise/data/` | unbegrenzt |
| HA-Backups | `backups/` | die letzten 10 (`backup.sh`) |
| Container-Logs | Docker (`json-file`) | höchstens 3 × 10 MB pro Container (`logging:` in jeder `compose.yml`) |
| nginx home.biber.solar | Container-Log | Seitenaufrufe, Logins, Fehler; erfolgreiche `/api/…`-Abrufe werden nicht protokolliert |

## Regeln

Siehe [CLAUDE.md](CLAUDE.md): keine Secrets im Repo, keine öffentlichen Ports ohne Rückfrage.

## Host-Konfiguration

- Zeitzone: `Europe/Zurich` (`timedatectl set-timezone`).
- Zusätzliche IP `192.168.1.23/24` auf `eno1` (ohne Gateway), in derselben Netplan-Datei.
- DNS: Cloudflare `1.1.1.1` / `1.0.0.1` statt Router, gesetzt in `/etc/netplan/00-installer-config.yaml` (`use-dns: false` für DHCP/RA + `nameservers`).
