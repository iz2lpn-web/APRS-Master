# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ricezione LoRa APRS tramite un modulo esterno (ad esempio una scheda ESP32
con firmware LoRa APRS), collegato alla porta seriale USB o in rete (TCP).

I firmware non scrivono tutti i pacchetti allo stesso modo, quindi il lettore
riconosce da solo:
- righe di testo con il pacchetto in formato TNC2 (SRC>DEST,PATH:info), anche
  in mezzo ai messaggi di diagnostica del firmware ("Rx: ...", orari, RSSI...);
- l'intestazione dei pacchetti LoRa APRS "<\\xff\\x01" davanti al testo;
- il protocollo KISS, con dentro un frame AX.25 oppure direttamente il testo.
"""
from __future__ import annotations

import logging
import re
import socket
import threading
from typing import Callable

from .ax25 import AX25Frame, decode_frame
from .kiss import FEND, _unescape

log = logging.getLogger("lora")

LORA_HEADER = "<\xff\x01"
BAUD_RATES = (9600, 19200, 38400, 57600, 115200, 230400)

_CALL = r"[A-Z0-9]{1,6}(?:-[0-9]{1,2})?"
TNC2_RE = re.compile(rf"(?<![A-Za-z0-9-])({_CALL})>({_CALL})((?:,[A-Za-z0-9*-]{{1,9}})*):(.+)")


def extract_tnc2(text: str) -> str | None:
    """Pacchetto TNC2 contenuto in una riga, oppure None."""
    text = text.rstrip("\r\n")
    i = text.find(LORA_HEADER)
    if i >= 0:
        text = text[i + len(LORA_HEADER):]
    m = TNC2_RE.search(text)
    return m.group(0) if m else None


def tnc2_to_frame(line: str) -> AX25Frame | None:
    """Scompone 'SRC>DEST,PATH:info' (serve all'iGate e al registro)."""
    head, sep, info = line.partition(":")
    if not sep or ">" not in head:
        return None
    source, rest = head.split(">", 1)
    parts = rest.split(",")
    return AX25Frame(dest=parts[0], source=source, path=parts[1:],
                     info=info.encode("latin-1", errors="replace"))


class StreamParser:
    """Divide un flusso di byte in pacchetti TNC2, in modalità testo o KISS
    (scelta automaticamente al primo byte FEND)."""

    MAX_BUF = 8192

    def __init__(self, on_packet: Callable[[str], None]):
        self.on_packet = on_packet
        self.buf = bytearray()
        self.kiss = False

    def feed(self, data: bytes) -> None:
        self.buf.extend(data)
        if not self.kiss and FEND in self.buf:
            self.kiss = True
            log.info("LoRa: il dispositivo usa il protocollo KISS")
        if self.kiss:
            self._kiss()
        else:
            self._lines()
        if len(self.buf) > self.MAX_BUF:          # rumore senza separatori
            del self.buf[:-1024]

    def _lines(self) -> None:
        while True:
            i = self.buf.find(b"\n")
            if i < 0:
                return
            raw = bytes(self.buf[:i])
            del self.buf[:i + 1]
            pkt = extract_tnc2(raw.decode("latin-1"))
            if pkt:
                self.on_packet(pkt)

    def _kiss(self) -> None:
        while True:
            start = self.buf.find(FEND)
            if start < 0:
                self.buf.clear()
                return
            end = self.buf.find(FEND, start + 1)
            if end < 0:
                del self.buf[:start]
                return
            raw = _unescape(bytes(self.buf[start + 1:end]))
            del self.buf[:end]
            if len(raw) < 2 or (raw[0] & 0x0F) != 0:       # solo frame dati
                continue
            payload = raw[1:]
            frame = decode_frame(payload, has_fcs=False)
            pkt = frame.tnc2() if frame else extract_tnc2(payload.decode("latin-1"))
            if pkt:
                self.on_packet(pkt)


def list_serial_ports() -> list[tuple[str, str]]:
    """[(porta, descrizione)]; vuota se pyserial non è installato."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return [(p.device, f"{p.device} - {p.description}" if p.description and p.description != "n/a"
             else p.device) for p in sorted(list_ports.comports(), key=lambda p: p.device)]


class LoraSource:
    """Lettura in un thread, con riconnessione automatica.
    mode: 'serial' (port, baud) oppure 'tcp' (host, port)."""

    def __init__(self, mode: str, on_packet: Callable[[str], None], serial_port: str = "",
                 baud: int = 115200, host: str = "", tcp_port: int = 8001):
        self.mode = mode
        self.serial_port, self.baud = serial_port, int(baud)
        self.host, self.tcp_port = host, int(tcp_port)
        self.on_packet = on_packet
        self.stop_evt = threading.Event()
        self.thread: threading.Thread | None = None
        self.status = "Non collegato"
        self.connected = False
        self.packets = 0
        self.error = ""

    @property
    def target(self) -> str:
        return self.serial_port if self.mode == "serial" else f"{self.host}:{self.tcp_port}"

    def start(self) -> None:
        self.stop_evt.clear()
        self.thread = threading.Thread(target=self._run, daemon=True, name="lora")
        self.thread.start()

    def stop(self) -> None:
        self.stop_evt.set()
        if self.thread:
            self.thread.join(timeout=3)
        self.connected = False
        self.status = "Non collegato"

    def _packet(self, line: str) -> None:
        self.packets += 1
        self.on_packet(line)

    def _run(self) -> None:
        delay = 3
        while not self.stop_evt.is_set():
            parser = StreamParser(self._packet)
            try:
                if self.mode == "serial":
                    self._serial(parser)
                else:
                    self._tcp(parser)
                delay = 3
            except ImportError:
                self.error = self.status = "Manca pyserial: installa con  pip install pyserial"
                log.error(self.status)
                return
            except Exception as e:                  # porta scollegata, rete assente...
                self.status = f"Errore su {self.target}: {e}"
                log.warning("LoRa: %s", self.status)
            self.connected = False
            if self.stop_evt.wait(delay):
                break
            delay = min(delay * 2, 30)

    def _serial(self, parser: StreamParser) -> None:
        import serial
        with serial.Serial(self.serial_port, self.baud, timeout=0.5) as ser:
            self.connected, self.error = True, ""
            self.status = f"Collegato a {self.serial_port} ({self.baud} baud)"
            log.info("LoRa: %s", self.status)
            while not self.stop_evt.is_set():
                data = ser.read(ser.in_waiting or 1)
                if data:
                    parser.feed(data)

    def _tcp(self, parser: StreamParser) -> None:
        with socket.create_connection((self.host, self.tcp_port), timeout=5) as s:
            s.settimeout(0.5)
            self.connected, self.error = True, ""
            self.status = f"Collegato a {self.host}:{self.tcp_port}"
            log.info("LoRa: %s", self.status)
            while not self.stop_evt.is_set():
                try:
                    data = s.recv(4096)
                except socket.timeout:
                    continue
                if not data:
                    raise OSError("connessione chiusa dal dispositivo")
                parser.feed(data)
