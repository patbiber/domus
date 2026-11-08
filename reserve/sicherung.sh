#!/usr/bin/env bash
# Läuft täglich um 04:30 auf dem Reserve-PC (domus-reserve-sicherung.timer, als root):
# 1. Versionierte Sicherung von ~/domus auf die 6-TB-Platte /srv/backup/domus/<Datum>/ (rsync mit Hardlinks:
#    unveränderte Dateien belegen keinen Platz). Behalten: 30 Tage täglich, danach der Monatserste 24 Monate.
#    Die verschlüsselten Gesamt-Backups des NUC liegen zusätzlich unter /srv/backup/extern/ (kopiert abgleich.sh).
# 2. Solange die Reserve nur Reserve ist: Images vorab holen und die Academy bauen, damit eine Übernahme schnell geht.
set -uo pipefail
DOMUS=/home/claude/domus
ZIEL=/srv/backup/domus
mountpoint -q /srv/backup || { echo "Backup-Platte nicht eingehängt" | runuser -u claude -- mail -s "domus-reserve: Backup-Platte fehlt" root; exit 1; }
mkdir -p "$ZIEL"
HEUTE=$(date +%F)
LETZTE=$(ls -1d "$ZIEL"/20??-??-?? 2>/dev/null | grep -v "$HEUTE" | tail -1 || true)
rsync -aHAX --numeric-ids --delete ${LETZTE:+--link-dest="$LETZTE"} --exclude='/backups/reserve-stand/*.neu' \
  "$DOMUS/" "$ZIEL/$HEUTE/" || [ $? -eq 24 ] || { echo "rsync Code $?" | runuser -u claude -- mail -s "domus-reserve: Sicherung fehlgeschlagen" root; exit 1; }
for d in "$ZIEL"/20??-??-??; do
  tag=$(basename "$d")
  alter=$(( ( $(date +%s) - $(date -d "$tag" +%s) ) / 86400 ))
  if [ "$alter" -gt 30 ] && { [ "${tag:8:2}" != "01" ] || [ "$alter" -gt 730 ]; }; then rm -rf -- "$d"; fi
done
df -h /srv/backup | tail -1

if [ "$(cat /var/lib/domus/rolle 2>/dev/null)" != "aktiv" ]; then
  for d in pihole homeassistant energie umami proxy energy academy biber-solar biber-solar-test training unifi pihole-zweit; do
    [ -f "$DOMUS/$d/compose.yml" ] || continue
    (cd "$DOMUS/$d" && docker compose pull -q --ignore-buildable 2>&1 | tail -2)
  done
  (cd "$DOMUS/academy" && docker compose build -q 2>&1 | tail -2)
  docker image prune -f >/dev/null
fi
echo "Sicherung $HEUTE fertig"
