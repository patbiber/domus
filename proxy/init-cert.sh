#!/usr/bin/env bash
# Erstmaliges Let's-Encrypt-Zertifikat holen.
# Voraussetzung: Ports 80/443 im Router auf den NUC weitergeleitet, DNS zeigt auf die öffentliche IP.
# Aufruf: ./init-cert.sh [--staging]
set -euo pipefail
cd "$(dirname "$0")"

DOMAIN=domus.biber.solar
EMAIL="${LETSENCRYPT_EMAIL:-$(grep -E '^LETSENCRYPT_EMAIL=' ../.env | cut -d= -f2-)}"
STAGING=""
[[ "${1:-}" == "--staging" ]] && STAGING="--staging"
LIVE=certbot/conf/live/$DOMAIN

mkdir -p certbot/www "$LIVE"

# 1. Dummy-Zertifikat, damit nginx starten kann
# (Prüfung im Container: live/ gehört root, lokal nicht lesbar)
if ! docker compose run --rm --entrypoint test certbot -f /etc/letsencrypt/live/$DOMAIN/fullchain.pem 2>/dev/null; then
  openssl req -x509 -nodes -newkey rsa:2048 -days 1 -subj "/CN=$DOMAIN" \
    -keyout "$LIVE/privkey.pem" -out "$LIVE/fullchain.pem" 2>/dev/null
fi
docker compose up -d nginx

# 2. Dummy entfernen, echtes Zertifikat holen
docker compose run --rm --entrypoint rm certbot -rf \
  /etc/letsencrypt/live/$DOMAIN /etc/letsencrypt/archive/$DOMAIN /etc/letsencrypt/renewal/$DOMAIN.conf
docker compose run --rm --entrypoint certbot certbot certonly --webroot -w /var/www/certbot \
  $STAGING --email "$EMAIL" --agree-tos --no-eff-email -d "$DOMAIN"

# 3. nginx neu laden, Erneuerungs-Container starten
docker compose exec nginx nginx -s reload
docker compose up -d certbot
