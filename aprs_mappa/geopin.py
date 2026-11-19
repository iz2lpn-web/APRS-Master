# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pubblicazione dei pacchetti ricevuti sul portale Geopin.

Ogni pacchetto ricevuto (VHF, LoRa e APRS-IS) entra in una coda su disco
(~/.aprs_mappa_geopin.db) e viene inviato al portale a intervalli regolari, a
gruppi, con la chiave del ricevitore. La coda sopravvive alla chiusura del
programma e alle interruzioni di rete: quello che non parte ora parte al giro
successivo. Ogni pacchetto ha un numero progressivo, così un invio ripetuto
dopo un errore di rete non crea doppioni sul portale.
"""
from __future__ import annotations

import gzip
import json
import logging
import sqlite3
import threading
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit
from pathlib import Path
from typing import Callable

from . import __version__, rete

log = logging.getLogger("geopin")

QUEUE_PATH = Path.home() / ".aprs_mappa_geopin.db"
INTERVALS = (1, 5, 15, 30, 60)          # minuti proposti nel pannello
BATCH = 2000                            # pacchetti per richiesta
MAX_QUEUE = 300_000                     # oltre si scartano i più vecchi
TIMEOUT_S = 30


def publish_url(address: str) -> str:
    """Accetta l'indirizzo del portale (https://geopin.esempio.it/) oppure
    quello completo di pubblicazione, e ritorna quest'ultimo."""
    a = address.strip()
    if not a:
        return ""
    if "://" not in a:
        a = "https://" + a
    if a.lower().endswith(".php"):
        return a
    return a.rstrip("/") + "/api/publish.php"


LINK_PREFIX = "geopin1:"


def encode_link(address: str, key: str) -> str:
    """Codice di collegamento: indirizzo del portale e chiave in un'unica riga da copiare."""
    import base64
    data = json.dumps({"u": address.strip(), "k": key.strip()}, separators=(",", ":")).encode()
    return LINK_PREFIX + base64.urlsafe_b64encode(data).decode().rstrip("=")


def decode_link(code: str) -> tuple[str, str] | None:
    """(indirizzo, chiave) dal codice di collegamento mostrato da Geopin, o None se non lo è."""
    import base64
    c = "".join(code.split())
    if not c.lower().startswith(LINK_PREFIX):
        return None
    body = c[len(LINK_PREFIX):]
    try:
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        url, key = str(data["u"]).strip(), str(data["k"]).strip()
    except (ValueError, KeyError, TypeError):
        return None
    if not url or not key or len(url) > 500 or len(key) > 200:
        return None
    return url, key


def receiver_status(rx, cfg) -> dict:
    """Stato del ricevitore che Geopin mostra accanto al nome della stazione."""
    try:
        home = cfg.home_tuple()
    except ValueError:
        home = None
    src = {"audio": "Scheda audio", "kiss": "Direwolf/TNC", "wav": "File WAV", "none": "Nessuna VHF"}
    store = getattr(rx, "store", None)
    if store is not None and getattr(store, "home", None):
        home = store.home                  # anche se scelta sulla mappa a ricezione avviata
    s = {"running": rx.running, "source": src.get(cfg.source, cfg.source), "home": home}
    st = getattr(store, "station", None) or {}
    if st:
        # come appare la stazione ricevente: Geopin la evidenzia sulla mappa come fa APRS Master
        s["station"] = {k: st.get(k, "") for k in ("call", "label", "table", "code", "comment", "equipment")}
    if rx.running and rx.aprsis is not None:
        s["aprsis"] = {"connected": rx.aprsis.connected, "igate": rx.aprsis.igate}
    if rx.running and rx.lora is not None:
        s["lora"] = {"connected": rx.lora.connected}
    return s



class GeopinQueue:
    """Coda persistente dei pacchetti da pubblicare (SQLite, accesso da più thread)."""

    def __init__(self, path: Path | str = QUEUE_PATH):
        self.path = str(path)
        self.lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        with self.lock, self.conn:
            self.conn.execute("CREATE TABLE IF NOT EXISTS queue (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                              " t REAL NOT NULL, origin TEXT NOT NULL, raw TEXT NOT NULL)")
            self.conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
            row = self.conn.execute("SELECT v FROM meta WHERE k = 'run'").fetchone()
            if row is None:
                # identifica questa coda: i numeri progressivi valgono dentro la stessa coda
                self.run = uuid.uuid4().hex
                self.conn.execute("INSERT INTO meta (k, v) VALUES ('run', ?)", (self.run,))
            else:
                self.run = row[0]
        self._since_trim = 0

    def add(self, t: float, origin: str, raw: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO queue (t, origin, raw) VALUES (?, ?, ?)", (t, origin, raw))
            self._since_trim += 1
            if self._since_trim >= 1000:
                self._since_trim = 0
                n = self.conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]
                if n > MAX_QUEUE:
                    self.conn.execute("DELETE FROM queue WHERE id IN (SELECT id FROM queue ORDER BY id LIMIT ?)",
                                      (n - MAX_QUEUE,))
                    log.warning("Geopin: coda piena, scartati %d pacchetti vecchi", n - MAX_QUEUE)

    def batch(self, n: int = BATCH) -> list[tuple[int, float, str, str]]:
        with self.lock:
            return self.conn.execute("SELECT id, t, origin, raw FROM queue ORDER BY id LIMIT ?", (n,)).fetchall()

    def remove_upto(self, last_id: int) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM queue WHERE id <= ?", (last_id,))

    def count(self) -> int:
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM queue").fetchone()[0]

    def clear(self) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM queue")

    def close(self) -> None:
        with self.lock:
            self.conn.close()


class GeopinError(Exception):
    pass


def post(url: str, key: str, body: dict, timeout: float = TIMEOUT_S) -> dict:
    """Invia una richiesta al portale e ritorna la risposta JSON; solleva
    GeopinError con un messaggio comprensibile."""
    data = gzip.compress(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json", "Content-Encoding": "gzip",
        "Authorization": f"Bearer {key}", "User-Agent": f"APRSMaster/{__version__} (Geopin)"})
    try:
        with rete.urlopen(req, timeout=timeout) as r:      # verifica TLS robusta, vedi rete.py
            payload = r.read()
    except urllib.error.HTTPError as e:
        msg = ""
        try:
            msg = json.loads(e.read().decode("utf-8", "replace")).get("error", "")
        except (ValueError, OSError, AttributeError):
            pass
        if e.code == 403:
            raise GeopinError(msg or "chiave non valida") from None
        if e.code == 404:
            raise GeopinError("indirizzo del portale errato (pagina non trovata)") from None
        raise GeopinError(f"il portale ha risposto {e.code}" + (f": {msg}" if msg else "")) from None
    except urllib.error.URLError as e:
        import ssl
        if isinstance(e.reason, ssl.SSLCertVerificationError):
            raise GeopinError(rete.explain(e, urlsplit(url).hostname or "")) from None
        raise GeopinError(f"portale non raggiungibile ({e.reason})") from None
    except (TimeoutError, OSError) as e:
        raise GeopinError(f"portale non raggiungibile ({e})") from None
    try:
        res = json.loads(payload.decode("utf-8"))
    except ValueError:
        raise GeopinError("risposta non valida: l'indirizzo è davvero quello di Geopin?") from None
    if not isinstance(res, dict) or not res.get("ok"):
        raise GeopinError((res or {}).get("error", "risposta non valida") if isinstance(res, dict)
                          else "risposta non valida")
    return res


class GeopinPublisher:
    """Thread che pubblica la coda ogni interval_min minuti (o subito con publish_now)."""

    def __init__(self, address: str, key: str, interval_min: int = 5,
                 queue_path: Path | str = QUEUE_PATH,
                 status_fn: Callable[[], dict] | None = None):
        self.url = publish_url(address)
        self.key = key.strip()
        self.interval_s = max(1, int(interval_min)) * 60
        self.queue = GeopinQueue(queue_path)
        self.status_fn = status_fn          # stato del ricevitore da mostrare sul portale
        self.stop_evt = threading.Event()
        self.now_evt = threading.Event()
        self.thread: threading.Thread | None = None
        self.status = "In attesa della prima pubblicazione"
        self.ok = None                      # True/False dopo il primo tentativo
        self.last_ok: float | None = None   # ora dell'ultima pubblicazione riuscita
        self.next_at: float | None = None
        self.sent_total = 0
        self.receiver = ""                  # nome del ricevitore sul portale
        self.busy = False
        self.publish_rf = True              # False con i file WAV: non sono ricezioni in diretta

    # ------------------------------------------------------------ comandi
    def add(self, t: float, origin: str, raw: str) -> None:
        if origin == "rf" and not self.publish_rf:
            return
        try:
            self.queue.add(t, origin, raw)
        except sqlite3.Error as e:          # un problema alla coda non ferma la ricezione
            log.error("Geopin: coda non disponibile (%s)", e)

    def start(self) -> None:
        self.stop_evt.clear()
        self.next_at = time.time() + 10     # primo invio poco dopo l'avvio (stato e arretrati)
        self.thread = threading.Thread(target=self._run, daemon=True, name="geopin")
        self.thread.start()

    def publish_now(self) -> None:
        self.now_evt.set()

    def stop(self, final: bool = True, timeout: float = 15) -> None:
        """Ferma il thread; con final=True prova un'ultima pubblicazione."""
        self.stop_evt.set()
        self.now_evt.set()
        if self.thread:
            self.thread.join(timeout)
        if final and not (self.thread and self.thread.is_alive()):
            try:
                self._publish_all(stopping=True, deadline=time.time() + timeout)
            except Exception as e:            # all'uscita non si mostra nulla
                log.warning("Geopin: ultima pubblicazione non riuscita (%s)", e)
        self.queue.close()

    @property
    def pending(self) -> int:
        try:
            return self.queue.count()
        except sqlite3.Error:
            return 0

    def test(self) -> str:
        """Prova collegamento e chiave senza inviare pacchetti; ritorna una descrizione."""
        if not self.url or not self.key:
            raise GeopinError("indica indirizzo e chiave")
        res = post(self.url, self.key, {"run": self.queue.run, "packets": [], "status": {"test": True}},
                   timeout=15)
        r = res.get("receiver") or {}
        return f"Collegamento riuscito: ricevitore \"{r.get('name', '?')}\" di {r.get('owner', '?')}."

    # ------------------------------------------------------------ interno
    def _status(self, stopping: bool = False) -> dict:
        s: dict = {"version": __version__, "interval_min": self.interval_s // 60, "pending": self.pending}
        if self.status_fn:
            try:
                s.update(self.status_fn() or {})
            except Exception:                  # lo stato è un di più: mai bloccare l'invio
                pass
        if stopping:
            s["stopped"] = True
        return s

    def _publish_all(self, stopping: bool = False, deadline: float | None = None) -> int:
        """Invia tutta la coda a gruppi (almeno una richiesta, che porta anche lo stato)."""
        if not self.url or not self.key:
            raise GeopinError("indirizzo o chiave mancanti")
        sent = 0
        first = True
        while first or not self.stop_evt.is_set() or stopping:
            first = False
            rows = self.queue.batch(BATCH)
            body = {"run": self.queue.run, "status": self._status(stopping),
                    "packets": [{"seq": i, "t": round(t, 3), "o": o, "raw": raw} for i, t, o, raw in rows]}
            tmo = TIMEOUT_S if deadline is None else max(3.0, min(TIMEOUT_S, deadline - time.time()))
            res = post(self.url, self.key, body, timeout=tmo)
            if rows:
                self.queue.remove_upto(rows[-1][0])
            sent += len(rows)
            r = res.get("receiver") or {}
            self.receiver = r.get("name", self.receiver)
            if len(rows) < BATCH or (deadline and time.time() > deadline):
                break
        return sent

    def _run(self) -> None:
        delay_err = 60
        while not self.stop_evt.is_set():
            wait = max(0.0, (self.next_at or 0) - time.time())
            self.now_evt.wait(wait)
            if self.stop_evt.is_set():
                break
            self.now_evt.clear()
            self.busy = True
            try:
                n = self._publish_all()
                self.sent_total += n
                self.ok, self.last_ok = True, time.time()
                what = "Nessun pacchetto nuovo" if n == 0 else ("Pubblicato 1 pacchetto" if n == 1 else f"Pubblicati {n} pacchetti")
                self.status = (f"{what} alle {time.strftime('%H:%M', time.localtime())}"
                               + (f" su \"{self.receiver}\"" if self.receiver else ""))
                self.next_at = time.time() + self.interval_s
                delay_err = 60
                log.info("Geopin: %s", self.status)
            except GeopinError as e:
                self.ok = False
                self.status = f"Pubblicazione non riuscita: {e}"
                # dopo un errore si riprova prima, ma senza insistere (al massimo l'intervallo)
                self.next_at = time.time() + min(delay_err, self.interval_s)
                delay_err = min(delay_err * 2, 1800)
                log.warning("Geopin: %s", e)
            except sqlite3.Error as e:
                self.ok = False
                self.status = f"Coda non disponibile: {e}"
                self.next_at = time.time() + self.interval_s
            finally:
                self.busy = False
