#!/usr/bin/env bash
# Wöchentlich: gibt es neuere Docker-Images für die laufenden Container? Nur melden, nichts aktualisieren.
# Home-Assistant-Updates nur mit Backup und nach Rückfrage (CLAUDE.md).
set -uo pipefail
neu=""
for c in $(docker ps --format '{{.Names}}'); do
  img=$(docker inspect -f '{{.Config.Image}}' "$c")
  lokal=$(docker image inspect "$img" --format '{{index .RepoDigests 0}}' 2>/dev/null | cut -d@ -f2)
  remote=$(timeout 60 docker buildx imagetools inspect "$img" --format '{{json .Manifest.Digest}}' 2>/dev/null | tr -d '"')
  [ -z "$remote" ] && { neu+="  $c ($img): Registry nicht erreichbar"$'\n'; continue; }
  if [ "$lokal" != "$remote" ]; then
    v_alt=$(docker image inspect "$img" --format '{{index .Config.Labels "org.opencontainers.image.version"}}' 2>/dev/null)
    v_neu=$(timeout 60 docker buildx imagetools inspect "$img" --format '{{ with index .Image "linux/amd64" }}{{ index .Config.Labels "org.opencontainers.image.version" }}{{ end }}' 2>/dev/null)
    neu+="  $c ($img): ${v_alt:-?} -> ${v_neu:-neue Version}"$'\n'
  fi
done
[ -z "$neu" ] && exit 0
printf 'Für diese Container gibt es neuere Images:\n\n%s\nNichts wurde automatisch aktualisiert.\nSag Claude in der domus-Session, welche aktualisiert werden sollen\n(Home Assistant immer mit Backup vorher).\n' "$neu" \
  | mail -s "domus: neue Docker-Images verfügbar" root
