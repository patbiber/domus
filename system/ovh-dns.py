#!/usr/bin/env python3
"""DNS-Einträge (Typ A) von biber.solar und homi.solar über die OVH-API lesen und auf eine neue IP setzen.

Nur Python-Standardbibliothek. Zugangsdaten aus ~/domus/.env (nicht im Repo):
  OVH_ENDPOINT=https://eu.api.ovh.com/1.0
  OVH_APPLICATION_KEY=...   OVH_APPLICATION_SECRET=...   OVH_CONSUMER_KEY=...
Rechte des Tokens (nur diese, je Zone): GET/PUT /domain/zone/<zone>/record*, POST /domain/zone/<zone>/refresh
Fehlen die Rechte für eine Zone, wird sie übersprungen und gemeldet (Exit-Code 2); die anderen Zonen laufen weiter.

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

ZONEN = {                                       # "" = die Domain selbst, "*" = Wildcard
    "biber.solar": ["", "www", "training", "domus", "home", "test", "energy", "*"],
    "homi.solar": ["", "www", "*"],             # Kunden-Instanzen <name>.homi.solar laufen über den Wildcard
}
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
            raise OvhFehler(f"OVH-API {methode} {pfad}: HTTP {e.code} {e.read().decode(errors='replace')[:300]}")

    def a_eintraege(self, zone):
        out = []
        for sub in ZONEN[zone]:
            ids = self.call("GET", f"/domain/zone/{zone}/record?fieldType=A&subDomain={sub}")
            for i in ids:
                out.append(self.call("GET", f"/domain/zone/{zone}/record/{i}"))
        return out


class OvhFehler(Exception):
    pass


def name(rec):
    return f"{rec['subDomain']}.{rec['zone']}" if rec["subDomain"] else rec["zone"]


def main():
    ovh = Ovh(lade_env())
    setzen = len(sys.argv) == 3 and sys.argv[1] == "--setzen"
    if len(sys.argv) != 1 and not setzen:
        sys.exit(__doc__)
    neu = str(ipaddress.IPv4Address(sys.argv[2])) if setzen else None    # bricht bei ungültiger IP ab
    probleme = []
    for zone in ZONEN:
        try:
            eintraege = ovh.a_eintraege(zone)
            if not setzen:
                for rec in eintraege:
                    print(f"{name(rec):24} A {rec['target']:16} TTL {rec.get('ttl', 0)}  (id {rec['id']})")
                continue
            geaendert = []
            for rec in eintraege:
                if rec["target"] != neu:
                    ovh.call("PUT", f"/domain/zone/{zone}/record/{rec['id']}", {"target": neu})
                    geaendert.append(f"{name(rec)}: {rec['target']} -> {neu}")
            if geaendert:
                ovh.call("POST", f"/domain/zone/{zone}/refresh")
                print(f"OVH-DNS {zone} angepasst:\n  " + "\n  ".join(geaendert))
            else:
                print(f"OVH-DNS {zone}: alle überwachten A-Einträge zeigen bereits auf {neu}")
        except OvhFehler as e:
            probleme.append(zone)
            print(f"OVH-DNS {zone}: NICHT möglich – {e}")
    if probleme:
        print(f"Bitte bei OVH von Hand prüfen: {', '.join(probleme)} (Rechte des API-Tokens?)")
        sys.exit(2)


if __name__ == "__main__":
    main()
