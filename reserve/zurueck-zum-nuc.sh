#!/usr/bin/env bash
# Zurückwechseln von der Reserve auf den NUC (von Hand, auf dem NUC als claude: ~/domus/reserve/zurueck-zum-nuc.sh).
# 1. Sicherung des alten NUC-Stands nach backups/vor-rueckwechsel-<Zeit>.tar.gz
# 2. Reserve: Datenbank-Kopien ziehen, Timer und Container stoppen
# 3. Daten Reserve → NUC kopieren (die Reserve hat die neueren)
# 4. Reserve gibt 192.168.1.23 ab, wird wieder «reserve», zweiter Pi-hole startet
# 5. NUC übernimmt .23, Docker und Container starten, Umami-Datenbank aus dem Auszug
set -euo pipefail
DOMUS="$HOME/domus"
RESERVE=claude@192.168.1.24
SSHO=(-i "$HOME/.ssh/domus_reserve" -o BatchMode=yes -o ConnectTimeout=10 -o UserKnownHostsFile="$HOME/.ssh/known_hosts")
R() { ssh "${SSHO[@]}" "$RESERVE" "$@"; }
ZEIT=$(date +%F-%H%M)

[ "$(R 'cat /var/lib/domus/rolle 2>/dev/null')" = "aktiv" ] || { echo "Die Reserve ist nicht aktiv – nichts zu tun."; exit 0; }
echo "1/5 Sicherung des alten NUC-Stands"
sudo tar czf "$DOMUS/backups/vor-rueckwechsel-$ZEIT.tar.gz" --exclude='./backups' --exclude='./umami/data/postgres' -C "$DOMUS" . || [ $? -eq 1 ]

echo "2/5 Reserve anhalten"
R 'set -e
   d=/home/claude/domus/backups/reserve-stand; mkdir -p $d
   docker exec umami-db pg_dump -U umami --clean --if-exists umami | gzip > $d/umami.sql.gz || echo "Umami-Auszug fehlgeschlagen"
   for u in $(systemctl --user list-units --type=timer,path --state=active --no-legend --plain | awk "{print \$1}"); do
     systemctl --user disable --now "$u" >/dev/null 2>&1 || true
   done
   docker ps --format "{{.Names}}" | grep -vx pihole-zweit | xargs -r docker stop -t 60 >/dev/null'

echo "3/5 Daten Reserve → NUC"
sudo rsync -aHAX --numeric-ids --delete -e "ssh ${SSHO[*]}" --rsync-path="sudo rsync" \
  --exclude='/backups/' --exclude='/.git/' --exclude='*.tmp' --exclude='/umami/data/postgres/' --exclude='/pihole-zweit/' \
  --exclude='/reserve/' \
  "$RESERVE:/home/claude/domus/" "$DOMUS/"
sudo rsync -a -e "ssh ${SSHO[*]}" --rsync-path="sudo rsync" "$RESERVE:/home/claude/domus/backups/reserve-stand/umami.sql.gz" "$DOMUS/backups/reserve-stand/umami.sql.gz" || true

echo "4/5 Reserve gibt 192.168.1.23 ab"
R 'sudo ip addr del 192.168.1.23/24 dev eno1 2>/dev/null || true
   echo reserve | sudo tee /var/lib/domus/rolle >/dev/null; sudo rm -f /var/lib/domus/datenbanken-eingesetzt
   cd /home/claude/domus/pihole-zweit && docker compose up -d >/dev/null'

echo "5/5 NUC übernimmt"
sudo ip addr add 192.168.1.23/24 dev eno1 2>/dev/null || true
sudo arping -q -U -c 3 -I eno1 192.168.1.23 2>/dev/null || true
sudo systemctl reset-failed domus-vorrang.service docker.service 2>/dev/null || true
sudo systemctl start domus-vorrang.service docker.service
for d in pihole homeassistant energie energy academy biber-solar biber-solar-test training unifi umami proxy; do
  [ -f "$DOMUS/$d/compose.yml" ] && (cd "$DOMUS/$d" && docker compose up -d) >/dev/null
done
if [ -s "$DOMUS/backups/reserve-stand/umami.sql.gz" ]; then
  for _ in $(seq 1 30); do docker exec umami-db pg_isready -U umami -d umami >/dev/null 2>&1 && break; sleep 2; done
  gunzip -c "$DOMUS/backups/reserve-stand/umami.sql.gz" | docker exec -i umami-db psql -q -U umami -d umami >/dev/null 2>&1 || echo "Hinweis: Umami-Auszug nicht eingespielt"
fi
docker ps --format '{{.Names}}' | sort | tr '\n' ' '; echo
printf 'Der NUC betreibt wieder alle Dienste (%s). Der Reserve-PC ist wieder Reserve, der Abgleich läuft weiter.\nAlter NUC-Stand gesichert: backups/vor-rueckwechsel-%s.tar.gz\n' "$(date '+%d.%m.%Y %H:%M')" "$ZEIT" \
  | mail -s "domus: zurück auf dem NUC" root || true
echo "Fertig – der NUC ist wieder aktiv."
