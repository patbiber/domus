"""Minimaler lokaler Tuya-Client (Protokoll 3.3) ohne Zusatzbibliotheken: Status lesen, Datenpunkte setzen.

Tuya 3.3: TCP-Port 6668, jede Nachricht = Kopf 0x000055AA, Nummer, Befehl, Länge, Nutzdaten, CRC32, Ende 0x0000AA55.
Nutzdaten sind JSON, verschlüsselt mit AES-128-ECB (PKCS7) und dem Local Key des Geräts. Befehle ausser der
Statusabfrage tragen vorne «3.3» + 12 Nullbytes. Viele Tuya-Geräte erlauben nur eine lokale Verbindung gleichzeitig –
darum hier pro Abfrage verbinden und gleich wieder trennen (und das Gerät NICHT zusätzlich in Home Assistant mit
Tuya Local einbinden). AES ist unten in reinem Python (FIPS-197), für die paar Bytes pro Abfrage schnell genug.
"""
import json
import socket
import struct
import time
import zlib

PORT = 6668
DP_QUERY, CONTROL, STATUS = 0x0A, 0x07, 0x08
PREFIX, SUFFIX = 0x000055AA, 0x0000AA55


# ---------- AES-128 (nur ECB, Blöcke à 16 Bytes) ----------
def _tabellen():
    sbox, p, q = [0] * 256, 1, 1
    while True:
        p = (p ^ (p << 1) ^ (0x1B if p & 0x80 else 0)) & 0xFF
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        r = lambda x, n: ((x << n) | (x >> (8 - n))) & 0xFF
        sbox[p] = q ^ r(q, 1) ^ r(q, 2) ^ r(q, 3) ^ r(q, 4) ^ 0x63
        if p == 1:
            break
    sbox[0] = 0x63
    inv = [0] * 256
    for i, v in enumerate(sbox):
        inv[v] = i
    return sbox, inv


SBOX, INV_SBOX = _tabellen()
xt = lambda a: ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _schluessel(key):
    w = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = [SBOX[b] for b in t[1:] + t[:1]]
            t[0] ^= rcon
            rcon = xt(rcon)
        w.append([a ^ b for a, b in zip(w[i - 4], t)])
    return [sum(w[4 * r:4 * r + 4], []) for r in range(11)]


def _mix(s):
    for c in range(0, 16, 4):
        a0, a1, a2, a3 = s[c:c + 4]
        t = a0 ^ a1 ^ a2 ^ a3
        s[c:c + 4] = [a0 ^ t ^ xt(a0 ^ a1), a1 ^ t ^ xt(a1 ^ a2), a2 ^ t ^ xt(a2 ^ a3), a3 ^ t ^ xt(a3 ^ a0)]


def _block(blk, rk, rueck=False):
    s = [a ^ b for a, b in zip(blk, rk[10 if rueck else 0])]
    for r in (range(9, 0, -1) if rueck else range(1, 10)):
        if rueck:                                                # InvShiftRows + InvSubBytes, Rundenschlüssel
            s = [INV_SBOX[s[(i % 4) + 4 * ((i // 4 - i % 4) % 4)]] for i in range(16)]
            s = [a ^ b for a, b in zip(s, rk[r])]
            for c in range(0, 16, 4):                            # InvMixColumns = Vorstufe + MixColumns
                u, v = xt(xt(s[c] ^ s[c + 2])), xt(xt(s[c + 1] ^ s[c + 3]))
                s[c] ^= u; s[c + 1] ^= v; s[c + 2] ^= u; s[c + 3] ^= v
            _mix(s)
        else:
            s = [SBOX[s[(i % 4) + 4 * ((i // 4 + i % 4) % 4)]] for i in range(16)]
            _mix(s)
            s = [a ^ b for a, b in zip(s, rk[r])]
    if rueck:
        s = [INV_SBOX[s[(i % 4) + 4 * ((i // 4 - i % 4) % 4)]] for i in range(16)]
        return bytes(a ^ b for a, b in zip(s, rk[0]))
    s = [SBOX[s[(i % 4) + 4 * ((i // 4 + i % 4) % 4)]] for i in range(16)]
    return bytes(a ^ b for a, b in zip(s, rk[10]))


def verschluesseln(daten, key):
    rk, n = _schluessel(key), 16 - len(daten) % 16
    daten += bytes([n]) * n
    return b"".join(_block(daten[i:i + 16], rk) for i in range(0, len(daten), 16))


def entschluesseln(daten, key):
    rk = _schluessel(key)
    klar = b"".join(_block(daten[i:i + 16], rk, rueck=True) for i in range(0, len(daten), 16))
    return klar[:-klar[-1]] if klar and 0 < klar[-1] <= 16 else klar


# ---------- Tuya 3.3 ----------
class TuyaGeraet:
    def __init__(self, dev_id, ip, key, timeout=5):
        self.id, self.ip, self.key, self.timeout, self.nr = dev_id, ip, key.encode(), timeout, 0

    def _paket(self, befehl, daten):
        self.nr += 1
        kopf = struct.pack(">4I", PREFIX, self.nr, befehl, len(daten) + 8)
        return kopf + daten + struct.pack(">2I", zlib.crc32(kopf + daten) & 0xFFFFFFFF, SUFFIX)

    def _lesen(self, s):
        """Eine Antwort lesen → (Befehl, JSON oder None)."""
        kopf = b""
        while len(kopf) < 16:
            t = s.recv(16 - len(kopf))
            if not t:
                raise ConnectionError("Verbindung geschlossen")
            kopf += t
        prefix, _, befehl, laenge = struct.unpack(">4I", kopf)
        if prefix != PREFIX or laenge > 4096:
            raise IOError("unbekannte Antwort")
        rest = b""
        while len(rest) < laenge:
            t = s.recv(laenge - len(rest))
            if not t:
                raise ConnectionError("Verbindung geschlossen")
            rest += t
        daten = rest[:-8]
        if len(daten) >= 4 and struct.unpack(">I", daten[:4])[0] & 0xFFFFFF00 == 0:   # Rückgabecode
            daten = daten[4:]
        if daten.startswith(b"3.3"):
            daten = daten[15:]
        if not daten:
            return befehl, None
        klar = entschluesseln(daten, self.key)
        try:
            return befehl, json.loads(klar)
        except ValueError:
            raise IOError("Antwort nicht lesbar (Local Key falsch?)")

    def _senden(self, befehl, inhalt, warten_auf):
        daten = verschluesseln(json.dumps(inhalt, separators=(",", ":")).encode(), self.key)
        if befehl != DP_QUERY:
            daten = b"3.3" + b"\0" * 12 + daten
        with socket.create_connection((self.ip, PORT), timeout=self.timeout) as s:
            s.sendall(self._paket(befehl, daten))
            ende = time.time() + self.timeout
            while time.time() < ende:
                b, j = self._lesen(s)
                if b in warten_auf and j and "dps" in j:
                    return j["dps"]
        raise IOError("keine Antwort mit Datenpunkten")

    def status(self):
        """{"1": True, "2": 150, …} – Datenpunkte des Geräts."""
        t = str(int(time.time()))
        return self._senden(DP_QUERY, {"gwId": self.id, "devId": self.id, "uid": self.id, "t": t}, (DP_QUERY, STATUS))

    def setzen(self, dps):
        """Datenpunkte setzen, z. B. {"1": True, "2": 180}; gibt die geänderten Datenpunkte zurück."""
        t = str(int(time.time()))
        return self._senden(CONTROL, {"devId": self.id, "uid": self.id, "t": t, "dps": dps}, (STATUS, CONTROL))
