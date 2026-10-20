#!/usr/bin/env bash
# Verschlüsseltes Gesamt-Backup: alles, was nur auf dem NUC liegt, als tar.gz mit gpg (AES-256), Probe-Entschlüsselung.
# Ablage lokal in backups/extern/ (die letzten 3) und auf dem USB-Stick «BackupDomus» (die letzten 60);
# später zusätzlich auf den Reserve-PC domus-reserve (reserve/).
# (Online-Backup auf Proton Drive am 14.10.2026 verworfen – Patrick will stattdessen einen Reserve-PC im Haus.)
#
# Inhalt: .env, homi-Daten (Archiv, Push-Schlüssel, Speicher), Login-Daten (Anmelde-Adressen, Sitzungen, Passkeys),
#         Verkaufsseite (Abos, Anfragen), nginx-Konfiguration inkl. Kunden-Instanzen, Zertifikate, Tarifarchiv,
#         Mail-Konfiguration, neuestes Home-Assistant-Backup, Webstatistik (Umami-Datenbank), Liste der systemd-Units.
# Schlüssel: ~/.config/domus/backup-schluessel (nicht im Repo; Patrick hat eine Kopie im Passwortmanager).
# Wiederherstellen: gpg --decrypt homi-backup-….tar.gz.gpg | tar xzf -
set -euo pipefail
umask 077
DOMUS="$HOME/domus"
SCHLUESSEL="$HOME/.config/domus/backup-schluessel"
LOKAL="$DOMUS/backups/extern"
STATUS="${XDG_STATE_HOME:-$HOME/.local/state}/domus/backup-extern"
HEUTE=$(date +%F)
NAME="homi-backup-$HEUTE.tar.gz.gpg"
mkdir -p "$LOKAL" "$(dirname "$STATUS")"

fehler() {
  echo "FEHLER: $*" >&2
  printf 'Das verschlüsselte Gesamt-Backup ist heute fehlgeschlagen:\n\n  %s\n\nLetztes erfolgreiches Backup: %s\nProtokoll: journalctl --user -u domus-backup-extern\n' \
    "$*" "$(cat "$STATUS" 2>/dev/null || echo 'noch keins')" | mail -s "domus: Backup FEHLGESCHLAGEN" root || true
  exit 1
}
[ -s "$SCHLUESSEL" ] || fehler "Schlüssel fehlt: $SCHLUESSEL"

# --- Packen (sudo: Zertifikate, Mail-Konfiguration und Container-Dateien gehören root)
ARBEIT=$(mktemp -d); trap 'rm -rf "$ARBEIT"' EXIT
{
  echo "homi/domus Backup vom $(date -Iseconds) auf $(hostname)"
  echo "Repo-Stand: $(git -C "$DOMUS" rev-parse --short HEAD) (github.com/patbiber/domus)"
  echo; echo "Aktive systemd-User-Units:"; systemctl --user list-unit-files --state=enabled --no-pager --no-legend | awk '{print "  " $1}'
  echo; echo "Wiederherstellen: Repo klonen, dieses Archiv darüber auspacken (Pfade relativ zu ~/domus bzw. /etc),"
  echo "Units verlinken (README), Container starten. HA: backups/homeassistant-*.tar.gz nach homeassistant/config entpacken."
} > "$ARBEIT/LIESMICH.txt"
# Webstatistik (Umami): Datenbank-Auszug, falls der Dienst läuft
UMAMI_TEIL=()
if docker exec umami-db pg_dump -U umami umami 2>/dev/null | gzip > "$ARBEIT/umami.sql.gz" && [ -s "$ARBEIT/umami.sql.gz" ]; then
  UMAMI_TEIL=(umami.sql.gz)
fi
HA=$(ls -t "$DOMUS"/backups/homeassistant-*.tar.gz 2>/dev/null | head -1 || true)
TEILE=(.env energie/data login/data energy/abos energy/anfragen proxy/conf.d proxy/certbot/conf strompreise/data)
[ -n "$HA" ] && TEILE+=("${HA#$DOMUS/}")
sudo tar -czf "$ARBEIT/inhalt.tar.gz" -C "$DOMUS" "${TEILE[@]}" -C / etc/msmtprc etc/aliases -C "$ARBEIT" LIESMICH.txt "${UMAMI_TEIL[@]}" \
  || fehler "tar ist fehlgeschlagen"
sudo chown "$(id -u):$(id -g)" "$ARBEIT/inhalt.tar.gz"

# --- Verschlüsseln und prüfen (Entschlüsseln + Inhaltsliste muss klappen)
gpg --batch --yes --quiet --pinentry-mode loopback --passphrase-file "$SCHLUESSEL" --symmetric --cipher-algo AES256 \
  -o "$LOKAL/$NAME" "$ARBEIT/inhalt.tar.gz" || fehler "Verschlüsseln fehlgeschlagen"
ANZAHL=$(gpg --batch --quiet --pinentry-mode loopback --passphrase-file "$SCHLUESSEL" --decrypt "$LOKAL/$NAME" | tar tzf - | wc -l) \
  || fehler "Probe-Entschlüsseln fehlgeschlagen"
GROESSE=$(du -h "$LOKAL/$NAME" | cut -f1)
echo "Archiv $NAME: $GROESSE, $ANZAHL Einträge, Probe-Entschlüsseln ok"
ls -t "$LOKAL"/homi-backup-*.tar.gz.gpg | tail -n +4 | xargs -r rm -f        # lokal die letzten 3 behalten

# --- Kopie auf den USB-Stick «BackupDomus» (ext4), bis der Reserve-PC (reserve/) läuft. Nur während der Kopie
# eingehängt, damit Abziehen nichts kaputt macht. Die letzten 60 Stände bleiben (je ~56 MB).
USB_LABEL=BackupDomus
USB_MNT=/mnt/backup-usb
if [ -e "/dev/disk/by-label/$USB_LABEL" ]; then
  sudo mkdir -p "$USB_MNT"
  sudo mount -o noatime "LABEL=$USB_LABEL" "$USB_MNT" || fehler "USB-Stick $USB_LABEL lässt sich nicht einhängen"
  sudo install -d -o "$(id -u)" -g "$(id -g)" -m 700 "$USB_MNT/domus"
  if cp "$LOKAL/$NAME" "$USB_MNT/domus/$NAME.tmp" && mv "$USB_MNT/domus/$NAME.tmp" "$USB_MNT/domus/$NAME" \
     && cmp -s "$LOKAL/$NAME" "$USB_MNT/domus/$NAME"; then
    ls -t "$USB_MNT"/domus/homi-backup-*.tar.gz.gpg | tail -n +61 | xargs -r rm -f
    echo "USB-Stick: $(ls "$USB_MNT"/domus/homi-backup-*.tar.gz.gpg | wc -l) Stände, frei $(df -h --output=avail "$USB_MNT" | tail -1 | tr -d ' ')"
    sync; sudo umount "$USB_MNT"
  else
    sync; sudo umount "$USB_MNT" || true
    fehler "Kopie auf den USB-Stick $USB_LABEL fehlgeschlagen (voll oder defekt?)"
  fi
else
  printf 'Der USB-Stick «%s» steckt nicht am NUC – das heutige Backup liegt nur auf dem NUC.\nBitte wieder einstecken.\n' \
    "$USB_LABEL" | mail -s "domus: Backup-Stick fehlt" root || true
fi
date -Iseconds > "$STATUS"
echo "Backup ok: $NAME ($GROESSE)"
