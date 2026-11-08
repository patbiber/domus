#!/usr/bin/env bash
# Wächter auf dem Reserve-PC (domus-waechter.service, als root): prüft alle 20 Sekunden, ob der NUC lebt.
# Übernahme erst, wenn ALLE Bedingungen 3 Minuten lang ununterbrochen gelten (ein Neustart des NUC dauert weniger):
#   - der NUC antwortet weder auf 192.168.1.20 noch auf 192.168.1.23 (Ping und TCP 443),
#   - der Router 192.168.1.1 ist erreichbar (sonst ist eher die Reserve selbst vom Netz getrennt),
#   - der letzte Abgleich ist höchstens 24 Stunden alt (sonst nur Mail, keine Übernahme mit uralten Daten).
# Zurückgewechselt wird nur von Hand (reserve/zurueck-zum-nuc.sh auf dem NUC).
# Nach einem Neustart der Reserve im Zustand «aktiv» holt sie sich die Adresse .23 zurück.
set -uo pipefail
. "$(dirname "$(readlink -f "$0")")/rollen.conf"
DOMUS=/home/claude/domus
ROLLE=/var/lib/domus/rolle
SCHWELLE=180
TAKT=20
mkdir -p /var/lib/domus
[ -f "$ROLLE" ] || echo reserve > "$ROLLE"

lebt() {   # $1 = Adresse; zwei Pings und TCP 443 – erst wenn alles ausbleibt, gilt sie als weg
  ping -c2 -W2 "$1" >/dev/null 2>&1 && { grund="Ping $1"; return 0; }
  timeout 3 bash -c "</dev/tcp/$1/443" 2>/dev/null && { grund="TCP $1:443"; return 0; }
  return 1
}
router_da() {   # der Router (Sunrise) verschluckt einzelne Pings – darum mehrere Wege
  ping -c3 -W2 "$ROUTER_IP" >/dev/null 2>&1 && return 0
  timeout 3 bash -c "</dev/tcp/$ROUTER_IP/80" 2>/dev/null && return 0
  return 1
}
melden() { printf '%s\n' "$2" | runuser -u claude -- mail -s "$1" root 2>/dev/null || true; }

weg_seit=0
gemeldet_alt=0
grund=""
while true; do
  if [ "$(cat "$ROLLE")" = "aktiv" ]; then
    ip -4 addr show "$DEV" | grep -q "$VIP/" || { ip addr add "$VIP/24" dev "$DEV"; arping -q -U -c 3 -I "$DEV" "$VIP" || true; }
    sleep 60; continue
  fi
  if lebt "$HAUPT_IP" || lebt "$VIP"; then
    [ "$weg_seit" -ne 0 ] && echo "NUC wieder erreichbar ($grund)"
    weg_seit=0; gemeldet_alt=0
  elif ! router_da; then
    echo "Router nicht erreichbar – Reserve selbst vom Netz? Zählung angehalten"
  else
    [ "$weg_seit" -eq 0 ] && { weg_seit=$(date +%s); echo "NUC antwortet nicht – beobachte"; }
    dauer=$(( $(date +%s) - weg_seit ))
    if [ "$dauer" -ge "$SCHWELLE" ]; then
      zeit=$(cat /var/lib/domus/abgleich-zeit 2>/dev/null || echo "")
      alter=$(( $(date +%s) - $(date -d "${zeit:-1970-01-01}" +%s 2>/dev/null || echo 0) ))
      if [ "$alter" -le 86400 ]; then
        echo "NUC seit ${dauer}s weg – Übernahme"
        "$DOMUS/reserve/uebernehmen.sh" || echo "Übernahme mit Fehlern (siehe Protokoll)"
      elif [ "$gemeldet_alt" -eq 0 ]; then
        melden "domus: NUC weg, Reserve übernimmt NICHT" "Der NUC antwortet seit ${dauer} Sekunden nicht. Der letzte Abgleich ist aber älter als 24 Stunden (${zeit:-nie}),
darum übernimmt der Reserve-PC nicht automatisch. Von Hand übernehmen: auf der Reserve  sudo $DOMUS/reserve/uebernehmen.sh"
        gemeldet_alt=1
      fi
    fi
  fi
  sleep "$TAKT"
done
