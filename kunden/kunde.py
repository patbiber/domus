#!/usr/bin/env python3
"""Kunden-Instanzen von homi unter <name>.homi.solar einrichten: nginx-Konfiguration, Let's-Encrypt-Zertifikat, Passwort.

Der Wildcard-Eintrag *.homi.solar zeigt auf den NUC; der Standardserver (proxy/conf.d/00-default.conf) beantwortet die
Let's-Encrypt-Prüfung für jeden Namen, darum genügt ein Aufruf. Erzeugt werden (nur auf dem NUC, nicht im Repo):
  proxy/conf.d/kunde-<name>.conf       aus kunden/kunde.conf.vorlage
  proxy/conf.d/kunde-<name>.htpasswd   Benutzer «homi», Passwort wird einmal angezeigt

Aufruf:
  kunde.py neu <name> [--ziel <ziel>] [--ohne-passwort]
  kunde.py ziel <name> <ziel>          wohin die Adresse zeigt (siehe unten)
  kunde.py passwort <name>             neues Passwort erzeugen
  kunde.py liste
  kunde.py entfernen <name> --ja       Konfiguration, Passwort und Zertifikat löschen

Ziel:
  platzhalter            Seite «Hier wohnt bald ein homi» (Standard)
  host:<port>            homi-Dienst auf dem NUC selbst (z. B. host:8099)
  http://<name>:<port>   Container im Netz proxy_default oder Adresse im LAN/WireGuard

Name: 2–30 Zeichen a–z, 0–9, Bindestrich. Tipp: neutrale Namen (sonnenhof statt mueller-staefa) – Zertifikate
werden öffentlich protokolliert (Certificate Transparency), der Name ist also für alle sichtbar.
"""
import base64
import os
import re
import secrets
import subprocess
import sys
import tempfile
from datetime import date

DOMAIN = "homi.solar"
HIER = os.path.dirname(os.path.abspath(__file__))
CONF_D = os.path.join(HIER, "..", "proxy", "conf.d")
ENV = os.path.join(HIER, "..", ".env")
ICON = os.path.join(HIER, "..", "energie", "www", "icons", "homi-192.png")
BENUTZER = "homi"
RESERVIERT = {"www", "mail", "smtp", "imap", "pop", "api", "admin", "app", "status", "test", "demo", "homi", "kunden",
              "ftp", "ns1", "ns2", "autoconfig", "autodiscover", "webmail", "vpn", "login", "konto"}
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,28}[a-z0-9])$")
ZIEL_RE = re.compile(r"^https?://[A-Za-z0-9.-]+(?::\d{1,5})?$")


def fehler(text):
    sys.exit(f"Fehler: {text}")


def docker(*args, pruefen=True):
    r = subprocess.run(["docker", *args], capture_output=True, text=True)
    if pruefen and r.returncode:
        fehler(f"docker {' '.join(args[:3])} …: {(r.stderr or r.stdout).strip()[-500:]}")
    return r


def pfade(name):
    return os.path.join(CONF_D, f"kunde-{name}.conf"), os.path.join(CONF_D, f"kunde-{name}.htpasswd")


def name_pruefen(name):
    if not NAME_RE.match(name) or "--" in name:
        fehler("Name: 2–30 Zeichen a–z, 0–9 und Bindestrich (nicht am Anfang/Ende, nicht doppelt)")
    if name in RESERVIERT:
        fehler(f"«{name}» ist reserviert")


def ziel_aufloesen(ziel):
    """-> (Anzeige, proxy-URL oder None für den Platzhalter)"""
    if ziel == "platzhalter":
        return ziel, None
    m = re.fullmatch(r"host:(\d{1,5})", ziel)
    if m:
        gw = docker("network", "inspect", "proxy_default", "-f", "{{(index .IPAM.Config 0).Gateway}}").stdout.strip()
        return ziel, f"http://{gw}:{m.group(1)}"
    if ZIEL_RE.match(ziel):
        return ziel, ziel
    fehler("Ziel: platzhalter, host:<port> oder http://<name>:<port>")


def kopf(conf):
    werte = {}
    with open(conf, encoding="utf-8") as f:
        for zeile in f:
            m = re.match(r"^# (ziel|passwort|angelegt): (.*)$", zeile)
            if m:
                werte[m.group(1)] = m.group(2).strip()
            elif zeile.startswith("server"):
                break
    return werte


def inhalt(name, ziel_url, passwort):
    fqdn = f"{name}.{DOMAIN}"
    if ziel_url is None:
        with open(os.path.join(HIER, "platzhalter.html"), encoding="utf-8") as f:
            html = f.read()
        with open(ICON, "rb") as f:
            icon = "data:image/png;base64," + base64.b64encode(f.read()).decode()
        html = html.replace("{{FQDN}}", fqdn).replace("{{ICON}}", icon)
        if "'" in html or "$" in html or "\\" in html:
            fehler("platzhalter.html darf keine ' $ oder \\ enthalten (nginx-Zeichenkette)")
        html = " ".join(z.strip() for z in html.splitlines())
        return ("    location / {\n"
                "        default_type text/html;\n"
                "        charset utf-8;\n"
                "        add_header Cache-Control \"no-cache\";\n"
                f"        return 200 '{html}';\n"
                "    }")
    auth = (f"    auth_basic           \"{fqdn}\";\n"
            f"    auth_basic_user_file /etc/nginx/conf.d/kunde-{name}.htpasswd;\n") if passwort else ""
    weiter = ("        proxy_pass $ziel;\n"
              "        proxy_set_header Host $host;\n"
              "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n")
    return (f"    resolver 127.0.0.11 valid=30s ipv6=off;\n"
            f"    set $ziel {ziel_url};\n\n"
            f"{auth}\n"
            "    # App-Hülle (Manifest, Service Worker, Icons, Offline-Seite) ohne Passwort, enthält keine Daten\n"
            "    location ~ ^/(manifest\\.webmanifest|sw\\.js|offline\\.html|icons/[a-z0-9-]+\\.png)$ {\n"
            "        auth_basic off;\n"
            "        limit_req zone=home_api burst=20 nodelay;\n"
            f"{weiter}"
            "    }\n\n"
            "    location = /api/push {\n"
            "        limit_req zone=home_push burst=10 nodelay;\n"
            "        client_max_body_size 4k;\n"
            f"{weiter}"
            "    }\n\n"
            "    location / {\n"
            "        limit_req zone=home_api burst=20 nodelay;\n"
            f"{weiter}"
            "    }")


def schreiben(name, ziel, passwort, angelegt):
    """Konfiguration schreiben, mit nginx -t prüfen, bei Fehler den alten Stand wiederherstellen, dann neu laden"""
    conf, _ = pfade(name)
    anzeige, url = ziel_aufloesen(ziel)
    with open(os.path.join(HIER, "kunde.conf.vorlage"), encoding="utf-8") as f:
        text = f.read()
    for k, v in {"{{FQDN}}": f"{name}.{DOMAIN}", "{{NAME}}": name, "{{ZIEL}}": anzeige,
                 "{{PASSWORT}}": "an" if passwort else "aus", "{{ANGELEGT}}": angelegt,
                 "{{INHALT}}": inhalt(name, url, passwort)}.items():
        text = text.replace(k, v)
    alt = open(conf, encoding="utf-8").read() if os.path.exists(conf) else None
    fd, tmp = tempfile.mkstemp(dir=CONF_D, prefix=".kunde-", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, conf)
    test = docker("exec", "nginx", "nginx", "-t", pruefen=False)
    if test.returncode:
        if alt is None:
            os.remove(conf)
        else:
            with open(conf, "w", encoding="utf-8") as f:
                f.write(alt)
        fehler("nginx-Prüfung fehlgeschlagen, nichts geändert:\n" + test.stderr.strip()[-800:])
    docker("exec", "nginx", "nginx", "-s", "reload")


def passwort_setzen(name):
    _, htpasswd = pfade(name)
    pw = secrets.token_urlsafe(12)
    hash_ = subprocess.run(["openssl", "passwd", "-apr1", "-stdin"], input=pw, capture_output=True, text=True,
                           check=True).stdout.strip()
    fd = os.open(htpasswd + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)   # nginx (anderer Benutzer) liest mit
    with os.fdopen(fd, "w") as f:
        f.write(f"{BENUTZER}:{hash_}\n")
    os.replace(htpasswd + ".tmp", htpasswd)
    return pw


def zertifikat(fqdn):
    if docker("exec", "certbot", "test", "-f", f"/etc/letsencrypt/live/{fqdn}/fullchain.pem", pruefen=False).returncode == 0:
        return
    email = next((z.split("=", 1)[1].strip() for z in open(ENV, encoding="utf-8") if z.startswith("LETSENCRYPT_EMAIL=")), "")
    if not email:
        fehler("LETSENCRYPT_EMAIL fehlt in .env")
    print(f"Hole Zertifikat für {fqdn} …")
    docker("exec", "certbot", "certbot", "certonly", "--webroot", "-w", "/var/www/certbot", "--email", email,
           "--agree-tos", "--no-eff-email", "-n", "-d", fqdn)


def dns_pruefen(fqdn):
    ip = subprocess.run(["dig", "+short", "A", fqdn, "@1.1.1.1"], capture_output=True, text=True).stdout.split()
    try:
        eigene = open(os.path.expanduser("~/.local/state/domus/public_ip")).read().strip()
    except OSError:
        eigene = None
    if not ip or (eigene and ip[-1] != eigene):
        fehler(f"{fqdn} zeigt im DNS auf {ip[-1] if ip else 'nichts'}, nicht auf den NUC ({eigene}). Wildcard *.{DOMAIN} prüfen.")


def neu(name, ziel, passwort):
    name_pruefen(name)
    conf, _ = pfade(name)
    if os.path.exists(conf):
        fehler(f"{name}.{DOMAIN} gibt es schon (kunde.py liste)")
    ziel_aufloesen(ziel)
    fqdn = f"{name}.{DOMAIN}"
    dns_pruefen(fqdn)
    zertifikat(fqdn)
    pw = passwort_setzen(name) if passwort else None
    schreiben(name, ziel, passwort, date.today().isoformat())
    print(f"\n✓ https://{fqdn} ist eingerichtet (Ziel: {ziel})")
    if pw:
        print(f"  Benutzer: {BENUTZER}\n  Passwort: {pw}   ← jetzt notieren, wird nicht gespeichert")


def ziel_aendern(name, ziel):
    conf, _ = pfade(name)
    if not os.path.exists(conf):
        fehler(f"{name}.{DOMAIN} gibt es nicht")
    k = kopf(conf)
    schreiben(name, ziel, k.get("passwort") == "an", k.get("angelegt", "?"))
    print(f"✓ https://{name}.{DOMAIN} zeigt jetzt auf: {ziel}")


def passwort(name):
    conf, _ = pfade(name)
    if not os.path.exists(conf):
        fehler(f"{name}.{DOMAIN} gibt es nicht")
    k = kopf(conf)
    pw = passwort_setzen(name)
    if k.get("passwort") != "an":
        schreiben(name, k.get("ziel", "platzhalter"), True, k.get("angelegt", "?"))
    print(f"✓ Neues Passwort für https://{name}.{DOMAIN}\n  Benutzer: {BENUTZER}\n  Passwort: {pw}")


def liste():
    dateien = sorted(f for f in os.listdir(CONF_D) if f.startswith("kunde-") and f.endswith(".conf"))
    if not dateien:
        print("noch keine Kunden-Instanzen")
    for f in dateien:
        k = kopf(os.path.join(CONF_D, f))
        print(f"{f[6:-5] + '.' + DOMAIN:34} Ziel {k.get('ziel', '?'):24} Passwort {k.get('passwort', '?'):4} "
              f"seit {k.get('angelegt', '?')}")


def entfernen(name):
    name_pruefen(name)
    conf, htpasswd = pfade(name)
    if not os.path.exists(conf):
        fehler(f"{name}.{DOMAIN} gibt es nicht")
    os.remove(conf)
    if os.path.exists(htpasswd):
        os.remove(htpasswd)
    docker("exec", "nginx", "nginx", "-s", "reload")
    docker("exec", "certbot", "certbot", "delete", "-n", "--cert-name", f"{name}.{DOMAIN}", pruefen=False)
    print(f"✓ {name}.{DOMAIN} entfernt")


if __name__ == "__main__":
    a = sys.argv[1:]
    if len(a) >= 2 and a[0] == "neu":
        z = a[a.index("--ziel") + 1] if "--ziel" in a and a.index("--ziel") + 1 < len(a) else "platzhalter"
        neu(a[1], z, "--ohne-passwort" not in a)
    elif len(a) == 3 and a[0] == "ziel":
        ziel_aendern(a[1], a[2])
    elif len(a) == 2 and a[0] == "passwort":
        passwort(a[1])
    elif a == ["liste"]:
        liste()
    elif len(a) == 3 and a[0] == "entfernen" and a[2] == "--ja":
        entfernen(a[1])
    else:
        sys.exit(__doc__)
