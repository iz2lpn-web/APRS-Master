#!/usr/bin/env python3
# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Crea un WAV di prova con un beacon APRS che si muove (posizioni non
compresse, compresse e Mic-E), con rumore, per verificare il demodulatore
senza radio:
    python -m aprs_mappa.genera_test -o prova.wav
    aprs-mappa --source wav --wav prova.wav --realtime
"""
import argparse
import math
import wave

import numpy as np

from .afsk import AFSKModulator
from .ax25 import encode_frame


def b91(v: int, n: int) -> str:
    out = ""
    for _ in range(n):
        out = chr(v % 91 + 33) + out
        v //= 91
    return out


def uncompressed(lat, lon, table, code, course, speed_kn, alt_m, text):
    la = f"{int(abs(lat)):02d}{(abs(lat) % 1) * 60:05.2f}{'N' if lat >= 0 else 'S'}"
    lo = f"{int(abs(lon)):03d}{(abs(lon) % 1) * 60:05.2f}{'E' if lon >= 0 else 'W'}"
    return f"!{la}{table}{lo}{code}{course:03d}/{speed_kn:03d}/A={int(alt_m / 0.3048):06d}{text}"


def compressed(lat, lon, table, code, text):
    y = b91(int(round(380926 * (90 - lat))), 4)
    x = b91(int(round(190463 * (180 + lon))), 4)
    return f"={table}{y}{x}{code} sT{text}"


def mic_e(lat, lon, speed_kn, course, code, table, text):
    """Ritorna (destinazione, info) con stato 'In viaggio' (bit 110)."""
    lat_min = (abs(lat) % 1) * 60
    digits = f"{int(abs(lat)):02d}{int(lat_min):02d}{int(round((lat_min % 1) * 100)):02d}"
    msg = [1, 1, 0]
    dest = ""
    for i, d in enumerate(digits):
        flag = msg[i] if i < 3 else [lat >= 0, abs(lon) >= 100, lon < 0][i - 3]
        dest += chr(ord("P") + int(d)) if flag else d
    deg = int(abs(lon))
    d = deg if deg < 100 else deg - 100
    m = (abs(lon) % 1) * 60
    mm, hh = int(m), int(round((m % 1) * 100))
    mm = mm + 60 if mm < 10 else mm
    sp, dc, se = speed_kn // 10, (speed_kn % 10) * 10 + course // 100, course % 100
    info = "`" + "".join(chr(v + 28) for v in (d, mm, hh, sp, dc, se)) + code + table + text
    return dest, info


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", default="test_aprs.wav")
    ap.add_argument("--rate", type=int, default=48000)
    ap.add_argument("--snr", type=float, default=12.0, help="rapporto segnale/rumore in dB")
    a = ap.parse_args()

    mod = AFSKModulator(a.rate, amplitude=0.3)
    rng = np.random.default_rng(1)
    pieces = [np.zeros(a.rate)]
    path = ["WIDE1-1", "WIDE2-1"]

    # beacon N0CALL-9 che percorre un arco nei dintorni di Bologna
    for i in range(12):
        ang = i / 12 * math.pi
        lat, lon = 44.494 + 0.03 * math.sin(ang), 11.343 + 0.05 * math.cos(ang)
        course = int((math.degrees(-ang) + 360) % 360) or 360
        if i % 3 == 0:
            dest, info = mic_e(lat, lon, 25, course % 360, ">", "/", "Beacon di prova")
        elif i % 3 == 1:
            dest, info = "APZ001", compressed(lat, lon, "/", ">", "Beacon compresso")
        else:
            dest, info = "APZ001", uncompressed(lat, lon, "/", ">", course, 25, 60 + i * 3, " Beacon di prova")
        frame = encode_frame("N0CALL-9", dest, path, info.encode("latin-1"))
        pieces += [mod.modulate(frame), np.zeros(int(a.rate * 1.5))]

    # una stazione fissa (digipeater) e un oggetto
    for src, info in [("N0CALL-10", "!4431.80N/01117.40E#Digi di prova"),
                      ("N0CALL", ";RIFUGIO  *111111z4430.00N/01120.00E;Punto d'appoggio")]:
        frame = encode_frame(src, "APZ001", ["WIDE2-2"], info.encode())
        pieces += [mod.modulate(frame), np.zeros(a.rate)]

    sig = np.concatenate(pieces)
    noise_amp = math.sqrt(np.mean(sig[sig != 0] ** 2)) / (10 ** (a.snr / 20))
    sig = sig + rng.normal(0, noise_amp, sig.size)
    sig = np.clip(sig, -1, 1)

    with wave.open(a.o, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(a.rate)
        w.writeframes((sig * 32767).astype("<i2").tobytes())
    print(f"Scritto {a.o}: {sig.size / a.rate:.0f} s, 14 pacchetti, SNR {a.snr} dB")


if __name__ == "__main__":
    main()
