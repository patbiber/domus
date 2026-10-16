"""Gemeinsamer Laufzeit-Zustand (Livewerte, Verlauf, Archiv- und Speicher-Objekt)."""
import threading
from collections import deque


state = {"status": None, "boerse": None, "prognose": None, "modbus": None}

history = deque(maxlen=24 * 60)

lock = threading.Lock()

speicher_sim = None

archiv = None

steuerung = None                   # Wechselrichter-Steuerung (Testmodus), siehe steuerung.py
