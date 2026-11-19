#!/usr/bin/env python3
# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Rigenera le icone del programma dai disegni SVG in strumenti/icone/.

    python -m pip install cairosvg pillow
    python strumenti/crea_icone.py

icona.svg è il disegno principale; icona-piccola.svg è una versione
semplificata usata per le misure fino a 32 pixel, dove i dettagli
diventerebbero rumore. Su Windows cairosvg richiede le librerie Cairo
(ad esempio tramite GTK o MSYS2): le icone già generate sono comunque
incluse nel pacchetto, quindi questo script serve solo se cambi il disegno.
"""
import io
import shutil
from pathlib import Path

import cairosvg
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "strumenti" / "icone"
STATIC = ROOT / "aprs_mappa" / "static"
SMALL_MAX = 32


def render(size: int) -> Image.Image:
    svg = SRC / ("icona-piccola.svg" if size <= SMALL_MAX else "icona.svg")
    png = cairosvg.svg2png(url=str(svg), output_width=size, output_height=size)
    return Image.open(io.BytesIO(png)).convert("RGBA")


def main() -> None:
    # Windows: un file .ico con tutte le misure, ognuna disegnata alla sua dimensione
    ico_sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    imgs = [render(s) for s in ico_sizes]
    imgs[-1].save(SRC / "icona.ico", format="ICO", sizes=[(s, s) for s in ico_sizes],
                  append_images=imgs[:-1])

    # macOS: .icns fino a 1024 px (schermi Retina)
    icns_sizes = [16, 32, 64, 128, 256, 512, 1024]
    imgs = [render(s) for s in icns_sizes]
    imgs[-1].save(SRC / "icona.icns", format="ICNS", append_images=imgs[:-1])

    # PNG per Linux e per la finestra del pannello; SVG per la scheda del browser
    for s in (16, 32, 48, 64, 128, 256, 512):
        render(s).save(SRC / f"icona-{s}.png", optimize=True)
    for s in (32, 64, 256):
        shutil.copy(SRC / f"icona-{s}.png", STATIC / f"icona-{s}.png")
    shutil.copy(SRC / "icona.svg", STATIC / "icona.svg")
    print("Icone create in", SRC, "e", STATIC)


if __name__ == "__main__":
    main()
