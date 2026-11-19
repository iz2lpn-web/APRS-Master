# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Archivio delle stazioni ricevute e server HTTP che serve la mappa e
inoltra i nuovi pacchetti al browser tramite Server-Sent Events."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import queue
import re
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .aprs import AprsPacket

STATIC = Path(__file__).with_name("static")
ICONS = {"/icona.svg", "/icona-32.png", "/icona-64.png", "/icona-256.png"}
MAX_TRACK = 500
MAX_LOG = 300
MAX_SERIES = 600                 # letture meteo e telemetria in memoria per stazione
WX_KEYS = ["temp", "hum", "press", "wind", "gust", "wind_dir", "rain_1h", "rain_24h"]



LOCAL = ("127.0.0.1", "::1", "::ffff:127.0.0.1")


def hash_password(password: str, iterations: int = 200_000) -> str:
    """Hash della password del pannello (PBKDF2-SHA256 con sale casuale)."""
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${dk.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        algo, it, salt, digest = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(it))
        return hmac.compare_digest(dk.hex(), digest)
    except (ValueError, TypeError):
        return False


class _Failures:
    """Tentativi di accesso sbagliati per indirizzo: dopo 10 in 10 minuti si aspetta."""

    def __init__(self):
        self.lock = threading.Lock()
        self.data: dict[str, list[float]] = {}

    def blocked(self, ip: str) -> bool:
        now = time.time()
        with self.lock:
            lst = [x for x in self.data.get(ip, []) if now - x < 600]
            self.data[ip] = lst
            return len(lst) >= 10

    def add(self, ip: str) -> None:
        with self.lock:
            self.data.setdefault(ip, []).append(time.time())
            if len(self.data) > 1000:
                self.data.clear()


# copia dei simboli APRS sulla CDN jsDelivr, usata dal browser se il programma non li scarica
SYMBOL_CDN = "https://cdn.jsdelivr.net/gh/hessu/aprs-symbols@master/png/aprs-symbols-24-{n}.png"

class StationStore:
    def __init__(self, home: tuple[float, float] | None = None, history=None, tiles=None):
        from .telemetria import TelemetryBook
        self.tiles = tiles              # mappe.TileService oppure None (= OpenStreetMap diretto)
        self.lock = threading.Lock()
        self.stations: dict[str, dict] = {}
        self.log: deque[dict] = deque(maxlen=MAX_LOG)
        self.clients: list[queue.Queue] = []
        self.home = home
        self.total = 0
        self.history = history          # storico.History oppure None
        self.station: dict | None = None    # tipo e commento della stazione ricevente
        self.weather: dict[str, dict] = {}          # ultima lettura meteo per stazione
        self.wx_series: dict[str, deque] = {}       # letture recenti per i grafici
        self.telemetry: dict[str, dict] = {}        # ultima telemetria per stazione (convertita)
        self.tlm_series: dict[str, deque] = {}
        self.tlm_book = TelemetryBook()
        # funzioni (pkt, raw, origin, t) chiamate per ogni pacchetto nuovo, fuori dal lock:
        # messaggi, avvisi
        self.listeners: list = []
        self.messages = None            # messaggi.MessageCenter oppure None
        self.alerts = None              # avvisi.AlertEngine oppure None
        self.panel = None               # servizio.Service (pannello web) oppure None
        self.panel_password = ""        # hash della password del pannello ("" = solo da questo computer)
        self.on_rules = None            # funzione(regole) per salvare gli avvisi cambiati dalla pagina

    def add(self, pkt: AprsPacket, raw: str, t: float | None = None,
            record: bool = True, broadcast: bool = True, origin: str = "rf") -> None:
        """origin: 'rf' = radio VHF (AFSK 1200), 'lora' = LoRa APRS, 'is' = APRS-IS."""
        now = time.time() if t is None else t
        if record and self.history is not None:
            try:
                self.history.record(now, pkt, raw, origin)
            except Exception:           # un problema allo storico non ferma la ricezione
                logging.getLogger("storico").exception("salvataggio nello storico non riuscito")
        with self.lock:
            self._add_locked(pkt, raw, now, broadcast, origin)
        if broadcast:
            for fn in list(self.listeners):
                try:
                    fn(pkt, raw, origin, now)
                except Exception:
                    logging.getLogger("server").exception("elaborazione del pacchetto non riuscita")

    def _add_locked(self, pkt: AprsPacket, raw: str, now: float, broadcast: bool, origin: str) -> None:
        entry = {"t": now, "raw": raw, "kind": pkt.kind, "source": pkt.source, "origin": origin}
        self.total += 1
        self.log.append(entry)
        d = pkt.to_dict()
        wx, tel, msg = d.pop("wx", None), d.pop("telemetry", None), d.pop("message", None)
        key = pkt.object_name or pkt.source
        extra: dict = {}
        if msg and msg.get("type") == "tlm_def":
            # le definizioni si mandano come messaggio "a se stessi" (o alla stazione che trasmette)
            self.tlm_book.define(msg.get("to") or pkt.source, msg.get("text", ""))
            if (msg.get("to") or "").upper() in self.telemetry:
                t_key = msg["to"].upper()
                cur = self.telemetry[t_key]
                cur["values"] = self.tlm_book.scaled(t_key, cur["raw"])
        if wx:
            extra["weather"] = self._weather_locked(key, wx, now, origin, pkt)
        if tel:
            extra["telemetry"] = self._telemetry_locked(pkt.source.upper(), tel, now, origin)
        st = None
        if pkt.has_position:
            st = self.stations.setdefault(key, {"id": key, "track": [], "packets": 0,
                                                "rf_last": None, "lora_last": None, "is_last": None})
            st.setdefault("lora_last", None)
            radio_last = max(st["rf_last"] or 0, st["lora_last"] or 0)
            if origin == "is" and radio_last and now - radio_last < 60:
                # lo stesso pacchetto già ricevuto via radio: conta solo come conferma
                st["is_last"] = now
                if broadcast:
                    self._broadcast({"packet": entry, "station": dict(st), "total": self.total, **extra})
                return
            st.update(d)
            st["id"], st["via"] = key, pkt.source
            st["last_heard"] = now
            st[{"rf": "rf_last", "lora": "lora_last"}.get(origin, "is_last")] = now
            if origin != "is":
                from .storico import heard_direct
                st["direct"] = heard_direct(raw)
            st["packets"] += 1
            last = st["track"][-1] if st["track"] else None
            if not last or (last[0], last[1]) != (pkt.lat, pkt.lon):
                st["track"].append([pkt.lat, pkt.lon, now])
                del st["track"][:-MAX_TRACK]
            w = self.weather.get(key)
            if w:
                st["wx"], st["wx_t"] = w["wx"], w["t"]
                w["lat"], w["lon"] = pkt.lat, pkt.lon
            if key.upper() in self.telemetry:
                st["tlm_t"] = self.telemetry[key.upper()]["t"]
            st = dict(st)
        elif (wx or tel) and key in self.stations:
            # meteo o telemetria senza posizione: si aggiungono alla stazione già sulla mappa
            s0 = self.stations[key]
            if wx:
                s0["wx"], s0["wx_t"] = wx, now
            if tel:
                s0["tlm_t"] = now
            s0["last_heard"] = now
            st = dict(s0)
        if broadcast:
            self._broadcast({"packet": entry, "station": st, "total": self.total, **extra})

    def _weather_locked(self, key: str, wx: dict, now: float, origin: str, pkt: AprsPacket) -> dict:
        prev = self.weather.get(key, {})
        w = {"id": key, "t": now, "wx": wx, "origin": origin,
             "lat": pkt.lat if pkt.has_position else prev.get("lat"),
             "lon": pkt.lon if pkt.has_position else prev.get("lon")}
        if w["lat"] is None and key in self.stations:
            w["lat"], w["lon"] = self.stations[key].get("lat"), self.stations[key].get("lon")
        self.weather[key] = w
        series = self.wx_series.setdefault(key, deque(maxlen=MAX_SERIES))
        if not series or now - series[-1][0] >= 30:       # stessa lettura da radio e da internet
            series.append([now] + [wx.get(k) for k in WX_KEYS])
        return w

    def _telemetry_locked(self, key: str, tel: dict, now: float, origin: str) -> dict:
        values = self.tlm_book.scaled(key, tel)
        e = {"id": key, "t": now, "raw": tel, "values": values, "origin": origin}
        self.telemetry[key] = e
        series = self.tlm_series.setdefault(key, deque(maxlen=MAX_SERIES))
        if not series or series[-1][1] != tel.get("seq") or now - series[-1][0] >= 30:
            series.append([now, tel.get("seq")] + list(tel.get("analog", [None] * 5))[:5] + [tel.get("bits", "")])
        return e

    def push(self, obj: dict) -> None:
        """Invia un evento (messaggi, avvisi, pannello) alle pagine aperte."""
        with self.lock:
            self._broadcast(obj)

    def weather_detail(self, key: str, hours: float = 24) -> dict | None:
        """Ultima lettura e serie per i grafici; con lo storico le ore richieste, sennò la memoria."""
        since = time.time() - hours * 3600
        with self.lock:
            last = dict(self.weather[key]) if key in self.weather else None
            series = [r for r in self.wx_series.get(key, ()) if r[0] >= since]
        if self.history is not None:
            try:
                rows = self.history.weather(since=since, stations=[key])
                if rows:
                    series, prev = [], 0.0
                    for t, _, wx, _ in rows:
                        if t - prev >= 30:
                            series.append([t] + [wx.get(k) for k in WX_KEYS])
                            prev = t
                    if last is None:
                        t, _, wx, origin = rows[-1]
                        last = {"id": key, "t": t, "wx": wx, "origin": origin}
            except Exception:
                logging.getLogger("storico").exception("lettura del meteo non riuscita")
        if last is None:
            return None
        return {"id": key, "last": last, "keys": WX_KEYS, "series": series, "hours": hours}

    def telemetry_detail(self, key: str) -> dict | None:
        key = key.upper()
        with self.lock:
            e = self.telemetry.get(key)
            if e is None:
                return None
            e = dict(e)
            series = list(self.tlm_series.get(key, ()))
        e["values"] = self.tlm_book.scaled(key, e["raw"])
        # serie convertite con le definizioni attuali
        names = [a["name"] for a in e["values"]["analog"]]
        conv = []
        for r in series:
            v = self.tlm_book.scaled(key, {"seq": r[1], "analog": r[2:7], "bits": r[7]})
            conv.append([r[0], r[1]] + [next((a["value"] for a in v["analog"] if a["i"] == i), None)
                                         for i in range(5)])
        e["series"], e["names"] = conv, names
        return e

    def set_tiles(self, tiles) -> None:
        with self.lock:
            self.tiles = tiles
            self._broadcast({"tiles": tiles.client_config()})

    def load_history(self, hours: float) -> int:
        """Ricarica nella mappa le posizioni delle ultime 'hours' ore."""
        if self.history is None or hours <= 0:
            return 0
        rows = self.history.positions(since=time.time() - hours * 3600, with_origin=True)
        for t, raw, pkt, origin in rows:
            self.add(pkt, raw, t=t, record=False, broadcast=False, origin=origin)
        try:
            wx_rows = self.history.weather(since=time.time() - hours * 3600)
        except Exception:
            wx_rows = []
            logging.getLogger("storico").exception("lettura del meteo non riuscita")
        with self.lock:
            for t, key, wx, origin in wx_rows:
                pkt = AprsPacket(source=key, kind="Meteo")
                self._weather_locked(key, wx, t, origin, pkt)
                if key in self.stations:
                    self.stations[key]["wx"], self.stations[key]["wx_t"] = wx, t
        if rows or wx_rows:
            with self.lock:
                self._broadcast({"reload": True})
        return len(rows)

    def _broadcast(self, obj: dict) -> None:
        """Da chiamare con self.lock acquisito."""
        msg = json.dumps(obj)
        for q in list(self.clients):
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass

    def subscribe(self, maxsize: int = 1000) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=maxsize)
        with self.lock:
            self.clients.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)

    def clear(self) -> None:
        with self.lock:
            self.stations.clear()
            self.log.clear()
            self.weather.clear()
            self.wx_series.clear()
            self.telemetry.clear()
            self.tlm_series.clear()
            self.total = 0
            self._broadcast({"reset": True, "total": 0})

    def set_station(self, station_type: str, comment: str, call: str = "", equipment: str = "") -> None:
        """Tipo di dispositivo e commento della stazione ricevente (segnaposto sulla mappa)."""
        from .aprsis import STATION_TYPES, default_comment
        label, table, code = STATION_TYPES.get(station_type, STATION_TYPES["igate_vhf"])
        with self.lock:
            self.station = {"type": station_type, "label": label, "table": table, "code": code,
                            "comment": comment.strip() or default_comment(), "call": call.upper(),
                            "equipment": equipment.strip()}
            self._broadcast({"station_info": self.station})

    def set_home(self, home: tuple[float, float] | None, picked: bool = False) -> None:
        """picked=True quando la posizione è stata scelta sulla mappa: la GUI
        la riceve e la copia nel campo "Posizione ricevente"."""
        with self.lock:
            self.home = home
            self._broadcast({"home": home, "picked": picked})

    def snapshot(self) -> str:
        with self.lock:
            return json.dumps({"stations": list(self.stations.values()),
                               "log": list(self.log), "home": self.home,
                               "total": self.total, "history": self.history is not None,
                               "station_info": self.station,
                               "weather": list(self.weather.values()),
                               "telemetry": sorted(self.telemetry),
                               "features": {"messages": self.messages is not None,
                                            "alerts": self.alerts is not None,
                                            "panel": self.panel is not None},
                               "tiles": (self.tiles.client_config() if self.tiles else None)})


def make_handler(store: StationStore):
    from .lingua import dictionary, language, t
    failures = _Failures()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):    # silenzia il log di accesso
            pass

        def _send(self, body: bytes, ctype: str, code: int = 200, cache: str = "no-store"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path          # ignora ?scegli=1 ecc.
            if path in ("/", "/index.html"):
                self._send((STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif path in ICONS:
                f = STATIC / path[1:]
                ctype = "image/svg+xml" if path.endswith(".svg") else "image/png"
                self._send(f.read_bytes(), ctype)
            elif path == "/api/state":
                self._send(store.snapshot().encode(), "application/json")
            elif path == "/api/gpx":
                self._gpx()
            elif path == "/api/copertura":
                self._coverage()
            elif path.startswith("/tiles/"):
                self._tile(path)
            elif path.startswith("/simboli/"):
                m = re.fullmatch(r"/simboli/aprs-symbols-24-([012])\.png", path)
                if not m:
                    return self._send(b"not found", "text/plain", 404)
                data = store.tiles.symbol_sheet(m.group(1)) if store.tiles else None
                if data is None:
                    # il programma non è riuscito a scaricarli (rete, proxy): ci prova il browser
                    # dalla CDN jsDelivr; il rimando resta in cache un'ora
                    self.send_response(302)
                    self.send_header("Location", SYMBOL_CDN.format(n=m.group(1)))
                    self.send_header("Cache-Control", "public, max-age=3600")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self._send(data, "image/png", cache="public, max-age=86400")
            elif path.startswith("/static/"):
                self._static(path)
            elif path == "/events":
                self._events()
            elif path == "/pannello":
                self._send((STATIC / "pannello.html").read_bytes(), "text/html; charset=utf-8")
            elif path.startswith("/api/"):
                self._api_get(path)
            else:
                self._send(b"not found", "text/plain", 404)

        # ---------------------------------------------------------------- accesso
        def _local(self) -> bool:
            return self.client_address[0] in LOCAL

        def _authorized(self) -> bool:
            """Cambiare qualcosa (inviare messaggi, regole, pannello) si può dal computer su cui
            gira il programma oppure, se è impostata, con la password del pannello."""
            if self._local():
                return True
            if not store.panel_password:
                return False
            ip = self.client_address[0]
            h = self.headers.get("Authorization", "")
            if not h.startswith("Basic ") or failures.blocked(ip):
                return False
            try:
                _, _, pw = base64.b64decode(h[6:], validate=True).decode("utf-8").partition(":")
            except (ValueError, UnicodeDecodeError):
                failures.add(ip)
                return False
            if check_password(pw, store.panel_password):
                return True
            failures.add(ip)
            return False

        def _deny(self):
            if store.panel_password and failures.blocked(self.client_address[0]):
                return self._json({"error": t("Troppi tentativi sbagliati: riprova tra qualche minuto.")}, 429)
            if store.panel_password:
                body = json.dumps({"error": t("Serve la password del pannello.")}).encode()
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="APRS Master", charset="UTF-8"')
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return None
            return self._json({"error": t("Si può fare solo dal computer su cui gira APRS Master "
                                          "(oppure imposta la password del pannello).")}, 403)

        def _json(self, obj, code: int = 200):
            self._send(json.dumps(obj).encode(), "application/json", code)

        def _body(self, limit: int = 65536) -> dict:
            n = min(int(self.headers.get("Content-Length", 0) or 0), limit)
            data = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(data, dict):
                raise ValueError
            return data

        # ---------------------------------------------------------------- API
        def _api_get(self, path):
            q = parse_qs(urlsplit(self.path).query)
            one = lambda k, d="": (q.get(k) or [d])[0]
            if path == "/api/whoami":
                return self._json({"write": self._authorized(), "local": self._local(),
                                   "password": bool(store.panel_password), "lang": language()})
            if path == "/api/login":
                return self._json({"ok": True}) if self._authorized() else self._deny()
            if path == "/api/lingua":
                lang = one("lang") if one("lang") in ("it", "en") else language()
                return self._json({"lang": lang, "dict": dictionary(lang)})
            if path == "/api/meteo":
                key = one("id")
                if not key:
                    with store.lock:
                        return self._json({"weather": list(store.weather.values())})
                try:
                    hours = max(1.0, min(24 * 31, float(one("ore", "24"))))
                except ValueError:
                    hours = 24.0
                d = store.weather_detail(key, hours)
                return self._json(d) if d else self._json({"error": t("Nessun dato meteo per questa stazione.")}, 404)
            if path == "/api/telemetria":
                d = store.telemetry_detail(one("id"))
                return self._json(d) if d else self._json({"error": t("Nessuna telemetria per questa stazione.")}, 404)
            if path == "/api/messaggi":
                if store.messages is None:
                    return self._json({"error": "off"}, 404)
                snap = store.messages.snapshot()
                snap["write"] = self._authorized()
                return self._json(snap)
            if path == "/api/avvisi":
                if store.alerts is None:
                    return self._json({"error": "off"}, 404)
                return self._json(store.alerts.snapshot())
            if path == "/api/pannello":
                if store.panel is None:
                    return self._json({"error": "off"}, 404)
                if not self._authorized():
                    return self._deny()
                return self._json(store.panel.status())
            self._send(b"not found", "text/plain", 404)

        def _static(self, path):
            """File statici della pagina (Leaflet locale), solo dentro la cartella static."""
            target = (STATIC / path[len("/static/"):]).resolve()
            types = {".js": "application/javascript", ".css": "text/css", ".png": "image/png",
                     ".svg": "image/svg+xml", ".txt": "text/plain; charset=utf-8"}
            if (not target.is_relative_to(STATIC.resolve()) or not target.is_file()
                    or target.suffix not in types):
                return self._send(b"not found", "text/plain", 404)
            self._send(target.read_bytes(), types[target.suffix], cache="public, max-age=86400")

        def _tile(self, path):
            m = re.fullmatch(r"/tiles/(\d+)/(\d+)/(\d+)(?:\.\w+)?", path)
            res = store.tiles.tile(*map(int, m.groups())) if (m and store.tiles) else None
            if res is None:
                return self._send(b"", "text/plain", 404)
            self._send(res[0], res[1], cache="public, max-age=86400")

        def _coverage(self):
            """/api/copertura?giorni=7 (0 = tutto lo storico)"""
            from .storico import coverage
            band = parse_qs(urlsplit(self.path).query).get("banda", ["vhf"])[0]
            origin = "lora" if band == "lora" else "rf"
            if store.history is None:
                return self._send(b'{"error": "storico disattivato"}', "application/json", 404)
            q = parse_qs(urlsplit(self.path).query)
            try:
                days = float(q.get("giorni", ["7"])[0])
            except ValueError:
                days = 7.0
            since = time.time() - days * 86400 if days > 0 else None
            self._send(json.dumps(coverage(store.history, since, store.home, origin)).encode(),
                       "application/json")

        def _gpx(self):
            """/api/gpx?id=NOMINATIVO&ore=24 (id ripetibile; senza id: tutte)"""
            from . import __version__
            from .storico import safe_filename, to_gpx
            if store.history is None:
                return self._send("Storico disattivato".encode(), "text/plain; charset=utf-8", 404)
            q = parse_qs(urlsplit(self.path).query)
            ids = q.get("id") or None
            try:
                hours = float(q.get("ore", ["24"])[0])
            except ValueError:
                hours = 24.0
            since = time.time() - hours * 3600 if hours > 0 else None
            tracks = store.history.tracks(since=since, stations=ids)
            if not tracks:
                return self._send("Nessuna posizione nel periodo richiesto".encode(),
                                  "text/plain; charset=utf-8", 404)
            gpx, _ = to_gpx(tracks, f"APRS Master {__version__}")
            name = safe_filename(ids[0] if ids and len(ids) == 1 else "tracce_aprs")
            body = gpx.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/gpx+xml")
            self.send_header("Content-Disposition",
                             f'attachment; filename="{name}_{time.strftime("%Y%m%d")}.gpx"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            path = urlsplit(self.path).path
            if path not in ("/api/home", "/api/messaggi", "/api/messaggi/letti", "/api/avvisi",
                            "/api/avvisi/cancella", "/api/pannello"):
                return self._send(b"not found", "text/plain", 404)
            # solo dal computer su cui gira il programma (o con la password del pannello),
            # anche se la mappa è condivisa in rete locale
            if not self._authorized():
                return self._deny()
            try:
                data = self._body()
            except (ValueError, TypeError):
                return self._json({"error": t("Richiesta non valida.")}, 400)
            if path == "/api/home":
                try:
                    lat, lon = float(data["lat"]), float(data["lon"])
                    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                        raise ValueError
                except (ValueError, KeyError, TypeError):
                    return self._json({"error": t("coordinate non valide")}, 400)
                store.set_home((round(lat, 6), round(lon, 6)), picked=True)
                return self._json({"ok": True})
            if path.startswith("/api/messaggi"):
                if store.messages is None:
                    return self._json({"error": "off"}, 404)
                if path.endswith("/letti"):
                    store.messages.mark_read(str(data.get("peer") or "") or None)
                    return self._json({"ok": True})
                try:
                    return self._json(store.messages.send(str(data.get("to", "")), str(data.get("text", ""))))
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
            if path.startswith("/api/avvisi"):
                if store.alerts is None:
                    return self._json({"error": "off"}, 404)
                if path.endswith("/cancella"):
                    store.alerts.clear()
                    store.push({"alerts_cleared": True})
                    return self._json({"ok": True})
                store.alerts.configure(data.get("watch", ""), data.get("zones", []),
                                       data.get("emergency", True), data.get("messages", True),
                                       data.get("quiet_min", 30))
                rules = store.alerts.rules()
                if store.on_rules:
                    store.on_rules(rules)
                store.push({"alert_rules": rules})
                return self._json(rules)
            if store.panel is None:
                return self._json({"error": "off"}, 404)
            try:
                return self._json(store.panel.command(data))
            except ValueError as e:
                return self._json({"error": str(e)}, 400)

        def _events(self):
            q = store.subscribe()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                while True:
                    try:
                        data = q.get(timeout=15)
                        self.wfile.write(f"data: {data}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                store.unsubscribe(q)

    return Handler


class WebServer:
    """Server della mappa; può essere riavviato su un'altra porta/interfaccia."""

    def __init__(self, store: StationStore):
        self.store = store
        self.srv: ThreadingHTTPServer | None = None
        self.addr: tuple[str, int] | None = None

    def ensure(self, bind: str, port: int) -> None:
        if self.srv and self.addr == (bind, port):
            return
        self.stop()
        srv = ThreadingHTTPServer((bind, port), make_handler(self.store))
        srv.daemon_threads = True
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.srv, self.addr = srv, (bind, port)

    def stop(self) -> None:
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()
            self.srv = self.addr = None

    @property
    def url(self) -> str:
        return f"http://localhost:{self.addr[1]}" if self.addr else ""
