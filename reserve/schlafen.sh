#!/usr/bin/env bash
# Reserve-PC: schaltet sich selbst aus – ausser im Mittagsfenster, bei aktivem Knopf «Reserve Server On» in homi, nach einer
# Übernahme oder wenn der NUC sich nicht meldet (dann bleibt sie an und kann übernehmen). Vor dem Ausschalten stellt sie den
# RTC-Wecker auf den nächsten Fensterbeginn (rtcwake). Läuft auf der Reserve jede Minute (domus-reserve-schlafen.timer).
set -uo pipefail
. "$(dirname "$(readlink -f "$0")")/rollen.conf"
log() { echo "$*"; logger -t domus-schlafen "$*"; }
[ "$(cat /var/lib/domus/rolle 2>/dev/null)" = reserve ] || exit 0          # nie als Hauptrechner oder nach einer Übernahme
min() { echo $(( 10#${1%%:*} * 60 + 10#${1##*:} )); }
jetzt=$(( 10#$(date +%H) * 60 + 10#$(date +%M) ))
[ "$jetzt" -ge "$(min "$FENSTER_VON")" ] && [ "$jetzt" -lt "$(min "$FENSTER_BIS")" ] && exit 0   # Mittagsfenster

# Nur ausschalten, wenn der NUC da ist: Er legt jede Minute (mittag.sh) den Knopf-Zustand in /var/lib/domus/nuc-knopf
M=/var/lib/domus/nuc-knopf
[ -f "$M" ] && [ $(( $(date +%s) - $(stat -c %Y "$M") )) -lt 180 ] || { log "NUC meldet sich nicht – bleibe an"; exit 0; }
[ "$(cat "$M")" = 0 ] || exit 0          # Knopf «Reserve Server On» aktiv

# Nicht mitten in Sicherung oder Abgleich
systemctl is-active --quiet domus-reserve-sicherung.service && exit 0
pgrep -x rsync >/dev/null && exit 0

wecker=$(date -d "today $FENSTER_VON" +%s)
[ "$jetzt" -ge "$(min "$FENSTER_VON")" ] && wecker=$(date -d "tomorrow $FENSTER_VON" +%s)
log "fahre herunter, Wecker auf $(date -d @"$wecker" '+%F %H:%M')"
rtcwake -m off -t "$wecker" || { log "Wecker nicht stellbar – fahre ohne Wecker herunter"; systemctl poweroff; }
