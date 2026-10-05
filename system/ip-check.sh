#!/usr/bin/env bash
# Überwacht die öffentliche IPv4-Adresse des Anschlusses (alle 5 min per Timer domus-ip-check).
# Ändert sie sich, geht sofort eine Mail an root (-> patrick@biber.solar) mit der neuen IP und den DNS-Einträgen,
# die bei OVH angepasst werden müssen. Zusätzlich: Mail, wenn ein DNS-Eintrag nicht (mehr) auf die aktuelle IP zeigt.
# Stimmt der DNS nicht und sind OVH-API-Zugangsdaten in ~/domus/.env, werden die A-Einträge automatisch
# per system/ovh-dns.py nachgeführt. Jede Meldung kommt einmal pro neuem Zustand, danach höchstens alle 6 h.
set -uo pipefail
NAMEN=(${IP_CHECK_NAMEN:-biber.solar www.biber.solar training.biber.solar domus.biber.solar home.biber.solar test.biber.solar energy.biber.solar})
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/domus"
mkdir -p "$STATE_DIR"
IP_FILE="$STATE_DIR/public_ip"
MELDUNG_FILE="$STATE_DIR/ip_meldung"          # "<zustand>|<zeitpunkt der letzten mail>"
ERINNERUNG_S=$((6 * 3600))

# Öffentliche IP: mindestens zwei Dienste müssen übereinstimmen (ein Dienst allein kann sich irren)
ips=()
for u in https://api.ipify.org https://ipv4.icanhazip.com https://ifconfig.me/ip; do
  ip=$(curl -4 -s -m 8 "$u" | tr -d '[:space:]')
  [[ $ip =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]] && ips+=("$ip")
done
neu=$(printf '%s\n' "${ips[@]}" | sort | uniq -c | sort -rn | awk '$1 >= 2 {print $2; exit}')
[ -z "$neu" ] && exit 0                         # kein Internet oder Dienste uneinig: nächster Lauf

alt=$(cat "$IP_FILE" 2>/dev/null || true)
echo "$neu" > "$IP_FILE"

# DNS-Einträge prüfen (Cloudflare-Resolver)
falsch=""
for n in "${NAMEN[@]}"; do
  a=$(dig +short A "$n" @1.1.1.1 | tail -1)
  [ "$a" != "$neu" ] && falsch+="  $n  zeigt auf ${a:-(nichts)}  -> neu: $neu"$'\n'
done

# Automatisch bei OVH nachführen (falls Zugangsdaten vorhanden)
auto=""; auto_ok=0
if [ -n "$falsch" ] && [ -x "$(dirname "$0")/ovh-dns.py" ]; then
  auto=$("$(dirname "$0")/ovh-dns.py" --setzen "$neu" 2>&1) && auto_ok=1
fi

zustand="ip=$neu;dns=$( [ -z "$falsch" ] && echo ok || echo falsch )"
read -r letzt_zustand letzt_zeit < <( [ -f "$MELDUNG_FILE" ] && tr '|' ' ' < "$MELDUNG_FILE" || echo "- 0")
jetzt=$(date +%s)

if [ -n "$alt" ] && [ "$alt" != "$neu" ]; then
  betreff="domus: ÖFFENTLICHE IP GEÄNDERT -> $neu"
elif [ -n "$falsch" ]; then
  [ "$zustand" = "$letzt_zustand" ] && [ $((jetzt - letzt_zeit)) -lt $ERINNERUNG_S ] && exit 0
  betreff="domus: DNS zeigt nicht auf $neu"
elif [ "$letzt_zustand" != "-" ] && [ "$letzt_zustand" != "$zustand" ] && [[ $letzt_zustand == *dns=falsch ]]; then
  betreff="domus: DNS wieder in Ordnung ($neu)"
else
  [ "$letzt_zustand" != "$zustand" ] && echo "$zustand|$jetzt" > "$MELDUNG_FILE"
  exit 0
fi

{
  if [ -n "$alt" ] && [ "$alt" != "$neu" ]; then
    echo "Die öffentliche IP des NUC hat sich geändert."
    echo
    echo "   alt: $alt"
    echo "   NEU: $neu"
    echo
  fi
  if [ -n "$falsch" ] && [ $auto_ok = 1 ]; then
    echo "Die DNS-Einträge wurden AUTOMATISCH per OVH-API angepasst – du musst nichts tun:"
    echo
    echo "$auto" | sed 's/^/  /'
    echo
    echo "Je nach TTL sind die Seiten in wenigen Minuten wieder erreichbar. Entwarnung folgt per Mail."
  elif [ -n "$falsch" ]; then
    [ -n "$auto" ] && printf 'Automatische Anpassung über OVH nicht möglich:\n  %s\n\n' "$auto"
    echo "Diese DNS-Einträge (Typ A) bitte bei OVH auf $neu ändern:"
    echo
    printf '%s' "$falsch"
    echo
    echo "OVH: Web Cloud -> Domainnamen -> biber.solar -> DNS-Zone -> A-Einträge bearbeiten."
    echo "NICHT ändern: MX, SPF/TXT, DKIM, DMARC (Proton-Mail)."
    echo
    echo "Bis zur Änderung sind die betroffenen Seiten von aussen nicht erreichbar."
  else
    echo "Alle DNS-Einträge zeigen auf $neu. Alles erreichbar."
  fi
  echo
  echo "Geprüft: $(date '+%d.%m.%Y %H:%M') (alle 5 Minuten)"
} | mail -s "$betreff" root
echo "$zustand|$jetzt" > "$MELDUNG_FILE"
echo "$betreff"
