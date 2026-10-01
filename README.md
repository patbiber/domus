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
| `system/` | Neustart-Bericht und wöchentliches Docker-Image-Update mit Prüfung und Zurückrollen (systemd-User-Dienste) | läuft |
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

Erneuerung läuft automatisch (certbot prüft alle 12h, nginx lädt alle 6h neu). Port 80 leitet auf 443 weiter.
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
- **Logbuch** `logbuch.html` (Link im Kassenbuch): Tag (10 min), Woche, Monat, Jahr, Alles; Ansicht per `#tag`, `#monat` …
- liefert die Webseite `energie/www/index.html` aus (Retro-Adventure-Look, zufällige Geräte passend zum Smart-Meter-Verbrauch).
  Tag/Nacht richtet sich nach der PV-Leistung: nachts Mond, Sterne, beleuchtete Räume, Nachtstrom-Sprüche;
  der Schlafmodus erscheint nur noch, wenn der Fronius keine Daten liefert.

https://home.biber.solar – nginx (`proxy/conf.d/home.biber.solar.conf`) leitet an `host.docker.internal:8099`,
nur GET/HEAD, API mit Rate-Limit. Zertifikat einmalig geholt mit
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

### Updates und Neustarts (`system/`)

- **Ubuntu** (`unattended-upgrades`, Ergänzung in `/etc/apt/apt.conf.d/52domus-unattended`): täglich ~06:00
  Sicherheitsupdates, Fehlerbehebungen (`-updates`) und Docker CE. Neustart **nur wenn nötig**, dann um **03:30**.
  Bericht per Mail an root (→ patrick@biber.solar), wenn sich etwas geändert hat. Release-Upgrades (z. B. 28.04) nie automatisch.
- **Nach jedem Neustart** schickt `system/boot-report.sh` (User-Dienst `domus-boot-report`, 3 min nach dem Start) eine Mail:
  Kernel, Grund, Home Assistant, homi/Fronius, nginx, Container.
- **Docker-Images** (Home Assistant, nginx, certbot, Python): `system/image-update.sh` (Timer `domus-image-update`,
  Montag 04:15) holt neue Images, macht vor einem Home-Assistant-Update automatisch ein Backup, merkt das alte Image
  als `domus-rollback/<dienst>:letzte`, erstellt nur geänderte Container neu und prüft danach (bis 5 min):
  HA antwortet (200), homi liefert `/api/status`, domus.biber.solar 200 und home.biber.solar 401, certbot läuft.
  Schlägt die Prüfung fehl, wird automatisch die alte Version wiederhergestellt. Mail an root nur bei Änderung/Fehler.
  Test des Zurückrollens: `NO_PULL=1 PRUEF_VERSUCHE=6 system/image-update.sh` mit absichtlich falsch getaggtem Image.

```bash
ln -sf ~/domus/system/domus-boot-report.service ~/domus/system/domus-image-update.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable domus-boot-report.service && systemctl --user enable --now domus-image-update.timer
systemctl --user start domus-image-update.service   # Update sofort ausführen
cat /var/run/reboot-required 2>/dev/null   # Neustart nötig?
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
