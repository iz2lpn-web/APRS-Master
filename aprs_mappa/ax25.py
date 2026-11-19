# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Codifica/decodifica di frame AX.25 UI (quelli usati da APRS)."""
from __future__ import annotations

from dataclasses import dataclass, field


def _make_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
        table.append(crc)
    return table


_CRC_TABLE = _make_table()


def crc16_x25(data: bytes) -> int:
    """FCS AX.25: CRC-16-CCITT riflesso, init 0xFFFF, xor finale 0xFFFF."""
    crc = 0xFFFF
    t = _CRC_TABLE
    for b in data:
        crc = (crc >> 8) ^ t[(crc ^ b) & 0xFF]
    return crc ^ 0xFFFF


def fcs_ok(frame: bytes) -> bool:
    return len(frame) >= 3 and crc16_x25(frame[:-2]) == (frame[-2] | (frame[-1] << 8))


# ---------------------------------------------------------------- correzione

_SYNDROME_CACHE: dict[int, tuple[dict[int, int], dict[int, int]]] = {}


def _syndromes(nbytes: int) -> tuple[dict[int, int], dict[int, int]]:
    """Per un frame di nbytes byte (FCS inclusa) ritorna due dizionari
    sindrome -> posizione di bit: errori singoli ed errori su due bit adiacenti.

    Il CRC è lineare: invertire il bit p cambia la sindrome
    (crc(dati) xor FCS ricevuta) di un valore fisso che dipende solo da p e
    dalla lunghezza, quindi si può precalcolare."""
    if nbytes in _SYNDROME_CACHE:
        return _SYNDROME_CACHE[nbytes]
    nd = (nbytes - 2) * 8
    eff = [0] * (nd + 16)
    s = 0x8408                     # effetto dell'ultimo bit dei dati
    for p in range(nd - 1, -1, -1):
        eff[p] = s
        s = (s >> 1) ^ 0x8408 if s & 1 else s >> 1
    for j in range(16):            # bit della FCS (trasmessa LSB per primo)
        eff[nd + j] = 1 << j
    single, pair = {}, {}
    for p, e in enumerate(eff):
        single.setdefault(e, p)
        if p + 1 < len(eff):
            pair.setdefault(e ^ eff[p + 1], p)
    _SYNDROME_CACHE[nbytes] = (single, pair)
    if len(_SYNDROME_CACHE) > 400:
        _SYNDROME_CACHE.pop(next(iter(_SYNDROME_CACHE)))
    return single, pair


def _flip(frame: bytes, *bits: int) -> bytes:
    out = bytearray(frame)
    for b in bits:
        out[b >> 3] ^= 1 << (b & 7)
    return bytes(out)


def plausible(frame: bytes) -> bool:
    """Controllo di plausibilità di un frame UI APRS (senza FCS): serve a non
    accettare come 'corretto' un frame nato dal rumore."""
    pos = 0
    while True:
        chunk = frame[pos:pos + 7]
        if len(chunk) < 7:
            return False
        call = "".join(chr(b >> 1) for b in chunk[:6])
        if any(b & 1 for b in chunk[:6]):
            return False
        stripped = call.rstrip(" ")
        if not stripped or not all(c.isdigit() or "A" <= c <= "Z" for c in stripped):
            return False
        pos += 7
        if chunk[6] & 1:
            break
        if pos >= 70:                       # al massimo 10 indirizzi
            return False
    return pos >= 14 and frame[pos:pos + 2] == b"\x03\xF0"


def correct_frame(frame: bytes) -> tuple[bytes, int, int] | None:
    """Prova a recuperare un frame con FCS errata invertendo 1 bit o 2 bit
    adiacenti (l'errore tipico della codifica NRZI). Ritorna
    (frame_corretto, bit_invertiti, posizione_primo_bit) oppure None."""
    if len(frame) < 18 or len(frame) > 400:
        return None
    syn = crc16_x25(frame[:-2]) ^ (frame[-2] | (frame[-1] << 8))
    single, pair = _syndromes(len(frame))
    for table, nbits in ((single, 1), (pair, 2)):
        p = table.get(syn)
        if p is None:
            continue
        fixed = _flip(frame, *range(p, p + nbits))
        if fcs_ok(fixed) and plausible(fixed[:-2]):
            return fixed, nbits, p
    return None


@dataclass
class AX25Frame:
    dest: str
    source: str
    path: list[str] = field(default_factory=list)
    info: bytes = b""

    def tnc2(self) -> str:
        """Rappresentazione testuale classica: SRC>DST,PATH:info"""
        hdr = self.source + ">" + ",".join([self.dest] + self.path)
        return hdr + ":" + self.info.decode("latin-1", errors="replace")


def _decode_addr(chunk: bytes) -> tuple[str, bool, bool]:
    """Ritorna (nominativo-SSID, ultimo_indirizzo, bit H 'ripetuto')."""
    call = "".join(chr(b >> 1) for b in chunk[:6]).strip()
    ssid = (chunk[6] >> 1) & 0x0F
    name = f"{call}-{ssid}" if ssid else call
    return name, bool(chunk[6] & 0x01), bool(chunk[6] & 0x80)


def decode_frame(frame: bytes, has_fcs: bool = True) -> AX25Frame | None:
    """Decodifica un frame. has_fcs=True per frame da demodulatore HDLC
    (gli ultimi 2 byte sono la FCS), False per frame KISS."""
    if has_fcs:
        if len(frame) < 18:
            return None
        if not fcs_ok(frame):
            return None
        frame = frame[:-2]
    if len(frame) < 16:
        return None

    addrs: list[str] = []
    pos = 0
    while pos + 7 <= len(frame):
        name, last, h_bit = _decode_addr(frame[pos:pos + 7])
        # il bit H vale solo per i digipeater (indirizzi dal terzo in poi)
        if len(addrs) >= 2 and h_bit:
            name += "*"
        addrs.append(name)
        pos += 7
        if last:
            break
    else:
        return None
    if len(addrs) < 2 or pos + 2 > len(frame):
        return None

    control, pid = frame[pos], frame[pos + 1]
    if control != 0x03 or pid != 0xF0:  # solo frame UI senza layer 3
        return None
    return AX25Frame(dest=addrs[0], source=addrs[1], path=addrs[2:],
                     info=frame[pos + 2:])


def _encode_addr(name: str, last: bool) -> bytes:
    repeated = name.endswith("*")
    name = name.rstrip("*")
    call, _, ssid = name.partition("-")
    out = bytes((ord(c) << 1) for c in call.upper().ljust(6)[:6])
    b = 0x60 | ((int(ssid or 0) & 0x0F) << 1) | (1 if last else 0)
    if repeated:
        b |= 0x80
    return out + bytes([b])


def encode_frame(source: str, dest: str, path: list[str], info: bytes,
                 with_fcs: bool = True) -> bytes:
    addrs = [dest, source] + list(path)
    body = b"".join(_encode_addr(a, i == len(addrs) - 1) for i, a in enumerate(addrs))
    body += b"\x03\xF0" + info
    if with_fcs:
        crc = crc16_x25(body)
        body += bytes([crc & 0xFF, crc >> 8])
    return body
