#!/usr/bin/env bash
# Wöchentliches Update der Docker-Images mit Gesundheitsprüfung und automatischem Zurückrollen.
#  1. neue Images holen (docker compose pull), nur geänderte Dienste werden angefasst
#  2. vor einem Home-Assistant-Update: Backup (homeassistant/backup.sh, Regel aus CLAUDE.md)
#  3. altes Image als Rückfallversion merken, Container neu erstellen
#  4. prüfen, ob die Anwendungen laufen; sonst altes Image zurück und Alarm-Mail
#  5. Bericht per Mail an root, nur wenn sich etwas geändert hat oder etwas schiefging
# Aufruf: system/image-update.sh   (Timer domus-image-update, Montag 04:15)
set -uo pipefail
cd "$(dirname "$0")/.."
PROJEKTE=(homeassistant proxy energie biber-solar biber-solar-test)
bericht=""
fehler=0
geaendert=0

log() { bericht+="$*"$'\n'; echo "$*"; }

version() {   # Version aus Label oder *_VERSION-Umgebungsvariable des Images
  local v
  v=$(docker image inspect "$1" --format '{{index .Config.Labels "org.opencontainers.image.version"}}' 2>/dev/null)
  [ -z "$v" ] && v=$(docker image inspect "$1" --format '{{range .Config.Env}}{{println .}}{{end}}' 2>/dev/null \
                     | grep -m1 -E '^(NGINX|PYTHON|CERTBOT)?_?VERSION=' | cut -d= -f2)
  echo "${v:-${1:7:12}}"
}

gesund() {    # gesund <projekt>: 0 = läuft, sonst Fehler; wartet bis 5 Minuten
  local p=$1 i code
  for i in $(seq 1 "${PRUEF_VERSUCHE:-30}"); do
    case $p in
      homeassistant) code=$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://127.0.0.1:8123/) ; [ "$code" = 200 ] && return 0 ;;
      energie)       curl -s -m 5 http://127.0.0.1:8099/api/status | grep -q '"zeit"' && return 0 ;;
      biber-solar|biber-solar-test)   # intern über das Proxy-Netz prüfen (geht auch vor der DNS-Umstellung)
                     docker exec nginx wget -q -O /dev/null "http://$(cd "$p" && docker compose ps --format '{{.Name}}' | head -1)/" 2>/dev/null && return 0 ;;
      proxy)         [ "$(curl -s -o /dev/null -m 5 -w '%{http_code}' --resolve domus.biber.solar:443:127.0.0.1 https://domus.biber.solar/)" = 200 ] \
                       && [ "$(curl -s -o /dev/null -m 5 -w '%{http_code}' --resolve home.biber.solar:443:127.0.0.1 https://home.biber.solar/)" = 401 ] \
                       && [ "$(docker inspect -f '{{.State.Running}}' certbot)" = true ] && return 0 ;;
    esac
    sleep 10
  done
  return 1
}

for p in "${PROJEKTE[@]}"; do
  if [ "${NO_PULL:-0}" != 1 ]; then   # NO_PULL=1 nur für Tests
    ( cd "$p" && docker compose pull -q ) || { log "$p: Pull fehlgeschlagen (Registry nicht erreichbar?)"; fehler=1; continue; }
  fi
  # Dienste, deren laufendes Image nicht mehr dem (neu geholten) Image entspricht
  declare -A alt=()
  while read -r svc ctr; do
    img=$(docker inspect -f '{{.Config.Image}}' "$ctr" 2>/dev/null)   # konfigurierter Name, z. B. nginx:stable-alpine
    lauf=$(docker inspect -f '{{.Image}}' "$ctr" 2>/dev/null)         # ID des laufenden Images
    neu=$(docker image inspect -f '{{.Id}}' "$img" 2>/dev/null)        # ID nach dem Pull
    [ -n "$lauf" ] && [ -n "$neu" ] && [ "$lauf" != "$neu" ] && alt[$svc]="$lauf $img $ctr"
  done < <(cd "$p" && docker compose ps --format '{{.Service}} {{.Name}}')
  [ ${#alt[@]} -eq 0 ] && continue
  geaendert=1

  if [ "$p" = homeassistant ]; then
    homeassistant/backup.sh >/dev/null 2>&1 && log "homeassistant: Backup erstellt" \
      || { log "homeassistant: BACKUP FEHLGESCHLAGEN, Update übersprungen"; fehler=1; continue; }
  fi
  for svc in "${!alt[@]}"; do
    read -r id img ctr <<<"${alt[$svc]}"
    docker tag "$id" "domus-rollback/$svc:letzte"
    va=$(version "$id"); vn=$(version "$img")
    [ "$va" = "$vn" ] && vn="$vn (neu gebaut, Sicherheits-/Basis-Updates)"
    log "$p/$svc: $va -> $vn"
  done

  # gezielt nur die geänderten Dienste neu erstellen (compose erkennt ein neu geholtes Image nicht immer)
  ( cd "$p" && docker compose up -d --pull never --force-recreate --no-deps "${!alt[@]}" >/dev/null 2>&1 )
  if gesund "$p"; then
    log "$p: läuft (Prüfung ok)"
  else
    log "$p: PRÜFUNG FEHLGESCHLAGEN -> zurück zur vorherigen Version"
    for svc in "${!alt[@]}"; do
      read -r id img ctr <<<"${alt[$svc]}"
      docker tag "$id" "$img"
    done
    ( cd "$p" && docker compose up -d --pull never --force-recreate --no-deps "${!alt[@]}" >/dev/null 2>&1 )
    if gesund "$p"; then log "$p: alte Version läuft wieder"; else log "$p: AUCH ALTE VERSION LÄUFT NICHT – bitte sofort prüfen"; fi
    fehler=1
  fi
  unset alt
done

docker image prune -f >/dev/null 2>&1   # alte, unbenutzte Images (Rückfallversionen bleiben getaggt)

if [ $geaendert = 1 ] || [ $fehler = 1 ]; then
  {
    echo "Docker-Image-Update vom $(date '+%d.%m.%Y %H:%M')"
    echo
    printf '%s' "$bericht"
    echo
    echo "Container:"
    docker ps --format '  {{.Names}}: {{.Status}}'
    [ $fehler = 1 ] && printf '\nEs gab Probleme. Sag Claude in der domus-Session Bescheid.\n'
  } | mail -s "domus: Docker-Update $( [ $fehler = 0 ] && echo 'ok' || echo 'mit FEHLERN')" root
fi
exit $fehler
