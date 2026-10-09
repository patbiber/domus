#!/usr/bin/env bash
# Wildcard-Zertifikat *.homi.solar per DNS-01 (OVH-API) holen – für alle Kunden-Instanzen <name>.homi.solar.
# Vorteil gegenüber Einzelzertifikaten: Kundennamen erscheinen nicht in den öffentlichen Certificate-Transparency-Logs,
# neue Kunden sind ohne Zertifikatsabruf sofort erreichbar.
# Schreibt die OVH-Zugangsdaten aus ../.env nach certbot/conf/ovh.ini (600, nicht im Repo) – nach einem Schlüsselwechsel
# in .env dieses Skript erneut aufrufen. DNS-Eintrag setzt/löscht ovh-dns-hook.py (wird nach certbot/conf/ kopiert);
# die Erneuerung macht danach der Container certbot mit denselben Hooks.
# Aufruf: ./wildcard-cert.sh [--dry-run]
set -euo pipefail
cd "$(dirname "$0")"

wert() { grep -E "^$1=" ../.env | cut -d= -f2- | tr -d '"'"'"; }
EMAIL=$(wert LETSENCRYPT_EMAIL)
INI=certbot/conf/ovh.ini
umask 077
cat > "$INI" <<EOF
dns_ovh_endpoint = ovh-eu
dns_ovh_application_key = $(wert OVH_APPLICATION_KEY)
dns_ovh_application_secret = $(wert OVH_APPLICATION_SECRET)
dns_ovh_consumer_key = $(wert OVH_CONSUMER_KEY)
EOF
chmod 600 "$INI"
install -m 755 ovh-dns-hook.py certbot/conf/ovh-dns-hook.py

docker compose run --rm --entrypoint certbot certbot certonly ${1:-} \
  --manual --preferred-challenges dns \
  --manual-auth-hook '/etc/letsencrypt/ovh-dns-hook.py auth' \
  --manual-cleanup-hook '/etc/letsencrypt/ovh-dns-hook.py cleanup' \
  --cert-name wildcard.homi.solar -d '*.homi.solar' \
  --email "$EMAIL" --agree-tos --no-eff-email -n --keep-until-expiring

[ "${1:-}" = "--dry-run" ] || docker compose exec nginx nginx -s reload
