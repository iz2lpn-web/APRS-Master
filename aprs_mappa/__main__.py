# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""python -m aprs_mappa          -> pannello di controllo grafico
python -m aprs_mappa --help   -> riga di comando (stesse opzioni di aprs-mappa)"""
import sys


def run() -> None:
    if len(sys.argv) > 1:
        from .main import main
    else:
        from .gui import main
    main()


if __name__ == "__main__":
    run()
