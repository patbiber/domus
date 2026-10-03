#!/usr/bin/env bash
# Holt github.com/patbiber/solartech nach ./src und baut die MkDocs-Seite neu, wenn es einen neuen Commit gibt.
# Erst nach erfolgreichem Build wird ./ausgabe/aktuell umgeschaltet; ältere Builds werden entfernt.
set -euo pipefail
cd "$(dirname "$0")"
REPO=https://github.com/patbiber/solartech.git
BILD=squidfunk/mkdocs-material:9
if [ ! -d src/.git ]; then git clone -q --depth 1 "$REPO" src; else
  git -C src fetch -q --depth 1 origin HEAD && git -C src reset -q --hard FETCH_HEAD; fi
commit=$(git -C src rev-parse --short HEAD)
ziel=build-$commit
if [ "$(readlink ausgabe/aktuell 2>/dev/null)" = "$ziel" ] && [ "${1:-}" != "--neu" ]; then exit 0; fi
rm -rf "ausgabe/$ziel"
# docs/ verlinkt Bilder absolut auf den alten OVH-Pfad -> Klon zusätzlich dort einhängen, damit die Links stimmen
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/src:/docs:ro" -v "$PWD/src:/var/www/html/training.biber.solar/solartech:ro" \
  -v "$PWD/ausgabe:/out" "$BILD" \
  build --quiet --site-dir "/out/$ziel" 2>&1 | grep -vE '^\s*$|│|Warning from the Material' || true
[ -f "ausgabe/$ziel/index.html" ] || { echo "Build fehlgeschlagen ($commit)"; exit 1; }
ln -sfn "$ziel" ausgabe/aktuell.neu && mv -T ausgabe/aktuell.neu ausgabe/aktuell
find ausgabe -maxdepth 1 -name 'build-*' ! -name "$ziel" -exec rm -rf {} +
echo "veröffentlicht: $commit $(git -C src log -1 --format='%s')"
