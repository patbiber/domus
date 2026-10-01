#!/usr/bin/env bash
# Backup der Home-Assistant-Konfiguration nach ~/domus/backups/ und Rotation:
# behält die neuesten KEEP Backups (Standard 10), ältere werden gelöscht.
# Aufruf: ./backup.sh   (vor jeder Änderung an Home Assistant, siehe CLAUDE.md)
set -euo pipefail
cd "$(dirname "$0")"

KEEP="${KEEP:-10}"
DEST=../backups
FILE="$DEST/homeassistant-$(date +%F-%H%M).tar.gz"
mkdir -p "$DEST"

# Die Datenbank wird laufend geschrieben; tar meldet dann "file changed" (Exit 1).
# Die Konfiguration ist trotzdem vollständig gesichert, nur Exit >1 ist ein echter Fehler.
sudo tar czf "$FILE" -C . config || [ $? -eq 1 ]
echo "Backup: $FILE ($(sudo du -h "$FILE" | cut -f1))"

ls -1t "$DEST"/homeassistant-*.tar.gz | tail -n +$((KEEP + 1)) | while read -r alt; do
  sudo rm -f -- "$alt"
  echo "Entfernt (älter als die letzten $KEEP): $alt"
done
