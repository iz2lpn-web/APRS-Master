# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Collegamento ad APRS-IS: iGate in sola ricezione (radio -> Internet) e
ricezione dei pacchetti della zona per il confronto con quanto si sente via radio.

Regole di inoltro (https://aprs-is.net/IGateDetails.aspx):
- non si inoltrano pacchetti con TCPIP, TCPXX, NOGATE o RFONLY nel percorso;
- i pacchetti di terze parti ('}') con TCPIP o TCPXX nell'intestazione interna
  non si inoltrano; gli altri si inoltrano togliendo l'intestazione radio;
- le interrogazioni generiche ('?') non si inoltrano;
- il percorso non si modifica, si aggiunge solo ",qAO,NOMINATIVO" (qAO perché
  l'iGate è in sola ricezione);
- nessuna eliminazione dei duplicati lato client: la fa il server.
"""
from __future__ import annotations

import logging
import socket
import threading
import time
from typing import Callable

from . import __version__
from .ax25 import AX25Frame

log = logging.getLogger("aprs-is")

DEFAULT_SERVER = "rotate.aprs2.net"
DEFAULT_PORT = 14580
# identificativo del software nel destinatario dei pacchetti: aprs.fi lo usa per il campo
# "Dispositivo". APZxxx = sperimentale; per uno assegnato vedi RICHIESTA_TOCALL.md
TOCALL = "APZRAP"
BLOCKED_PATH = {"TCPIP", "TCPXX", "NOGATE", "RFONLY"}
BEACON_INTERVAL_S = 30 * 60


def passcode(callsign: str) -> int:
    """Passcode APRS-IS del nominativo (senza SSID). Da usare solo per il
    proprio nominativo: APRS-IS è riservato ai radioamatori."""
    call = callsign.upper().split("-")[0].strip()
    h = 0x73E2
    for i in range(0, len(call), 2):
        h ^= ord(call[i]) << 8
        if i + 1 < len(call):
            h ^= ord(call[i + 1])
    return h & 0x7FFF


def valid_callsign(call: str) -> bool:
    base, _, ssid = call.upper().partition("-")
    if not (3 <= len(base) <= 6 and base.isalnum() and any(c.isdigit() for c in base)):
        return False
    return not ssid or (ssid.isdigit() and 0 <= int(ssid) <= 15)


def _path_blocked(path: list[str]) -> bool:
    return any(p.rstrip("*").upper() in BLOCKED_PATH for p in path)


def gate_line(frame: AX25Frame, igate_call: str) -> str | None:
    """Riga TNC2 da inviare ad APRS-IS per un frame ricevuto via radio,
    oppure None se il frame non va inoltrato."""
    if _path_blocked(frame.path) or _path_blocked([frame.dest]):
        return None
    info = frame.info
    header = frame.source + ">" + ",".join([frame.dest] + frame.path)
    if info[:1] == b"}":
        # terze parti: si valuta e si inoltra il pacchetto interno
        inner = info[1:].decode("latin-1", errors="replace")
        head, sep, payload = inner.partition(":")
        if not sep or ">" not in head:
            return None
        inner_path = head.split(">", 1)[1].split(",")
        if _path_blocked(inner_path):
            return None
        header, info = head, payload.encode("latin-1")
    if info[:1] == b"?":
        return None
    # una riga APRS-IS termina al primo CR/LF
    text = info.decode("latin-1", errors="replace").split("\r")[0].split("\n")[0]
    if not text:
        return None
    return f"{header},qAO,{igate_call.upper()}:{text}"


# Tipi di dispositivo della stazione ricevente: (descrizione, tabella o overlay, simbolo APRS)
STATION_TYPES = {
    "igate_vhf": ("iGate RX VHF", "R", "&"),          # R& = iGate in sola ricezione
    "igate_lora": ("iGate RX LoRa", "L", "&"),        # L& = iGate LoRa
    "fissa": ("Stazione fissa", "/", "-"),            # casa
    "portatile": ("Stazione portatile", "/", ";"),    # attività portatile / campo
}
DEFAULT_STATION_TYPE = "igate_vhf"
COMMENT_MAX = 43          # lunghezza massima del commento di una posizione (APRS 1.0.1)
STATUS_MAX = 62           # lunghezza massima di un pacchetto di stato senza orario


def default_comment() -> str:
    from . import APP_NAME
    return f"iGate RX - {APP_NAME.split(' by ')[0]} {__version__}"


def comment_error(text: str, max_len: int = COMMENT_MAX, what: str = "Il commento") -> str | None:
    """Messaggio d'errore se il testo non è adatto ad APRS, altrimenti None."""
    if len(text) > max_len:
        return f"{what} supera il limite: {len(text)} caratteri su {max_len}."
    bad = sorted({c for c in text if not (" " <= c <= "}") or c == "|"})
    if bad:
        return (f"{what} contiene caratteri non ammessi in APRS: " + " ".join(bad) +
                ". Usa lettere senza accenti, cifre e punteggiatura semplice.")
    return None


def _coord(value: float, deg_digits: int, pos: str, neg: str) -> str:
    hund = round(abs(value) * 6000)                 # centesimi di primo, arrotondati una volta sola
    deg, rest = divmod(hund, 6000)
    return f"{deg:0{deg_digits}d}{rest // 100:02d}.{rest % 100:02d}{pos if value >= 0 else neg}"


def beacon_line(igate_call: str, lat: float, lon: float, station_type: str = DEFAULT_STATION_TYPE,
                comment: str = "") -> str:
    """Annuncio di posizione della stazione ricevente verso APRS-IS."""
    _, table, code = STATION_TYPES.get(station_type, STATION_TYPES[DEFAULT_STATION_TYPE])
    text = comment.strip() or default_comment()
    return (f"{igate_call.upper()}>{TOCALL},TCPIP*:!{_coord(lat, 2, 'N', 'S')}{table}"
            f"{_coord(lon, 3, 'E', 'W')}{code}{text[:COMMENT_MAX]}")


def equipment_error(text: str) -> str | None:
    return comment_error(text, STATUS_MAX, "La descrizione dell'apparato")


def status_line(igate_call: str, equipment: str) -> str:
    """Pacchetto di stato con la descrizione dell'apparato (aprs.fi lo mostra come stato)."""
    return f"{igate_call.upper()}>{TOCALL},TCPIP*:>{equipment.strip()[:STATUS_MAX]}"


class AprsIsClient:
    """Connessione ad APRS-IS in un thread, con riconnessione automatica.

    on_line(riga) riceve le righe dei pacchetti arrivati dal server
    (ricezione per il confronto); gate(frame) accoda un frame radio da inoltrare.
    """

    def __init__(self, callsign: str, pcode: str, server: str = DEFAULT_SERVER,
                 port: int = DEFAULT_PORT, filter_str: str = "", igate: bool = False,
                 home: tuple[float, float] | None = None,
                 on_line: Callable[[str], None] | None = None,
                 station_type: str = DEFAULT_STATION_TYPE, comment: str = "", equipment: str = "",
                 beacon: bool = True):
        self.call = callsign.upper().strip()
        self.pcode = pcode.strip() or "-1"
        self.server, self.port = server.strip() or DEFAULT_SERVER, int(port)
        self.filter_str = filter_str.strip()
        self.igate = igate
        self.home = home
        self.station_type, self.comment = station_type, comment
        self.equipment = equipment
        self.beacon = beacon                # annuncio della stazione anche senza iGate
        self.beacon_delay_s = 10.0          # primo annuncio poco dopo il collegamento
        self.last_beacon: float | None = None
        # posizione aggiornata (scelta sulla mappa a ricezione avviata); None = quella iniziale
        self.home_fn: Callable[[], tuple[float, float] | None] | None = None
        self.igate_note = ""
        self.on_line = on_line
        self.stop_evt = threading.Event()
        self.thread: threading.Thread | None = None
        self.sock: socket.socket | None = None
        self.send_lock = threading.Lock()
        self.connected = False
        self.verified = False
        self.server_name = ""
        self.status = "Non collegato"
        self.gated = 0
        self.received = 0

    # ------------------------------------------------------------ comandi
    def start(self) -> None:
        self.stop_evt.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="aprs-is")
        self.thread.start()

    def stop(self) -> None:
        self.stop_evt.set()
        s = self.sock
        if s:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self.thread:
            self.thread.join(timeout=3)
        self.connected = False
        self.status = "Non collegato"

    def gate(self, frame: AX25Frame) -> bool:
        """Inoltra un frame ricevuto via radio, se l'iGate è attivo e le regole lo consentono."""
        if not (self.igate and self.connected and self.verified):
            return False
        line = gate_line(frame, self.call)
        if line is None:
            return False
        if self._send(line):
            self.gated += 1
            return True
        return False

    def send(self, line: str) -> bool:
        """Invia una riga propria (messaggi): solo con il collegamento verificato."""
        return bool(self.connected and self.verified) and self._send(line)

    # ------------------------------------------------------------ interno
    def _send(self, line: str) -> bool:
        s = self.sock
        if s is None:
            return False
        try:
            with self.send_lock:
                s.sendall((line + "\r\n").encode("latin-1", errors="replace"))
            return True
        except OSError as e:
            log.warning("APRS-IS: invio non riuscito (%s)", e)
            return False

    def _run(self) -> None:
        delay = 5
        while not self.stop_evt.is_set():
            try:
                self._session()
                delay = 5
            except OSError as e:
                self.status = f"Errore di rete: {e}"
                log.warning("APRS-IS %s:%d: %s", self.server, self.port, e)
            finally:
                self.connected = self.verified = False
                if self.sock:
                    try:
                        self.sock.close()
                    except OSError:
                        pass
                    self.sock = None
            if self.stop_evt.wait(delay):
                break
            self.status = f"Riconnessione a {self.server}…"
            delay = min(delay * 2, 120)

    def _session(self) -> None:
        self.status = f"Connessione a {self.server}…"
        s = socket.create_connection((self.server, self.port), timeout=15)
        s.settimeout(1.0)
        self.sock = s
        login = f"user {self.call} pass {self.pcode} vers APRSMaster {__version__}"
        if self.filter_str:
            login += f" filter {self.filter_str}"
        self._send(login)
        buf = b""
        last_rx = time.time()
        next_beacon = time.time() + self.beacon_delay_s
        while not self.stop_evt.is_set():
            try:
                chunk = s.recv(4096)
                if not chunk:
                    raise OSError("connessione chiusa dal server")
                last_rx = time.time()
                buf += chunk
            except socket.timeout:
                if time.time() - last_rx > 120:          # il server manda un keepalive ogni ~20 s
                    raise OSError("nessun dato dal server da 2 minuti") from None
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                self._handle(raw.rstrip(b"\r").decode("latin-1", errors="replace"))
            home = self.current_home
            if (self.igate or self.beacon) and self.verified and home and time.time() >= next_beacon:
                if self._send(beacon_line(self.call, *home, self.station_type, self.comment)):
                    self.last_beacon = time.time()
                    log.info("APRS-IS: stazione %s annunciata", self.call)
                if self.equipment.strip():
                    self._send(status_line(self.call, self.equipment))
                next_beacon = time.time() + BEACON_INTERVAL_S

    @property
    def current_home(self) -> tuple[float, float] | None:
        if self.home_fn is not None:
            try:
                return self.home_fn() or None
            except Exception:
                pass
        return self.home

    @property
    def beacon_info(self) -> str:
        """Perché la stazione è o non è visibile su aprs.fi, in una riga."""
        if not (self.beacon or self.igate) or not self.connected:
            return ""
        if not self.verified:
            return ("Stazione non annunciata: serve un passcode valido" if self.pcode in ("", "-1")
                    else "Stazione non annunciata: passcode non accettato dal server")
        if not self.current_home:
            return "Stazione non annunciata: manca la posizione ricevente"
        if self.last_beacon is None:
            return f"Annuncio di {self.call} tra pochi secondi"
        return f"Stazione annunciata come {self.call} alle " + time.strftime("%H:%M", time.localtime(self.last_beacon))

    def _handle(self, line: str) -> None:
        if not line:
            return
        if line.startswith("#"):
            low = line.lower()
            if "logresp" in low:
                self.connected = True
                self.verified = " verified" in low and "unverified" not in low
                parts = line.split()
                self.server_name = parts[-1] if parts else ""
                if self.verified:
                    self.status = f"Collegato a {self.server_name}"
                elif self.pcode in ("", "-1"):
                    self.status = f"Collegato a {self.server_name} in sola ricezione (senza passcode)"
                else:
                    self.status = f"Collegato a {self.server_name}, NON verificato: controlla il passcode"
                log.info("APRS-IS: %s", line[2:])
            return
        self.received += 1
        if self.on_line:
            self.on_line(line)
