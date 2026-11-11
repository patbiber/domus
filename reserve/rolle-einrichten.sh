#!/usr/bin/env bash
# Richtet DIESEN Rechner als Hauptrechner oder als Reserve ein (Adressen in reserve/rollen.conf).
#   rolle-einrichten.sh haupt    – betreibt die Dienste: Adresse VIP fest (Netplan), Vorrang-Prüfung vor Docker,
#                                  Abgleich auf die Reserve, alle Timer, Claude-Hauptsitzung; Wächter aus
#   rolle-einrichten.sh reserve  – wartet: VIP weg, alle Container aus ausser pihole-zweit, Timer aus,
#                                  Wächter und tägliche Sicherung an
# Typischer Fall: NUC endgültig kaputt, domus2 hat übernommen (Rolle «aktiv») → in rollen.conf HAUPT_IP auf die
# Adresse von domus2 und RESERVE_IP auf den neuen domus3 setzen, auf domus2  rolle-einrichten.sh haupt, domus3 wie
# domus2 vorbereiten (Ubuntu, Benutzer claude) und dort  rolle-einrichten.sh reserve.
set -euo pipefail
HIER="$(dirname "$(readlink -f "$0")")"
. "$HIER/rollen.conf"
DOMUS=/home/claude/domus
[ "$(id -un)" = claude ] || { echo "Als Benutzer claude ausführen" >&2; exit 2; }
ROLLE="${1:-}"
ip -4 addr show "$DEV" >/dev/null 2>&1 || { echo "Netzwerkkarte $DEV fehlt" >&2; exit 1; }
EIGENE=$(ip -4 -o addr show "$DEV" | awk '{print $4}' | cut -d/ -f1)

netplan_vip() {   # $1 = add|del – VIP in der Netplan-Datei dauerhaft eintragen/entfernen (ohne «netplan apply»)
  sudo python3 - "$1" "$VIP/24" "$DEV" <<'PY'
import glob, sys, yaml
was, vip, dev = sys.argv[1:]
for p in sorted(glob.glob('/etc/netplan/*.yaml')):
    d = yaml.safe_load(open(p))
    e = (d.get('network') or {}).get('ethernets') or {}
    if dev not in e: continue
    a = e[dev].setdefault('addresses', [])
    if (was == 'add') == (vip in a): print('Netplan', p, 'unverändert'); continue
    a.append(vip) if was == 'add' else a.remove(vip)
    kopf = ''.join(z for z in open(p) if z.startswith('#'))
    open(p, 'w').write(kopf + yaml.safe_dump(d, sort_keys=False, allow_unicode=True))
    print('Netplan', p, was, vip)
PY
}
user_units() {   # $1 = enable|disable  – Timer/Pfade aus der Liste des Hauptrechners
  local liste=$DOMUS/backups/reserve-stand/units-aktiv.txt
  [ -s "$liste" ] || liste=<(systemctl --user list-unit-files --state=enabled --no-legend | awk '{print $1}')
  grep -E '\.(timer|path)$' "$liste" | grep -v '^launchpadlib' | while read -r u; do
    if [ "$1" = enable ]; then
      systemctl --user enable --now "$u" >/dev/null 2>&1 || echo "  $u: einschalten nicht möglich"
    else   # nicht «disable»: das löscht bei verknüpften Units die Verknüpfung selbst
      systemctl --user stop "$u" >/dev/null 2>&1 || true
      rm -f ~/.config/systemd/user/*.wants/"$u"
    fi
  done
  systemctl --user daemon-reload
}

case "$ROLLE" in
  haupt)
    echo "$EIGENE" | grep -qx "$HAUPT_IP" || { echo "Dieser Rechner hat nicht HAUPT_IP $HAUPT_IP (rollen.conf)" >&2; exit 1; }
    sudo mkdir -p /var/lib/domus; echo haupt | sudo tee /var/lib/domus/rolle >/dev/null
    sudo systemctl disable --now domus-waechter.service 2>/dev/null || true
    ip -4 addr show "$DEV" | grep -q "$VIP/" || { sudo ip addr add "$VIP/24" dev "$DEV"; sudo arping -q -U -c 3 -I "$DEV" "$VIP" || true; }
    netplan_vip add
    sudo ln -sf "$HIER/domus-vorrang.service" /etc/systemd/system/domus-vorrang.service
    sudo mkdir -p /etc/systemd/system/docker.service.d
    sudo cp "$HIER/docker-vorrang.conf" /etc/systemd/system/docker.service.d/domus-vorrang.conf
    [ -f "$DOMUS/system/domus-firewall.service" ] && sudo cp "$DOMUS/system/domus-firewall.service" /etc/systemd/system/
    sudo ln -sf "$HIER/domus-vorrang-pruefen.service" "$HIER/domus-vorrang-pruefen.timer" /etc/systemd/system/
    sudo systemctl daemon-reload; sudo systemctl enable domus-vorrang.service domus-firewall.service
    sudo systemctl enable --now domus-vorrang-pruefen.timer
    docker rm -f pihole-zweit >/dev/null 2>&1 || true
    ln -sf "$HIER/domus-reserve-abgleich.service" "$HIER/domus-reserve-abgleich.timer" "$HIER/domus-reserve-mittag.service" "$HIER/domus-reserve-mittag.timer" ~/.config/systemd/user/
    systemctl --user daemon-reload
    user_units enable
    systemctl --user enable domus-reserve-abgleich.timer domus-reserve-mittag.timer claude-sitzung.service >/dev/null 2>&1 || true
    systemctl --user start domus-reserve-abgleich.timer domus-reserve-mittag.timer
    [ -f /etc/systemd/system/homi-login.service ] && sudo systemctl enable --now homi-login.service
    for d in pihole homeassistant energie umami proxy energy academy biber-solar biber-solar-test training unifi; do
      [ -f "$DOMUS/$d/compose.yml" ] && (cd "$DOMUS/$d" && docker compose up -d >/dev/null 2>&1 || echo "  $d: Start fehlgeschlagen")
    done
    docker restart nginx >/dev/null 2>&1 || true
    echo "Dieser Rechner ($HAUPT_IP) ist jetzt Hauptrechner. Reserve laut rollen.conf: $RESERVE_IP"
    ;;
  reserve)
    echo "$EIGENE" | grep -qx "$RESERVE_IP" || { echo "Dieser Rechner hat nicht RESERVE_IP $RESERVE_IP (rollen.conf)" >&2; exit 1; }
    sudo mkdir -p /var/lib/domus; echo reserve | sudo tee /var/lib/domus/rolle >/dev/null
    sudo rm -f /var/lib/domus/datenbanken-eingesetzt
    systemctl --user stop domus-reserve-abgleich.timer domus-reserve-mittag.timer >/dev/null 2>&1 || true
    rm -f ~/.config/systemd/user/*.wants/domus-reserve-abgleich.timer ~/.config/systemd/user/*.wants/domus-reserve-mittag.timer ~/.config/systemd/user/*.wants/claude-sitzung.service
    tmux kill-session -t claude 2>/dev/null || true
    user_units disable; systemctl --user reset-failed || true
    sudo systemctl disable --now homi-login.service 2>/dev/null || true
    docker ps --format '{{.Names}}' | { grep -vx pihole-zweit || true; } | xargs -r docker stop -t 60 >/dev/null
    sudo ip addr del "$VIP/24" dev "$DEV" 2>/dev/null || true
    netplan_vip del
    sudo rm -f /etc/systemd/system/docker.service.d/domus-vorrang.conf
    sudo systemctl disable domus-vorrang.service 2>/dev/null || true
    sudo systemctl disable --now domus-vorrang-pruefen.timer 2>/dev/null || true
    sudo cp "$HIER/domus-waechter.service" "$HIER/domus-reserve-sicherung.service" "$HIER/domus-reserve-sicherung.timer" /etc/systemd/system/
    [ -f "$DOMUS/system/domus-firewall.service" ] && sudo cp "$DOMUS/system/domus-firewall.service" /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now domus-firewall.service domus-waechter.service
    mountpoint -q /srv/backup && sudo systemctl enable --now domus-reserve-sicherung.timer
    (cd "$DOMUS/pihole-zweit" && docker compose up -d >/dev/null)
    echo "Dieser Rechner ($RESERVE_IP) ist jetzt Reserve für $HAUPT_IP."
    ;;
  *) echo "Aufruf: $0 haupt|reserve   (Adressen: $HIER/rollen.conf)" >&2; exit 2 ;;
esac
