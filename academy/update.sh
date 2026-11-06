#!/usr/bin/env bash
# Academy neu bauen und starten (nach Änderungen in ./repo). Prüft danach /healthz.
set -euo pipefail
cd "$(dirname "$0")"
docker compose build --pull -q
docker compose up -d
for i in $(seq 1 20); do
  docker exec nginx wget -q -O /dev/null http://academy:8000/healthz 2>/dev/null && { echo "academy läuft"; exit 0; }
  sleep 2
done
echo "academy antwortet nicht" >&2; docker compose logs --tail 30; exit 1
