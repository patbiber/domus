#!/usr/bin/env bash
# Holt den neuesten Stand von github.com/patbiber/biber-solar nach ./site (nginx liefert ihn sofort aus).
set -euo pipefail
cd "$(dirname "$0")"
REPO=https://github.com/patbiber/biber-solar.git
if [ ! -d site/.git ]; then
  git clone -q --depth 1 "$REPO" site
  echo "geklont: $(git -C site log -1 --format='%h %s')"
  exit 0
fi
vorher=$(git -C site rev-parse HEAD)
git -C site fetch -q --depth 1 origin
git -C site reset -q --hard origin/HEAD
nachher=$(git -C site rev-parse HEAD)
[ "$vorher" != "$nachher" ] && echo "aktualisiert: $(git -C site log -1 --format='%h %s')" || true
