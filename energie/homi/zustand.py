"""Gemeinsamer Laufzeit-Zustand (Livewerte, Verlauf, Archiv- und Speicher-Objekt)."""
import threading
from collections import deque


state = {"status": None, "boerse": None, "prognose": None}

history = deque(maxlen=24 * 60)

lock = threading.Lock()

speicher_sim = None

archiv = None
