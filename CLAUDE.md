# Domus – Regeln für Claude

Home-System auf einem Intel NUC (Ubuntu Server). Jeder Dienst hat einen eigenen Ordner mit eigener `compose.yml`.

## Arbeitsweise
- Nach jeder funktionierenden Änderung **lokal committen, aber nicht selbst pushen**. Patricks Wunsch (21.10.2026): GitHub bekommt die Commits nur gesammelt einmal am Abend, zu einer zufälligen Zeit zwischen 20:00 und 22:00 – das macht der Timer `github-abend` (`system/github-abend.py`, setzt die Zeitstempel ins Abendfenster). Kein `git push` von Hand, auch nicht bei biber.solar.
- EnergyHub Academy: eigenes Repo in `academy/repo` (nicht in domus committen), nach Änderungen `academy/update.sh`. Inhalte immer Englisch, Französisch und Deutsch (Deutsch seit 07.11.2026, Schweizer Schreibweise mit «ss», Anrede «Sie»). Unter jeder Formel mit Abkürzungen oder Symbolen eine Legende: jedes Kürzel ausgeschrieben und erklärt, mit Einheit (Patricks Wunsch, 06.11.2026).
- biber.solar: Änderungen in `biber-solar-test/site` (Vorschau test.biber.solar) committen; nach Patricks «ok» mit `git -C biber-solar-test/site branch -f freigegeben main` freigeben und `biber-solar/update.sh` ausführen – live geht es sofort vom NUC, GitHub bekommt es am Abend.
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
- Zweisprachig (seit 21.10.2026): homi.solar und biber.solar haben je eine englische Fassung unter `/en/` (homi: `energy/www/en/index.html`, `en/privacy.html`; Texte in `energy/www/app.js` über `tr(deutsch, englisch)`). Deutsche Texte immer zusammen mit der englischen Fassung ändern. Umschalter oben rechts (Schweizer Wappen / Globus), «/» leitet bei nicht-deutscher Browsersprache nach `/en/` um (homi: `energy/server.py`, biber.solar: `biber-solar*/nginx.conf`), kein Cookie.
- Datenschutz: Ausnahme EnergyHub Academy (academy.biber.solar, Auftrag MPower) – eigene Datenschutzerklärung `/privacy` im Academy-Repo (seit 06.11.2026). Sonst: Jede Seite mit Anmeldung (heute und künftig, z. B. `<name>.homi.solar`, home.biber.solar, Login) verlinkt `https://homi.solar/datenschutz.html`. Neue Datenbearbeitungen (Felder, Dienste Dritter, Aufbewahrung) dort nachtragen, bevor sie live gehen.
- Webstatistik Umami (`umami/`, stats.biber.solar) nur auf öffentlichen Seiten (biber.solar, homi.solar), nie auf Seiten mit Anmeldung (auch nicht in der Academy). Session-Replay und Heatmaps in Umami nicht einschalten (widerspräche der Datenschutzerklärung).
- Reserve-PC 192.168.1.24 (seit 08.11.2026, `reserve/README.md`): übernimmt automatisch, wenn der NUC 3 Minuten weg ist; zurück nur von Hand (`reserve/zurueck-zum-nuc.sh`). Neue Dienste in die Dienstlisten von `reserve/uebernehmen.sh`, `zurueck-zum-nuc.sh` und `sicherung.sh` eintragen. Nie Container auf der Reserve starten, solange sie nicht aktiv ist (ausser `pihole-zweit`).
- Docker-Images werden wöchentlich automatisch aktualisiert (`system/image-update.sh`, mit HA-Backup, Prüfung und Zurückrollen). Manuelle Image-Updates nur über dieses Skript.
