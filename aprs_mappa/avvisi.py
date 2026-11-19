# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Avvisi: una stazione osservata torna in aria, una stazione entra o esce da una zona, un
segnale di emergenza Mic-E, un messaggio per la propria stazione.

Le regole stanno nella configurazione:
- alert_watch: nominativi osservati, separati da virgole; "IZ2*" vale per tutti quelli che iniziano
  così; senza SSID vale per tutti gli SSID (IZ2LPN = IZ2LPN, IZ2LPN-9, ...).
- alert_zones: [{"name", "lat", "lon", "radius_km", "calls"}]; calls vuoto = tutte le stazioni.
- alert_emergency, alert_messages: sì/no.

Una stazione osservata dà l'avviso quando si sente per la prima volta o dopo un'assenza di
alert_quiet_min minuti, così non suona a ogni pacchetto.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Callable

from .lingua import t as tr

MAX_ALERTS = 200


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(h)))


def parse_calls(text) -> list[str]:
    items = text if isinstance(text, list) else str(text or "").replace(";", ",").replace(" ", ",").split(",")
    return [c.strip().upper() for c in items if c and c.strip()]


def call_matches(call: str, patterns: list[str]) -> bool:
    call = call.upper()
    for p in patterns:
        if p.endswith("*"):
            if call.startswith(p[:-1]):
                return True
        elif call == p or ("-" not in p and call.split("-")[0] == p):
            return True
    return False


def clean_zones(zones) -> list[dict]:
    """Zone valide dalla configurazione (quelle sbagliate si scartano)."""
    out = []
    for z in zones or []:
        try:
            lat, lon, r = float(z["lat"]), float(z["lon"]), float(z["radius_km"])
        except (KeyError, TypeError, ValueError):
            continue
        if -90 <= lat <= 90 and -180 <= lon <= 180 and 0 < r <= 20000:
            out.append({"name": str(z.get("name") or "").strip()[:40] or f"{lat:.3f},{lon:.3f}",
                        "lat": lat, "lon": lon, "radius_km": r, "calls": parse_calls(z.get("calls", ""))})
    return out


class AlertEngine:
    def __init__(self):
        self.lock = threading.Lock()
        self.watch: list[str] = []
        self.zones: list[dict] = []
        self.emergency = True
        self.messages = True
        self.quiet_s = 30 * 60
        self.alerts: deque[dict] = deque(maxlen=MAX_ALERTS)
        self.last_heard: dict[str, float] = {}
        self.inside: dict[tuple[str, str], bool] = {}
        self.emerg_last: dict[str, float] = {}
        self.on_alert: Callable[[dict], None] | None = None
        self._n = 0

    def configure(self, watch="", zones=None, emergency=True, messages=True, quiet_min=30) -> None:
        with self.lock:
            self.watch = parse_calls(watch)
            self.zones = clean_zones(zones)
            self.emergency, self.messages = bool(emergency), bool(messages)
            self.quiet_s = max(1, int(quiet_min or 30)) * 60
            names = {z["name"] for z in self.zones}
            self.inside = {k: v for k, v in self.inside.items() if k[1] in names}

    def rules(self) -> dict:
        with self.lock:
            return {"watch": list(self.watch), "zones": [dict(z) for z in self.zones],
                    "emergency": self.emergency, "messages": self.messages, "quiet_min": self.quiet_s // 60}

    # ------------------------------------------------------------ verifica
    def check(self, pkt, origin: str, now: float | None = None) -> list[dict]:
        """Da chiamare per ogni pacchetto ricevuto; ritorna gli avvisi nati (già notificati)."""
        now = time.time() if now is None else now
        call = (pkt.object_name or pkt.source or "").upper()
        if not call:
            return []
        out = []
        with self.lock:
            if self.watch and call_matches(call, self.watch):
                last = self.last_heard.get(call)
                self.last_heard[call] = now
                if last is None or now - last >= self.quiet_s:
                    text = (tr("{call} è di nuovo in aria") if last else tr("{call} è in aria")).replace("{call}", call)
                    out.append(self._new("watch", call, text, now, pkt, origin))
            if pkt.has_position:
                for z in self.zones:
                    if z["calls"] and not call_matches(call, z["calls"]):
                        continue
                    d = distance_km((pkt.lat, pkt.lon), (z["lat"], z["lon"]))
                    now_in = d <= z["radius_km"]
                    key = (call, z["name"])
                    was = self.inside.get(key)
                    self.inside[key] = now_in
                    if was is None:
                        # prima posizione sentita: avvisa solo per le stazioni scelte per la zona,
                        # sennò ogni stazione fissa della zona suonerebbe a ogni avvio
                        if now_in and z["calls"]:
                            out.append(self._new("zone_in", call, tr("{call} è nella zona {zone}").replace(
                                "{call}", call).replace("{zone}", z["name"]), now, pkt, origin, zone=z["name"]))
                    elif now_in != was:
                        kind = "zone_in" if now_in else "zone_out"
                        text = tr("{call} è entrato nella zona {zone}") if now_in else tr("{call} è uscito dalla zona {zone}")
                        out.append(self._new(kind, call, text.replace("{call}", call).replace("{zone}", z["name"]),
                                             now, pkt, origin, zone=z["name"]))
            status = (pkt.mic_e_status or "")
            if self.emergency and status.upper() in ("EMERGENZA", "EMERGENCY"):
                last = self.emerg_last.get(call)
                if last is None or now - last >= 10 * 60:
                    self.emerg_last[call] = now
                    out.append(self._new("emergency", call, tr("EMERGENZA da {call}").replace("{call}", call),
                                         now, pkt, origin, level="critical"))
        for a in out:
            self._notify(a)
        return out

    def message(self, ev: dict) -> dict | None:
        """Messaggio ricevuto per la propria stazione (evento di MessageCenter)."""
        if ev.get("type") != "message":
            return None
        with self.lock:
            if not self.messages:
                return None
            a = self._new("message", ev.get("from", ""), tr("Messaggio da {call}: {text}").replace(
                "{call}", ev.get("from", "")).replace("{text}", ev.get("text", "")), ev.get("t") or time.time())
        self._notify(a)
        return a

    def _new(self, kind, call, text, now, pkt=None, origin="", zone=None, level=None) -> dict:
        self._n += 1
        a = {"n": self._n, "t": now, "kind": kind, "call": call, "text": text, "origin": origin,
             "level": level or ("warning" if kind in ("zone_in", "zone_out") else "info")}
        if zone:
            a["zone"] = zone
        if pkt is not None and getattr(pkt, "has_position", False):
            a["lat"], a["lon"] = pkt.lat, pkt.lon
        self.alerts.append(a)
        return a

    def _notify(self, a: dict) -> None:
        if self.on_alert:
            try:
                self.on_alert(a)
            except Exception:
                import logging
                logging.getLogger("avvisi").exception("notifica dell'avviso non riuscita")

    def snapshot(self) -> dict:
        with self.lock:
            alerts = list(self.alerts)[::-1]
        return {"alerts": alerts, "rules": self.rules()}

    def clear(self) -> None:
        with self.lock:
            self.alerts.clear()
