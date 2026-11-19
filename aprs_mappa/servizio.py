# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Messaggi e avvisi collegati al ricevitore (Engine), e APRS Master come servizio senza finestra
(Service) con il pannello di controllo nel browser: pensato per un Raspberry Pi in soffitta.

  aprs-mappa --servizio                       # usa la configurazione salvata dal pannello grafico
  aprs-mappa --servizio --config /etc/aprs-master.json
  aprs-mappa --panel-password                 # imposta la password del pannello web
  aprs-mappa --installa-servizio              # scrive il file per systemd
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
import time
from dataclasses import asdict, fields
from pathlib import Path

from . import __version__
from .avvisi import AlertEngine
from .lingua import t
from .messaggi import DB_PATH as MSG_DB, MessageCenter
from .receiver import CONFIG_PATH, Config, Receiver

log = logging.getLogger("servizio")


class Engine:
    """Collega centro messaggi e avvisi al ricevitore e alla mappa; lo usano GUI e servizio."""

    def __init__(self, store, rx: Receiver, db_path=MSG_DB):
        self.store, self.rx = store, rx
        self.messages = MessageCenter(db_path)
        self.alerts = AlertEngine()
        self.callbacks: list = []          # funzioni (tipo, evento) per la GUI
        store.messages, store.alerts = self.messages, self.alerts
        store.listeners.append(self._on_packet)
        self.messages.on_event = self._on_message
        self.alerts.on_alert = self._on_alert
        self.messages.sender = self._send
        self.messages.can_send = self._can_send
        self.messages.start()

    def configure(self, cfg: Config) -> None:
        self.messages.configure(cfg.aprsis_call if cfg.messages_enabled else "",
                                [c for c in cfg.messages_calls.split(",") if c.strip()]
                                if cfg.messages_enabled else [])
        self.alerts.configure(cfg.alert_watch, cfg.alert_zones, cfg.alert_emergency, cfg.alert_messages,
                              cfg.alert_quiet_min)

    def _send(self, line: str) -> bool:
        c = self.rx.aprsis
        return bool(c and c.send(line))

    def _can_send(self) -> bool:
        c = self.rx.aprsis
        return bool(self.rx.running and c and c.connected and c.verified and self.messages.my_call)

    def _on_packet(self, pkt, raw, origin, now) -> None:
        if pkt.message and self.messages.my_call:
            self.messages.handle(pkt.source, pkt.message, origin, now)
        self.alerts.check(pkt, origin, now)

    def _on_message(self, ev: dict) -> None:
        self.store.push({"msg_event": ev, "unread": self.messages.unread()})
        self.alerts.message(ev)
        self._fire("message", ev)

    def _on_alert(self, a: dict) -> None:
        self.store.push({"alert": a})
        self._fire("alert", a)

    def _fire(self, kind: str, ev: dict) -> None:
        for cb in list(self.callbacks):
            try:
                cb(kind, ev)
            except Exception:
                log.exception("notifica non riuscita")

    def close(self) -> None:
        self.messages.close()


# campi della configurazione che il pannello web può cambiare (le password a parte)
PANEL_FIELDS = (
    "source", "device", "rate", "channel", "kiss_host", "kiss_port", "home", "filters",
    "demod_profile", "fix_bits", "history_enabled", "history_days", "history_load_hours",
    "station_type", "station_comment", "station_equipment",
    "geopin_enabled", "geopin_url", "geopin_key", "geopin_interval_min",
    "aprsis_enabled", "aprsis_call", "aprsis_server", "aprsis_port", "aprsis_radius_km",
    "igate_enabled", "aprsis_beacon",
    "lora_enabled", "lora_mode", "lora_serial", "lora_baud", "lora_host", "lora_port",
    "map_source", "map_url", "map_attribution", "mbtiles_path", "map_cache_mb",
    "messages_enabled", "messages_calls",
    "alert_watch", "alert_zones", "alert_emergency", "alert_messages", "alert_quiet_min",
    "language",
)
SECRET_FIELDS = ("aprsis_passcode",)


def validate(cfg: Config) -> str | None:
    """Primo errore della configurazione, in parole, o None."""
    from .aprsis import comment_error, equipment_error, valid_callsign
    if cfg.source not in ("audio", "kiss", "wav", "none"):
        return t("Sorgente non valida.")
    try:
        cfg.home_tuple()
    except ValueError:
        return t("La posizione ricevente va scritta come lat,lon (es. 44.49,11.34).")
    if cfg.aprsis_enabled and not valid_callsign(cfg.aprsis_call):
        return t("Per APRS-IS serve un nominativo valido (es. IZ2LPN-10).")
    if cfg.igate_enabled and not cfg.aprsis_passcode.strip():
        return t("L'iGate richiede il passcode APRS-IS.")
    if cfg.geopin_enabled and not (cfg.geopin_url.strip() and cfg.geopin_key.strip()):
        return t("Per Geopin servono l'indirizzo del portale e la chiave del ricevitore.")
    if cfg.source == "wav" and not cfg.wav:
        return t("Scegli il file WAV.")
    if cfg.source == "none" and not (cfg.lora_enabled or cfg.aprsis_enabled):
        return t("Senza sorgente VHF serve LoRa oppure APRS-IS.")
    return comment_error(cfg.station_comment) or equipment_error(cfg.station_equipment)


def apply_changes(cfg: Config, data: dict) -> Config:
    """Nuova configurazione con i valori del pannello (solo i campi ammessi, convertiti al tipo giusto)."""
    from .avvisi import clean_zones
    cur = asdict(cfg)
    types = {f.name: type(cur[f.name]) for f in fields(Config)}
    for name, value in data.items():
        if name not in PANEL_FIELDS and name not in SECRET_FIELDS:
            continue
        if name in SECRET_FIELDS and not str(value or "").strip():
            continue                       # vuoto = lascia quella che c'è
        ty = types[name]
        try:
            if ty is bool:
                v = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "on", "si", "sì", "yes")
            elif ty is int:
                v = int(float(value))
            elif ty is list:
                if name == "alert_zones":
                    v = clean_zones(value if isinstance(value, list) else [])
                else:
                    v = [str(x).strip() for x in (value if isinstance(value, list) else str(value).split(","))
                         if str(x).strip()]
            else:
                v = str(value).strip()
        except (TypeError, ValueError):
            raise ValueError(t("Valore non valido per {name}.", name=name)) from None
        cur[name] = v
    return Config(**cur)


class Service:
    """Ricevitore, mappa, Geopin, messaggi e avvisi senza finestra, comandati dal pannello web."""

    def __init__(self, config_path: Path | str = CONFIG_PATH, messages_db: Path | str = MSG_DB):
        from .lingua import set_language
        from .server import StationStore, WebServer
        self.config_path = Path(config_path)
        self.cfg = Config.load(self.config_path)
        set_language(self.cfg.language)
        self.started = time.time()
        self.lock = threading.RLock()
        self.history = None
        self._open_history()
        self.store = StationStore(None, history=self.history, tiles=self._tiles())
        self.store.panel = self
        self.store.panel_password = self.cfg.panel_password
        self.store.on_rules = self._save_rules
        self.rx = Receiver(self.store)
        self.engine = Engine(self.store, self.rx, messages_db)
        self.web = WebServer(self.store)
        self.publisher = None
        self.message = ""
        self._stop = threading.Event()

    # ------------------------------------------------------------ avvio
    def _open_history(self) -> None:
        if self.history is not None:
            return
        if self.cfg.history_enabled:
            from .storico import History
            try:
                self.history = History()
                self.history.prune(self.cfg.history_days)
            except Exception as e:
                log.error("Storico non disponibile (%s): continuo senza", e)
                self.history = None

    def _tiles(self):
        from .mappe import TileService
        c = self.cfg
        tiles = TileService(c.map_source, c.map_url, c.map_attribution, c.mbtiles_path, c.map_cache_mb,
                            contact=c.aprsis_call.split("-")[0])
        if tiles.error:
            log.error("Mappe: %s. Uso OpenStreetMap.", tiles.error)
        return tiles

    def run(self) -> None:
        import signal
        self.web.ensure(self.cfg.bind, self.cfg.web_port)
        log.info("APRS Master %s come servizio: mappa su http://%s:%d/ e pannello su /pannello",
                 __version__, self.cfg.bind, self.cfg.web_port)
        if self.cfg.bind not in ("127.0.0.1", "localhost", "::1") and not self.cfg.panel_password:
            log.warning("Pannello web senza password: si comanda solo dal computer stesso. "
                        "Imposta la password con: aprs-mappa --panel-password")
        if self.history is not None:
            n = self.store.load_history(self.cfg.history_load_hours)
            if n:
                log.info("Ricaricate dallo storico %d posizioni", n)
        try:
            self.start()
        except Exception as e:
            self.message = str(e)
            log.error("Ricezione non avviata: %s", e)
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, lambda *_: self._stop.set())
            except (ValueError, OSError):
                pass
        try:
            while not self._stop.wait(1):
                pass
        finally:
            self.shutdown()

    def start(self) -> None:
        with self.lock:
            if self.rx.running:
                return
            err = validate(self.cfg)
            if err:
                raise ValueError(err)
            c = self.cfg
            try:
                home = c.home_tuple()
            except ValueError:
                home = None
            self.store.set_home(home)
            self.store.set_station(c.station_type, c.station_comment, c.aprsis_call, c.station_equipment)
            self.engine.configure(c)
            if c.geopin_enabled:
                from .geopin import GeopinPublisher, receiver_status
                self.publisher = GeopinPublisher(c.geopin_url, c.geopin_key, c.geopin_interval_min,
                                                 status_fn=lambda: receiver_status(self.rx, self.cfg))
                self.publisher.start()
            self.rx.publisher = self.publisher
            self.rx.start(c)
            self.message = t("Ricezione avviata")
            log.info("Ricezione avviata (%s)", c.source)
        self._push()

    def stop(self) -> None:
        with self.lock:
            if self.rx.running:
                self.rx.stop()
            if self.publisher is not None:
                self.publisher.stop(final=True)
                self.publisher = None
            self.rx.publisher = None
            self.message = t("Ricezione fermata")
        self._push()

    def shutdown(self) -> None:
        log.info("Uscita: fermo la ricezione")
        self.stop()
        self.web.stop()
        self.engine.close()
        if self.history is not None:
            self.history.close()

    # ------------------------------------------------------------ pannello
    def status(self) -> dict:
        rx, c = self.rx, self.cfg
        out = {"version": __version__, "running": rx.running, "status": rx.status, "error": rx.error,
               "message": self.message, "uptime_s": round(time.time() - self.started),
               "frames": rx.frames, "fixed": rx.fixed, "total": self.store.total,
               "stations": len(self.store.stations), "peak": round(rx.peak, 3),
               "aprsis": None, "lora": None, "geopin": None,
               "config": {k: getattr(c, k) for k in PANEL_FIELDS},
               "secrets": {k: bool(getattr(c, k)) for k in SECRET_FIELDS},
               "password": bool(c.panel_password), "config_path": str(self.config_path),
               "devices": self._devices(), "host": _host_info()}
        if rx.aprsis is not None:
            a = rx.aprsis
            out["aprsis"] = {"status": a.status, "connected": a.connected, "verified": a.verified,
                             "gated": a.gated, "received": a.received, "beacon": a.beacon_info}
        if rx.lora is not None:
            out["lora"] = {"connected": rx.lora.connected, "frames": rx.handler.lora_frames if rx.handler else 0}
        if self.publisher is not None:
            p = self.publisher
            out["geopin"] = {"pending": p.pending, "status": p.status, "ok": p.ok,
                             "sent": p.sent_total, "receiver": p.receiver}
        out["unread"] = self.engine.messages.unread()
        return out

    def _devices(self) -> list:
        try:
            from .receiver import list_input_devices
            return [{"i": i, "name": n} for i, n in list_input_devices()]
        except Exception:
            return []

    def command(self, data: dict) -> dict:
        action = str(data.get("action", ""))
        if action == "start":
            self.start()
        elif action == "stop":
            self.stop()
        elif action == "restart":
            self.stop()
            self.start()
        elif action == "save":
            self.save(data.get("config") or {}, bool(data.get("restart", True)))
        elif action == "password":
            pw = str(data.get("password", ""))
            if pw and len(pw) < 8:
                raise ValueError(t("La password deve avere almeno 8 caratteri."))
            from .server import hash_password
            with self.lock:
                self.cfg.panel_password = hash_password(pw) if pw else ""
                self.cfg.save(self.config_path)
                self.store.panel_password = self.cfg.panel_password
            self.message = t("Password del pannello cambiata") if pw else t("Password del pannello tolta")
        elif action == "geopin_code":
            from .geopin import decode_link
            d = decode_link(str(data.get("code", "")))
            if d is None:
                raise ValueError(t("Codice di collegamento Geopin non valido."))
            self.save({"geopin_enabled": True, "geopin_url": d[0], "geopin_key": d[1]}, True)
        elif action == "geopin_test":
            from .geopin import GeopinError, GeopinPublisher
            c = self.cfg
            if not (c.geopin_url and c.geopin_key):
                raise ValueError(t("Per Geopin servono l'indirizzo del portale e la chiave del ricevitore."))
            p = GeopinPublisher(c.geopin_url, c.geopin_key)
            try:
                self.message = p.test()
            except GeopinError as e:
                raise ValueError(t("Collegamento non riuscito: {e}", e=e)) from None
            finally:
                p.stop(final=False)
        elif action == "geopin_now":
            if self.publisher is None:
                raise ValueError(t("La pubblicazione su Geopin non è attiva."))
            self.publisher.publish_now()
            self.message = t("Pubblicazione su Geopin avviata")
        else:
            raise ValueError(t("Comando sconosciuto."))
        st = self.status()
        self._push()
        return st

    def save(self, changes: dict, restart: bool = True) -> None:
        with self.lock:
            new = apply_changes(self.cfg, changes)
            new.panel_password = self.cfg.panel_password
            err = validate(new)
            if err:
                raise ValueError(err)
            was = self.rx.running
            if was and restart:
                self.stop()
            map_changed = any(getattr(new, k) != getattr(self.cfg, k) for k in
                              ("map_source", "map_url", "map_attribution", "mbtiles_path", "map_cache_mb"))
            self.cfg = new
            self.cfg.save(self.config_path)
            from .lingua import set_language
            set_language(new.language)
            if map_changed:
                self.store.set_tiles(self._tiles())
            self.engine.configure(new)
            self.message = t("Configurazione salvata")
            if (was and restart) or (restart and not was and changes.get("_start")):
                self.start()

    def _save_rules(self, rules: dict) -> None:
        """Le regole degli avvisi cambiate dalla mappa si salvano nella configurazione."""
        with self.lock:
            self.cfg.alert_watch = ", ".join(rules["watch"])
            self.cfg.alert_zones = rules["zones"]
            self.cfg.alert_emergency, self.cfg.alert_messages = rules["emergency"], rules["messages"]
            self.cfg.alert_quiet_min = rules["quiet_min"]
            self.cfg.save(self.config_path)

    def _push(self) -> None:
        try:
            self.store.push({"panel": True})
        except Exception:
            pass


def _host_info() -> dict:
    """Temperatura del processore e spazio libero (Raspberry Pi e Linux in genere)."""
    out: dict = {}
    try:
        out["cpu_temp"] = round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
    except (OSError, ValueError):
        pass
    try:
        out["disk_free_mb"] = shutil.disk_usage(Path.home()).free // (1024 * 1024)
    except OSError:
        pass
    try:
        out["load"] = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        pass
    return out


def systemd_unit(config_path: Path | str = CONFIG_PATH, user: str | None = None) -> str:
    """File per systemd: APRS Master parte all'accensione e si riavvia se si ferma."""
    import getpass
    user = user or getpass.getuser()
    exe = shutil.which("aprs-mappa")
    cmd = f"{exe}" if exe else f"{sys.executable} -m aprs_mappa"
    return f"""[Unit]
Description=APRS Master (ricevitore APRS, mappa e pannello web)
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User={user}
SupplementaryGroups=audio dialout
ExecStart={cmd} --servizio --config {config_path}
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
"""
