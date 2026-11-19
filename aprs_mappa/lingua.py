# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Lingua dell'interfaccia. I testi sono scritti in italiano nel codice e t() li traduce con il
dizionario della lingua scelta (lingua_en.py): una frase che manca resta in italiano, quindi una
traduzione incompleta non rompe mai niente. La stessa tabella serve alla mappa nel browser."""
from __future__ import annotations

import locale
import os
import re

LANGS = {"it": "Italiano", "en": "English"}
_current = "it"


def detect() -> str:
    """Lingua del sistema: inglese se il sistema non è in italiano."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        v = os.environ.get(var, "")
        if v:
            return "it" if v.lower().startswith("it") else "en"
    try:
        loc = locale.getlocale()[0] or ""
    except (ValueError, TypeError):
        loc = ""
    if not loc and os.name == "nt":
        try:
            import ctypes
            loc = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), "")
        except Exception:
            loc = ""
    return "it" if not loc or loc.lower().startswith("it") else "en"


def set_language(lang: str) -> str:
    """'auto', 'it' o 'en'; ritorna la lingua effettiva."""
    global _current
    _current = detect() if lang not in LANGS else lang
    return _current


def language() -> str:
    return _current


def dictionary(lang: str | None = None) -> dict[str, str]:
    lang = lang or _current
    if lang == "it":
        return {}
    from .lingua_en import EN
    return EN


def t(text: str, **values) -> str:
    """Traduce un testo italiano; {nome} si sostituisce con values['nome']."""
    out = dictionary().get(text, text)
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v))
    return out


_compiled: dict[str, list] = {}


def _patterns(lang: str) -> list:
    """Testi con segnaposto ({nome}) trasformati in espressioni regolari, per tradurre i testi
    composti dagli altri moduli (stati del collegamento, errori) senza cambiarli."""
    if lang not in _compiled:
        out = []
        for it, en in dictionary(lang).items():
            if "{" not in it:
                continue
            parts = re.split(r"\{(\w+)\}", it)
            rx = "".join(re.escape(p) if i % 2 == 0 else f"(?P<{p}>.+?)" for i, p in enumerate(parts))
            try:
                out.append((re.compile(rx + r"\Z", re.S), en))
            except re.error:
                continue
        out.sort(key=lambda x: -len(x[0].pattern))          # i modelli più lunghi per primi
        _compiled[lang] = out
    return _compiled[lang]


def tx(text: str, _depth: int = 0) -> str:
    """Come t(), ma per testi già composti: li riconosce anche se contengono valori variabili
    ("Collegato a euro.aprs2.net" -> "Connected to euro.aprs2.net"). Riga per riga."""
    if not text or _current == "it":
        return text
    d = dictionary()
    if text in d:
        return d[text]
    if "\n" in text:
        return "\n".join(tx(x, _depth) for x in text.split("\n"))
    for rx, en in _patterns(_current):
        m = rx.match(text)
        if m:
            out = en
            for k, v in m.groupdict().items():
                out = out.replace("{" + k + "}", tx(v, _depth + 1) if _depth < 1 else v)
            return out
    return text
