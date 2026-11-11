#!/usr/bin/env bash
# Reserve-PC nur über Mittag (FENSTER_VON–FENSTER_BIS aus rollen.conf, heute 12:00–12:30) – oder dauernd, wenn der Knopf
# «Reserve Server On» in homi aktiv ist (energie/data/reserve.json: {"dauerhaft": true}).
# Läuft auf dem Hauptrechner jede Minute (User-Timer domus-reserve-mittag):
#   - Soll «an» und die Reserve ist aus: Magic Packet (Wake-on-LAN) schicken.
#   - Soll «aus» und die Reserve läuft: herunterfahren – aber NIE, wenn sie «aktiv» ist (hat übernommen), und erst, wenn
#     ihre Sicherung und der Abgleich fertig sind.
# Schreibt den Zustand für die Webseite nach energie/data/reserve-status.json und das Soll nach
# ~/.local/state/domus/reserve-abgleich/soll (der Abgleich lässt die ausgeschaltete Reserve dann in Ruhe).
set -uo pipefail
. "$(dirname "$(readlink -f "$0")")/rollen.conf"
DOMUS="$HOME/domus"
ZUSTAND="${XDG_STATE_HOME:-$HOME/.local/state}/domus/reserve-abgleich"
mkdir -p "$ZUSTAND"
KONFIG="$DOMUS/energie/data/reserve.json"
STATUS="$DOMUS/energie/data/reserve-status.json"
SSH=(ssh -i "$HOME/.ssh/domus_reserve" -o BatchMode=yes -o ConnectTimeout=8 -o UserKnownHostsFile="$HOME/.ssh/known_hosts" "claude@$RESERVE_IP")

# Nur auf dem Hauptrechner (nie auf der Reserve, auch nicht nach einer Übernahme)
ip -4 addr show "$DEV" | grep -q "inet $HAUPT_IP/" || exit 0
[ "$(cat /var/lib/domus/rolle 2>/dev/null || echo haupt)" = haupt ] || exit 0

min() { echo $(( 10#${1%%:*} * 60 + 10#${1##*:} )); }
jetzt=$(( 10#$(date +%H) * 60 + 10#$(date +%M) ))
dauerhaft=$(python3 -c "import json,sys; print('1' if json.load(open(sys.argv[1])).get('dauerhaft') is True else '0')" "$KONFIG" 2>/dev/null || echo 0)
im_fenster=0; [ "$jetzt" -ge "$(min "$FENSTER_VON")" ] && [ "$jetzt" -lt "$(min "$FENSTER_BIS")" ] && im_fenster=1
if [ "$dauerhaft" = 1 ]; then modus=dauerhaft; soll=an
elif [ "$im_fenster" = 1 ]; then modus=mittag; soll=an
else modus=mittag; soll=aus; fi

erreichbar=0; rolle=""; beschaeftigt=0
if ping -c1 -W2 "$RESERVE_IP" >/dev/null 2>&1; then
  rolle=$("${SSH[@]}" 'cat /var/lib/domus/rolle 2>/dev/null || echo reserve' 2>/dev/null) && erreichbar=1
fi
vorher=$(cat "$ZUSTAND/erreichbar" 2>/dev/null || echo 0)
aktion=""

if [ "$erreichbar" = 1 ] && [ "$rolle" = aktiv ]; then
  aktion="Reserve ist aktiv (hat übernommen) – wird nie heruntergefahren"; soll=an
elif [ "$soll" = an ]; then
  echo an > "$ZUSTAND/soll"
  if [ "$erreichbar" = 0 ]; then
    letzte=$(cat "$ZUSTAND/wecken" 2>/dev/null || echo 0)
    if [ $(( $(date +%s) - letzte )) -ge 150 ]; then
      python3 - "$RESERVE_MAC" <<'PY'
import socket, sys
mac = bytes.fromhex(sys.argv[1].replace(":", ""))
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
for ziel in ("255.255.255.255", "192.168.1.255"):
    s.sendto(b"\xff" * 6 + mac * 16, (ziel, 9))
PY
      date +%s > "$ZUSTAND/wecken"; aktion="Magic Packet geschickt"
    else aktion="startet …"; fi
  elif [ "$vorher" = 0 ]; then
    systemctl --user start --no-block domus-reserve-abgleich.service 2>/dev/null || true   # gleich nach dem Start abgleichen
    aktion="läuft"
  fi
else   # soll aus
  echo aus > "$ZUSTAND/soll"
  if [ "$erreichbar" = 1 ]; then
    if systemctl --user is-active --quiet domus-reserve-abgleich.service \
       || "${SSH[@]}" 'systemctl is-active --quiet domus-reserve-sicherung.service' 2>/dev/null; then
      aktion="Abgleich oder Sicherung läuft noch – Herunterfahren wartet"
    else
      # Wecker (RTC) auf den nächsten Fensterbeginn minus 2 min stellen und ausschalten; klappt der Wecker im BIOS nicht,
      # bleibt Wake-on-LAN (Knopf in homi) – und wer nur ausschalten will, bekommt einfach poweroff
      von=$(min "$FENSTER_VON"); wecker=$(date -d "today $FENSTER_VON" +%s)
      [ "$jetzt" -ge "$von" ] && wecker=$(date -d "tomorrow $FENSTER_VON" +%s)
      "${SSH[@]}" "sudo rtcwake -m off -t $(( wecker - 120 )) || sudo systemctl poweroff" >/dev/null 2>&1 || true
      aktion="heruntergefahren (Wecker ${FENSTER_VON} gestellt)"; erreichbar=0
    fi
  fi
fi
echo "$erreichbar" > "$ZUSTAND/erreichbar"
[ -n "$aktion" ] && [ "$aktion" != läuft ] && echo "$(date '+%F %T') $modus soll=$soll: $aktion"

python3 - "$STATUS" "$modus" "$soll" "$erreichbar" "$rolle" "$FENSTER_VON" "$FENSTER_BIS" "$aktion" <<'PY'
import json, os, sys, time
datei, modus, soll, erreichbar, rolle, von, bis, aktion = sys.argv[1:9]
d = {"zeit": time.strftime("%Y-%m-%dT%H:%M:%S"), "modus": modus, "soll": soll, "an": erreichbar == "1",
     "rolle": rolle or None, "fenster": f"{von}–{bis}", "hinweis": aktion}
tmp = datei + ".neu"
with open(tmp, "w") as f:
    json.dump(d, f, ensure_ascii=False)
os.chmod(tmp, 0o644); os.replace(tmp, datei)
PY
