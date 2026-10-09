#!/usr/bin/env python3
"""certbot-Hook für DNS-01 über die OVH-API (Wildcard-Zertifikat *.homi.solar, siehe wildcard-cert.sh).

Braucht nur die Rechte, die der domus-Token ohnehin hat: POST/DELETE /domain/zone/<zone>/record*,
POST /domain/zone/<zone>/refresh. (Das offizielle Plugin certbot-dns-ovh will zusätzlich alle Zonen auflisten.)
Läuft im certbot-Container (nur Python-Standardbibliothek), Zugangsdaten aus /etc/letsencrypt/ovh.ini.

  ovh-dns-hook.py auth      TXT _acme-challenge setzen (certbot: --manual-auth-hook), gibt die Eintrags-ID aus
  ovh-dns-hook.py cleanup   diesen Eintrag wieder löschen (certbot: --manual-cleanup-hook, liest CERTBOT_AUTH_OUTPUT)
"""
import hashlib
import json
import os
import sys
import time
import urllib.request

INI = os.environ.get("OVH_INI", "/etc/letsencrypt/ovh.ini")
ENDPUNKT = "https://eu.api.ovh.com/1.0"
WARTEN_S = 90                    # bis die OVH-Nameserver den neuen TXT-Eintrag ausliefern


def zugang():
    werte = {}
    with open(INI, encoding="utf-8") as f:
        for z in f:
            if "=" in z:
                k, v = z.split("=", 1)
                werte[k.strip()] = v.strip()
    return werte["dns_ovh_application_key"], werte["dns_ovh_application_secret"], werte["dns_ovh_consumer_key"]


def api(methode, pfad, daten=None):
    ak, as_, ck = zugang()
    with urllib.request.urlopen(ENDPUNKT + "/auth/time", timeout=15) as r:
        ts = str(int(r.read()))
    url = ENDPUNKT + pfad
    body = json.dumps(daten) if daten is not None else ""
    sig = "$1$" + hashlib.sha1("+".join([as_, ck, methode, url, body, ts]).encode()).hexdigest()
    req = urllib.request.Request(url, data=body.encode() if body else None, method=methode, headers={
        "X-Ovh-Application": ak, "X-Ovh-Consumer": ck, "X-Ovh-Timestamp": ts, "X-Ovh-Signature": sig,
        "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        inhalt = r.read()
        return json.loads(inhalt) if inhalt else None


def zone_und_name(domain):
    """homi.solar -> ("homi.solar", "_acme-challenge"); a.homi.solar -> ("homi.solar", "_acme-challenge.a")"""
    teile = domain.split(".")
    zone = ".".join(teile[-2:])
    rest = ".".join(teile[:-2])
    return zone, "_acme-challenge" + ("." + rest if rest else "")


if __name__ == "__main__":
    domain = os.environ["CERTBOT_DOMAIN"]
    zone, name = zone_und_name(domain)
    if sys.argv[1:] == ["auth"]:
        rec = api("POST", f"/domain/zone/{zone}/record",
                  {"fieldType": "TXT", "subDomain": name, "target": os.environ["CERTBOT_VALIDATION"], "ttl": 60})
        api("POST", f"/domain/zone/{zone}/refresh")
        time.sleep(WARTEN_S)
        print(rec["id"])                        # certbot reicht das als CERTBOT_AUTH_OUTPUT an cleanup weiter
    elif sys.argv[1:] == ["cleanup"]:
        rid = os.environ.get("CERTBOT_AUTH_OUTPUT", "").strip().splitlines()
        if rid and rid[-1].isdigit():
            api("DELETE", f"/domain/zone/{zone}/record/{rid[-1]}")
            api("POST", f"/domain/zone/{zone}/refresh")
    else:
        sys.exit(__doc__)
