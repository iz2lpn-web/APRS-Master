#!/usr/bin/env python3
# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Confronto fra le versioni del demodulatore.

Su una registrazione reale (WAV PCM 16 bit, mono o stereo):
    python strumenti/banco_demodulatore.py --wav registrazione.wav

Su segnali simulati con difetti tipici delle radio (de-enfasi, pre-enfasi,
audio limitato a 300-3000 Hz, rumore in banda):
    python strumenti/banco_demodulatore.py --simula

"originale" è il demodulatore della versione 1.0 (un solo slicer, senza
correzione), utile come riferimento.
"""
from __future__ import annotations

import argparse
import math
import sys
import time
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aprs_mappa.afsk import PROFILES, AFSKDemodulator, AFSKModulator   # noqa: E402
from aprs_mappa.ax25 import decode_frame, encode_frame                  # noqa: E402

VERSIONI = {
    "originale": dict(space_gains=(1.0,), emphasis_gains=(), fix_bits=False),
    "leggero": dict(PROFILES["leggero"], fix_bits=True),
    "completo": dict(PROFILES["completo"], fix_bits=True),
}


def decodifica(x: np.ndarray, fs: int, kw: dict) -> tuple[set[bytes], int, float]:
    got: set[bytes] = set()
    t = time.perf_counter()
    d = AFSKDemodulator(fs, lambda b: got.add(b[:-2]), **kw)
    step = fs // 10
    for i in range(0, len(x), step):
        d.process(x[i:i + step])
    d.flush()
    return got, d.stats["fixed"], time.perf_counter() - t


# ---------------------------------------------------------------- WAV reale

def prova_wav(path: str, canale: int) -> None:
    with wave.open(path, "rb") as w:
        if w.getsampwidth() != 2:
            sys.exit("Il WAV deve essere PCM a 16 bit")
        fs, nch = w.getframerate(), w.getnchannels()
        raw = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").reshape(-1, nch)
    x = raw[:, min(canale, nch - 1)].astype(np.float32) / 32768
    dur = len(x) / fs
    print(f"{path}: {dur:.0f} s, {fs} Hz, picco {20 * math.log10(max(np.abs(x).max(), 1e-5)):.1f} dBFS\n")
    risultati = {}
    for nome, kw in VERSIONI.items():
        got, fixed, sec = decodifica(x, fs, kw)
        risultati[nome] = got
        print(f"  {nome:10s} {len(got):5d} pacchetti  ({fixed} con correzione)   "
              f"CPU {100 * sec / dur:.1f}% di un core")
    nuovi = risultati["completo"] - risultati["originale"]
    persi = risultati["originale"] - risultati["completo"]
    print(f"\nTrovati solo dal nuovo demodulatore: {len(nuovi)}")
    for b in sorted(nuovi)[:15]:
        f = decode_frame(b, has_fcs=False)
        print("   ", f.tnc2()[:100] if f else b[:40])
    if persi:
        print(f"Trovati solo dall'originale: {len(persi)} (segnalamelo: non dovrebbe succedere)")


# ---------------------------------------------------------------- simulazione

def _risposta(f: np.ndarray, rc_n: int, pre: bool, band=(300, 3000)) -> np.ndarray:
    fc = 1 / (2 * math.pi * 750e-6)
    h = np.ones_like(f, dtype=complex)
    for _ in range(rc_n):                           # de-enfasi RC 750 us
        h *= 1 / (1 + 1j * f / fc)
    if pre:                                         # pre-enfasi non compensata
        h *= 1 + 1j * f / fc
    lo, hi = band
    return h / np.sqrt(1 + (lo / np.maximum(f, 1)) ** 8) / np.sqrt(1 + (f / hi) ** 8)


def segnale(fs: int, n: int, snr_db: float, rc_n=0, pre=False, seed=1):
    rng = np.random.default_rng(seed)
    mod = AFSKModulator(fs, amplitude=0.3)
    frames, parts = [], [np.zeros(fs // 2)]
    for i in range(n):
        testo = "".join(chr(int(c)) for c in rng.integers(32, 126, int(rng.integers(20, 70))))
        info = f"!{4400 + i % 60:04d}.{i % 100:02d}N/01120.00E>{testo}".encode()
        f = encode_frame(f"N0CALL-{i % 15}", "APZ001", ["WIDE1-1", "WIDE2-1"], info)
        frames.append(f[:-2])
        parts += [mod.modulate(f), np.zeros(fs // 3)]
    sig = np.concatenate(parts)
    fr = np.fft.rfftfreq(sig.size, 1 / fs)
    h = _risposta(fr, rc_n, pre)
    y = np.fft.irfft(np.fft.rfft(sig) * h, sig.size)
    noise = np.fft.irfft(np.fft.rfft(rng.normal(0, 1, sig.size)) * _risposta(fr, 0, False), sig.size)
    p = np.mean(y[sig != 0] ** 2)
    y += noise * math.sqrt(p / np.mean(noise ** 2)) / 10 ** (snr_db / 20)
    return frames, y


def prova_simulata(n: int) -> None:
    fs = 48000
    scenari = [("canale piatto", 0, False), ("de-enfasi (-5 dB)", 1, False),
               ("pre-enfasi (+5 dB)", 0, True), ("doppia de-enfasi (-10 dB)", 2, False)]
    print(f"Pacchetti decodificati su {n} (errati fra parentesi, se presenti)\n")
    print(f"{'scenario':28s}{'SNR':>5s}" + "".join(f"{v:>12s}" for v in VERSIONI))
    for nome, rc_n, pre in scenari:
        for snr in (12, 9, 7):
            frames, y = segnale(fs, n, snr, rc_n, pre)
            riga = ""
            for kw in VERSIONI.values():
                got, _, _ = decodifica(y, fs, kw)
                ok, bad = len(got & set(frames)), len(got - set(frames))
                riga += f"{ok:>8d}" + (f" ({bad})" if bad else "    ")
            print(f"{nome:28s}{snr:>4d} " + riga, flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--wav", help="registrazione da analizzare")
    g.add_argument("--simula", action="store_true", help="prova su segnali simulati")
    ap.add_argument("--canale", type=int, default=0, help="canale del WAV (0 = sinistro)")
    ap.add_argument("-n", type=int, default=20, help="pacchetti per scenario simulato")
    a = ap.parse_args()
    if a.wav:
        prova_wav(a.wav, a.canale)
    else:
        prova_simulata(a.n)


if __name__ == "__main__":
    main()
