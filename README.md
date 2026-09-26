# Domus

Home-System auf einem Intel NUC mit Ubuntu Server und Docker.

## Struktur

Jeder Dienst hat einen eigenen Ordner mit eigener `compose.yml`:

| Ordner | Dienst | Status |
|---|---|---|
| `homeassistant/` | Home Assistant (Container, `network_mode: host`) – https://domus.biber.solar | läuft |
| `mosquitto/` | MQTT-Broker | geplant |
| `zigbee2mqtt/` | Zigbee-Geräte (Zigbee-Stick folgt) | geplant |
| – | Hue über die Hue Bridge (Integration in Home Assistant) | geplant |
| `proxy/` | nginx (Reverse Proxy für Home Assistant) + certbot (Let's Encrypt) | läuft |
| `claude-remote/` | Dauerhafte Claude-Code-Session mit Remote Control (systemd-User-Dienst, kein Docker) | läuft |

## Einrichtung

```bash
cp .env.example .env   # Werte anpassen
cd homeassistant && docker compose up -d
```

Home Assistant: https://domus.biber.solar (Internet) bzw. http://192.168.178.121:8123 (LAN)

### Webseite / Zertifikat (`proxy/`)

Voraussetzung: Fritzbox leitet TCP 80 und 443 an `192.168.178.121` weiter, `domus.biber.solar` zeigt (A-Record bei OVH) auf die öffentliche IP.

```bash
cd proxy
./init-cert.sh --staging   # Testlauf gegen Let's-Encrypt-Staging
./init-cert.sh             # echtes Zertifikat
```

Erneuerung läuft automatisch (certbot prüft alle 12h, nginx lädt alle 6h neu). Port 80 leitet auf 443 weiter.
Neue Seiten: weitere Datei in `proxy/conf.d/` anlegen.

`domus.biber.solar` leitet auf Home Assistant weiter (`host.docker.internal:8123`, WebSockets aktiv).
Die Proxy-Einstellungen von Home Assistant stehen seit 2026.x **nicht** mehr in `configuration.yaml` (`http:` wird ignoriert),
sondern in `homeassistant/config/.storage/http` (nur bei gestopptem HA bearbeiten):

- `use_x_forwarded_for: true`, `trusted_proxies: [172.16.0.0/12]` (Docker-Netze)
- `ip_ban_enabled: true`, `login_attempts_threshold: 5` – Sperren landen in `config/ip_bans.yaml`

Für jeden Benutzer 2-Faktor-Login (TOTP) im HA-Profil aktivieren.

### Claude Remote Control (`claude-remote/`)

Dauerhafte Claude-Code-Session, steuerbar über https://claude.ai/code oder die Claude-App (Umgebung „domus“).
Läuft als systemd-User-Dienst in tmux, startet nach Absturz/Neustart automatisch (Linger aktiv).

```bash
mkdir -p ~/.config/systemd/user
ln -sf ~/domus/claude-remote/claude-remote.service ~/.config/systemd/user/
sudo loginctl enable-linger $USER
systemctl --user daemon-reload && systemctl --user enable --now claude-remote

systemctl --user status claude-remote   # Status
tmux attach -t claude-remote            # zuschauen (verlassen: Ctrl-b d)
```

Rechte: `--permission-mode default` – Befehle werden in der App bestätigt.

## Betrieb

```bash
# Update eines Dienstes (vorher Backup!)
cd <dienst> && docker compose pull && docker compose up -d

# Logs
docker compose logs -f
```

### Backup Home Assistant

```bash
mkdir -p ~/domus/backups
sudo tar czf ~/domus/backups/homeassistant-$(date +%F-%H%M).tar.gz -C ~/domus/homeassistant config
```

## Regeln

Siehe [CLAUDE.md](CLAUDE.md): keine Secrets im Repo, keine öffentlichen Ports ohne Rückfrage.

## Host-Konfiguration

- Zeitzone: `Europe/Zurich` (`timedatectl set-timezone`).
- Zusätzliche IP `192.168.1.23/24` auf `eno1` (ohne Gateway), in derselben Netplan-Datei.
- DNS: Cloudflare `1.1.1.1` / `1.0.0.1` statt Router, gesetzt in `/etc/netplan/00-installer-config.yaml` (`use-dns: false` für DHCP/RA + `nameservers`).
