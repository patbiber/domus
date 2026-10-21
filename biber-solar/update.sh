#!/usr/bin/env bash
# Veröffentlicht den freigegebenen Stand nach ./site; nginx liefert ihn sofort aus.
# Quelle seit 21.10.2026: lokaler Branch «freigegeben» in ../biber-solar-test/site (das, wofür Patrick «ok» gesagt hat).
# GitHub bekommt denselben Stand erst am Abend (system/github-abend.py, 20–22 Uhr). Fehlt die lokale Quelle, gilt GitHub.
set -euo pipefail
cd "$(dirname "$0")"
LOKAL="$(cd .. && pwd)/biber-solar-test/site"
if git -C "$LOKAL" rev-parse -q --verify refs/heads/freigegeben >/dev/null 2>&1; then
  REPO="file://$LOKAL"; BRANCH=freigegeben
else
  REPO=https://github.com/patbiber/biber-solar.git; BRANCH=main
fi
if [ ! -d site/.git ]; then
  git clone -q --depth 1 --branch "$BRANCH" "$REPO" site
  echo "geklont: $(git -C site log -1 --format='%h %s')"
  exit 0
fi
vorher=$(git -C site rev-parse HEAD)
git -C site fetch -q --depth 1 "$REPO" "$BRANCH"
git -C site reset -q --hard FETCH_HEAD
nachher=$(git -C site rev-parse HEAD)
[ "$vorher" != "$nachher" ] && echo "veröffentlicht: $(git -C site log -1 --format='%h %s')" || true
