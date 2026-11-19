# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Client KISS su TCP (Direwolf, soundmodem, TNC con interfaccia di rete)."""
from __future__ import annotations

import logging
import socket
import time
from typing import Callable

FEND, FESC, TFEND, TFESC = 0xC0, 0xDB, 0xDC, 0xDD
log = logging.getLogger("kiss")


def _unescape(data: bytes) -> bytes:
    out, esc = bytearray(), False
    for b in data:
        if esc:
            out.append(FEND if b == TFEND else FESC if b == TFESC else b)
            esc = False
        elif b == FESC:
            esc = True
        else:
            out.append(b)
    return bytes(out)


def run_kiss_tcp(host: str, port: int, on_frame: Callable[[bytes], None],
                 stop=lambda: False) -> None:
    """Si collega e chiama on_frame(ax25_senza_fcs) per ogni frame dati.
    Si riconnette automaticamente se la connessione cade."""
    while not stop():
        try:
            with socket.create_connection((host, port), timeout=3) as s:
                s.settimeout(1.0)
                log.info("Connesso al TNC KISS %s:%d", host, port)
                buf = bytearray()
                while not stop():
                    try:
                        chunk = s.recv(4096)
                    except socket.timeout:
                        continue
                    if not chunk:
                        raise ConnectionError("connessione chiusa dal TNC")
                    buf.extend(chunk)
                    while True:
                        start = buf.find(FEND)
                        if start < 0:
                            buf.clear()
                            break
                        end = buf.find(FEND, start + 1)
                        if end < 0:
                            del buf[:start]
                            break
                        raw = _unescape(bytes(buf[start + 1:end]))
                        del buf[:end]          # il FEND finale può aprire il frame dopo
                        if len(raw) > 1 and (raw[0] & 0x0F) == 0:   # comando 0 = dati
                            on_frame(raw[1:])
        except OSError as e:
            if stop():
                break
            log.warning("TNC KISS %s:%d non raggiungibile (%s), riprovo tra 5 s", host, port, e)
            for _ in range(50):
                if stop():
                    return
                time.sleep(0.1)
