# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Demodulatore software AFSK Bell 202 (1200 baud, mark 1200 Hz / space 2200 Hz)
e deframer HDLC. Lavora a blocchi: va bene sia per la scheda audio in tempo
reale sia per i file WAV.

Catena: correlatori in quadratura sui due toni -> più "slicer" in parallelo,
ognuno con un peso diverso fra i toni (compensa lo sbilanciamento dovuto a
de-enfasi/pre-enfasi della radio), più un secondo ramo con pre-enfasi per
radio con de-enfasi marcata -> recupero del clock di bit (PLL digitale) ->
NRZI -> HDLC -> verifica della FCS, con correzione degli errori su 1 o 2 bit
adiacenti validata dalla confidenza delle decisioni.
"""
from __future__ import annotations

from typing import Callable

import numpy as np

from .ax25 import correct_frame, fcs_ok

MARK_HZ = 1200
SPACE_HZ = 2200
BAUD = 1200


class HDLCDeframer:
    """Flag, bit-stuffing e raccolta dei byte. Per ogni bit conserva anche la
    'confidenza' della decisione, usata per validare le correzioni."""
    MIN_BYTES = 18   # 2 indirizzi + control + PID + FCS
    MAX_BYTES = 400

    def __init__(self, on_frame: Callable[[bytes, list[float]], None]):
        self.on_frame = on_frame
        self.bits: list[int] = []
        self.confs: list[float] = []
        self.ones = 0

    def push(self, bit: int, conf: float = 1.0) -> None:
        if bit:
            self.ones += 1
            if self.ones > 6:          # abort: troppi 1 consecutivi
                self.bits.clear()
                self.confs.clear()
                return
            self.bits.append(1)
            self.confs.append(conf)
            return

        if self.ones == 6:             # flag 01111110 completato
            # nel buffer ci sono lo 0 iniziale e i sei 1 del flag: via
            frame_bits, frame_confs = self.bits[:-7], self.confs[:-7]
            self.bits, self.confs = [], []
            self._emit(frame_bits, frame_confs)
        elif self.ones == 5:           # bit di stuffing: da scartare
            pass
        else:
            self.bits.append(0)
            self.confs.append(conf)
        self.ones = 0
        if len(self.bits) > self.MAX_BYTES * 8:
            self.bits.clear()
            self.confs.clear()

    def _emit(self, bits: list[int], confs: list[float]) -> None:
        n = len(bits)
        if n < self.MIN_BYTES * 8 or n % 8:
            return
        out = bytearray(n // 8)
        for i in range(n // 8):
            v = 0
            for k in range(8):          # LSB per primo
                v |= bits[i * 8 + k] << k
            out[i] = v
        self.on_frame(bytes(out), confs)


# Pesi del tono space nei vari slicer (rapporto di energia). Coprono uno
# sbilanciamento fra i toni ("twist") di circa +-12 dB, tipico di radio con
# de-enfasi (space attenuato) o di uscite dati piatte con pre-enfasi.
DEFAULT_SPACE_GAINS = (1 / 8, 1 / 4, 1 / 2, 1.0, 2.0, 4.0, 8.0)
# Pesi degli slicer sul ramo con pre-enfasi
EMPHASIS_SPACE_GAINS = DEFAULT_SPACE_GAINS


# Profili del demodulatore: "completo" (predefinito) e "leggero" per computer
# poco potenti. Misure sul banco di prova (48 kHz, % di un core):
#   leggero   ~2%   3 slicer, senza ramo con pre-enfasi
#   completo ~10%   7 slicer + 7 sul ramo con pre-enfasi
PROFILES = {
    "completo": dict(space_gains=DEFAULT_SPACE_GAINS, emphasis_gains=DEFAULT_SPACE_GAINS),
    "leggero": dict(space_gains=(1 / 4, 1.0, 4.0), emphasis_gains=()),
}


def make_demodulator(sample_rate: int, on_frame: Callable[[bytes], None],
                     profile: str = "completo", fix_bits: bool = True) -> "AFSKDemodulator":
    return AFSKDemodulator(sample_rate, on_frame, fix_bits=fix_bits,
                           **PROFILES.get(profile, PROFILES["completo"]))


class _Slicer:
    """Decisione mark/space con un peso, recupero del clock di bit, NRZI e HDLC."""

    def __init__(self, spb: float, gain: float, sink: Callable[[int, bytes, list[float]], None]):
        self.spb, self.gain = spb, gain
        self.phase = 0.0
        self.level = False
        self.last_bit_level = False
        self.n = 0
        self.hdlc = HDLCDeframer(lambda b, c: sink(self.n, b, c))

    def run(self, values: list[float], base: int) -> None:
        """values: statistica di decisione normalizzata in [-1, 1];
        > 0 mark, < 0 space, il modulo è la confidenza."""
        spb, half = self.spb, self.spb / 2
        phase, level, last = self.phase, self.level, self.last_bit_level
        push = self.hdlc.push
        for i, v in enumerate(values):
            lv = v > 0
            if lv != level:
                # transizione: idealmente cade sul bordo di bit (fase 0)
                err = phase if phase < half else phase - spb
                phase -= 0.3 * err
                level = lv
            prev = phase
            phase += 1.0
            if prev < half <= phase:       # centro del bit: campiona
                self.n = base + i
                # NRZI: nessuna transizione = 1, transizione = 0
                push(1 if lv == last else 0, v if v >= 0 else -v)
                last = lv
            if phase >= spb:
                phase -= spb
        self.phase, self.level, self.last_bit_level = phase, level, last


class AFSKDemodulator:
    """Demodulatore con più slicer in parallelo e correzione dei bit.

    on_frame riceve solo frame con FCS valida (eventualmente dopo la
    correzione), FCS inclusa, una volta sola anche se più slicer li decodificano.
    """

    def __init__(self, sample_rate: int, on_frame: Callable[[bytes], None],
                 space_gains=DEFAULT_SPACE_GAINS, fix_bits: bool = True,
                 emphasis_gains=EMPHASIS_SPACE_GAINS):
        self.fs = int(sample_rate)
        self.on_frame = on_frame
        self.fix_bits = fix_bits
        self.win = max(4, int(round(self.fs / BAUD)))      # 1 bit di correlazione
        self.spb = self.fs / BAUD                          # campioni per bit
        self.kernel = np.ones(self.win)
        self.hist = np.zeros(self.win)                     # 1 bit + 1 campione
        # ramo con pre-enfasi 750 us: y = x + tau*dx/dt annulla esattamente il
        # polo di una de-enfasi RC (ampiezza e fase) e recupera radio con
        # de-enfasi marcata o doppia
        self.k_emph = 750e-6 * self.fs
        self.n = 0                                         # indice campione assoluto
        self.events: list[tuple[int, int, bytes, list[float]]] = []   # (fine, slicer, frame, conf)
        self.recent: dict[bytes, int] = {}
        self.stats = {"ok": 0, "fixed": 0}
        self.slicers = [
            _Slicer(self.spb, g, lambda n, b, c, k=k: self.events.append((n, k, b, c)))
            for k, g in enumerate(space_gains)
        ]
        off = len(self.slicers)
        self.emph_slicers = [
            _Slicer(self.spb, g, lambda n, b, c, k=k: self.events.append((n, k, b, c)))
            for k, g in enumerate(emphasis_gains, start=off)
        ]
        # tabelle oscillatori: periodiche su fs campioni (toni interi in Hz)
        idx = np.arange(self.fs)
        self.tables = {
            f: (np.cos(2 * np.pi * f * idx / self.fs), np.sin(2 * np.pi * f * idx / self.fs))
            for f in (MARK_HZ, SPACE_HZ)
        }

    def _tone_energy(self, x: np.ndarray, idx: np.ndarray, f: int) -> np.ndarray:
        c, s = self.tables[f]
        i = np.convolve(x * c[idx], self.kernel, "valid")
        q = np.convolve(x * s[idx], self.kernel, "valid")
        return i * i + q * q

    def process(self, samples: np.ndarray) -> None:
        """samples: array mono (int16 o float). Può essere chiamato a blocchi."""
        if samples.size == 0:
            return
        full = np.concatenate([self.hist, samples.astype(np.float64)])
        x = full[1:]                                   # win-1 campioni di storia + nuovi
        idx = (np.arange(len(x)) + self.n - (self.win - 1)) % self.fs
        self.hist = full[-self.win:]
        base = self.n
        self.n += len(samples)
        branches = [(x, self.slicers)]
        if self.emph_slicers:
            branches.append((x + self.k_emph * (x - full[:-1]), self.emph_slicers))
        for sig, slicers in branches:
            em = self._tone_energy(sig, idx, MARK_HZ)
            es = self._tone_energy(sig, idx, SPACE_HZ)
            for sl in slicers:
                ges = sl.gain * es
                sl.run(((em - ges) / (em + ges + 1e-12)).tolist(), base)
        self._resolve(final=False)

    def flush(self) -> None:
        """Da chiamare a fine file: elabora i frame ancora in attesa."""
        self._resolve(final=True)

    # ------------------------------------------------------------
    def _resolve(self, final: bool) -> None:
        """Raggruppa i frame che terminano quasi nello stesso istante (lo
        stesso pacchetto visto da più slicer) e ne emette uno solo."""
        if not self.events:
            return
        window = int(2 * self.spb)
        self.events.sort(key=lambda ev: (ev[0], ev[1]))
        groups, cur = [], [self.events[0]]
        for ev in self.events[1:]:
            if ev[0] - cur[-1][0] <= window:
                cur.append(ev)
            else:
                groups.append(cur)
                cur = [ev]
        groups.append(cur)
        # l'ultimo gruppo potrebbe completarsi nel blocco successivo
        if not final and self.n - groups[-1][-1][0] <= window:
            self.events = groups.pop()
        else:
            self.events = []
        for g in groups:
            self._emit_group(g)

    # Una correzione è accettata solo se il livello invertito era fra il 5%
    # delle decisioni più incerte del frame: un errore vero nasce da una
    # decisione al limite, una correzione sbagliata cade in un punto qualsiasi.
    # Sul banco di prova questo elimina le false correzioni conservando ~3/4
    # dei recuperi (vedi strumenti/banco_demodulatore.py).
    FIX_MAX_RANK = 0.05

    def _emit_group(self, group) -> None:
        good = {ev[2] for ev in group if fcs_ok(ev[2])}
        if good:
            for b in good:
                self._emit(b, group[-1][0], fixed=False)
            return
        if not self.fix_bits:
            return
        fixed = set()
        seen = set()
        for _, _, b, confs in group:
            if b in seen:
                continue
            seen.add(b)
            r = correct_frame(b)
            if not r or len(confs) != len(b) * 8:
                continue
            c = confs[r[2]]
            rank = sum(1 for x in confs if x < c)
            if rank <= self.FIX_MAX_RANK * len(confs):
                fixed.add(r[0])
        if len(fixed) == 1:                 # correzioni discordanti: meglio scartare
            self._emit(fixed.pop(), group[-1][0], fixed=True)

    def _emit(self, frame: bytes, end: int, fixed: bool) -> None:
        horizon = self.fs                   # stesso frame entro 1 s: duplicato
        self.recent = {k: v for k, v in self.recent.items() if end - v < horizon}
        if frame in self.recent:
            return
        self.recent[frame] = end
        self.stats["fixed" if fixed else "ok"] += 1
        self.on_frame(frame)


class AFSKModulator:
    """Generatore AFSK, utile per creare file di prova."""

    def __init__(self, sample_rate: int = 48000, amplitude: float = 0.6):
        self.fs = sample_rate
        self.amp = amplitude

    @staticmethod
    def _hdlc_bits(frame: bytes, preamble_flags: int = 40, tail_flags: int = 4) -> list[int]:
        flag = [0, 1, 1, 1, 1, 1, 1, 0]
        bits = flag * preamble_flags
        ones = 0
        for byte in frame:
            for k in range(8):
                b = (byte >> k) & 1
                bits.append(b)
                ones = ones + 1 if b else 0
                if ones == 5:
                    bits.append(0)
                    ones = 0
        return bits + flag * tail_flags

    def modulate(self, frame_with_fcs: bytes) -> np.ndarray:
        bits = self._hdlc_bits(frame_with_fcs)
        tone_mark = True
        freqs = []
        for b in bits:                     # NRZI
            if b == 0:
                tone_mark = not tone_mark
            freqs.append(MARK_HZ if tone_mark else SPACE_HZ)
        spb = self.fs / BAUD
        n_total = int(len(freqs) * spb)
        bit_idx = np.minimum((np.arange(n_total) / spb).astype(int), len(freqs) - 1)
        inst_f = np.asarray(freqs, dtype=float)[bit_idx]
        phase = 2 * np.pi * np.cumsum(inst_f) / self.fs   # fase continua
        return self.amp * np.sin(phase)
