#!/usr/bin/env bash
# Mail nach einem Neustart des NUC: läuft alles wieder? (systemd-User-Dienst domus-boot-report)
set -uo pipefail
sleep "${WARTEN:-180}"                       # Container und Dienste hochkommen lassen

ok() { [ "$1" = "$2" ] && echo "ok" || echo "FEHLER ($1)"; }
ha=$(curl -s -o /dev/null -m 10 -w '%{http_code}' http://127.0.0.1:8123/)
homi=$(curl -s -m 10 http://127.0.0.1:8099/api/status | grep -o '"fronius_ok": *[a-z]*' | grep -o '[a-z]*$')
web=$(curl -s -o /dev/null -m 10 -w '%{http_code}' --resolve home.biber.solar:443:127.0.0.1 https://home.biber.solar/)
fehler=0
for x in "$(ok "$ha" 200)" "$(ok "${homi:-?}" true)" "$(ok "$web" 401)"; do [ "$x" = ok ] || fehler=1; done

{
  echo "Der NUC ist neu gestartet."
  echo
  echo "Zeit:     $(date '+%d.%m.%Y %H:%M')"
  echo "Kernel:   $(uname -r)"
  echo "System:   $(lsb_release -ds)"
  echo "Grund:    $(journalctl -b -1 -n 200 --no-pager 2>/dev/null | grep -m1 -oE 'domus: [^\"]*|unattended-upgrade[^\"]*' || echo 'unbekannt (z. B. Stromausfall oder manuell)')"
  echo
  echo "Home Assistant (8123):    $(ok "$ha" 200)"
  echo "homi / Fronius:           $(ok "${homi:-?}" true)"
  echo "home.biber.solar (nginx): $(ok "$web" 401)   (401 = Passwortschutz aktiv, korrekt)"
  echo
  echo "Container:"
  docker ps --format '  {{.Names}}: {{.Status}}'
} | mail -s "domus: Neustart $( [ $fehler = 0 ] && echo 'ok' || echo 'mit FEHLERN')" root
