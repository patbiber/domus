# Reserve-PC (Ausfallsicherung des NUC)

Seit 08.11.2026. Lenovo ThinkCentre 10NM (4 Kerne, 8 GB RAM, 256 GB NVMe), Ubuntu 26.04, Hostname `domus2`,
feste Adresse **192.168.1.24** (Netzwerkkarte wie beim NUC auf `eno1` umbenannt), SSH vom NUC mit `~/.ssh/domus_reserve`.
6-TB-Platte (WD60EZRX) als `/srv/backup` (ext4, Label `backup`).

| Was | Wo | Wie oft |
|---|---|---|
| Abgleich NUC → Reserve (`~/domus` ohne backups/, Datenbank-Kopien, Mail-Konfig, Units ohne `*.wants`) | NUC, `abgleich.sh`, User-Timer `domus-reserve-abgleich` | alle 10 min |
| Wächter: Übernahme, wenn NUC (.20 und .23) 3 min weg, Router erreichbar, Abgleich < 24 h | Reserve, `waechter.sh`, `domus-waechter.service` | dauernd |
| Übernahme: Rolle `aktiv`, Adresse .23, Datenbanken einsetzen, Container + Timer starten, Mail | Reserve, `uebernehmen.sh` | im Ausfall |
| Vorrang: NUC startet Docker nicht, solange die Reserve aktiv ist | NUC, `vorrang.sh`, `domus-vorrang.service` + Drop-in `docker.service.d/domus-vorrang.conf` | beim Start |
| Rücktritt im Betrieb (Reserve aktiv, NUC hält noch .23): Container + Timer stoppen, .23 abgeben | NUC, `vorrang.sh pruefen`, System-Timer `domus-vorrang-pruefen` | alle 30 s |
| Neustart der Reserve im Zustand «aktiv»: fehlende Dienste nachstarten (ohne Mail) | Reserve, `waechter.sh` → `uebernehmen.sh` | beim Start |
| **Reserve nur über Mittag** (seit 11.11.2026): Reserve schaltet sich selbst aus (`schlafen.sh`, System-Timer `domus-reserve-schlafen`, jede Minute) – nur wenn der NUC sich in den letzten 3 min gemeldet hat, ausserhalb 12:00–12:30, Knopf aus, nicht «aktiv», keine Sicherung/rsync – und stellt vorher den **RTC-Wecker** auf den nächsten Fensterbeginn (`rtcwake`). NUC (`mittag.sh`, User-Timer `domus-reserve-mittag`, jede Minute) meldet sich samt Knopf-Zustand (`/var/lib/domus/nuc-knopf`), weckt per **Wake-on-LAN nur für den Knopf «Reserve Server On»** in homi (home.biber.solar, `energie/data/reserve.json`, Zustand `reserve-status.json`) und meldet per Protokoll + Mail, wenn der Wecker bis 12:03 nicht gewirkt hat | NUC + Reserve; `homi/reserve.py` | jede Minute |
| Zurückwechseln | NUC, `zurueck-zum-nuc.sh` | **nur von Hand** |
| Versionierte Sicherung auf `/srv/backup/domus/<Datum>` (30 Tage, dann Monatserste 24 Monate), Images vorab holen | Reserve, `sicherung.sh`, Timer `domus-reserve-sicherung` | täglich 04:30 |
| Claude Code: Programm, `~/.claude` (Gedächtnis, Unterhaltungen, Anmeldung) und `~/.claude.json` auf die Reserve; dort startet es nur bei der Übernahme (tmux «claude», dieselbe Unterhaltung als Abzweigung, Remote Control «domus-reserve»); der Rückwechsel holt neuere Unterhaltungen und die Anmeldung zurück | NUC `abgleich.sh` / Reserve `uebernehmen.sh` | alle 10 min / im Ausfall |
| Zweiter Pi-hole, nur DNS, 192.168.1.24 (`pihole-zweit/`) | Reserve | dauernd |

**Rollen frei wählbar** (seit 08.11.2026): Adressen in `rollen.conf` (HAUPT_IP, RESERVE_IP, VIP). `rolle-einrichten.sh haupt`
bzw. `reserve` richtet den Rechner, auf dem es läuft, für seine Rolle ein (Netplan-VIP, Vorrang, Abgleich, Timer,
Wächter, Sicherung, pihole-zweit). NUC endgültig kaputt: domus2 hat übernommen → in `rollen.conf` HAUPT_IP=192.168.1.24,
RESERVE_IP=<domus3>, auf domus2 `rolle-einrichten.sh haupt`; domus3 mit Ubuntu + Benutzer claude vorbereiten, SSH-Schlüssel
`domus_reserve.pub` hinterlegen, dort `rolle-einrichten.sh reserve` (und pihole-Adressen anpassen, siehe rollen.conf).
GitHub-Deploy-Keys und `~/.gitconfig` werden mit abgeglichen, der Abend-Upload läuft auch während einer Übernahme.
Systemd-Units, die als Verknüpfung eingebunden sind, nie mit `disable` abschalten (löscht die Verknüpfung) – nur stoppen
und den Eintrag in `*.wants/` entfernen.

Zustand der Reserve: `/var/lib/domus/rolle` (`reserve` oder `aktiv`), Protokoll der Übernahme `/var/lib/domus/uebernahme.log`.
Docker-Netze haben feste Adressen (proxy_default 172.18, umami_intern 172.19, statistik 172.20), damit nginx, homi und homi-login auf beiden Rechnern gleich funktionieren. homi-login (Systemdienst, Benutzer uid 999/gid 982 auf beiden) startet auf der Reserve nur bei der Übernahme.
Neuer Dienst mit `compose.yml`: in die Listen in `uebernehmen.sh`, `zurueck-zum-nuc.sh` und `sicherung.sh` eintragen.
`autoinstall-vorlage.yaml` / `usb-erzeugen.py`: Stick-Dateien für eine Neuinstallation (wurde am 08.11. nicht verwendet,
Patrick hat von Hand installiert).

**Reserve nur über Mittag** (11.11.2026, Wunsch Patrick): `FENSTER_VON/BIS` und `RESERVE_MAC` in `rollen.conf`. Ausserhalb des Fensters
ist die Reserve aus – **dann gibt es keine automatische Übernahme** (nur mit dem Knopf «Reserve Server On» ist der Schutz durchgehend;
fällt der NUC aus, schaltet sich die Reserve nicht mehr ab und kann übernehmen). Starten: Mittag = RTC-Wecker (BIOS «Wake Up on Alarm»
darf aus sein, `rtcwake` stellt ihn; am 11.11. mit Probefenster 20:50 getestet), Knopf = Wake-on-LAN (`wakeonlan: true` in
`/etc/netplan/00-installer-config.yaml` der Reserve, Vorlage `~/00-installer-config.yaml.vor-wol`; BIOS «Wake on LAN» = Automatic).
Bewusst kein Wake-on-LAN als Notweckung im Fenster (Patrick: Wake-on-LAN nur für die App), damit klar ist, was gewirkt hat.
nginx: `POST:/api/reserve` steht in der Methoden-Liste von `proxy/conf.d/home.biber.solar.conf` (sonst 405 → «nur mit Passwort»).
Der Abgleich lässt die absichtlich ausgeschaltete Reserve in Ruhe (`.../reserve-abgleich/soll`), die Sicherung läuft um 12:05.
Die Retro-Kachel «Reserve-Server» zeigt «Backup: <Zeit>» der letzten Sicherung: `sicherung.sh` schreibt `/var/lib/domus/sicherung` («Zeit ok|fehler»), `mittag.sh` holt den Wert beim Start der Reserve und merkt ihn auf dem NUC (`~/.local/state/domus/reserve-abgleich/sicherung`, Fallback: Zeit des neuesten Sicherungsordners).

Die Kachel «Datensicherheit» (Retro-Seite, System-Gruppe) zeigt Abgleich NUC → Reserve, Backup NUC (verschlüsselt, 03:10), Home-Assistant-Backup, Backup auf der Reserve und den letzten GitHub-Upload; rot, wenn etwas zu alt (> 30 h, HA > 10 Tage) oder fehlgeschlagen ist. Quelle: `system/sicherheit-status.py` (User-Timer `domus-sicherheit-status`, jede Minute, nur Hauptrechner) → `energie/data/sicherheit-status.json` → `/api/sicherheit` (nur home.biber.solar; ohne frische Meldung zeigt die Kachel «keine Meldung»).

Port 8099 des NUC ist für die Reserve gesperrt – deshalb meldet der NUC den Knopf-Zustand per SSH statt dass die Reserve ihn abfragt.
