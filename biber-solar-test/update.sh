#!/usr/bin/env bash
# Vorschau test.biber.solar = Arbeitskopie ./site von github.com/patbiber/biber-solar.
# Holt neue Commits von GitHub nur, wenn hier nichts in Arbeit ist (keine lokalen Änderungen/Commits),
# damit eine laufende Änderung nie überschrieben wird. Push: git -C site push (Deploy-Key, Host github-biber-solar).
set -euo pipefail
cd "$(dirname "$0")"
if [ ! -d site/.git ]; then
  git clone -q https://github.com/patbiber/biber-solar.git site
  git -C site remote set-url --push origin git@github-biber-solar:patbiber/biber-solar.git
  echo "geklont: $(git -C site log -1 --format='%h %s')"
  exit 0
fi
git -C site fetch -q origin
if [ -n "$(git -C site status --porcelain)" ] || [ "$(git -C site rev-list --count origin/main..HEAD)" != 0 ]; then
  echo "Arbeitskopie hat Änderungen in Arbeit – nicht aktualisiert"
  exit 0
fi
vorher=$(git -C site rev-parse HEAD)
git -C site merge -q --ff-only origin/main
nachher=$(git -C site rev-parse HEAD)
[ "$vorher" != "$nachher" ] && echo "aktualisiert: $(git -C site log -1 --format='%h %s')" || true
