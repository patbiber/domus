#!/usr/bin/env python3
"""DNS-Einträge (Typ A) von biber.solar über die OVH-API lesen und auf eine neue IP setzen.

Nur Python-Standardbibliothek. Zugangsdaten aus ~/domus/.env (nicht im Repo):
  OVH_ENDPOINT=https://eu.api.ovh.com/1.0
  OVH_APPLICATION_KEY=...   OVH_APPLICATION_SECRET=...   OVH_CONSUMER_KEY=...
Rechte des Tokens (nur diese): GET/PUT /domain/zone/biber.solar/record*, POST /domain/zone/biber.solar/refresh

Aufruf:
  ovh-dns.py                 zeigt die A-Einträge (nur lesen)
  ovh-dns.py --setzen <ip>   setzt alle überwachten A-Einträge auf <ip> (nur die abweichenden) und lädt die Zone neu
"""
import hashlib
import ipaddress
import json
import os
import sys
import time
import urllib.error
import urllib.request

ZONE = "biber.solar"
SUBDOMAINS = ["", "www", "training", "domus", "home", "test"]     # "" = biber.solar selbst
ENV = os.path.expanduser("~/domus/.env")


def lade_env():
    werte = {}
    try:
        with open(ENV, encoding="utf-8") as f:
            for zeile in f:
                if "=" in zeile and not zeile.lstrip().startswith("#"):
                    k, v = zeile.strip().split("=", 1)
                    werte[k] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    noetig = ["OVH_APPLICATION_KEY", "OVH_APPLICATION_SECRET", "OVH_CONSUMER_KEY"]
    fehlt = [k for k in noetig if not werte.get(k) or werte[k].startswith("<")]
    if fehlt:
        sys.exit(f"OVH-Zugangsdaten fehlen in {ENV}: {', '.join(fehlt)}")
    werte.setdefault("OVH_ENDPOINT", "https://eu.api.ovh.com/1.0")
    return werte


class Ovh:
    def __init__(self, cfg):
        self.ep = cfg["OVH_ENDPOINT"].rstrip("/")
        self.ak, self.as_, self.ck = cfg["OVH_APPLICATION_KEY"], cfg["OVH_APPLICATION_SECRET"], cfg["OVH_CONSUMER_KEY"]
        with urllib.request.urlopen(self.ep + "/auth/time", timeout=15) as r:
            self.delta = int(r.read()) - int(time.time())

    def call(self, methode, pfad, daten=None):
        url = self.ep + pfad
        body = json.dumps(daten) if daten is not None else ""
        ts = str(int(time.time()) + self.delta)
        sig = "$1$" + hashlib.sha1("+".join([self.as_, self.ck, methode, url, body, ts]).encode()).hexdigest()
        req = urllib.request.Request(url, data=body.encode() if body else None, method=methode, headers={
            "X-Ovh-Application": self.ak, "X-Ovh-Consumer": self.ck, "X-Ovh-Timestamp": ts,
            "X-Ovh-Signature": sig, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                inhalt = r.read()
                return json.loads(inhalt) if inhalt else None
        except urllib.error.HTTPError as e:
            sys.exit(f"OVH-API {methode} {pfad}: HTTP {e.code} {e.read().decode(errors='replace')[:300]}")

    def a_eintraege(self):
        out = []
        for sub in SUBDOMAINS:
            ids = self.call("GET", f"/domain/zone/{ZONE}/record?fieldType=A&subDomain={sub}")
            for i in ids:
                rec = self.call("GET", f"/domain/zone/{ZONE}/record/{i}")
                out.append(rec)
        return out


def name(rec):
    return f"{rec['subDomain']}.{ZONE}" if rec["subDomain"] else ZONE


def main():
    ovh = Ovh(lade_env())
    if len(sys.argv) == 1:
        for rec in ovh.a_eintraege():
            print(f"{name(rec):24} A {rec['target']:16} TTL {rec.get('ttl', 0)}  (id {rec['id']})")
        return
    if len(sys.argv) != 3 or sys.argv[1] != "--setzen":
        sys.exit(__doc__)
    neu = str(ipaddress.IPv4Address(sys.argv[2]))          # bricht bei ungültiger IP ab
    geaendert = []
    for rec in ovh.a_eintraege():
        if rec["target"] != neu:
            ovh.call("PUT", f"/domain/zone/{ZONE}/record/{rec['id']}", {"target": neu})
            geaendert.append(f"{name(rec)}: {rec['target']} -> {neu}")
    if geaendert:
        ovh.call("POST", f"/domain/zone/{ZONE}/refresh")
        print("OVH-DNS angepasst:\n  " + "\n  ".join(geaendert))
    else:
        print(f"OVH-DNS: alle überwachten A-Einträge zeigen bereits auf {neu}")


if __name__ == "__main__":
    main()
