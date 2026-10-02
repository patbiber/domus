#!/usr/bin/env bash
# Schaltet biber.solar (+ www) auf dem NUC frei, sobald der DNS hierher zeigt:
#   1. prüft, ob biber.solar und www.biber.solar auf die öffentliche IP des NUC zeigen (wie home.biber.solar)
#   2. holt das Let's-Encrypt-Zertifikat (webroot, Port 80 geht schon an den NUC)
#   3. aktiviert proxy/conf.d/biber.solar.conf, testet nginx, lädt neu, prüft die Seite
#   4. Mail an root; bei Erfolg schaltet sich der Wacht-Timer biber-solar-aktivieren.timer selbst ab
# Aufruf: ./biber-solar-aktivieren.sh          (prüft und aktiviert, meldet alles)
#         ./biber-solar-aktivieren.sh --auto   (vom Timer: still, solange der DNS noch nicht umgestellt ist)
set -uo pipefail
cd "$(dirname "$0")"
AUTO=0; [ "${1:-}" = "--auto" ] && AUTO=1
CONF=conf.d/biber.solar.conf
melde() { echo "$1"; printf '%s\n' "$2" | mail -s "domus: biber.solar – $1" root; }

nuc_ip=$(dig +short A home.biber.solar @1.1.1.1 | tail -1)
apex=$(dig +short A biber.solar @1.1.1.1 | tail -1)
www=$(dig +short A www.biber.solar @1.1.1.1 | tail -1)

if [ -f "$CONF" ]; then
  [ $AUTO = 1 ] && { systemctl --user disable --now biber-solar-aktivieren.timer >/dev/null 2>&1; exit 0; }
  echo "biber.solar ist bereits aktiv."; exit 0
fi
if [ -z "$nuc_ip" ] || [ "$apex" != "$nuc_ip" ] || [ "$www" != "$nuc_ip" ]; then
  [ $AUTO = 1 ] && exit 0
  echo "DNS noch nicht umgestellt: biber.solar=$apex www=$www, NUC=$nuc_ip"; exit 1
fi

EMAIL=$(grep -E '^LETSENCRYPT_EMAIL=' ../.env | cut -d= -f2-)
if ! out=$(docker exec certbot certbot certonly --webroot -w /var/www/certbot -d biber.solar -d www.biber.solar \
           --email "$EMAIL" --agree-tos --no-eff-email -n 2>&1); then
  melde "Zertifikat fehlgeschlagen" "DNS zeigt auf den NUC ($nuc_ip), aber Let's Encrypt hat kein Zertifikat ausgestellt.
Der Timer versucht es weiter. Ausgabe von certbot:

$out"
  exit 1
fi

cp conf.d/biber.solar.conf.vorbereitet "$CONF"
if ! docker exec nginx nginx -t >/dev/null 2>&1; then
  rm -f "$CONF"
  melde "nginx-Konfiguration fehlerhaft" "Zertifikat ist da, aber nginx -t schlägt fehl. biber.solar bleibt inaktiv, nichts anderes ist betroffen."
  exit 1
fi
docker exec nginx nginx -s reload >/dev/null 2>&1; sleep 3
c1=$(curl -s -o /dev/null -m 10 -w '%{http_code}' --resolve biber.solar:443:127.0.0.1 https://biber.solar/)
c2=$(curl -s -o /dev/null -m 10 -w '%{http_code} %{redirect_url}' --resolve www.biber.solar:443:127.0.0.1 https://www.biber.solar/)
c3=$(curl -s -o /dev/null -m 10 -w '%{http_code}' --resolve domus.biber.solar:443:127.0.0.1 https://domus.biber.solar/)
systemctl --user disable --now biber-solar-aktivieren.timer >/dev/null 2>&1
melde "jetzt live auf dem NUC" "biber.solar läuft jetzt auf dem NUC ($nuc_ip).

  https://biber.solar        -> $c1 (erwartet 200)
  https://www.biber.solar    -> $c2 (erwartet 301 auf https://biber.solar/)
  https://domus.biber.solar  -> $c3 (unverändert 200)

Zertifikat: Let's Encrypt für biber.solar + www.biber.solar, Erneuerung automatisch.
Inhalt: github.com/patbiber/biber-solar (main), wird alle 5 Minuten veröffentlicht.
Nicht vergessen: training.biber.solar zeigt noch auf den OVH-Server."
