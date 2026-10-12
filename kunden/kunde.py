#!/usr/bin/env python3
"""Kunden-Instanzen von homi unter <name>.homi.solar einrichten: nginx-Konfiguration, Zertifikat, Zugang.

Der Wildcard-Eintrag *.homi.solar zeigt auf den NUC. Zertifikat: das Wildcard-Zertifikat *.homi.solar
(proxy/wildcard-cert.sh), sonst ein Einzelzertifikat per HTTP-01 über den Standardserver (proxy/conf.d/00-default.conf). Erzeugt werden (nur auf dem NUC, nicht im Repo):
  proxy/conf.d/kunde-<name>.conf       aus kunden/kunde.conf.vorlage
  proxy/conf.d/kunde-<name>.htpasswd   nur bei Zugang «passwort»: Benutzer «homi»
  login/data/instanzen/<name>.json     bei Zugang «login»: wer sich anmelden darf

Aufruf:
  kunde.py neu <name> [--ziel <ziel>] [--email <adresse>] [--zugang login|passwort|offen]
  kunde.py ziel <name> <ziel>          wohin die Adresse zeigt (siehe unten)
  kunde.py zugang <name> login|passwort|offen
  kunde.py benutzer <name> [+adresse …] [-adresse …]   Anmelde-Adressen zeigen, hinzufügen, entfernen
  kunde.py passwort <name>             neues Browser-Passwort (stellt auf Zugang «passwort» um)
  kunde.py liste
  kunde.py entfernen <name> --ja       Konfiguration, Zugang und Zertifikat löschen

Zugang:
  login     Anmeldung mit Code per Mail oder Passkey (Face ID/Fingerabdruck), 180 Tage angemeldet (Standard)
  passwort  Browser-Passwortfenster (Basic Auth), Benutzer «homi»
  offen     ohne Anmeldung (nur für Musterseiten wie beispiel.homi.solar)

Ziel:
  platzhalter            Seite «Hier wohnt bald ein homi» (Standard)
  host:<port>            homi-Dienst auf dem NUC selbst (z. B. host:8099)
  http://<name>:<port>   Container im Netz proxy_default oder Adresse im LAN/WireGuard

Name: 2–30 Zeichen a–z, 0–9, Bindestrich. Tipp: neutrale Namen (sonnenhof statt mueller-staefa) – Zertifikate
werden öffentlich protokolliert (Certificate Transparency), der Name ist also für alle sichtbar.
"""
import base64
import json
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
LOGIN_DATEN = os.path.join(HIER, "..", "login", "data")
LOGIN_PORT = 8098                                # homi-login (Host-Dienst) am Gateway des Docker-Netzes
ZUGAENGE = ("login", "passwort", "offen")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[a-z]{2,24}$", re.I)
BENUTZER = "homi"
WILDCARD = f"wildcard.{DOMAIN}"                  # Zertifikatsname von proxy/wildcard-cert.sh
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
        return ziel, f"http://{gateway()}:{m.group(1)}"
    if ZIEL_RE.match(ziel):
        return ziel, ziel
    fehler("Ziel: platzhalter, host:<port> oder http://<name>:<port>")


def kopf(conf):
    werte = {}
    with open(conf, encoding="utf-8") as f:
        for zeile in f:
            m = re.match(r"^# (ziel|passwort|zugang|angelegt): (.*)$", zeile)
            if m:
                werte[m.group(1)] = m.group(2).strip()
            elif zeile.startswith("server"):
                break
    if "zugang" not in werte:                    # ältere Konfigurationen: «passwort: an|aus»
        werte["zugang"] = "passwort" if werte.get("passwort") == "an" else "offen"
    return werte


def gateway():
    return docker("network", "inspect", "proxy_default", "-f", "{{(index .IPAM.Config 0).Gateway}}").stdout.strip()


def inhalt(name, ziel_url, zugang):
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
            f"    auth_basic_user_file /etc/nginx/conf.d/kunde-{name}.htpasswd;\n") if zugang == "passwort" else ""
    weiter = ("        proxy_pass $ziel;\n"
              "        proxy_set_header Host $host;\n"
              "        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n")
    pruefen = ("        auth_request /_auth;\n"
               "        error_page 401 = @anmelden;\n") if zugang == "login" else ""
    login = ""
    if zugang == "login":
        lg = f"http://{gateway()}:{LOGIN_PORT}"
        login = (
            "    # Anmeldung über homi-login (Host-Dienst, nur im Docker-Netz erreichbar)\n"
            "    location /login/ {\n"
            "        limit_req zone=home_login burst=20 nodelay;\n"
            "        client_max_body_size 16k;\n"
            f"        proxy_pass {lg};\n"
            "        proxy_set_header Host $host;\n"
            f"        proxy_set_header X-Homi-Instanz {name};\n"
            "        proxy_set_header X-Forwarded-For $remote_addr;\n"
            "    }\n\n"
            "    location = /_auth {\n"
            "        internal;\n"
            f"        proxy_pass {lg}/pruefen;\n"
            "        proxy_pass_request_body off;\n"
            "        proxy_set_header Content-Length \"\";\n"
            "        proxy_set_header Host $host;\n"
            f"        proxy_set_header X-Homi-Instanz {name};\n"
            "    }\n\n"
            "    # nicht angemeldet: Daten-Abfragen bekommen 401, Seiten leiten zur Anmeldung\n"
            "    location @anmelden {\n"
            "        if ($uri ~ ^/api/) { return 401; }\n"
            "        return 302 /login/?ziel=$uri;\n"
            "    }\n\n")
    return (f"    resolver 127.0.0.11 valid=30s ipv6=off;\n"
            f"    set $ziel {ziel_url};\n\n"
            f"{auth}\n"
            f"{login}"
            "    # App-Hülle (Manifest, Service Worker, Icons, Offline-Seite) ohne Anmeldung, enthält keine Daten\n"
            "    location ~ ^/(manifest\\.webmanifest|sw\\.js|offline\\.html|icons/[a-z0-9-]+\\.(png|svg))$ {\n"
            "        auth_basic off;\n"
            "        limit_req zone=home_api burst=60 nodelay;\n"
            f"{weiter}"
            "    }\n\n"
            "    location = /api/push {\n"
            f"{pruefen}"
            "        limit_req zone=home_push burst=10 nodelay;\n"
            "        client_max_body_size 4k;\n"
            f"{weiter}"
            "    }\n\n"
            "    location / {\n"
            f"{pruefen}"
            "        limit_req zone=home_api burst=60 nodelay;\n"
            f"{weiter}"
            "    }")


def schreiben(name, ziel, zugang, angelegt):
    """Konfiguration schreiben, mit nginx -t prüfen, bei Fehler den alten Stand wiederherstellen, dann neu laden"""
    conf, _ = pfade(name)
    anzeige, url = ziel_aufloesen(ziel)
    with open(os.path.join(HIER, "kunde.conf.vorlage"), encoding="utf-8") as f:
        text = f.read()
    for k, v in {"{{FQDN}}": f"{name}.{DOMAIN}", "{{NAME}}": name, "{{ZERTIFIKAT}}": zert_name(f"{name}.{DOMAIN}"), "{{ZIEL}}": anzeige,
                 "{{ZUGANG}}": zugang, "{{ANGELEGT}}": angelegt,
                 "{{INHALT}}": inhalt(name, url, zugang)}.items():
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


def zert_da(name):
    return docker("exec", "certbot", "test", "-f", f"/etc/letsencrypt/live/{name}/fullchain.pem", pruefen=False).returncode == 0


def zert_name(fqdn):
    """Wildcard-Zertifikat, falls vorhanden – dann taucht der Kundenname in keinem öffentlichen Zertifikats-Log auf"""
    return WILDCARD if zert_da(WILDCARD) else fqdn


def zertifikat(fqdn):
    if zert_da(WILDCARD) or zert_da(fqdn):
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


def benutzer_datei(name):
    return os.path.join(LOGIN_DATEN, "instanzen", name + ".json")


def benutzer_lesen(name):
    try:
        with open(benutzer_datei(name), encoding="utf-8") as f:
            return json.load(f).get("benutzer", [])
    except (OSError, ValueError):
        return []


def benutzer_schreiben(name, liste):
    os.makedirs(os.path.dirname(benutzer_datei(name)), mode=0o700, exist_ok=True)
    tmp = benutzer_datei(name) + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"benutzer": sorted(set(liste))}, f, ensure_ascii=False)
    os.replace(tmp, benutzer_datei(name))


def neu(name, ziel, zugang, email=None):
    name_pruefen(name)
    conf, _ = pfade(name)
    if os.path.exists(conf):
        fehler(f"{name}.{DOMAIN} gibt es schon (kunde.py liste)")
    if zugang not in ZUGAENGE:
        fehler(f"Zugang: {', '.join(ZUGAENGE)}")
    if email and not EMAIL_RE.match(email):
        fehler(f"ungültige Adresse: {email}")
    ziel_aufloesen(ziel)
    fqdn = f"{name}.{DOMAIN}"
    dns_pruefen(fqdn)
    zertifikat(fqdn)
    pw = passwort_setzen(name) if zugang == "passwort" else None
    if email:
        benutzer_schreiben(name, benutzer_lesen(name) + [email.lower()])
    schreiben(name, ziel, zugang, date.today().isoformat())
    print(f"\n✓ https://{fqdn} ist eingerichtet (Ziel: {ziel}, Zugang: {zugang})")
    if pw:
        print(f"  Benutzer: {BENUTZER}\n  Passwort: {pw}   ← jetzt notieren, wird nicht gespeichert")
    if zugang == "login":
        print(f"  Anmelden können: {', '.join(benutzer_lesen(name)) or 'noch niemand – kunde.py benutzer ' + name + ' +adresse'}")


def vorhanden(name):
    conf, _ = pfade(name)
    if not os.path.exists(conf):
        fehler(f"{name}.{DOMAIN} gibt es nicht")
    return kopf(conf)


def ziel_aendern(name, ziel):
    k = vorhanden(name)
    schreiben(name, ziel, k["zugang"], k.get("angelegt", "?"))
    print(f"✓ https://{name}.{DOMAIN} zeigt jetzt auf: {ziel}")


def zugang_aendern(name, zugang):
    k = vorhanden(name)
    if zugang not in ZUGAENGE:
        fehler(f"Zugang: {', '.join(ZUGAENGE)}")
    if zugang == "passwort" and not os.path.exists(pfade(name)[1]):
        fehler("noch kein Passwort – kunde.py passwort " + name)
    schreiben(name, k.get("ziel", "platzhalter"), zugang, k.get("angelegt", "?"))
    print(f"✓ https://{name}.{DOMAIN}: Zugang {zugang}")
    if zugang == "login" and not benutzer_lesen(name):
        print(f"  Achtung: noch niemand darf sich anmelden – kunde.py benutzer {name} +adresse")


def benutzer(name, aenderungen):
    vorhanden(name)
    liste = benutzer_lesen(name)
    for x in aenderungen:
        adr = x[1:].lower()
        if x[:1] not in "+-" or not EMAIL_RE.match(adr):
            fehler(f"«{x}»: +adresse zum Hinzufügen, -adresse zum Entfernen")
        liste = liste + [adr] if x[0] == "+" else [e for e in liste if e != adr]
    if aenderungen:
        benutzer_schreiben(name, liste)
    print(f"{name}.{DOMAIN}: " + (", ".join(sorted(set(liste))) or "niemand"))


def passwort(name):
    k = vorhanden(name)
    pw = passwort_setzen(name)
    if k["zugang"] != "passwort":
        schreiben(name, k.get("ziel", "platzhalter"), "passwort", k.get("angelegt", "?"))
    print(f"✓ Neues Passwort für https://{name}.{DOMAIN}\n  Benutzer: {BENUTZER}\n  Passwort: {pw}")


def liste():
    dateien = sorted(f for f in os.listdir(CONF_D) if f.startswith("kunde-") and f.endswith(".conf"))
    if not dateien:
        print("noch keine Kunden-Instanzen")
    for f in dateien:
        k = kopf(os.path.join(CONF_D, f))
        name = f[6:-5]
        wer = f" ({', '.join(benutzer_lesen(name)) or 'niemand'})" if k["zugang"] == "login" else ""
        print(f"{name + '.' + DOMAIN:30} Ziel {k.get('ziel', '?'):20} Zugang {k['zugang']}{wer}  seit {k.get('angelegt', '?')}")


def entfernen(name):
    name_pruefen(name)
    conf, htpasswd = pfade(name)
    if not os.path.exists(conf):
        fehler(f"{name}.{DOMAIN} gibt es nicht")
    os.remove(conf)
    if os.path.exists(htpasswd):
        os.remove(htpasswd)
    for p in (benutzer_datei(name), os.path.join(LOGIN_DATEN, "passkeys", name + ".json")):
        if os.path.exists(p):
            os.remove(p)
    docker("exec", "nginx", "nginx", "-s", "reload")
    if zert_da(f"{name}.{DOMAIN}"):
        docker("exec", "certbot", "certbot", "delete", "-n", "--cert-name", f"{name}.{DOMAIN}", pruefen=False)
    print(f"✓ {name}.{DOMAIN} entfernt")


if __name__ == "__main__":
    a = sys.argv[1:]
    wert = lambda opt, std=None: a[a.index(opt) + 1] if opt in a and a.index(opt) + 1 < len(a) else std
    if len(a) >= 2 and a[0] == "neu":
        neu(a[1], wert("--ziel", "platzhalter"), "offen" if "--ohne-passwort" in a else wert("--zugang", "login"), wert("--email"))
    elif len(a) == 3 and a[0] == "ziel":
        ziel_aendern(a[1], a[2])
    elif len(a) == 3 and a[0] == "zugang":
        zugang_aendern(a[1], a[2])
    elif len(a) >= 2 and a[0] == "benutzer":
        benutzer(a[1], a[2:])
    elif len(a) == 2 and a[0] == "passwort":
        passwort(a[1])
    elif a == ["liste"]:
        liste()
    elif len(a) == 3 and a[0] == "entfernen" and a[2] == "--ja":
        entfernen(a[1])
    else:
        sys.exit(__doc__)
