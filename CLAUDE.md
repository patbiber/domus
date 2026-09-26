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
- Backups liegen unter `backups/` (nicht im Repo).
