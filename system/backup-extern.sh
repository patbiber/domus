#!/usr/bin/env bash
# Backup ausser Haus: alles, was nur auf dem NUC liegt, verschlüsselt (gpg, AES-256) nach Proton Drive.
# Timer domus-backup-extern (täglich 03:10). Das Repo selbst liegt auf GitHub und ist nicht enthalten.
#
# Inhalt: .env, homi-Daten (Archiv, Push-Schlüssel, Speicher), Login-Daten (Anmelde-Adressen, Sitzungen, Passkeys),
#         Verkaufsseite (Abos, Anfragen), nginx-Konfiguration inkl. Kunden-Instanzen, Zertifikate, Tarifarchiv,
#         Mail-Konfiguration, neuestes Home-Assistant-Backup, Liste der systemd-Units.
# Ablage: Proton Drive (eigenes Backup-Konto) /my-files/homi-backup/homi-backup-JJJJ-MM-TT.tar.gz.gpg
#         behalten: die letzten 7 Tage + der jeweils erste Stand jedes Monats (12 Monate); lokal die letzten 3.
# Schlüssel: ~/.config/domus/backup-schluessel (nicht im Repo; Patrick hat eine Kopie im Passwortmanager).
# Wiederherstellen: gpg --decrypt homi-backup-….tar.gz.gpg | tar xzf -
# Aufruf: backup-extern.sh [--ohne-upload]   (--ohne-upload: nur lokal packen und prüfen)
set -euo pipefail
umask 077
DOMUS="$HOME/domus"
SCHLUESSEL="$HOME/.config/domus/backup-schluessel"
LOKAL="$DOMUS/backups/extern"
ZIEL="/my-files/homi-backup"
STATUS="${XDG_STATE_HOME:-$HOME/.local/state}/domus/backup-extern"
PD="$HOME/.local/bin/proton-drive"
export PROTON_DRIVE_CREDENTIALS_STORE=unsafe_file
HEUTE=$(date +%F)
NAME="homi-backup-$HEUTE.tar.gz.gpg"
mkdir -p "$LOKAL" "$(dirname "$STATUS")"

fehler() {
  echo "FEHLER: $*" >&2
  printf 'Das Backup ausser Haus (Proton Drive) ist heute fehlgeschlagen:\n\n  %s\n\nLetztes erfolgreiches Backup: %s\nProtokoll: journalctl --user -u domus-backup-extern\n' \
    "$*" "$(cat "$STATUS" 2>/dev/null || echo 'noch keins')" | mail -s "domus: Backup ausser Haus FEHLGESCHLAGEN" root || true
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
HA=$(ls -t "$DOMUS"/backups/homeassistant-*.tar.gz 2>/dev/null | head -1 || true)
TEILE=(.env energie/data login/data energy/abos energy/anfragen proxy/conf.d proxy/certbot/conf strompreise/data)
[ -n "$HA" ] && TEILE+=("${HA#$DOMUS/}")
sudo tar -czf "$ARBEIT/inhalt.tar.gz" -C "$DOMUS" "${TEILE[@]}" -C / etc/msmtprc etc/aliases -C "$ARBEIT" LIESMICH.txt \
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

[ "${1:-}" = "--ohne-upload" ] && { echo "ohne Upload beendet"; exit 0; }

# --- Hochladen
[ -x "$PD" ] || fehler "proton-drive fehlt ($PD)"
"$PD" filesystem list "$ZIEL" >/dev/null 2>&1 || "$PD" filesystem create-folder /my-files homi-backup >/dev/null \
  || fehler "Ordner $ZIEL nicht erreichbar – Anmeldung abgelaufen? (proton-drive auth login)"
"$PD" filesystem upload -t -f replace "$LOKAL/$NAME" "$ZIEL" >/dev/null 2>"$ARBEIT/upload.err" \
  || fehler "Upload fehlgeschlagen: $(tail -2 "$ARBEIT/upload.err" | tr '\n' ' ')"
"$PD" filesystem list "$ZIEL" 2>/dev/null | grep -q "$NAME" || fehler "Upload nicht in der Ablage sichtbar"

# --- Aufräumen: 7 Tage + erster Stand jedes Monats (12 Monate) behalten
BEHALTEN=$(python3 - "$("$PD" filesystem list "$ZIEL" 2>/dev/null)" <<'EOF'
import re, sys
from datetime import date, timedelta
namen = sorted(set(re.findall(r"homi-backup-(\d{4}-\d{2}-\d{2})\.tar\.gz\.gpg", sys.argv[1])))
heute = date.today()
behalten = {d for d in namen if date.fromisoformat(d) > heute - timedelta(days=7)}
erste = {}
for d in namen:
    erste.setdefault(d[:7], d)
behalten |= {d for m, d in erste.items() if date.fromisoformat(d) > heute - timedelta(days=366)}
print(" ".join(f"homi-backup-{d}.tar.gz.gpg" for d in namen if d not in behalten))
EOF
)
for alt in $BEHALTEN; do
  "$PD" filesystem trash "$ZIEL/$alt" >/dev/null 2>&1 && echo "entfernt: $alt"
done
[ -n "$BEHALTEN" ] && "$PD" filesystem empty-trash >/dev/null 2>&1 || true

date -Iseconds > "$STATUS"
echo "Backup ausser Haus ok: $NAME ($GROESSE)"
