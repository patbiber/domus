#!/usr/bin/env bash
# Rendert den homi-Kurzfilm: Einzelbilder mit Puppeteer, Video mit ffmpeg (beides in Containern).
# Ergebnis: film/out/homi-film.mp4 (1080×1350, 60 s, H.264) und film/out/homi-film-vorschau.png
# Vorschau im Browser: homi-film.html direkt öffnen (spielt in Echtzeit ab).
set -euo pipefail
cd "$(dirname "$0")"
OUT="$PWD/out"; FRAMES="$OUT/frames"
mkdir -p "$FRAMES"; chmod 777 "$FRAMES"
docker run --rm -v "$PWD":/film:ro -v "$FRAMES":/frames -e OUT=/frames -e NODE_PATH=/home/pptruser/node_modules \
  ghcr.io/puppeteer/puppeteer:latest node /film/render.js | grep -v '^{'
docker run --rm -v "$OUT":/out alpine:3 sh -c 'apk add --no-cache ffmpeg >/dev/null &&
  ffmpeg -y -loglevel error -framerate 30 -i /out/frames/f%05d.png \
    -c:v libx264 -preset slow -crf 16 -tune animation -pix_fmt yuv420p -movflags +faststart /out/homi-film.mp4 &&
  cp /out/frames/f00820.png /out/homi-film-vorschau.png && chown -R '"$(id -u):$(id -g)"' /out'
rm -rf "${FRAMES:?}"
ls -lh "$OUT"
