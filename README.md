# Domus

Home-System auf einem Intel NUC mit Ubuntu Server und Docker.

## Struktur

Jeder Dienst hat einen eigenen Ordner mit eigener `compose.yml`:

| Ordner | Dienst | Status |
|---|---|---|
| `homeassistant/` | Home Assistant (Container, `network_mode: host`) | läuft |
| `mosquitto/` | MQTT-Broker | geplant |
| `zigbee2mqtt/` | Zigbee-Geräte (Zigbee-Stick folgt) | geplant |
| – | Hue über die Hue Bridge (Integration in Home Assistant) | geplant |
| `proxy/` | Reverse Proxy + Webhosting | geplant |

## Einrichtung

```bash
cp .env.example .env   # Werte anpassen
cd homeassistant && docker compose up -d
```

Home Assistant: http://192.168.178.121:8123

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
