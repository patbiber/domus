#!/usr/bin/env bash
# Strompreise/Tarife der Gemeindewerke Stäfa (GWS) herunterladen und ablegen.
# - Maschinenlesbare Tarife (JSON, via strompreisvergleich.ch, von gws.ch verlinkt)
# - alle Tarif-PDFs der Seite https://gws.ch/strom-tarife-produkte/
# Ablage: data/<JJJJ-MM-TT>/, data/latest zeigt auf den neuesten Stand.
# Bei Änderungen gegenüber dem letzten Stand oder bei Fehlern: Mail an root (-> /etc/aliases).
# Aufruf: ./fetch-tarife.sh   (monatlich per systemd-User-Timer strompreise.timer)
set -euo pipefail
cd "$(dirname "$0")"

PAGE_URL=https://gws.ch/strom-tarife-produkte/
MAIL_TO=root
UA="Mozilla/5.0 (domus Tarifarchiv)"
DATA=data
DIR="$DATA/$(date +%F)"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

fail() {
  echo "FEHLER: $*" >&2
  printf 'Der monatliche Abruf der GWS-Stromtarife ist fehlgeschlagen:\n\n%s\n\nSkript: %s\n' "$*" "$PWD/$(basename "$0")" \
    | mail -s "Domus: Abruf GWS-Stromtarife fehlgeschlagen" "$MAIL_TO" || true
  exit 1
}

fetch() { curl -sSfL -m 60 -A "$UA" "$1" -o "$2"; }

# 1. Tarifseite laden und Links extrahieren
fetch "$PAGE_URL" "$TMP/seite.html" || fail "Tarifseite $PAGE_URL nicht erreichbar"

JSON_URL=$(grep -oE 'href="[^"]*tarife\.json"' "$TMP/seite.html" | head -1 | cut -d'"' -f2)
[[ -n "$JSON_URL" ]] || fail "Link zu den maschinenlesbaren Tarifen (JSON) nicht gefunden – Seite geändert?"

mapfile -t PDFS < <(grep -oE 'href="[^"]*\.pdf"' "$TMP/seite.html" | cut -d'"' -f2 \
  | grep -iE 'tarif|stromprodukte' | sort -u)
(( ${#PDFS[@]} > 0 )) || fail "Keine Tarif-PDFs auf $PAGE_URL gefunden – Seite geändert?"

# 2. Herunterladen
mkdir -p "$TMP/neu"
fetch "$JSON_URL" "$TMP/neu/tarife.json" || fail "JSON $JSON_URL nicht ladbar"
jq -e '.tariffs | length > 0' "$TMP/neu/tarife.json" >/dev/null || fail "JSON $JSON_URL enthält keine Tarife"
for url in "${PDFS[@]}"; do
  fetch "$url" "$TMP/neu/$(basename "$url")" || fail "PDF $url nicht ladbar"
done
printf '%s\n' "$JSON_URL" "${PDFS[@]}" > "$TMP/neu/quellen.txt"
(cd "$TMP/neu" && sha256sum -- * > SHA256SUMS)

# 3. Mit letztem Stand vergleichen und ablegen
AENDERUNG=""
if [[ -f "$DATA/latest/SHA256SUMS" ]]; then
  AENDERUNG=$(diff <(grep -v quellen.txt "$DATA/latest/SHA256SUMS" | awk '{print $2, $1}' | sort) \
                   <(grep -v quellen.txt "$TMP/neu/SHA256SUMS" | awk '{print $2, $1}' | sort) || true)
fi

mkdir -p "$DIR"
cp -a "$TMP/neu/." "$DIR/"
ln -sfn "$(basename "$DIR")" "$DATA/latest"
echo "Abgelegt: $DIR (${#PDFS[@]} PDFs + tarife.json)"

if [[ -n "$AENDERUNG" ]]; then
  echo "Änderungen gegenüber dem letzten Stand:"; echo "$AENDERUNG"
  printf 'Die Stromtarife der Gemeindewerke Stäfa haben sich geändert.\n\nNeuer Stand: %s\n\nGeänderte/neue Dateien (< alt, > neu):\n%s\n' \
    "$PWD/$DIR" "$AENDERUNG" | mail -s "Domus: Neue GWS-Stromtarife" "$MAIL_TO" || true
fi
