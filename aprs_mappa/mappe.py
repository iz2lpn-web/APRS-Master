# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sorgenti delle mappe e cache.

Tre modalità:
- "osm": il browser scarica le tile direttamente da tile.openstreetmap.org e le
  conserva nella propria cache secondo le intestazioni HTTP. È ciò che chiede la
  policy di OpenStreetMap, che vieta l'uso offline e sconsiglia proxy con cache
  davanti ai suoi server (https://operations.osmfoundation.org/policies/tiles/).
- "custom": tile da un server indicato dall'utente (un proprio server o un
  fornitore che consente la cache), scaricate dal programma e conservate su disco
  per almeno CACHE_MIN_DAYS giorni; se la rete manca si usano quelle salvate.
- "mbtiles": mappa offline da un file MBTiles raster, senza alcun accesso alla rete.

I simboli APRS (immagini di hessu/aprs-symbols) vengono scaricati una volta e
conservati, così la mappa resta completa anche senza Internet.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path

from . import __version__, rete

log = logging.getLogger("mappe")

CACHE_DIR = Path.home() / ".aprs_mappa_cache"
CACHE_MIN_DAYS = 7
OSM_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
OSM_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
SYMBOL_URL = "https://raw.githubusercontent.com/hessu/aprs-symbols/master/png/aprs-symbols-24-{n}.png"
SYMBOL_NAMES = {"0", "1", "2"}


def user_agent(contact: str = "") -> str:
    extra = f"; {contact}" if contact else ""
    return f"APRSMaster/{__version__} (ricevitore APRS{extra})"


def _max_age(headers) -> float:
    """Durata di validità indicata dal server (Cache-Control o Expires), in secondi."""
    cc = headers.get("Cache-Control", "") or ""
    m = re.search(r"max-age=(\d+)", cc)
    if m:
        return float(m.group(1))
    exp = headers.get("Expires")
    if exp:
        try:
            return max(0.0, parsedate_to_datetime(exp).timestamp() - time.time())
        except (TypeError, ValueError):
            pass
    return 0.0


class TileService:
    def __init__(self, mode: str = "osm", url: str = "", attribution: str = "",
                 mbtiles: str = "", cache_mb: int = 500, contact: str = "",
                 cache_dir: Path = CACHE_DIR):
        self.mode = mode if mode in ("osm", "custom", "mbtiles") else "osm"
        self.url = url.strip()
        self.attribution = attribution.strip()
        self.mbtiles = mbtiles
        self.cache_mb = cache_mb
        self.contact = contact
        self.cache_dir = Path(cache_dir)
        self.fetch_sem = threading.Semaphore(2)      # al massimo 2 download contemporanei
        self.lock = threading.Lock()
        self.writes = 0
        self.mb_conn: sqlite3.Connection | None = None
        self.mb_meta: dict[str, str] = {}
        self.error = ""
        if self.mode == "custom" and not re.match(r"^https?://.+\{z\}.+\{x\}.+\{y\}", self.url):
            self.error = "URL del server non valido: deve contenere {z}, {x} e {y}"
            self.mode = "osm"
        if self.mode == "mbtiles":
            self._open_mbtiles()

    # ------------------------------------------------------------ configurazione per la pagina
    def client_config(self) -> dict:
        if self.mode == "mbtiles":
            return {"url": "/tiles/{z}/{x}/{y}", "attribution": self.mb_meta.get("attribution", "")
                    or "Mappa offline", "maxZoom": int(self.mb_meta.get("maxzoom", 18)),
                    "minZoom": int(self.mb_meta.get("minzoom", 0)), "mode": "mbtiles"}
        if self.mode == "custom":
            return {"url": "/tiles/{z}/{x}/{y}", "attribution": self.attribution or "Mappa",
                    "maxZoom": 19, "minZoom": 0, "mode": "custom"}
        return {"url": OSM_URL, "attribution": OSM_ATTRIBUTION, "maxZoom": 19, "minZoom": 0, "mode": "osm"}

    # ------------------------------------------------------------ MBTiles
    def _open_mbtiles(self) -> None:
        try:
            uri = Path(self.mbtiles).resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            self.mb_meta = dict(conn.execute("SELECT name, value FROM metadata").fetchall())
            conn.execute("SELECT 1 FROM tiles LIMIT 1")
            fmt = self.mb_meta.get("format", "png").lower()
            if fmt not in ("png", "jpg", "jpeg", "webp"):
                raise ValueError(f"formato '{fmt}' non supportato: serve un MBTiles raster (png/jpg/webp)")
            self.mb_conn = conn
        except (sqlite3.Error, ValueError, OSError) as e:
            self.error = f"File MBTiles non utilizzabile: {e}"
            self.mode = "osm"

    def _mbtiles_tile(self, z: int, x: int, y: int) -> tuple[bytes, str] | None:
        tms_y = (1 << z) - 1 - y                      # MBTiles numera le righe dal basso
        with self.lock:
            row = self.mb_conn.execute(
                "SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=?",
                (z, x, tms_y)).fetchone()
        if not row:
            return None
        fmt = self.mb_meta.get("format", "png").lower()
        return bytes(row[0]), {"jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(fmt, "image/png")

    # ------------------------------------------------------------ cache su disco
    def _cache_path(self, z: int, x: int, y: int) -> Path:
        key = hashlib.sha1(self.url.encode()).hexdigest()[:12]
        return self.cache_dir / "tiles" / key / str(z) / str(x) / f"{y}.tile"

    def _fetch(self, url: str) -> tuple[bytes, float] | None:
        req = urllib.request.Request(url, headers={"User-Agent": user_agent(self.contact)})
        with self.fetch_sem:
            try:
                with rete.urlopen(req, timeout=10) as r:
                    data, age = r.read(), _max_age(r.headers)
            except (urllib.error.URLError, OSError, ValueError) as e:
                log.debug("download non riuscito %s: %s", url, e)
                return None
        return data, age

    def _cached(self, path: Path, url: str) -> bytes | None:
        """Tile dalla cache se ancora valida; altrimenti scaricata e salvata;
        se la rete non risponde, la copia scaduta è meglio di niente."""
        try:
            if path.stat().st_mtime > time.time():      # mtime = scadenza
                return path.read_bytes()
        except OSError:
            pass
        got = self._fetch(url)
        if got is None:
            try:
                return path.read_bytes()
            except OSError:
                return None
        data, age = got
        expiry = time.time() + max(age, CACHE_MIN_DAYS * 86400)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".tmp{threading.get_ident()}")
            tmp.write_bytes(data)
            os.replace(tmp, path)
            os.utime(path, (time.time(), expiry))
        except OSError as e:
            log.warning("cache delle mappe: scrittura non riuscita (%s)", e)
        with self.lock:
            self.writes += 1
            prune = self.writes % 200 == 0
        if prune:
            threading.Thread(target=self.prune, daemon=True).start()
        return data

    def tile(self, z: int, x: int, y: int) -> tuple[bytes, str] | None:
        if not (0 <= z <= 22 and 0 <= x < (1 << z) and 0 <= y < (1 << z)):
            return None
        if self.mode == "mbtiles" and self.mb_conn is not None:
            return self._mbtiles_tile(z, x, y)
        if self.mode == "custom":
            url = self.url.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
            data = self._cached(self._cache_path(z, x, y), url)
            if data is None:
                return None
            ctype = ("image/jpeg" if data[:3] == b"\xff\xd8\xff" else
                     "image/webp" if data[8:12] == b"WEBP" else "image/png")
            return data, ctype
        return None                                   # modalità osm: il browser va diretto

    def symbol_sheet(self, n: str) -> bytes | None:
        """Fogli dei simboli APRS: scaricati una volta, poi sempre dalla cache."""
        if n not in SYMBOL_NAMES:
            return None
        return self._cached(self.cache_dir / "simboli" / f"aprs-symbols-24-{n}.png",
                            SYMBOL_URL.format(n=n))

    # ------------------------------------------------------------ manutenzione
    def cache_size(self) -> int:
        total = 0
        for root, _, files in os.walk(self.cache_dir / "tiles"):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return total

    def prune(self) -> int:
        """Riporta la cache sotto il limite togliendo per prime le tile che scadono prima."""
        limit = self.cache_mb * 1024 * 1024
        entries = []
        for root, _, files in os.walk(self.cache_dir / "tiles"):
            for f in files:
                p = os.path.join(root, f)
                try:
                    st = os.stat(p)
                    entries.append((st.st_mtime, st.st_size, p))
                except OSError:
                    pass
        total = sum(e[1] for e in entries)
        removed = 0
        if total <= limit:
            return 0
        for _, size, p in sorted(entries):
            try:
                os.remove(p)
                total -= size
                removed += 1
            except OSError:
                pass
            if total <= limit * 0.9:
                break
        return removed

    def clear(self) -> None:
        import shutil
        shutil.rmtree(self.cache_dir / "tiles", ignore_errors=True)
