#!/usr/bin/env bash
# Abgleich NUC → Reserve-PC (192.168.1.24), läuft auf dem NUC alle 10 Minuten (domus-reserve-abgleich.timer).
#
# 1. Ist die Reserve gerade AKTIV (hat übernommen), tritt der NUC zurück: Container stoppen, Adresse .23 abgeben,
#    Mail an Patrick – und es wird NICHTS kopiert (die Reserve hat dann die neueren Daten).
# 2. Sonst: in sich stimmige Kopien der SQLite-Datenbanken (Home Assistant, Academy) und ein Auszug der
#    Umami-Datenbank nach backups/reserve-stand/, dann rsync von ~/domus (ohne backups/), /etc/msmtprc,
#    /etc/aliases, ~/.config/domus und ~/.config/systemd/user auf die Reserve. Die verschlüsselten Gesamt-Backups
#    (backups/extern/) kommen zusätzlich auf deren Backup-Platte /srv/backup/extern/.
# Nach 3 Fehlversuchen in Folge gibt es eine Mail (und wieder eine, wenn es klappt).
set -uo pipefail
. "$(dirname "$(readlink -f "$0")")/rollen.conf"
umask 077
DOMUS="$HOME/domus"
RESERVE=claude@$RESERVE_IP
SCHL="$HOME/.ssh/domus_reserve"
SSH="ssh -i $SCHL -o BatchMode=yes -o ConnectTimeout=10 -o UserKnownHostsFile=$HOME/.ssh/known_hosts"
STAND="$DOMUS/backups/reserve-stand"
ZUSTAND="${XDG_STATE_HOME:-$HOME/.local/state}/domus/reserve-abgleich"
mkdir -p "$STAND" "$ZUSTAND"
exec 9>"$ZUSTAND/sperre"; flock -n 9 || { echo "Abgleich läuft schon"; exit 0; }

mail_an() { printf '%s\n' "$2" | mail -s "$1" root || true; }

fehler() {
  echo "FEHLER: $*" >&2
  n=$(( $(cat "$ZUSTAND/fehler" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$ZUSTAND/fehler"
  if [ "$n" -eq 3 ]; then
    mail_an "domus: Abgleich mit dem Reserve-PC klappt nicht" "Der Abgleich NUC → Reserve-PC ($RESERVE_IP) ist 3-mal hintereinander fehlgeschlagen:

  $*

Letzter erfolgreicher Abgleich: $(cat "$ZUSTAND/ok" 2>/dev/null || echo 'noch keiner')
Solange das so ist, hätte die Reserve bei einem Ausfall alte Daten. Ist der Reserve-PC eingeschaltet und am Netz?
Protokoll: journalctl --user -u domus-reserve-abgleich"
  fi
  exit 1
}

# --- 1. Hat die Reserve übernommen?
ANTWORT=$($SSH "$RESERVE" 'cat /var/lib/domus/rolle 2>/dev/null || echo reserve') || fehler "Reserve-PC per SSH nicht erreichbar"
if [ "$ANTWORT" = "aktiv" ]; then
  if ip -4 addr show "$DEV" | grep -q "$VIP/"; then
    "$DOMUS/reserve/vorrang.sh" zuruecktreten
  fi
  echo "Reserve ist aktiv – kein Abgleich (zurück zum NUC: reserve/zurueck-zum-nuc.sh)"
  exit 0
fi

# --- 2. Stimmige Kopien der Datenbanken
sudo python3 -c "import sqlite3,sys; s=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True); d=sqlite3.connect(sys.argv[2]); s.backup(d); d.close()" \
  "$DOMUS/homeassistant/config/home-assistant_v2.db" "$STAND/home-assistant_v2.db" \
  || fehler "Home-Assistant-Datenbank nicht kopierbar"
docker exec academy python3 -c "import os,sqlite3; os.makedirs('/data/snapshot',exist_ok=True); s=sqlite3.connect('/data/academy.db'); d=sqlite3.connect('/data/snapshot/academy.db'); s.backup(d); d.close()" \
  || echo "Hinweis: Academy-Datenbank nicht kopiert (Container academy läuft nicht?)"
if docker exec umami-db pg_dump -U umami --clean --if-exists umami 2>/dev/null | gzip > "$STAND/umami.sql.gz.neu" && [ -s "$STAND/umami.sql.gz.neu" ]; then
  mv "$STAND/umami.sql.gz.neu" "$STAND/umami.sql.gz"
else
  rm -f "$STAND/umami.sql.gz.neu"; echo "Hinweis: Umami-Auszug fehlgeschlagen"
fi
systemctl --user list-unit-files --state=enabled --no-legend 2>/dev/null | awk '{print $1}' > "$STAND/units-aktiv.txt"
date -Iseconds > "$STAND/zeit"

# --- 3. Kopieren (als root, damit Container-Dateien mit Besitzer und Rechten ankommen)
RSYNC=(sudo rsync -aHAX --numeric-ids --delete --partial --mkpath -e "$SSH" --rsync-path="sudo rsync")
"${RSYNC[@]}" \
  --exclude='/backups/' --exclude='*.tmp' \
  --exclude='/homeassistant/config/home-assistant_v2.db*' \
  --exclude='/umami/data/postgres/' \
  --exclude='/academy/data/academy.db*' \
  --exclude='/pihole-zweit/data/' \
  --exclude='/unifi/data/logs/' \
  "$DOMUS/" "$RESERVE:/home/claude/domus/" || fehler "rsync ~/domus fehlgeschlagen (Code $?)"
"${RSYNC[@]}" --exclude='*-wal' --exclude='*-shm' --exclude='*.neu' "$STAND/" "$RESERVE:/home/claude/domus/backups/reserve-stand/" || fehler "rsync Datenbank-Kopien fehlgeschlagen"
"${RSYNC[@]}" /etc/msmtprc /etc/aliases "$RESERVE:/etc/" || fehler "rsync Mail-Konfiguration fehlgeschlagen"
"${RSYNC[@]}" /etc/systemd/system/homi-login.service "$RESERVE:/etc/systemd/system/" || fehler "rsync homi-login.service fehlgeschlagen"
"${RSYNC[@]}" "$HOME/.config/domus/" "$RESERVE:/home/claude/.config/domus/" || fehler "rsync ~/.config/domus fehlgeschlagen"
"${RSYNC[@]}" --exclude='*.wants/' "$HOME/.config/systemd/user/" "$RESERVE:/home/claude/.config/systemd/user/" || fehler "rsync systemd-Units fehlgeschlagen"
# SSH-Schlüssel (GitHub-Deploy-Keys, Schlüssel für die Reserve) und Git-Einstellungen: damit der Abend-Upload
# auch bei einer Übernahme läuft und die Reserve selbst Hauptrechner werden kann. authorized_keys bleibt je Rechner.
rsync -a -e "$SSH" --exclude=authorized_keys --exclude='known_hosts.old' --exclude='*.bak' "$HOME/.ssh/" "$RESERVE:/home/claude/.ssh/" \
  && rsync -a -e "$SSH" "$HOME/.gitconfig" "$RESERVE:/home/claude/.gitconfig" || fehler "rsync ~/.ssh fehlgeschlagen"
# Claude Code: Programm (aktuelle Version), Einstellungen, Gedächtnis, Unterhaltungen und Anmeldung – damit Claude
# nach einer Übernahme auf der Reserve weiterarbeiten kann (gestartet wird es dort nur im Ausfall, uebernehmen.sh)
CLAUDE_BIN=$(readlink -f "$HOME/.local/bin/claude")
rsync -a -e "$SSH" --mkpath "$CLAUDE_BIN" "$RESERVE:$CLAUDE_BIN" \
  && $SSH "$RESERVE" "ln -sfn '$CLAUDE_BIN' ~/.local/bin/claude" || fehler "rsync Claude-Programm fehlgeschlagen"
rsync -a --delete -e "$SSH" --exclude='*.lock' "$HOME/.claude/" "$RESERVE:/home/claude/.claude/" \
  && rsync -a -e "$SSH" "$HOME/.claude.json" "$RESERVE:/home/claude/.claude.json" || fehler "rsync ~/.claude fehlgeschlagen"
if ls "$DOMUS"/backups/extern/*.gpg >/dev/null 2>&1; then
  sudo rsync -a -e "$SSH" --rsync-path="sudo rsync" "$DOMUS"/backups/extern/ "$RESERVE:/srv/backup/extern/" \
    || fehler "rsync verschlüsselte Backups fehlgeschlagen"
fi
$SSH "$RESERVE" "sudo cp /home/claude/domus/backups/reserve-stand/zeit /var/lib/domus/abgleich-zeit" || true

# --- Erfolg
if [ "$(cat "$ZUSTAND/fehler" 2>/dev/null || echo 0)" -ge 3 ]; then
  mail_an "domus: Abgleich mit dem Reserve-PC klappt wieder" "Der Abgleich NUC → Reserve-PC läuft wieder ($(date '+%d.%m.%Y %H:%M'))."
fi
echo 0 > "$ZUSTAND/fehler"; date -Iseconds > "$ZUSTAND/ok"
echo "Abgleich ok"
