#!/usr/bin/env python3
"""Erzeugt die App-Icons von homi (Pixel-Art im Stil der Seite) als PNG in www/icons/.
Nur Python-Standardbibliothek. Einmalig bzw. nach Änderungen am Motiv aufrufen: ./icons.py"""
import os
import struct
import zlib

# 32 × 32 Pixel: Haus mit Solardach, Sonne, gelbes Fenster (wie das Favicon)
MOTIV = """
................................
................................
.......................YY.......
...................Y...YY...Y...
....................Y......Y....
.......................YY.......
......................YYYY......
..................YY.YYYYYY.YY..
......................YYYY......
.......................YY.......
....................Y......Y....
...............B...Y...YY...Y...
..............BBB......YY.......
.............BBCBB..............
............BBCBCBB.............
...........BBCBCBCBB............
..........BBCBCBCBCBB...........
.........BBCBCBCBCBCBB..........
........BBBBBBBBBBBBBBB.........
.........OOOOOOOOOOOOO..........
.........OOOOOOOOOOOOO..........
.........OOWWWOOOOOOOO..........
.........OOWWWOOOFFFOO..........
.........OOWWWOOOFFFOO..........
.........OOOOOOOOFFFOO..........
.........OOOOOOOOFFFOO..........
.........OOOOOOOOFFFOO..........
GGGGGGGGGGGGGGGGGGGGGGGGGGGGGGGG
gGgGgGgGgGgGgGgGgGgGgGgGgGgGgGgG
gggggggggggggggggggggggggggggggg
................................
................................
""".split()

FARBEN = {
    ".": (0, 0, 0), "Y": (255, 255, 85), "B": (85, 85, 255), "C": (85, 255, 255),
    "O": (170, 85, 0), "W": (255, 255, 85), "F": (85, 255, 255),
    "G": (85, 255, 85), "g": (0, 170, 0),
}


def png(pfad, breite, hoehe, pixel):
    """pixel(x, y) -> (r, g, b, a)"""
    roh = b"".join(b"\0" + b"".join(bytes(pixel(x, y)) for x in range(breite)) for y in range(hoehe))

    def chunk(typ, daten):
        return struct.pack(">I", len(daten)) + typ + daten + struct.pack(">I", zlib.crc32(typ + daten))
    with open(pfad, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", breite, hoehe, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(roh, 9)) + chunk(b"IEND", b""))


def icon(groesse, rand):
    """Motiv mittig, rand = Anteil Rand je Seite (für maskierbare Icons grösser)"""
    innen = groesse - 2 * round(groesse * rand)
    px = innen // 32
    off = (groesse - 32 * px) // 2

    def pixel(x, y):
        mx, my = (x - off) // px, (y - off) // px
        if 0 <= mx < 32 and 0 <= my < 32:
            return (*FARBEN[MOTIV[my][mx]], 255)
        return (0, 0, 0, 255)
    return pixel


def badge(groesse):
    """Einfarbiges Symbol für die Android-Statusleiste: nur der Alphakanal zählt (Haus + Sonne)"""
    px = groesse // 32
    off = (groesse - 32 * px) // 2

    def pixel(x, y):
        mx, my = (x - off) // px, (y - off) // px
        an = 0 <= mx < 32 and 0 <= my < 27 and MOTIV[my][mx] in "YBCOF"
        return (255, 255, 255, 255 if an else 0)
    return pixel


if __name__ == "__main__":
    ziel = os.path.join(os.path.dirname(os.path.abspath(__file__)), "www", "icons")
    os.makedirs(ziel, exist_ok=True)
    assert len(MOTIV) == 32 and all(len(z) == 32 for z in MOTIV)
    for name, g, r in (("homi-192.png", 192, 0), ("homi-512.png", 512, 0),
                       ("homi-maskable-512.png", 512, 0.12), ("apple-touch-icon.png", 180, 0.05)):
        png(os.path.join(ziel, name), g, g, icon(g, r))
    png(os.path.join(ziel, "badge-96.png"), 96, 96, badge(96))
    print("Icons geschrieben nach", ziel)
