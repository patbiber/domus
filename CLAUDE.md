# Domus – Regeln für Claude

Home-System auf einem Intel NUC (Ubuntu Server). Jeder Dienst hat einen eigenen Ordner mit eigener `compose.yml`.

## Arbeitsweise
- Nach jeder funktionierenden Änderung committen und zu GitHub pushen.
- Commit-Messages auf Deutsch.
- Antworten an den Nutzer auf Deutsch.
- Vor irreversiblen Aktionen nachfragen.

## Verbote / Sicherheit
- Keine Secrets ins Repo (Passwörter, Tokens, Keys, `.env`, Dienst-Daten). Nur `.env.example` mit Platzhaltern.
- Keine Ports öffentlich (ins Internet) öffnen ohne Rückfrage – weder Router-Portfreigaben noch Firewall-Regeln.
- Keine Datenträger formatieren oder partitionieren.
- Vor Änderungen an Home Assistant (Konfiguration, Update, Image-Wechsel) ein Backup erstellen.

## Konventionen
- Dienst starten: `cd <dienst> && docker compose up -d`
- Persistente Daten liegen im Dienstordner (z. B. `homeassistant/config/`) und sind per `.gitignore` ausgeschlossen.
- Gemeinsame Variablen stehen in `.env` im Repo-Root (Vorlage: `.env.example`); jede `compose.yml` bindet sie per `env_file: ../.env` ein.
- Backups liegen unter `backups/` (nicht im Repo). Home Assistant sichern mit `homeassistant/backup.sh` (behält die letzten 10).
- Jede `compose.yml` begrenzt das Docker-Log (`logging:` json-file, 3 × 10 MB).
- Neue oder fertige homi-Funktionen immer auch auf der Verkaufsseite homi.solar (`energy/www/`) nachtragen (Status ehrlich: live / bald), danach `?v=` hochzählen.
- Datenschutz: Jede Seite mit Anmeldung (heute und künftig, z. B. `<name>.homi.solar`, home.biber.solar, Login) verlinkt `https://homi.solar/datenschutz.html`. Neue Datenbearbeitungen (Felder, Dienste Dritter, Aufbewahrung) dort nachtragen, bevor sie live gehen.
- Webstatistik Umami (`umami/`, stats.biber.solar) nur auf öffentlichen Seiten (biber.solar, homi.solar), nie auf Seiten mit Anmeldung. Session-Replay und Heatmaps in Umami nicht einschalten (widerspräche der Datenschutzerklärung).
- Docker-Images werden wöchentlich automatisch aktualisiert (`system/image-update.sh`, mit HA-Backup, Prüfung und Zurückrollen). Manuelle Image-Updates nur über dieses Skript.
