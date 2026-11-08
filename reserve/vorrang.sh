#!/usr/bin/env bash
# Vorrang-Prüfung auf dem NUC: Es darf immer nur EIN Gerät die Dienste betreiben (Home Assistant, homi mit der
# Fronius-Steuerung, Pi-hole-DHCP, Datenbanken). Hat der Reserve-PC übernommen (/var/lib/domus/rolle = aktiv),
# bleibt der NUC still, bis Patrick mit reserve/zurueck-zum-nuc.sh zurückwechselt.
#
#   vorrang.sh boot           – von domus-vorrang.service vor Docker: Reserve aktiv → Adresse .23 abgeben und mit
#                               Code 1 enden, damit Docker (und damit kein Container) startet. Reserve nicht
#                               erreichbar oder nicht aktiv → Code 0, der NUC startet normal.
#   vorrang.sh zuruecktreten  – im Betrieb (aus abgleich.sh): alle Container stoppen, Adresse .23 abgeben.
set -uo pipefail
RESERVE=claude@192.168.1.24
VIP=192.168.1.23/24
DEV=eno1

rolle_reserve() {
  local ssh=(ssh -i /home/claude/.ssh/domus_reserve -o BatchMode=yes -o ConnectTimeout=5
             -o UserKnownHostsFile=/home/claude/.ssh/known_hosts "$RESERVE" 'cat /var/lib/domus/rolle 2>/dev/null')
  if [ "$(id -u)" -eq 0 ]; then runuser -u claude -- "${ssh[@]}"; else "${ssh[@]}"; fi
}
melden() {
  printf '%s\n' "$2" | runuser -u claude -- mail -s "$1" root 2>/dev/null || printf '%s\n' "$2" | mail -s "$1" root || true
}
als_root() { if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi; }

case "${1:-}" in
  boot)
    rolle=""
    for _ in 1 2 3 4; do                       # Netz braucht nach dem Start ein paar Sekunden
      rolle=$(rolle_reserve) && break
      sleep 5
    done
    if [ "$rolle" = "aktiv" ]; then
      ip addr del "$VIP" dev "$DEV" 2>/dev/null || true
      melden "domus: NUC gestartet, Reserve-PC hat aber übernommen" "Der NUC ist wieder da, der Reserve-PC betreibt die Dienste aber weiter (seit seiner Übernahme).
Damit nie zwei Geräte gleichzeitig schreiben, bleibt Docker auf dem NUC aus und die Adresse 192.168.1.23 bei der Reserve.

Zurückwechseln, wenn du willst: auf dem NUC  ~/domus/reserve/zurueck-zum-nuc.sh
(oder sag es Claude)."
      echo "Reserve ist aktiv – Docker bleibt auf dem NUC aus"
      exit 1
    fi
    exit 0
    ;;
  zuruecktreten)
    ids=$(als_root docker ps -q)
    [ -n "$ids" ] && als_root docker stop -t 60 $ids >/dev/null
    als_root ip addr del "$VIP" dev "$DEV" 2>/dev/null || true
    melden "domus: NUC tritt zurück, Reserve-PC hat übernommen" "Der NUC war offenbar eine Weile nicht erreichbar, der Reserve-PC hat die Dienste übernommen.
Jetzt ist der NUC wieder da. Er hat seine Container gestoppt und die Adresse 192.168.1.23 abgegeben, damit nicht
zwei Geräte gleichzeitig schreiben. Die Reserve läuft weiter.

Zurückwechseln, wenn du willst: auf dem NUC  ~/domus/reserve/zurueck-zum-nuc.sh"
    ;;
  *) echo "Aufruf: $0 boot|zuruecktreten" >&2; exit 2 ;;
esac
