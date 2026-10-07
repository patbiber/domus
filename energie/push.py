#!/usr/bin/env python3
"""Push-Nachrichten an die homi-App (Web Push mit VAPID, RFC 8291/8292). Läuft auf dem Host (python3-cryptography).

Abos legt die Seite über POST /api/push in data/push/abos/ ab (je Gerät eine Datei, mit gewählten Themen).
Der private VAPID-Schlüssel liegt nur in data/push/vapid_private.pem (nicht im Repo).

Aufruf:
  push.py schluessel                  VAPID-Schlüsselpaar erzeugen (nur falls noch keins da ist)
  push.py minuspreis [--test]         06:45 (Timer homi-push-minuspreis): heute negative Börsenpreise?
  push.py prognose                    19:00 (homi-prognose.service): Solarprognose für morgen, Minuspreise morgen
  push.py ausfall                     alle 5 min (Timer homi-push-ausfall): homi, Fronius oder Dienste ausgefallen?
  push.py test                        Testnachricht an die Geräte, die auf der Seite «Test» gedrückt haben
  push.py senden <thema> <titel> <text>   beliebige Nachricht an alle Geräte mit diesem Thema
  push.py liste                       angemeldete Geräte anzeigen
"""
import base64
import hashlib
import hmac
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

HIER = os.path.dirname(os.path.abspath(__file__))
PUSH = os.path.join(HIER, "data", "push")
API = "http://127.0.0.1:8099/api/"
KONTAKT = "mailto:domus@biber.solar"        # Pflichtangabe für die Push-Dienste (VAPID «sub»)
WT = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]


def b64(daten):
    return base64.urlsafe_b64encode(daten).rstrip(b"=").decode()


def unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def roh(oeffentlich):
    return oeffentlich.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


# ---------------------------------------------------------------- Schlüssel

def schluessel_erzeugen():
    pem = os.path.join(PUSH, "vapid_private.pem")
    for d in ("", "abos", "test"):
        os.makedirs(os.path.join(PUSH, d), exist_ok=True)
    if os.path.exists(pem):
        print("VAPID-Schlüssel existiert schon:", pem)
        return
    k = ec.generate_private_key(ec.SECP256R1())
    fd = os.open(pem, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()))
    with open(os.path.join(PUSH, "vapid_public.txt"), "w", encoding="ascii") as f:
        f.write(b64(roh(k.public_key())) + "\n")
    print("VAPID-Schlüssel erzeugt. Nicht löschen – sonst müssen sich alle Geräte neu anmelden.")


def vapid():
    with open(os.path.join(PUSH, "vapid_private.pem"), "rb") as f:
        return serialization.load_pem_private_key(f.read(), None)


def vapid_header(schluessel, endpoint):
    t = urlsplit(endpoint)
    kopf = b64(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    inhalt = b64(json.dumps({"aud": f"{t.scheme}://{t.netloc}", "exp": int(time.time()) + 12 * 3600,
                             "sub": KONTAKT}).encode())
    r, s = decode_dss_signature(schluessel.sign(f"{kopf}.{inhalt}".encode(), ec.ECDSA(hashes.SHA256())))
    jwt = f"{kopf}.{inhalt}.{b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    return f"vapid t={jwt}, k={b64(roh(schluessel.public_key()))}"


# ---------------------------------------------------------------- Verschlüsselung (RFC 8291, aes128gcm)

def hkdf(salz, ikm, info, laenge):
    prk = hmac.new(salz, ikm, hashlib.sha256).digest()
    return hmac.new(prk, info + b"\x01", hashlib.sha256).digest()[:laenge]


def verschluesseln(nachricht, p256dh, auth):
    ua_pub = unb64(p256dh)
    eph = ec.generate_private_key(ec.SECP256R1())
    as_pub = roh(eph.public_key())
    geteilt = eph.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub))
    ikm = hkdf(unb64(auth), geteilt, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    salz = os.urandom(16)
    cek = hkdf(salz, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = hkdf(salz, ikm, b"Content-Encoding: nonce\x00", 12)
    chiffre = AESGCM(cek).encrypt(nonce, nachricht + b"\x02", None)
    return salz + struct.pack(">IB", 4096, len(as_pub)) + as_pub + chiffre


# ---------------------------------------------------------------- Versand

def abos(thema=None):
    ordner = os.path.join(PUSH, "abos")
    for f in sorted(os.listdir(ordner)):
        if f.endswith(".json"):
            try:
                with open(os.path.join(ordner, f), encoding="utf-8") as h:
                    a = json.load(h)
            except (OSError, ValueError):
                continue
            if thema is None or thema in a.get("themen", []):
                yield f, a


def senden_an(datei, abo, nachricht, schluessel, ttl=6 * 3600):
    """True = zugestellt; abgelaufene Abos (404/410) werden gelöscht"""
    daten = verschluesseln(json.dumps(nachricht, ensure_ascii=False).encode(), abo["keys"]["p256dh"], abo["keys"]["auth"])
    req = urllib.request.Request(abo["endpoint"], data=daten, method="POST", headers={
        "Authorization": vapid_header(schluessel, abo["endpoint"]), "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream", "TTL": str(ttl),
        "Urgency": "high" if nachricht.get("thema") in ("minuspreis", "ausfall", "test") else "normal"})
    try:
        with urllib.request.urlopen(req, timeout=20):
            return True
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            os.remove(os.path.join(PUSH, "abos", datei))
            print(f"  {datei}: Abo abgelaufen (HTTP {e.code}) – gelöscht")
        else:
            print(f"  {datei}: HTTP {e.code} {e.read()[:200]!r}")
    except OSError as e:
        print(f"  {datei}: {e}")
    return False


def an_alle(thema, titel, text, url="/", ttl=6 * 3600):
    schluessel = vapid()
    ok = gesamt = 0
    for datei, abo in abos(thema):
        gesamt += 1
        ok += senden_an(datei, abo, {"thema": thema, "titel": titel, "text": text, "url": url}, schluessel, ttl)
    print(f"Push «{titel}» an {ok}/{gesamt} Geräte ({thema})")


def api(pfad):
    with urllib.request.urlopen(API + pfad, timeout=30) as r:
        return json.load(r)


# ---------------------------------------------------------------- Anlässe

def minuspreis(test=False):
    h = api("einspeisung").get("heute")
    if test:
        h = {"minuspreis_bloecke": ["11–15 Uhr"], "minuspreis_stunden": [11, 12, 13, 14],
             "ueberschuss_bei_minuspreis_kwh": 18.4,
             "stunden": [{"stunde": s, "preis_rp": p} for s, p in ((11, -2), (12, -6), (13, -8), (14, -5))]}
    if not h or not h.get("minuspreis_stunden"):
        print("heute keine negativen Preise – kein Push")
        return
    tief = min(x["preis_rp"] for x in h["stunden"] if x["stunde"] in h["minuspreis_stunden"])
    an_alle("minuspreis", ("TEST – " if test else "") + f"⚠️ Heute {' und '.join(h['minuspreis_bloecke'])} Minuspreise",
            f"Börse bis {tief:.1f} Rp/kWh. Rund {h['ueberschuss_bei_minuspreis_kwh']:.0f} kWh Überschuss: "
            "jetzt Waschmaschine, Boiler & Co. einplanen.", ttl=8 * 3600)


def prognose():
    p = api("prognose")
    if "morgen" not in p:
        print("Prognose noch nicht verfügbar")
        return
    m = p["morgen"]
    symbol = "☀️" if m["kwh"] >= 20 else "⛅" if m["kwh"] >= 8 else "☁️"
    f = m.get("bestes_fenster")
    text = f"Erwartet {m['kwh']:.1f} kWh" + (f", beste Zeit {f['von']}–{f['bis']} Uhr." if f else ".")
    try:
        mo = api("einspeisung").get("morgen") or {}
        if mo.get("minuspreis_stunden"):
            text += f" Achtung: {' und '.join(mo['minuspreis_bloecke'])} negative Strompreise."
    except (OSError, ValueError):
        pass
    tag_ = date.fromisoformat(m["datum"])
    an_alle("prognose", f"{symbol} Morgen ({WT[tag_.weekday()]}): {m['kwh']:.0f} kWh Sonne", text, ttl=12 * 3600)


def ausfall():
    """Meldet einen Ausfall erst, wenn er 15 min (Fronius tagsüber: 30 min) anhält, und dann die Erholung."""
    datei = os.path.join(PUSH, "ausfall.json")
    try:
        with open(datei, encoding="utf-8") as f:
            zustand = json.load(f)
    except (OSError, ValueError):
        zustand = {}
    jetzt = time.time()
    probleme = {}
    try:
        st = api("status")
        stunde = datetime.now().hour
        if not st.get("fronius_ok") and 9 <= stunde < 16:           # nachts schläft der Wechselrichter
            probleme["fronius"] = ("Der Wechselrichter", "Der Wechselrichter (Fronius) antwortet nicht.")
        for dienst, ok in ((st.get("server") or {}).get("dienste") or {}).items():
            if ok is False:
                probleme["dienst_" + dienst] = (f"Der Dienst «{dienst}»", f"Der Dienst «{dienst}» läuft nicht.")
    except (OSError, ValueError):
        probleme["homi"] = ("homi", "homi selbst antwortet nicht (Container energie).")
    neu = {}
    for k, (wer, text) in probleme.items():
        seit = zustand.get(k, {}).get("seit", jetzt)
        gemeldet = zustand.get(k, {}).get("gemeldet", False)
        if not gemeldet and jetzt - seit >= (30 if k == "fronius" else 15) * 60:
            an_alle("ausfall", "🚨 homi: Störung", text, ttl=3600)
            gemeldet = True
        neu[k] = {"seit": seit, "gemeldet": gemeldet, "wer": wer}
    for k, alt in zustand.items():
        if k not in probleme and alt.get("gemeldet"):
            dauer = round((jetzt - alt["seit"]) / 60)
            an_alle("ausfall", "✅ homi: wieder in Ordnung", f"{alt['wer']} läuft wieder (Störung dauerte {dauer} min).", ttl=3600)
    tmp = datei + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(neu, f)
    os.replace(tmp, datei)


def test():
    ordner = os.path.join(PUSH, "test")
    schluessel = vapid()
    for f in os.listdir(ordner):
        os.remove(os.path.join(ordner, f))
        pfad = os.path.join(PUSH, "abos", f)
        if not f.endswith(".json") or not os.path.exists(pfad):
            continue
        with open(pfad, encoding="utf-8") as h:
            abo = json.load(h)
        ok = senden_an(f, abo, {"thema": "test", "titel": "👋 Hallo von homi",
                                "text": "Push funktioniert. Ich melde mich bei Minuspreisen, mit der Prognose "
                                        "und wenn etwas klemmt."}, schluessel, ttl=600)
        print(f"Testnachricht an {f}: {'ok' if ok else 'fehlgeschlagen'}")


def liste():
    for f, a in abos():
        print(f"{f[:12]}  seit {a.get('seit', '?')}  {','.join(a.get('themen', []))}  "
              f"{urlsplit(a['endpoint']).hostname}  {a.get('geraet', '')[:60]}")


if __name__ == "__main__":
    befehl = sys.argv[1] if len(sys.argv) > 1 else ""
    if befehl == "schluessel":
        schluessel_erzeugen()
    elif befehl == "minuspreis":
        minuspreis("--test" in sys.argv)
    elif befehl == "prognose":
        prognose()
    elif befehl == "ausfall":
        ausfall()
    elif befehl == "test":
        test()
    elif befehl == "senden" and len(sys.argv) == 5:
        an_alle(sys.argv[2], sys.argv[3], sys.argv[4])
    elif befehl == "liste":
        liste()
    else:
        sys.exit(__doc__)
