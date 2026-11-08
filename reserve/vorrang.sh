#!/usr/bin/env bash
# Vorrang-Prüfung auf dem Hauptrechner (NUC): Es darf immer nur EIN Gerät die Dienste betreiben (Home Assistant,
# homi mit der Fronius-Steuerung, Pi-hole-DHCP, Datenbanken). Hat die Reserve übernommen (/var/lib/domus/rolle =
# aktiv), bleibt der Hauptrechner still, bis Patrick mit reserve/zurueck-zum-nuc.sh zurückwechselt.
#
#   vorrang.sh boot           – domus-vorrang.service vor Docker: Reserve aktiv → Adresse VIP abgeben und mit Code 1
#                               enden, damit Docker (und damit kein Container) startet. Reserve nicht erreichbar
#                               oder nicht aktiv → Code 0, der Rechner startet normal.
#   vorrang.sh pruefen        – domus-vorrang-pruefen.timer alle 30 s: hält dieser Rechner noch die VIP, obwohl die
#                               Reserve aktiv ist (z. B. Netz war weg, Rechner lief weiter) → zurücktreten.
#   vorrang.sh zuruecktreten  – alle Container und die Timer von claude stoppen, Adresse VIP abgeben, Mail.
set -uo pipefail
. "$(dirname "$(readlink -f "$0")")/rollen.conf"
RESERVE=claude@$RESERVE_IP
VIPN="$VIP/24"

rolle_reserve() {
  local ssh=(ssh -i /home/claude/.ssh/domus_reserve -o BatchMode=yes -o ConnectTimeout=5
             -o UserKnownHostsFile=/home/claude/.ssh/known_hosts "$RESERVE" 'cat /var/lib/domus/rolle 2>/dev/null')
  if [ "$(id -u)" -eq 0 ]; then runuser -u claude -- "${ssh[@]}"; else "${ssh[@]}"; fi
}
melden() {
  printf '%s\n' "$2" | runuser -u claude -- mail -s "$1" root 2>/dev/null || printf '%s\n' "$2" | mail -s "$1" root || true
}
als_root() { if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi; }
als_claude() { if [ "$(id -u)" -eq 0 ]; then runuser -u claude -- env XDG_RUNTIME_DIR=/run/user/1000 "$@"; else "$@"; fi; }
timer_stoppen() {   # alle Timer/Pfade von claude anhalten (nicht «disable» – kommen nach Neustart/Rückwechsel wieder)
  als_claude systemctl --user list-units --type=timer,path --state=active --no-legend --plain 2>/dev/null \
    | awk '{print $1}' | grep -vE '^(domus-reserve-abgleich\.timer|launchpadlib)' \
    | while read -r u; do als_claude systemctl --user stop "$u" 2>/dev/null || true; done
}

case "${1:-}" in
  boot)
    rolle=""
    for _ in 1 2 3 4; do                       # Netz braucht nach dem Start ein paar Sekunden
      rolle=$(rolle_reserve) && break
      sleep 5
    done
    if [ "$rolle" = "aktiv" ]; then
      ip addr del "$VIPN" dev "$DEV" 2>/dev/null || true
      melden "domus: NUC gestartet, Reserve-PC hat aber übernommen" "Der NUC ist wieder da, der Reserve-PC betreibt die Dienste aber weiter (seit seiner Übernahme).
Damit nie zwei Geräte gleichzeitig schreiben, bleibt Docker auf dem NUC aus und die Adresse $VIP bei der Reserve.

Zurückwechseln, wenn du willst: auf dem NUC  ~/domus/reserve/zurueck-zum-nuc.sh
(oder sag es Claude)."
      echo "Reserve ist aktiv – Docker bleibt auf dem NUC aus"
      exit 1
    fi
    exit 0
    ;;
  pruefen)
    if [ "$(rolle_reserve 2>/dev/null)" = "aktiv" ]; then
      if ip -4 addr show "$DEV" | grep -q "$VIP/"; then
        "$0" zuruecktreten
      elif [ ! -e /run/domus-timer-gestoppt ]; then   # nach einem Neustart: Timer einmal anhalten
        timer_stoppen; als_root touch /run/domus-timer-gestoppt
      fi
    fi
    ;;
  zuruecktreten)
    als_root ip addr del "$VIPN" dev "$DEV" 2>/dev/null || true
    ids=$(als_root docker ps -q 2>/dev/null)
    [ -n "$ids" ] && als_root docker stop -t 60 $ids >/dev/null
    timer_stoppen; als_root touch /run/domus-timer-gestoppt
    melden "domus: NUC tritt zurück, Reserve-PC hat übernommen" "Der NUC war offenbar eine Weile nicht erreichbar, der Reserve-PC hat die Dienste übernommen.
Jetzt ist der NUC wieder da. Er hat seine Container und Timer gestoppt und die Adresse $VIP abgegeben, damit nicht
zwei Geräte gleichzeitig schreiben. Die Reserve läuft weiter.

Zurückwechseln, wenn du willst: auf dem NUC  ~/domus/reserve/zurueck-zum-nuc.sh"
    ;;
  *) echo "Aufruf: $0 boot|pruefen|zuruecktreten" >&2; exit 2 ;;
esac
