#!/bin/bash
# homi-Monitor: richtet einen Raspberry Pi (Raspberry Pi OS Lite, Bookworm oder Trixie) als Anzeige ohne Bedienung ein.
# Zeigt http://192.168.1.23:8099/monitor.html im Vollbild (cage + Chromium), Bildschirm nachts aus.
# Aufruf auf dem Pi (als normaler Benutzer mit sudo):
#   curl -fsSL http://192.168.1.23:8099/pi/monitor-einrichten.sh | sudo bash
# Mehrfach ausführen schadet nicht. Anleitung: monitor/README.md im domus-Repo.
set -euo pipefail

URL="${HOMI_URL:-http://192.168.1.23:8099/monitor.html}"
AUS="${HOMI_AUS:-23}"          # Bildschirm aus um … Uhr
AN="${HOMI_AN:-6}"             # … und wieder an
NUTZER="${HOMI_NUTZER:-${SUDO_USER:-homi}}"

[ "$(id -u)" = 0 ] || { echo "Bitte mit sudo starten."; exit 1; }
id "$NUTZER" >/dev/null 2>&1 || { echo "Benutzer $NUTZER fehlt."; exit 1; }
MODELL=$(tr -d '\0' </proc/device-tree/model 2>/dev/null || echo unbekannt)
echo "== homi-Monitor auf: $MODELL (Benutzer $NUTZER)"
case "$(uname -m)" in
  armv6l) echo "Dieser Pi (Modell 1 / Zero) ist zu schwach für einen Browser – bitte Pi 2, 3, 4 oder 5 nehmen."; exit 1 ;;
esac

echo "== Zeitzone und Pakete"
timedatectl set-timezone Europe/Zurich
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends cage wlr-randr curl unattended-upgrades fonts-dejavu-core
apt-get install -y -q --no-install-recommends chromium || apt-get install -y -q --no-install-recommends chromium-browser
BROWSER=$(command -v chromium || command -v chromium-browser)

echo "== Startskript"
cat >/usr/local/bin/homi-monitor <<EOF
#!/bin/bash
# startet den Browser im Vollbild; läuft auf tty1 nach der automatischen Anmeldung
URL="$URL"
until curl -fs -o /dev/null -m 5 "\$URL"; do sleep 5; done      # warten, bis Netz und homi da sind
exec cage -s -- $BROWSER --kiosk --noerrdialogs --disable-infobars --no-first-run --incognito \\
  --disable-translate --disable-features=Translate,TranslateUI --check-for-update-interval=31536000 \\
  --disk-cache-size=1 --password-store=basic --ozone-platform=wayland --renderer-process-limit=2 "\$URL"
EOF
chmod 755 /usr/local/bin/homi-monitor

# Bildschirm an/aus (alle angeschlossenen Ausgänge), aus cron und von Hand: homi-bildschirm an|aus
UID_N=$(id -u "$NUTZER")
cat >/usr/local/bin/homi-bildschirm <<EOF
#!/bin/bash
export XDG_RUNTIME_DIR=/run/user/$UID_N WAYLAND_DISPLAY=wayland-0
[ "\$1" = aus ] && S=--off || S=--on
for o in \$(wlr-randr 2>/dev/null | grep -E '^[^ ]' | cut -d' ' -f1); do wlr-randr --output "\$o" \$S; done
EOF
chmod 755 /usr/local/bin/homi-bildschirm

echo "== Automatische Anmeldung auf tty1, Browser startet von selbst"
mkdir -p /etc/systemd/system/getty@tty1.service.d
cat >/etc/systemd/system/getty@tty1.service.d/autologin.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $NUTZER --noclear %I \$TERM
EOF
PROFIL=/home/$NUTZER/.bash_profile
touch "$PROFIL"; chown "$NUTZER:" "$PROFIL"
grep -q homi-monitor "$PROFIL" || cat >>"$PROFIL" <<'EOF'
# homi-Monitor: auf dem Bildschirm (tty1) den Browser starten, bei Absturz neu (SSH bleibt normal)
if [ "$(tty)" = /dev/tty1 ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
  while true; do /usr/local/bin/homi-monitor; sleep 5; done
fi
EOF
systemctl set-default multi-user.target
systemctl disable --now lightdm 2>/dev/null || true
usermod -aG video,render,input "$NUTZER" 2>/dev/null || true

echo "== Nachtruhe ${AUS}–${AN} Uhr, Neustart sonntags 04:30"
cat >/etc/cron.d/homi-monitor <<EOF
# homi-Monitor: Bildschirm nachts aus, wöchentlicher Neustart
0 $AUS * * * $NUTZER /usr/local/bin/homi-bildschirm aus
0 $AN * * * $NUTZER /usr/local/bin/homi-bildschirm an
30 4 * * 0 root /sbin/reboot
EOF

echo "== Dauerbetrieb: kein Bildschirmschoner, Watchdog, wenig Schreiben auf die SD-Karte"
CMD=/boot/firmware/cmdline.txt; [ -f "$CMD" ] || CMD=/boot/cmdline.txt
grep -q consoleblank "$CMD" || sed -i '1 s/$/ consoleblank=0/' "$CMD"
CFG=/boot/firmware/config.txt; [ -f "$CFG" ] || CFG=/boot/config.txt
grep -q '^dtparam=watchdog=on' "$CFG" || echo 'dtparam=watchdog=on' >>"$CFG"
grep -q '^disable_splash=1' "$CFG" || echo 'disable_splash=1' >>"$CFG"
mkdir -p /etc/systemd/system.conf.d /etc/systemd/journald.conf.d
printf '[Manager]\nRuntimeWatchdogSec=15\n' >/etc/systemd/system.conf.d/homi-watchdog.conf
printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=20M\n' >/etc/systemd/journald.conf.d/homi.conf
if [ -d /etc/NetworkManager/conf.d ]; then
  printf '[connection]\nwifi.powersave = 2\n' >/etc/NetworkManager/conf.d/homi-wlan.conf     # WLAN nicht einschlafen lassen
fi

echo
echo "Fertig. Der Pi startet jetzt neu und zeigt danach: $URL"
echo "Bildschirm von Hand: homi-bildschirm aus | homi-bildschirm an"
sleep 3
systemctl reboot
