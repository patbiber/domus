#!/usr/bin/env python3
"""homi-login: Anmeldung für die Kunden-Instanzen <name>.homi.solar (statt Browser-Passwortfenster).

Läuft auf dem Host (systemd-User-Dienst homi-login, python3-cryptography), lauscht nur am Docker-Netz proxy_default
(LOGIN_BIND, Standard 172.18.0.1:8098) – erreichbar für nginx, nicht aus dem LAN oder Internet. nginx setzt pro Instanz
den Kopf X-Homi-Instanz und fragt für jede Anfrage per auth_request /pruefen.

Anmelden: E-Mail → Mail mit 6-stelligem Code und Link (Code, weil installierte Apps auf dem iPhone eigene Cookies
haben) → Sitzung 180 Tage (Cookie __Host-homi, HttpOnly, Secure, SameSite=Lax). Danach optional Passkey
(Face ID / Fingerabdruck, WebAuthn ES256) für die Anmeldung mit einem Tipp.

Daten (nicht im Repo, nur Besitzer lesbar) in login/data/:
  instanzen/<name>.json   {"benutzer": ["mail@…"]}      (gepflegt mit kunden/kunde.py benutzer)
  sitzungen/<sha256>.json {"instanz", "email", "erstellt", "zuletzt", "geraet"}
  passkeys/<name>.json    [{"id", "x", "y", "zaehler", "email", "name", "erstellt", "zuletzt"}]

Wege (alle unter /login/ ausser /pruefen):
  GET  /pruefen                    200 = angemeldet, 401 = nicht (für nginx auth_request)
  GET  /login/                     Anmeldeseite
  POST /login/code                 {email}            Code + Link per Mail (Antwort immer gleich)
  POST /login/code-pruefen         {email, code}      → Sitzung
  GET  /login/link?t=…             Link aus der Mail → Seite mit Knopf (Mail-Scanner öffnen Links, klicken aber nicht)
  POST /login/link-pruefen         {t}                → Sitzung
  GET  /login/ich                  angemeldete Adresse und Passkeys
  POST /login/abmelden
  GET  /login/passkey/optionen     POST /login/passkey/anmelden {credential}
  GET  /login/passkey/neu-optionen POST /login/passkey/neu {credential, name}   POST /login/passkey/entfernen {id}
"""
import base64
import hashlib
import json
import os
import re
import secrets
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

HIER = os.path.dirname(os.path.abspath(__file__))
DATEN = os.path.join(HIER, "data")
sys.path.insert(0, os.path.join(HIER, "..", "energy"))
from mailer import senden as _senden  # noqa: E402  (msmtp, Absender domus@biber.solar)

MAIL_DATEI = os.environ.get("LOGIN_MAIL_DATEI")     # nur für Tests: Mails in eine Datei statt versenden


def senden(an, betreff, text, **kw):
    if MAIL_DATEI:
        with open(MAIL_DATEI, "a", encoding="utf-8") as f:
            f.write(json.dumps({"an": an, "betreff": betreff, "text": text}, ensure_ascii=False) + "\n")
        return
    _senden(an, betreff, text, **kw)

BIND = os.environ.get("LOGIN_BIND", "172.18.0.1:8098")
COOKIE = "__Host-homi"
SITZUNG_S = 180 * 86400
CODE_S, CODE_VERSUCHE = 15 * 60, 5
INSTANZ_RE = re.compile(r"^[a-z0-9-]{2,30}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[a-z]{2,24}$", re.I)
sperre = threading.Lock()
codes = {}          # (instanz, email) -> {code_h, link_h, ablauf, versuche}
links = {}          # sha256(link) -> (instanz, email, ablauf)
herausforderungen = {}   # challenge -> {instanz, zweck, email, ablauf}
mails_gesendet = {}      # (instanz, email) -> [zeitpunkte]


def b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def h(s):
    return hashlib.sha256(s.encode()).hexdigest()


def lesen(pfad, standard):
    try:
        with open(pfad, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return standard


def schreiben(pfad, inhalt):
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    tmp = pfad + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(inhalt, f, ensure_ascii=False)
    os.replace(tmp, pfad)


def benutzer(instanz):
    return [e.lower() for e in lesen(os.path.join(DATEN, "instanzen", instanz + ".json"), {}).get("benutzer", [])]


# ---------------------------------------------------------------- Sitzungen

def sitzung_neu(instanz, email, geraet):
    token = secrets.token_urlsafe(32)
    jetzt = time.time()
    schreiben(os.path.join(DATEN, "sitzungen", h(token) + ".json"),
              {"instanz": instanz, "email": email, "erstellt": jetzt, "zuletzt": jetzt, "geraet": geraet[:120]})
    return token


def sitzung(instanz, cookie_kopf):
    """-> (pfad, daten) der gültigen Sitzung dieser Instanz oder (None, None)"""
    m = re.search(rf"(?:^|;\s*){re.escape(COOKIE)}=([A-Za-z0-9_-]{{20,100}})", cookie_kopf or "")
    if not m:
        return None, None
    pfad = os.path.join(DATEN, "sitzungen", h(m.group(1)) + ".json")
    s = lesen(pfad, None)
    if not s or s.get("instanz") != instanz or time.time() - s.get("zuletzt", 0) > SITZUNG_S:
        return None, None
    if s["email"] not in benutzer(instanz):          # Zugang entzogen
        return None, None
    if time.time() - s["zuletzt"] > 3600:            # gleitend verlängern, höchstens stündlich schreiben
        s["zuletzt"] = time.time()
        schreiben(pfad, s)
    return pfad, s


def cookie(token, max_age=SITZUNG_S):
    return f"{COOKIE}={token}; Path=/; Max-Age={max_age}; Secure; HttpOnly; SameSite=Lax"


# ---------------------------------------------------------------- WebAuthn (Passkeys, nur ES256)

def cbor(d, i=0):
    """Minimaler CBOR-Decoder (genügt für attestationObject und COSE-Schlüssel)."""
    ib = d[i]; typ, info = ib >> 5, ib & 31; i += 1
    if info < 24:
        wert = info
    elif info in (24, 25, 26, 27):
        n = 1 << (info - 24)
        wert = int.from_bytes(d[i:i + n], "big"); i += n
    else:
        raise ValueError("CBOR: unbestimmte Länge nicht unterstützt")
    if typ == 0:
        return wert, i
    if typ == 1:
        return -1 - wert, i
    if typ in (2, 3):
        roh = d[i:i + wert]
        return (roh if typ == 2 else roh.decode()), i + wert
    if typ == 4:
        out = []
        for _ in range(wert):
            x, i = cbor(d, i); out.append(x)
        return out, i
    if typ == 5:
        out = {}
        for _ in range(wert):
            k, i = cbor(d, i); v, i = cbor(d, i); out[k] = v
        return out, i
    if typ == 7:
        return {20: False, 21: True, 22: None}.get(info), i
    raise ValueError("CBOR: Typ nicht unterstützt")


def client_data(roh_b64, typ, host):
    roh = unb64(roh_b64)
    cd = json.loads(roh)
    if cd.get("type") != typ or cd.get("origin") != f"https://{host}":
        raise ValueError("clientData passt nicht")
    with sperre:
        hf = herausforderungen.pop(cd.get("challenge", ""), None)
    if not hf or hf["ablauf"] < time.time():
        raise ValueError("Herausforderung ungültig oder abgelaufen")
    return roh, hf


def auth_daten(ad, host):
    if ad[:32] != hashlib.sha256(host.encode()).digest():
        raise ValueError("rpId passt nicht")
    if not ad[32] & 0x01:
        raise ValueError("Benutzer nicht anwesend")
    return ad[32], int.from_bytes(ad[33:37], "big")


def herausforderung(instanz, zweck, email=None):
    c = b64(secrets.token_bytes(32))
    with sperre:
        jetzt = time.time()
        for k in [k for k, v in herausforderungen.items() if v["ablauf"] < jetzt]:
            herausforderungen.pop(k)
        herausforderungen[c] = {"instanz": instanz, "zweck": zweck, "email": email, "ablauf": jetzt + 300}
    return c


def passkeys(instanz):
    return lesen(os.path.join(DATEN, "passkeys", instanz + ".json"), [])


# ---------------------------------------------------------------- HTTP

class Handler(BaseHTTPRequestHandler):
    server_version = "homi-login"
    sys_version = ""

    def log_message(self, *a):
        pass

    # --- Hilfen
    @property
    def instanz(self):
        i = self.headers.get("X-Homi-Instanz", "")
        return i if INSTANZ_RE.match(i) else None

    @property
    def host(self):
        return (self.headers.get("Host") or "").split(":")[0].lower()

    def antwort(self, code, daten=None, kopf=None):
        body = json.dumps(daten if daten is not None else {}, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        for k, v in (kopf or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def json_lesen(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not 0 < n <= 16384:
            raise ValueError("Grösse")
        d = json.loads(self.rfile.read(n))
        if not isinstance(d, dict):
            raise ValueError("Format")
        return d

    def gleicher_ursprung(self):
        return self.headers.get("Origin") == f"https://{self.host}"

    def angemeldet(self):
        return sitzung(self.instanz, self.headers.get("Cookie"))

    def anmelden(self, email):
        token = sitzung_neu(self.instanz, email, self.headers.get("User-Agent", ""))
        self.antwort(200, {"ok": True}, {"Set-Cookie": cookie(token)})

    # --- GET
    def do_GET(self):
        if not self.instanz:
            return self.antwort(400, {"fehler": "Instanz fehlt"})
        teile = urlsplit(self.path)
        pfad, q = teile.path, {k: v[0] for k, v in parse_qs(teile.query).items()}
        if pfad == "/pruefen":
            return self.antwort(200 if self.angemeldet()[1] else 401)
        if pfad in ("/login", "/login/"):
            with open(os.path.join(HIER, "www", "login.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)
        if pfad == "/login/link":
            t = q.get("t", "")
            self.send_response(302)
            self.send_header("Location", f"/login/?t={t}" if re.fullmatch(r"[A-Za-z0-9_-]{20,100}", t) else "/login/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if pfad == "/login/ich":
            _, s = self.angemeldet()
            if not s:
                return self.antwort(401, {"angemeldet": False})
            pk = [{"id": p["id"][:12], "name": p.get("name", "Passkey"), "erstellt": p.get("erstellt"),
                   "zuletzt": p.get("zuletzt")} for p in passkeys(self.instanz) if p.get("email") == s["email"]]
            return self.antwort(200, {"angemeldet": True, "email": s["email"], "passkeys": pk})
        if pfad == "/login/passkey/optionen":
            return self.antwort(200, {"challenge": herausforderung(self.instanz, "anmelden"), "rpId": self.host,
                                      "userVerification": "preferred", "timeout": 120000})
        if pfad == "/login/passkey/neu-optionen":
            _, s = self.angemeldet()
            if not s:
                return self.antwort(401, {"fehler": "nicht angemeldet"})
            vorhanden = [{"type": "public-key", "id": p["id"]} for p in passkeys(self.instanz) if p.get("email") == s["email"]]
            return self.antwort(200, {
                "challenge": herausforderung(self.instanz, "registrieren", s["email"]),
                "rp": {"id": self.host, "name": "homi"},
                "user": {"id": b64(hashlib.sha256(f"{self.instanz}:{s['email']}".encode()).digest()[:16]),
                         "name": s["email"], "displayName": s["email"]},
                "pubKeyCredParams": [{"type": "public-key", "alg": -7}],
                "authenticatorSelection": {"residentKey": "required", "userVerification": "preferred"},
                "excludeCredentials": vorhanden, "attestation": "none", "timeout": 120000})
        return self.antwort(404, {"fehler": "unbekannt"})

    # --- POST
    def do_POST(self):
        if not self.instanz:
            return self.antwort(400, {"fehler": "Instanz fehlt"})
        if not self.gleicher_ursprung():
            return self.antwort(403, {"fehler": "fremder Ursprung"})
        try:
            d = self.json_lesen()
        except (ValueError, json.JSONDecodeError):
            return self.antwort(400, {"fehler": "ungültige Anfrage"})
        pfad = urlsplit(self.path).path
        try:
            if pfad == "/login/code":
                return self.code_senden(str(d.get("email", "")).strip().lower())
            if pfad == "/login/link-pruefen":
                with sperre:
                    eintrag = links.pop(h(str(d.get("t", ""))), None)
                    if eintrag:
                        codes.pop((eintrag[0], eintrag[1]), None)
                if not eintrag or eintrag[0] != self.instanz or eintrag[2] < time.time():
                    return self.antwort(400, {"fehler": "Der Link ist abgelaufen oder wurde schon benutzt. Bitte einen neuen Code anfordern."})
                return self.anmelden(eintrag[1])
            if pfad == "/login/code-pruefen":
                return self.code_pruefen(str(d.get("email", "")).strip().lower(), str(d.get("code", "")).strip())
            if pfad == "/login/abmelden":
                p, _ = self.angemeldet()
                if p:
                    os.remove(p)
                return self.antwort(200, {"ok": True}, {"Set-Cookie": cookie("x" * 20, 0)})
            if pfad == "/login/passkey/anmelden":
                return self.passkey_anmelden(d.get("credential") or {})
            if pfad == "/login/passkey/neu":
                return self.passkey_neu(d.get("credential") or {}, str(d.get("name", "Passkey"))[:60])
            if pfad == "/login/passkey/entfernen":
                _, s = self.angemeldet()
                if not s:
                    return self.antwort(401, {"fehler": "nicht angemeldet"})
                liste = passkeys(self.instanz)
                neu = [p for p in liste if not (p["id"].startswith(str(d.get("id", "-"))) and p.get("email") == s["email"])]
                schreiben(os.path.join(DATEN, "passkeys", self.instanz + ".json"), neu)
                return self.antwort(200, {"ok": True, "entfernt": len(liste) - len(neu)})
        except (ValueError, KeyError, TypeError, IndexError, InvalidSignature) as e:
            return self.antwort(400, {"fehler": f"Prüfung fehlgeschlagen ({type(e).__name__})"})
        return self.antwort(404, {"fehler": "unbekannt"})

    def code_senden(self, email):
        antwort = {"ok": True, "text": "Wenn diese Adresse für dieses homi freigeschaltet ist, kommt gleich eine Mail mit dem Code."}
        if not EMAIL_RE.match(email) or email not in benutzer(self.instanz):
            time.sleep(0.4)                              # gleiche Antwortzeit wie beim Versand
            return self.antwort(200, antwort)
        jetzt, schl = time.time(), (self.instanz, email)
        with sperre:
            zeiten = [t for t in mails_gesendet.get(schl, []) if jetzt - t < 900]
            if len(zeiten) >= 3:
                return self.antwort(429, {"fehler": "Zu viele Mails – bitte in 15 Minuten nochmals versuchen."})
            mails_gesendet[schl] = zeiten + [jetzt]
            code = f"{secrets.randbelow(1_000_000):06d}"
            link = secrets.token_urlsafe(32)
            alt = codes.get(schl)
            if alt:
                links.pop(alt["link_h"], None)
            codes[schl] = {"code_h": h(f"{schl}:{code}"), "link_h": h(link), "ablauf": jetzt + CODE_S, "versuche": 0}
            links[h(link)] = (self.instanz, email, jetzt + CODE_S)
        senden(email, f"Dein homi-Code: {code}", f"""Hallo

Dein Anmeldecode für {self.host}:

    {code}

Gib ihn in der homi-App ein (gültig 15 Minuten). Oder tippe auf diesen Link, um dich direkt im Browser anzumelden:
https://{self.host}/login/link?t={link}

Hast du keinen Code angefordert? Dann kannst du diese Mail einfach löschen – ohne Code kommt niemand in dein homi.

homi von Biber Solar
""", absender="homi <domus@biber.solar>")
        return self.antwort(200, antwort)

    def code_pruefen(self, email, code):
        schl = (self.instanz, email)
        with sperre:
            e = codes.get(schl)
            if not e or e["ablauf"] < time.time() or e["versuche"] >= CODE_VERSUCHE:
                return self.antwort(400, {"fehler": "Der Code ist abgelaufen. Bitte einen neuen anfordern."})
            e["versuche"] += 1
            if not secrets.compare_digest(e["code_h"], h(f"{schl}:{code}")):
                rest = CODE_VERSUCHE - e["versuche"]
                return self.antwort(400, {"fehler": f"Der Code stimmt nicht. Noch {rest} Versuch{'e' if rest != 1 else ''}."
                                          if rest else "Der Code stimmt nicht. Bitte einen neuen anfordern."})
            codes.pop(schl, None)
            links.pop(e["link_h"], None)
        return self.anmelden(email)

    def passkey_neu(self, c, name):
        _, s = self.angemeldet()
        if not s:
            return self.antwort(401, {"fehler": "nicht angemeldet"})
        r = c["response"]
        _, hf = client_data(r["clientDataJSON"], "webauthn.create", self.host)
        if hf["zweck"] != "registrieren" or hf["instanz"] != self.instanz or hf["email"] != s["email"]:
            raise ValueError("falscher Zweck")
        att, _ = cbor(unb64(r["attestationObject"]))
        ad = att["authData"]
        flags, zaehler = auth_daten(ad, self.host)
        if not flags & 0x40:
            raise ValueError("keine Schlüsseldaten")
        lang = int.from_bytes(ad[53:55], "big")
        cred_id = ad[55:55 + lang]
        cose, _ = cbor(ad, 55 + lang)
        if cose.get(1) != 2 or cose.get(3) != -7 or cose.get(-1) != 1:
            raise ValueError("nur ES256 (P-256) unterstützt")
        ec.EllipticCurvePublicNumbers(int.from_bytes(cose[-2], "big"), int.from_bytes(cose[-3], "big"), ec.SECP256R1()).public_key()
        liste = [p for p in passkeys(self.instanz) if p["id"] != b64(cred_id)]
        jetzt = datetime.now().isoformat(timespec="minutes")
        liste.append({"id": b64(cred_id), "x": b64(cose[-2]), "y": b64(cose[-3]), "zaehler": zaehler, "email": s["email"],
                      "name": name, "erstellt": jetzt, "zuletzt": None})
        schreiben(os.path.join(DATEN, "passkeys", self.instanz + ".json"), liste)
        return self.antwort(200, {"ok": True})

    def passkey_anmelden(self, c):
        r = c["response"]
        roh_cd, hf = client_data(r["clientDataJSON"], "webauthn.get", self.host)
        if hf["zweck"] != "anmelden" or hf["instanz"] != self.instanz:
            raise ValueError("falscher Zweck")
        liste = passkeys(self.instanz)
        p = next((x for x in liste if x["id"] == c.get("id") or x["id"] == c.get("rawId")), None)
        if not p:
            return self.antwort(400, {"fehler": "Dieser Passkey ist für dieses homi nicht bekannt."})
        ad = unb64(r["authenticatorData"])
        _, zaehler = auth_daten(ad, self.host)
        schluessel = ec.EllipticCurvePublicNumbers(int.from_bytes(unb64(p["x"]), "big"), int.from_bytes(unb64(p["y"]), "big"),
                                                   ec.SECP256R1()).public_key()
        schluessel.verify(unb64(r["signature"]), ad + hashlib.sha256(roh_cd).digest(), ec.ECDSA(hashes.SHA256()))
        if p.get("zaehler") and zaehler and zaehler <= p["zaehler"]:
            raise ValueError("Zähler zurückgesetzt – möglicher kopierter Schlüssel")
        if p["email"] not in benutzer(self.instanz):
            return self.antwort(403, {"fehler": "Zugang für diese Adresse entfernt."})
        p["zaehler"], p["zuletzt"] = zaehler, datetime.now().isoformat(timespec="minutes")
        schreiben(os.path.join(DATEN, "passkeys", self.instanz + ".json"), liste)
        return self.anmelden(p["email"])


if __name__ == "__main__":
    for d in ("instanzen", "sitzungen", "passkeys"):
        os.makedirs(os.path.join(DATEN, d), mode=0o700, exist_ok=True)
    adresse, port = BIND.rsplit(":", 1)
    print(f"homi-login auf {adresse}:{port}", flush=True)
    ThreadingHTTPServer((adresse, int(port)), Handler).serve_forever()
