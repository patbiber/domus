"""Messung des homi-Servers (NUC): Leistung, Last, Temperatur."""
import os
import time

from .konfig import NUC_NETZTEIL_WG, NUC_REST_W, RAPL_DIR


class ServerMesser:
    """Misst den homi-Server (NUC): CPU+RAM-Leistung über RAPL, CPU-Last, Temperatur, RAM, Uptime."""

    def __init__(self):
        self.last = None            # (zeit, energie_pkg_uj, energie_dram_uj, cpu_busy, cpu_total)
        self.werte = {}
        self.dienste = {}
        self.dienste_ts = 0
        self.hoch_seit = None       # seit wann die CPU ohne Unterbruch über 95 % liegt

    @staticmethod
    def _lies(pfad, default=None):
        try:
            with open(pfad) as f:
                return f.read().strip()
        except OSError:
            return default

    def _rapl(self, sub):
        v = self._lies(os.path.join(RAPL_DIR, sub, "energy_uj") if sub else os.path.join(RAPL_DIR, "energy_uj"))
        return int(v) if v else None

    def _dram_dir(self):
        for d in ("intel-rapl:0:2", "intel-rapl:0:1", "intel-rapl:0:0"):
            if self._lies(os.path.join(RAPL_DIR, d, "name")) == "dram":
                return d
        return None

    def messen(self):
        t = time.time()
        pkg, dram = self._rapl(""), (self._rapl(self._dram_dir()) if self._dram_dir() else None)
        cpu = [int(x) for x in self._lies("/proc/stat", "cpu 0").splitlines()[0].split()[1:]]
        busy, total = sum(cpu) - cpu[3] - (cpu[4] if len(cpu) > 4 else 0), sum(cpu)
        w = {}
        if self.last:
            dt = t - self.last[0]
            wrap = int(self._lies(os.path.join(RAPL_DIR, "max_energy_range_uj"), "0") or 0)
            diff = lambda a, b: (a - b) if a >= b else (a + wrap - b)
            if pkg is not None and self.last[1] is not None and dt > 0:
                w["cpu_w"] = round(diff(pkg, self.last[1]) / dt / 1e6, 2)
            if dram is not None and self.last[2] is not None and dt > 0:
                w["ram_w"] = round(diff(dram, self.last[2]) / dt / 1e6, 2)
            if total > self.last[4]:
                w["cpu_pct"] = round(100 * (busy - self.last[3]) / (total - self.last[4]), 1)
        self.last = (t, pkg, dram, busy, total)
        if "cpu_pct" in w:
            self.hoch_seit = (self.hoch_seit or t) if w["cpu_pct"] >= 95 else None
        w["cpu_hoch_min"] = round((t - self.hoch_seit) / 60, 1) if self.hoch_seit else 0
        if "cpu_w" in w:
            intern = w["cpu_w"] + w.get("ram_w", 0) + NUC_REST_W     # hinter dem Netzteil
            w["leistung_w"] = round(intern / NUC_NETZTEIL_WG, 1)      # an der Steckdose
            w["rest_w_geschaetzt"] = NUC_REST_W
            w["netzteil_verlust_w"] = round(w["leistung_w"] - intern, 1)
            w["netzteil_wirkungsgrad"] = NUC_NETZTEIL_WG
        temp = self._lies("/sys/class/hwmon/hwmon1/temp1_input") or self._lies("/sys/class/thermal/thermal_zone2/temp")
        w["temp_c"] = round(int(temp) / 1000, 1) if temp else None
        w["uptime_s"] = int(float(self._lies("/proc/uptime", "0 0").split()[0]))
        w["load1"] = float(self._lies("/proc/loadavg", "0").split()[0])
        mem = dict(l.split(":", 1) for l in self._lies("/proc/meminfo", "").splitlines() if ":" in l)
        try:
            w["ram_pct"] = round(100 * (1 - int(mem["MemAvailable"].split()[0]) / int(mem["MemTotal"].split()[0])), 1)
        except (KeyError, ValueError):
            pass
        try:
            st = os.statvfs("/data")
            w["disk_pct"] = round(100 * (1 - st.f_bavail / st.f_blocks), 1)
        except OSError:
            pass
        w["warnungen"] = [x for x in (
            f"RAM {w['ram_pct']:.0f} % belegt" if w.get("ram_pct", 0) >= 85 else None,
            f"Disk {w['disk_pct']:.0f} % belegt" if w.get("disk_pct", 0) >= 85 else None,
            f"CPU seit {w['cpu_hoch_min']:.0f} min über 95 %" if w["cpu_hoch_min"] >= 10 else None) if x]
        if t - self.dienste_ts > 60:        # Dienste nur jede Minute prüfen
            import socket
            for name, port in (("homeassistant", 8123), ("nginx", 443)):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=2).close()
                    self.dienste[name] = True
                except OSError:
                    self.dienste[name] = False
            self.dienste_ts = t
        w["dienste"] = dict(self.dienste, homi=True)
        self.werte = w
        return w

server_messer = ServerMesser()
