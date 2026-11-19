# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Telemetria APRS: i pacchetti T# portano 5 valori grezzi e 8 bit; la stazione spiega a parte, con
messaggi indirizzati a se stessa, cosa sono (PARM), in che unità (UNIT), come convertirli
(EQNS: valore = a·x² + b·x + c) e quando un bit è "attivo" (BITS)."""
from __future__ import annotations

import threading

from .aprs import telemetry_definition


class TelemetryBook:
    """Definizioni della telemetria per stazione, e conversione dei valori grezzi."""

    def __init__(self):
        self.lock = threading.Lock()
        self.defs: dict[str, dict] = {}

    def define(self, station: str, text: str) -> bool:
        d = telemetry_definition(text)
        if d is None:
            return False
        kind, values = d
        with self.lock:
            e = self.defs.setdefault(station.upper(), {})
            if kind == "PARM":
                e["parm"] = values[:13]
            elif kind == "UNIT":
                e["unit"] = values[:13]
            elif kind == "EQNS":
                e["eqns"] = values
            else:
                e["bits"] = values
        return True

    def scaled(self, station: str, raw: dict) -> dict:
        """Valori con nome, unità e conversione; senza definizioni restano i valori grezzi."""
        with self.lock:
            e = dict(self.defs.get(station.upper(), {}))
        parm, unit = e.get("parm", []), e.get("unit", [])
        eqns = e.get("eqns") or [[0.0, 1.0, 0.0]] * 5
        analog = []
        for i, x in enumerate(raw.get("analog", [])[:5]):
            if x is None:
                continue
            a, b, c = eqns[i] if i < len(eqns) else (0.0, 1.0, 0.0)
            v = a * x * x + b * x + c
            name = parm[i] if i < len(parm) and parm[i] else f"A{i + 1}"
            analog.append({"i": i, "name": name, "unit": unit[i] if i < len(unit) else "",
                           "value": round(v, 3)})
        bits = []
        sense, project = (e.get("bits") or ["11111111", ""])[:2]
        for i, ch in enumerate((raw.get("bits") or "")[:8]):
            name = parm[5 + i] if 5 + i < len(parm) and parm[5 + i] else f"B{i + 1}"
            on = ch == (sense[i] if i < len(sense) else "1")
            label = unit[5 + i] if 5 + i < len(unit) and unit[5 + i] else ("ON" if on else "OFF")
            bits.append({"i": i, "name": name, "on": on, "label": label})
        return {"seq": raw.get("seq"), "analog": analog, "bits": bits, "project": project,
                "defined": bool(e)}
