#!/usr/bin/env python3
# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Crea un eseguibile autonomo del pannello di controllo con PyInstaller,
per usare il programma su computer senza Python.

    python -m pip install pyinstaller
    python strumenti/crea_eseguibile.py

Il risultato è nella cartella dist/APRSMaster (da copiare per intero).
PyInstaller crea eseguibili solo per il sistema su cui gira: per Windows
va lanciato su Windows, per macOS su macOS.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

try:
    import PyInstaller.__main__ as pyinstaller
except ImportError:
    sys.exit("PyInstaller non è installato:  python -m pip install pyinstaller")

launcher = ROOT / "build" / "avvio_eseguibile.py"
launcher.parent.mkdir(exist_ok=True)
# senza argomenti apre il pannello; con argomenti si comporta come aprs-mappa
# (serve anche per l'autotest della compilazione automatica: --self-test)
launcher.write_text("from aprs_mappa.__main__ import run\nrun()\n", encoding="utf-8")

static = ROOT / "aprs_mappa" / "static"
icone = ROOT / "strumenti" / "icone"
icona = {"win32": icone / "icona.ico", "darwin": icone / "icona.icns"}.get(sys.platform)
extra = ["--icon", str(icona)] if icona else []
if sys.platform == "darwin":
    extra += ["--osx-bundle-identifier", "it.ricezioneaprs.mappa"]
pyinstaller.run(extra + [
    str(launcher),
    "--name", "APRSMaster",
    "--windowed",                 # niente finestra di terminale
    "--noconfirm",
    "--clean",
    "--paths", str(ROOT),
    "--add-data", f"{static}{os.pathsep}aprs_mappa/static",
    "--add-data", f"{ROOT / 'LICENSE'}{os.pathsep}.",     # letta da Aiuto > Licenza
    "--collect-data", "sounddevice",    # include PortAudio su Windows e macOS
    "--hidden-import", "serial.tools.list_ports",   # modulo LoRa su porta seriale
    "--collect-data", "certifi",        # autorità per HTTPS (Geopin, mappe) anche senza archivio di sistema
    "--distpath", str(ROOT / "dist"),
    "--workpath", str(ROOT / "build" / "pyinstaller"),
    "--specpath", str(ROOT / "build"),
])
# la GPL va consegnata insieme al programma: copia ben visibile accanto all'eseguibile
out = ROOT / "dist" / "APRSMaster"
for name in ("LICENSE", "README.md"):
    if out.is_dir():
        (out / name).write_bytes((ROOT / name).read_bytes())
print("\nFatto: avvia", out)
