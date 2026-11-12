#!/usr/bin/env bash
# Reserve-PC nur über Mittag (FENSTER_VON–FENSTER_BIS aus rollen.conf, heute 12:00–12:30) – oder dauernd, wenn der Knopf
# «Reserve Server On» in homi aktiv ist (energie/data/reserve.json: {"dauerhaft": true}).
# Aufgabenteilung:
#   - EIN schaltet: der RTC-Wecker der Reserve (stellt sie selbst beim Ausschalten, schlafen.sh) – unabhängig vom NUC.
#     Dieses Skript (NUC, jede Minute, User-Timer domus-reserve-mittag) weckt per Wake-on-LAN NUR für den Knopf in homi.
#     Hat der Wecker bis 3 Minuten nach Fensterbeginn nicht gewirkt, gibt es Protokollzeile und Mail (kein Wake-on-LAN).
#   - AUS schaltet sich die Reserve selbst (schlafen.sh), nur wenn der NUC da ist.
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

erreichbar=0; rolle=""
if ping -c1 -W2 "$RESERVE_IP" >/dev/null 2>&1; then
  erreichbar=1   # läuft (auch wenn SSH beim Hochfahren noch nicht antwortet)
  # Lebenszeichen des NUC samt Knopf-Zustand auf die Reserve legen (schlafen.sh schaltet sie nur bei frischer Meldung aus)
  rolle=$("${SSH[@]}" "echo $dauerhaft | sudo tee /var/lib/domus/nuc-knopf >/dev/null; cat /var/lib/domus/rolle 2>/dev/null || echo reserve" 2>/dev/null) || rolle=""
fi
vorher=$(cat "$ZUSTAND/erreichbar" 2>/dev/null || echo 0)
letzte=$(cat "$ZUSTAND/wecken" 2>/dev/null || echo 0)
aktion=""

wecken() {   # Magic Packet (Wake-on-LAN), höchstens alle 150 s
  [ $(( $(date +%s) - letzte )) -ge 150 ] || { aktion="startet …"; return; }
  python3 - "$RESERVE_MAC" <<'PY'
import socket, sys
mac = bytes.fromhex(sys.argv[1].replace(":", ""))
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
for ziel in ("255.255.255.255", "192.168.1.255"):
    s.sendto(b"\xff" * 6 + mac * 16, (ziel, 9))
PY
  date +%s > "$ZUSTAND/wecken"; aktion="$1"
}

if [ "$erreichbar" = 1 ] && [ "$rolle" = aktiv ]; then
  aktion="Reserve ist aktiv (hat übernommen)"; soll=an
elif [ "$soll" = an ]; then
  echo an > "$ZUSTAND/soll"
  if [ "$erreichbar" = 0 ]; then
    if [ "$dauerhaft" = 1 ]; then wecken "Knopf: Magic Packet (Wake-on-LAN) geschickt"
    elif [ "$jetzt" -ge $(( $(min "$FENSTER_VON") + 3 )) ]; then
      aktion="WECKER HAT NICHT GEWIRKT – Reserve ist im Mittagsfenster nicht gestartet"
      if [ "$(cat "$ZUSTAND/wecker-mail" 2>/dev/null)" != "$(date +%F)" ]; then   # einmal pro Tag eine Mail
        date +%F > "$ZUSTAND/wecker-mail"
        printf 'Die Reserve (%s) ist im Mittagsfenster %s–%s nicht vom Wecker gestartet worden.\nWake-on-LAN gilt nur für den Knopf «Reserve Server On» in homi (home.biber.solar).\nBIOS prüfen: «Wake Up on Alarm» täglich %s, oder den Knopf drücken.\n' "$RESERVE_IP" "$FENSTER_VON" "$FENSTER_BIS" "$FENSTER_VON" \
          | mail -s "domus: Reserve wurde vom Wecker nicht gestartet" root || true
      fi
    else aktion="wartet auf den Wecker"; fi
  elif [ "$vorher" = 0 ]; then
    systemctl --user start --no-block domus-reserve-abgleich.service 2>/dev/null || true   # gleich nach dem Start abgleichen
    if [ $(( $(date +%s) - letzte )) -lt 600 ]; then aktion="läuft – geweckt per Wake-on-LAN"; else aktion="läuft – vom Wecker gestartet"; fi
  fi
else   # soll aus: die Reserve schaltet sich selbst aus (schlafen.sh), solange der NUC da ist
  echo aus > "$ZUSTAND/soll"
  [ "$erreichbar" = 1 ] && aktion="läuft noch – fährt von selbst herunter"
fi
echo "$erreichbar" > "$ZUSTAND/erreichbar"
case "$aktion" in ""|"wartet auf den Wecker"|"startet …"|"läuft noch"*) ;; *) echo "$(date '+%F %T') $modus soll=$soll: $aktion";; esac

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
