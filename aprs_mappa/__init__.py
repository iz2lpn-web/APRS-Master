# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""Ricevitore APRS (AFSK 1200 baud) con mappa OpenStreetMap e pannello di controllo."""
from __future__ import annotations

from pathlib import Path

__version__ = "1.7.3"
APP_NAME = "APRS Master by IZ2LPN op. Giorgio"
COPYRIGHT = "Copyright (C) 2026 IZ2LPN op. Giorgio"
LICENSE_NAME = "GNU GPL v3 o successiva (GPL-3.0-or-later)"
LICENSE_P1 = (
    "Questo programma è software libero: puoi ridistribuirlo e/o modificarlo "
    "secondo i termini della GNU General Public License pubblicata dalla Free "
    "Software Foundation, versione 3 della licenza o (a tua scelta) qualsiasi "
    "versione successiva.")
LICENSE_P2 = (
    "Questo programma è distribuito nella speranza che sia utile, ma SENZA "
    "ALCUNA GARANZIA, nemmeno la garanzia implicita di COMMERCIABILITÀ o di "
    "IDONEITÀ PER UN PARTICOLARE SCOPO. Per i dettagli vedi la GNU General "
    "Public License: https://www.gnu.org/licenses/gpl-3.0.html")
NOTICE = f"{APP_NAME} {__version__}\n{COPYRIGHT}\n\n{LICENSE_P1}\n\n{LICENSE_P2}"


def license_text() -> str | None:
    """Testo completo della licenza: dalla cartella del programma (sorgenti,
    eseguibile) o dai metadati del pacchetto installato con pip."""
    candidate = Path(__file__).resolve().parent.parent / "LICENSE"
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8")
    try:
        from importlib.metadata import distribution
        dist = distribution("aprs-mappa")
        for f in dist.files or []:
            if f.name in ("LICENSE", "COPYING"):
                return f.read_text(encoding="utf-8")
    except Exception:
        pass
    return None
