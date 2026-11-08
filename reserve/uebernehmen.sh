#!/usr/bin/env bash
# Übernahme: der Reserve-PC betreibt ab jetzt alle Dienste des NUC (als root, vom Wächter oder von Hand:
# sudo ~/domus/reserve/uebernehmen.sh). Ablauf:
#   Rolle «aktiv» setzen (ab jetzt kopiert der NUC nichts mehr und tritt zurück, falls er wieder auftaucht),
#   zweiten Pi-hole stoppen, Adresse 192.168.1.23 übernehmen (der Router leitet 80/443 dorthin, alle Geräte
#   fragen dort DNS), Datenbanken aus den Kopien des letzten Abgleichs einsetzen, Container und Timer starten,
#   Mail an Patrick.
set -uo pipefail
[ "$(id -u)" -eq 0 ] || exec sudo "$0" "$@"
DOMUS=/home/claude/domus
STAND=$DOMUS/backups/reserve-stand
LOG=/var/lib/domus/uebernahme.log
mkdir -p /var/lib/domus
exec > >(tee -a "$LOG") 2>&1
echo "=== Übernahme $(date -Iseconds)"
[ "$(cat /var/lib/domus/rolle 2>/dev/null)" = "aktiv" ] && echo "(war schon aktiv – starte nur fehlende Dienste)"
echo aktiv > /var/lib/domus/rolle

als_claude() { runuser -u claude -- env XDG_RUNTIME_DIR=/run/user/1000 "$@"; }
fehler=()

# --- Netz
docker rm -f pihole-zweit >/dev/null 2>&1 || true
ip -4 addr show eno1 | grep -q '192.168.1.23/' || ip addr add 192.168.1.23/24 dev eno1
arping -q -U -c 3 -I eno1 192.168.1.23 || true

# --- Datenbanken aus dem letzten Abgleich (nur beim ersten Mal, danach gehören sie der Reserve)
if [ ! -f /var/lib/domus/datenbanken-eingesetzt ]; then
  [ -s "$STAND/home-assistant_v2.db" ] && { cp -a "$STAND/home-assistant_v2.db" "$DOMUS/homeassistant/config/home-assistant_v2.db"; rm -f "$DOMUS"/homeassistant/config/home-assistant_v2.db-{wal,shm}; }
  [ -s "$DOMUS/academy/data/snapshot/academy.db" ] && { cp -a "$DOMUS/academy/data/snapshot/academy.db" "$DOMUS/academy/data/academy.db"; rm -f "$DOMUS"/academy/data/academy.db-{wal,shm}; }
  touch /var/lib/domus/datenbanken-eingesetzt
  UMAMI_NEU=1
fi

# --- Container (Reihenfolge wie auf dem NUC: zuerst DNS/DHCP, dann Dienste, zuletzt nginx)
for d in pihole homeassistant energie umami proxy energy academy biber-solar biber-solar-test training unifi; do
  [ -f "$DOMUS/$d/compose.yml" ] || continue
  (cd "$DOMUS/$d" && docker compose up -d --remove-orphans) || fehler+=("$d")
done
# Zweiter Durchgang für Dienste, die beim ersten Mal scheitern (z. B. Netz proxy_default noch nicht da)
if [ ${#fehler[@]} -gt 0 ]; then
  nochmals=("${fehler[@]}"); fehler=()
  for d in "${nochmals[@]}"; do (cd "$DOMUS/$d" && docker compose up -d --remove-orphans) || fehler+=("$d"); done
  docker restart nginx >/dev/null 2>&1 || true
fi
docker restart nginx >/dev/null 2>&1 || true
# Anmeldung (home.biber.solar, Kundenseiten) läuft ausserhalb von Docker auf 172.18.0.1 (proxy_default)
systemctl daemon-reload; systemctl start homi-login || fehler+=("homi-login")
# Umami: frische Datenbank aus dem Auszug füllen
if [ "${UMAMI_NEU:-0}" = 1 ] && [ -s "$STAND/umami.sql.gz" ]; then
  for _ in $(seq 1 30); do docker exec umami-db pg_isready -U umami -d umami >/dev/null 2>&1 && break; sleep 2; done
  gunzip -c "$STAND/umami.sql.gz" | docker exec -i umami-db psql -q -U umami -d umami >/dev/null 2>&1 && docker restart umami >/dev/null \
    || fehler+=("umami-Datenbank")
fi

# --- Timer und Pfad-Units wie auf dem NUC (ohne GitHub-Upload, Claude-Sitzung und den Abgleich selbst)
loginctl enable-linger claude
als_claude systemctl --user daemon-reload
if [ -s "$STAND/units-aktiv.txt" ]; then
  grep -E '\.(timer|path)$' "$STAND/units-aktiv.txt" | grep -vE '^(github-abend|domus-reserve-abgleich|launchpadlib)' | while read -r u; do
    als_claude systemctl --user enable --now "$u" >/dev/null 2>&1 || echo "Timer $u nicht gestartet"
  done
fi

# --- Claude Code: dieselbe Unterhaltung wie auf dem NUC als Abzweigung (neue Sitzungs-ID), Remote Control «domus-reserve»
SITZUNG=$(sed -n 's/^Environment=SITZUNG=//p' /home/claude/.config/systemd/user/claude-sitzung.service 2>/dev/null)
if [ -x /home/claude/.local/bin/claude ] && ! runuser -u claude -- tmux has-session -t claude 2>/dev/null; then
  runuser -u claude -- env HOME=/home/claude PATH=/home/claude/.local/bin:/usr/local/bin:/usr/bin:/bin \
    tmux new-session -d -s claude -c /home/claude/domus \
    "claude --dangerously-skip-permissions ${SITZUNG:+--resume $SITZUNG --fork-session} --remote-control domus-reserve" \
    || fehler+=("Claude")
fi
laufend=$(docker ps --format '{{.Names}}' | sort | tr '\n' ' ')
echo "Laufende Container: $laufend"
[ ${#fehler[@]} -gt 0 ] && echo "Fehler bei: ${fehler[*]}"

printf '%s\n' "Der NUC hat mehr als 3 Minuten nicht geantwortet. Der Reserve-PC (192.168.1.24) hat um $(date '+%H:%M') übernommen
und hält jetzt die Adresse 192.168.1.23: Webseiten, Home Assistant, homi, Pi-hole (DNS + DHCP) und die Academy laufen dort.
Claude ist in der App als «domus-reserve» erreichbar.

Datenstand: letzter Abgleich $(cat /var/lib/domus/abgleich-zeit 2>/dev/null || echo unbekannt) (was danach auf dem NUC passiert ist, fehlt).
Laufende Container: $laufend
${fehler:+Probleme: ${fehler[*]}}

Wenn der NUC wieder läuft, bleibt er still (Docker aus), bis du zurückwechselst:
auf dem NUC  ~/domus/reserve/zurueck-zum-nuc.sh   (oder sag es Claude).
Protokoll: $LOG" | runuser -u claude -- mail -s "domus: Reserve-PC hat übernommen" root || true
echo "=== Übernahme fertig"
