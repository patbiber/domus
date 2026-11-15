# homi-Monitor im Gang (Raspberry Pi)

Ein Raspberry Pi zeigt am 17-Zoll-Bildschirm im Gang die homi-Seite im C64-Look, ohne Bedienung.
Die Anzeige ist **http://192.168.1.23:8099/monitor.html** (`energie/www/monitor.html`). Die Adresse
192.168.1.23 ist die feste Adresse von homi und wechselt bei einem Ausfall mit auf den Reserve-PC.

- **Oben:** Sonne → Haus ← Netz, live, alle 10 s neu.
- **Unten:** Alle 15 s wechselt die Tafel: Börsenpreis heute · letzte 24 Stunden · Sonnenprognose ·
  Dachstock-Heizung · Wochenbericht · Tipp vom Energieberater.
- **Fusszeile:** homi sagt, was gerade los ist. Ohne Daten seit 2 Minuten blinkt sie rot.
- **Minuspreis:** Bei negativem Börsenpreis blinkt oben ein Hinweis.
- **Dauerbetrieb:**
  - Gegen Einbrennen verschiebt sich alles alle 3 Minuten um ein paar Pixel.
  - Die Seite lädt sich jede Nacht um 4 Uhr neu.
  - Der Bildschirm ist von 23 bis 6 Uhr aus.
  - Der Pi startet sonntags um 04:30 neu.
- **Test im Browser:** `monitor.html?tafel=preis|tag|prognose|heizung|woche|tipp` hält eine Tafel fest.

## Was es braucht

| Teil | Hinweis |
|---|---|
| Raspberry Pi **2, 3, 4 oder 5** | Pi 3 oder neuer läuft flüssig. Pi 2 geht, startet aber langsam. **Pi 1 und Zero (erste Version) sind zu schwach.** Den Pi 2 erkennt man an «Raspberry Pi 2 Model B» auf der Platine, den Pi 3 an «Raspberry Pi 3 Model B/B+». |
| microSD-Karte ab 8 GB | Besser eine Marke («A1»), keine No-Name-Karte. |
| Netzteil | Pi 2/3: 5 V / 2,5 A Micro-USB. Pi 4: USB-C 3 A. |
| Kabel zum Bildschirm | HDMI. Hat der Bildschirm nur DVI: Kabel HDMI→DVI. Nur VGA: Adapter HDMI→VGA mit eigener Stromversorgung. |
| Netz | Am besten LAN-Kabel. WLAN geht ab Pi 3 (Pi 2 hat kein WLAN). |

## 1. SD-Karte beschreiben (am PC oder Mac, ca. 15 Minuten)

1. **Raspberry Pi Imager** laden und installieren: https://www.raspberrypi.com/software/
2. Imager starten und auswählen:
   - **Gerät:** Ihr Pi-Modell (z. B. «Raspberry Pi 3»).
   - **Betriebssystem:** «Raspberry Pi OS (other)» → **«Raspberry Pi OS Lite (32-bit)»**. Beim Pi 4 oder 5 geht
     auch «Lite (64-bit)». Wichtig ist «Lite», also ohne Desktop.
   - **SD-Karte:** die eingesteckte Karte.
3. Bei **«Einstellungen anpassen»** (im neuen Imager die Seite «Anpassung») eintragen:
   - **Hostname:** `homi-monitor`
   - **Benutzername:** `homi`. Das Passwort wählen Sie frei; Sie brauchen es nur am Pi selbst. Schreiben Sie es nicht in den Chat.
   - **WLAN:** nur wenn kein Kabel geht. Netzname und Passwort Ihres WLAN, Land **CH**.
   - **Zeitzone:** Europe/Zurich, Tastatur: ch.
   - **SSH aktivieren** → **«Nur Authentifizierung über öffentlichen Schlüssel»** und diesen Schlüssel einfügen.
     Er gehört dem NUC und ist öffentlich, also kein Geheimnis:

     ```
     ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEHlMSX1P/qxfctjcTisoa6CJSDRRV8qJjtjlcq0mw4V domus→homi-monitor
     ```
4. **Schreiben**, warten, bis «Fertig» erscheint, Karte entnehmen.

## 2. Pi anschliessen

SD-Karte in den Pi stecken, Bildschirm, LAN-Kabel und zuletzt den Strom anschliessen. Der erste Start
dauert ein paar Minuten. Danach steht auf dem Bildschirm eine Anmeldezeile («homi-monitor login:»). Das ist richtig so.

## 3. Einrichten

**Weg A (einfach):** Sagen Sie Claude «Monitor-Pi läuft». Claude sucht den Pi im Netz, gibt ihm die feste
Adresse **192.168.1.30** und richtet alles per SSH vom NUC aus ein. Danach startet der Pi neu und zeigt homi.

**Weg B (selbst):** Am Pi eine Tastatur anschliessen und mit `homi` und Ihrem Passwort anmelden. Dann eingeben:

```
curl -fsSL http://192.168.1.23:8099/pi/monitor-einrichten.sh | sudo bash
```

Das Skript arbeitet 10–20 Minuten, startet den Pi neu, und dann erscheint homi. Die Tastatur können Sie danach
abziehen.

Das Skript (`energie/www/pi/monitor-einrichten.sh`) richtet ein:
- Zeitzone;
- Chromium im Vollbild mit `cage`, ohne Desktop, startet von selbst und bei einem Absturz neu;
- Bildschirm aus von 23 bis 6 Uhr (`/etc/cron.d/homi-monitor`);
- Watchdog: Der Pi startet neu, wenn er hängt;
- Protokoll nur im Arbeitsspeicher, damit die SD-Karte geschont wird;
- automatische Sicherheitsupdates.

Andere Zeiten lassen sich beim Aufruf mitgeben, z. B. `… | sudo HOMI_AUS=22 HOMI_AN=7 bash`.

## Im Betrieb

- **Bildschirm von Hand an/aus** (per SSH): `homi-bildschirm an` / `homi-bildschirm aus`.
- **Neue Anzeige:** Änderungen an `monitor.html` kommen spätestens nachts um 4 Uhr auf den Bildschirm.
  Sofort geht es mit `ssh homi-monitor sudo reboot`.
- **Zugang vom NUC:** `ssh homi-monitor`. Eintrag in `~/.ssh/config`, Schlüssel `~/.ssh/domus_monitor`.
- **Feste Adresse:** 192.168.1.30, eingetragen als Reservierung in `pihole/compose.yml` (`FTLCONF_dhcp_hosts`).
- **Bleibt der Bildschirm schwarz**, obwohl es Tag ist: Liefert der NUC keine Daten, zeigt die Seite «homi ist nicht erreichbar».
  Ist er ganz schwarz, den Strom des Pi kurz trennen.
- **Datenschutz:** Die Seite ist nur im Heimnetz erreichbar, ohne Anmeldung, wie der bisherige Port 8099.
  Es werden keine neuen Daten gesammelt.
