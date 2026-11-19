# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Storico persistente dei pacchetti (SQLite) ed esportazione delle tracce in GPX."""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from dataclasses import fields
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

from .aprs import AprsPacket

DB_PATH = Path.home() / ".aprs_mappa_storico.db"
# i campi semplici del pacchetto hanno una colonna; meteo, telemetria e messaggi si ricavano
# di nuovo dalla riga grezza quando servono
_DICT_FIELDS = ("wx", "telemetry", "message")
_PKT_FIELDS = [f.name for f in fields(AprsPacket) if f.name not in _DICT_FIELDS]
SEGMENT_GAP_S = 3600          # una pausa più lunga apre un nuovo segmento di traccia


class History:
    def __init__(self, path: Path | str = DB_PATH):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        cols = ", ".join(f"{name} {'REAL' if name in ('lat', 'lon', 'speed_kmh', 'altitude_m') else 'TEXT'}"
                         for name in _PKT_FIELDS if name != "course")
        with self.lock, self.conn:
            self.conn.execute(f"""CREATE TABLE IF NOT EXISTS packets (
                id INTEGER PRIMARY KEY, t REAL NOT NULL, station TEXT, raw TEXT,
                course INTEGER, {cols})""")
            cols_now = {r[1] for r in self.conn.execute("PRAGMA table_info(packets)")}
            if "origin" not in cols_now:          # storici creati dalla versione 1.1-1.2
                self.conn.execute("ALTER TABLE packets ADD COLUMN origin TEXT DEFAULT 'rf'")
            self.conn.execute("CREATE INDEX IF NOT EXISTS packets_t ON packets (t)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS packets_station ON packets (station, t)")

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    # ------------------------------------------------------------ scrittura
    def record(self, t: float, pkt: AprsPacket, raw: str, origin: str = "rf") -> None:
        """origin: 'rf' = ricevuto via radio, 'is' = ricevuto da APRS-IS."""
        station = (pkt.object_name or pkt.source) if pkt.has_position else None
        values = [getattr(pkt, n) for n in _PKT_FIELDS]
        names = ", ".join(_PKT_FIELDS)
        marks = ", ".join("?" * (len(_PKT_FIELDS) + 4))
        with self.lock, self.conn:
            self.conn.execute(f"INSERT INTO packets (t, station, raw, origin, {names}) VALUES ({marks})",
                              [t, station, raw, origin] + values)

    def prune(self, days: int) -> int:
        """Cancella i pacchetti più vecchi di days giorni (0 = conserva tutto)."""
        if days <= 0:
            return 0
        with self.lock, self.conn:
            n = self.conn.execute("DELETE FROM packets WHERE t < ?", (time.time() - days * 86400,)).rowcount
        if n > 1000:
            with self.lock:
                self.conn.execute("VACUUM")
        return n

    def clear(self) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM packets")
        with self.lock:
            self.conn.execute("VACUUM")

    # ------------------------------------------------------------ lettura
    def count(self) -> int:
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM packets").fetchone()[0]

    def positions(self, since: float | None = None, until: float | None = None,
                  stations: list[str] | None = None, origin: str | None = None,
                  with_origin: bool = False) -> list[tuple]:
        """Pacchetti con posizione, in ordine di tempo: [(t, raw, pacchetto)]
        oppure, con with_origin, [(t, raw, pacchetto, origine)]."""
        q = f"SELECT t, raw, COALESCE(origin, 'rf'), {', '.join(_PKT_FIELDS)} FROM packets WHERE station IS NOT NULL"
        args: list = []
        if origin is not None:
            q += " AND COALESCE(origin, 'rf') = ?"
            args.append(origin)
        if since is not None:
            q += " AND t >= ?"
            args.append(since)
        if until is not None:
            q += " AND t <= ?"
            args.append(until)
        if stations:
            q += f" AND station IN ({', '.join('?' * len(stations))})"
            args += list(stations)
        with self.lock:
            rows = self.conn.execute(q + " ORDER BY t", args).fetchall()
        out = []
        for r in rows:
            p = AprsPacket(**dict(zip(_PKT_FIELDS, r[3:])))
            out.append((r[0], r[1], p, r[2]) if with_origin else (r[0], r[1], p))
        return out

    def weather(self, since: float | None = None, stations: list[str] | None = None) -> list[tuple]:
        """Letture meteo: [(t, stazione, wx, origine)] in ordine di tempo."""
        from .aprs import parse_tnc2
        q = ("SELECT t, raw, COALESCE(origin, 'rf'), source, object_name FROM packets "
             "WHERE (kind = 'Meteo' OR symbol_code = '_')")
        args: list = []
        if since is not None:
            q += " AND t >= ?"
            args.append(since)
        if stations:
            q += f" AND COALESCE(object_name, source) IN ({', '.join('?' * len(stations))})"
            args += list(stations)
        with self.lock:
            rows = self.conn.execute(q + " ORDER BY t", args).fetchall()
        out = []
        for t, raw, origin, source, obj in rows:
            p = parse_tnc2(raw or "")
            if p is not None and p.wx:
                out.append((t, obj or source, p.wx, origin))
        return out

    def tracks(self, since: float | None = None, until: float | None = None,
               stations: list[str] | None = None) -> dict[str, list[tuple[float, AprsPacket]]]:
        out: dict[str, list[tuple[float, AprsPacket]]] = {}
        for t, _, p in self.positions(since, until, stations):
            out.setdefault(p.object_name or p.source, []).append((t, p))
        return out


# ---------------------------------------------------------------- copertura

CELL_DEG = 0.02          # lato delle celle della mappa di copertura (circa 2 km)


def heard_direct(raw: str) -> bool | None:
    """True se il pacchetto è stato ricevuto direttamente (nessun digipeater
    con il segno di ripetizione '*'), False se tramite digipeater, None se non
    è utilizzabile per la copertura (terze parti, formato non riconosciuto)."""
    head, sep, info = raw.partition(":")
    if not sep or ">" not in head or info.startswith("}"):
        return None
    path = head.split(">", 1)[1].split(",")[1:]
    return not any(p.endswith("*") for p in path)


def _haversine(a, b) -> float:
    import math
    r = math.radians
    dlat, dlon = r(b[0] - a[0]), r(b[1] - a[1])
    h = math.sin(dlat / 2) ** 2 + math.cos(r(a[0])) * math.cos(r(b[0])) * math.sin(dlon / 2) ** 2
    return 12742 * math.asin(min(1.0, math.sqrt(h)))


def _bearing(a, b) -> float:
    import math
    r = math.radians
    y = math.sin(r(b[1] - a[1])) * math.cos(r(b[0]))
    x = math.cos(r(a[0])) * math.sin(r(b[0])) - math.sin(r(a[0])) * math.cos(r(b[0])) * math.cos(r(b[1] - a[1]))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def coverage(history: "History", since: float | None, home: tuple[float, float] | None,
             origin: str = "rf") -> dict:
    """Dove sono le stazioni ricevute via radio (origin 'rf' = VHF, 'lora' = LoRa):
    celle con il numero di pacchetti
    ricevuti direttamente o solo tramite digipeater e, se è nota la posizione
    ricevente, la distanza massima di ricezione diretta per settori di 10°."""
    cells: dict[tuple[int, int], dict] = {}
    sectors: list[dict | None] = [None] * 36
    stations: dict[str, dict] = {}
    for t, raw, p in history.positions(since=since, origin=origin):
        if p.object_name:                    # la posizione di un oggetto non è quella del trasmettitore
            continue
        direct = heard_direct(raw)
        if direct is None:
            continue
        key = (int(p.lat // CELL_DEG), int(p.lon // CELL_DEG))
        c = cells.setdefault(key, {"lat": (key[0] + 0.5) * CELL_DEG, "lon": (key[1] + 0.5) * CELL_DEG,
                                   "direct": 0, "digi": 0, "stations": set()})
        c["direct" if direct else "digi"] += 1
        c["stations"].add(p.source)
        st = stations.setdefault(p.source, {"direct": 0, "digi": 0})
        st["direct" if direct else "digi"] += 1
        if direct and home:
            d = _haversine(home, (p.lat, p.lon))
            i = int(_bearing(home, (p.lat, p.lon)) // 10) % 36
            if sectors[i] is None or d > sectors[i]["km"]:
                sectors[i] = {"km": round(d, 1), "station": p.source, "lat": p.lat, "lon": p.lon}
    out_cells = [dict(c, stations=sorted(c["stations"])) for c in cells.values()]
    best = max((s for s in sectors if s), key=lambda s: s["km"], default=None)
    return {"cell_deg": CELL_DEG, "cells": out_cells, "sectors": sectors, "farthest": best,
            "home": home, "band": "lora" if origin == "lora" else "vhf",
            "stations_direct": sum(1 for v in stations.values() if v["direct"]),
            "stations_digi_only": sum(1 for v in stations.values() if not v["direct"])}


# ---------------------------------------------------------------- GPX

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _txt(s: str | None) -> str:
    return escape(_CTRL.sub("", s or ""))


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def to_gpx(tracks: dict[str, list[tuple[float, AprsPacket]]], creator: str) -> tuple[str, dict]:
    """Stazioni con più posizioni diverse -> traccia (<trk>); stazioni ferme
    -> punto (<wpt>). Ritorna (testo GPX, statistiche)."""
    stats = {"tracks": 0, "waypoints": 0, "points": 0}
    wpts, trks = [], []
    for name, pts in sorted(tracks.items()):
        distinct = {(round(p.lat, 6), round(p.lon, 6)) for _, p in pts}
        if len(distinct) == 1:
            t, p = pts[-1]
            ele = f"<ele>{p.altitude_m:.1f}</ele>" if p.altitude_m is not None else ""
            wpts.append(f'  <wpt lat="{p.lat:.6f}" lon="{p.lon:.6f}">{ele}<time>{_iso(t)}</time>'
                        f"<name>{_txt(name)}</name><desc>{_txt(p.comment)}</desc></wpt>")
            stats["waypoints"] += 1
            continue
        segs, cur, last_t = [], [], None
        for t, p in pts:
            if last_t is not None and t - last_t > SEGMENT_GAP_S and cur:
                segs.append(cur)
                cur = []
            ele = f"<ele>{p.altitude_m:.1f}</ele>" if p.altitude_m is not None else ""
            cur.append(f'      <trkpt lat="{p.lat:.6f}" lon="{p.lon:.6f}">{ele}<time>{_iso(t)}</time></trkpt>')
            last_t = t
        segs.append(cur)
        body = "\n".join("    <trkseg>\n" + "\n".join(s) + "\n    </trkseg>" for s in segs)
        trks.append(f"  <trk>\n    <name>{_txt(name)}</name>\n{body}\n  </trk>")
        stats["tracks"] += 1
        stats["points"] += len(pts)
    first = min((pts[0][0] for pts in tracks.values()), default=time.time())
    gpx = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           f'<gpx version="1.1" creator={quoteattr(creator)} xmlns="http://www.topografix.com/GPX/1/1">\n'
           f"  <metadata><name>Tracce APRS</name><time>{_iso(first)}</time></metadata>\n"
           + "\n".join(wpts + trks) + "\n</gpx>\n")
    return gpx, stats


def safe_filename(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "tracce"
