#!/usr/bin/env python3
# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pannello di controllo grafico del ricevitore APRS.

Avvio:  aprs-mappa-gui   (oppure: python -m aprs_mappa)
Permette di scegliere la sorgente (scheda audio, Direwolf/TNC KISS, file WAV),
regolare il livello audio con l'indicatore, impostare posizione e filtri,
avviare/fermare la ricezione e consultare stazioni, pacchetti e registro.
La configurazione viene salvata in ~/.aprs_mappa.json.
"""
from __future__ import annotations

import json
import logging
import math
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont
from urllib.parse import quote

from . import APP_NAME, COPYRIGHT, LICENSE_P1, LICENSE_P2, __version__, license_text
from .lingua import LANGS, set_language, t, tx
from .receiver import Config, Receiver, list_input_devices

# la lingua si sceglie prima di costruire i testi del pannello (anche le costanti qui sotto)
set_language(Config.load().language)
from .server import STATIC, StationStore, WebServer
from .storico import DB_PATH, History, safe_filename, to_gpx
from .aprsis import STATION_TYPES, comment_error, default_comment, equipment_error, passcode as aprsis_passcode, valid_callsign
from .mappe import CACHE_DIR, TileService
from .scorrimento import ScrollFrame, fit_to_screen

PROFILE_CHOICES = [("completo", t("Completo (consigliato)")), ("leggero", t("Leggero (meno CPU)"))]
RATES = ["48000", "44100", "22050", "96000"]
CHANNELS = [t("Sinistro"), t("Destro")]
DEFAULT_DEVICE = t("Predefinito di sistema")
THEMES = {
    "light": {
        "bg": "#e9edeb", "field": "#ffffff", "text_bg": "#ffffff", "fg": "#1d2b33",
        "muted": "#5b6b73", "line": "#b9c4c0", "select": "#cfe0f3", "select_fg": "#1d2b33",
        "button": "#dde3e0", "button_active": "#cdd6d2", "disabled": "#97a3a7",
        "track": "#d5dcd9", "zone_ok": "#cfe5d7", "zone_hi": "#f0d3cc", "tick": "#8a979c",
        "ok": "#2f8a57", "warn": "#c07a12", "err": "#b5432f", "idle": "#8a979c", "low": "#9aa6ab",
        "accent": "#2459a6",
    },
    "dark": {
        "bg": "#1a2328", "field": "#222d33", "text_bg": "#151d21", "fg": "#e3eae7",
        "muted": "#93a3a9", "line": "#3a4950", "select": "#26405a", "select_fg": "#e3eae7",
        "button": "#2a363c", "button_active": "#35444b", "disabled": "#64737a",
        "track": "#28333a", "zone_ok": "#1f3d2c", "zone_hi": "#4a2a26", "tick": "#64737a",
        "ok": "#4fb37a", "warn": "#e0a21b", "err": "#e0705c", "idle": "#7d8c92", "low": "#6c7b81",
        "accent": "#6f9fe0",
    },
}
THEME_CHOICES = [("auto", t("Come il sistema")), ("light", t("Chiaro")), ("dark", t("Scuro"))]
ALERT_ICONS = {"watch": "👁", "zone_in": "⤵", "zone_out": "⤴", "emergency": "🆘", "message": "✉"}


def system_prefers_dark() -> bool:
    """Rilevamento del tema di sistema, al meglio delle possibilità."""
    try:
        if sys.platform == "win32":
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
        if sys.platform == "darwin":
            r = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                               capture_output=True, text=True, timeout=2)
            return "dark" in r.stdout.lower()
        r = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                           capture_output=True, text=True, timeout=2)
        if "dark" in r.stdout.lower():
            return True
        r = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"],
                           capture_output=True, text=True, timeout=2)
        return "dark" in r.stdout.lower()
    except Exception:
        return False


class QueueLogHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.q.put_nowait((record.levelno, self.format(record)))
        except queue.Full:
            pass


def haversine(a, b) -> float:
    r = math.pi / 180
    h = (math.sin((b[0] - a[0]) * r / 2) ** 2 +
         math.cos(a[0] * r) * math.cos(b[0] * r) * math.sin((b[1] - a[1]) * r / 2) ** 2)
    return 2 * 6371 * math.asin(math.sqrt(h))


def bearing(a, b) -> float:
    r = math.pi / 180
    y = math.sin((b[1] - a[1]) * r) * math.cos(b[0] * r)
    x = (math.cos(a[0] * r) * math.sin(b[0] * r) -
         math.sin(a[0] * r) * math.cos(b[0] * r) * math.cos((b[1] - a[1]) * r))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def ago(when: float) -> str:
    s = max(0, time.time() - when)
    if s < 60:
        return t("{n} s fa", n=int(s))
    if s < 3600:
        return t("{n} min fa", n=int(s // 60))
    return t("{h} h {m} min fa", h=int(s // 3600), m=int(s % 3600 // 60))


def printable(s: str) -> str:
    return "".join(c if c.isprintable() else "·" for c in s)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} {__version__}")
        self._set_icon()
        # misura minima piccola: se la finestra è più piccola del contenuto compaiono le barre di
        # scorrimento; la misura iniziale si sceglie dopo aver costruito il pannello (_fit_window)
        self.minsize(480, 360)
        self.withdraw()

        self.cfg = Config.load()
        self.history = self._open_history() if self.cfg.history_enabled else None
        self.tiles = self._make_tiles(self.cfg)
        self.store = StationStore(history=self.history, tiles=self.tiles)
        self.web = WebServer(self.store)
        self.rx = Receiver(self.store)
        # messaggi APRS e avvisi: le notifiche arrivano da altri thread e passano da una coda
        from .servizio import Engine
        self.uiq: queue.Queue = queue.Queue(maxsize=2000)
        self.engine = Engine(self.store, self.rx)
        self.engine.configure(self.cfg)
        self.engine.callbacks.append(lambda kind, ev: self.uiq.put_nowait((kind, ev)))
        self.store.panel_password = self.cfg.panel_password
        self.store.on_rules = lambda rules: self.uiq.put_nowait(("rules", rules))
        self.alerts_unseen = 0
        self.msg_peer: str | None = None
        self.publisher = None          # geopin.GeopinPublisher se la pubblicazione è attiva
        self._gp_tick = 0
        self.events = self.store.subscribe(maxsize=5000)
        self.logq: queue.Queue = queue.Queue(maxsize=5000)
        handler = QueueLogHandler(self.logq)
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)

        self.stations: dict[str, dict] = {}
        self.devices: list[tuple[str, str]] = []      # (valore config, etichetta)
        self.level_db = -90.0
        self.was_running = False
        self.last_packet: float | None = None
        self.shown_packets = 0
        self.v_theme = tk.StringVar(value=self.cfg.theme if self.cfg.theme in THEMES or self.cfg.theme == "auto" else "auto")
        self.effective_theme = ""
        self.pal = THEMES["light"]
        self._last_status: tuple[str, str] | None = None

        self._style()
        self._build()
        self._build_menu()
        self._apply_theme()
        self._refresh_devices()
        self._load_cfg()
        self._station_changed()
        self._geopin_apply(self.cfg)       # pubblica subito anche gli arretrati in coda
        if self.history is not None:
            n = self.store.load_history(self.cfg.history_load_hours)
            if n:
                logging.info("Ricaricate dallo storico %d posizioni delle ultime %d ore",
                             n, self.cfg.history_load_hours)
        self._start_web(self.cfg)
        self._fit_window()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(100, self._poll)
        self.after(5000, self._refresh_ages)
        self.after(15000, self._watch_system_theme)

    # ------------------------------------------------------------ aspetto

    def _style(self) -> None:
        st = ttk.Style(self)
        if "clam" in st.theme_names():
            st.theme_use("clam")
        default = tkfont.nametofont("TkDefaultFont")
        if self.tk.call("tk", "windowingsystem") == "win32":
            default.configure(family="Segoe UI", size=10)
        else:
            default.configure(size=10)
        base = (default.actual("family"), 10)
        st.configure("Big.TButton", font=(base[0], 11, "bold"), padding=(10, 8))
        st.configure("Value.TLabel", font=(base[0], 15, "bold"))
        st.configure("Treeview", rowheight=24)

    def _fit_window(self) -> None:
        """Misura iniziale adattata allo schermo: quella del contenuto reale (che cresce con la scala
        dei caratteri di Windows), con un po' di spazio in più per la tabella, entro l'area utile.
        Se il pannello non ci sta, la finestra si apre ingrandita e si scorre."""
        cw, ch = self.scroll.content_size()
        want_w, want_h = max(1120, cw), max(980, ch + 120)
        w, h = fit_to_screen(self, want_w, want_h)
        if h < ch:                                 # servirà la barra verticale: le si fa posto in larghezza
            w, h = fit_to_screen(self, want_w + self.scroll.vsb.winfo_reqwidth() + 2, want_h)
        self.deiconify()
        if w < cw or h < ch:
            try:
                if self.tk.call("tk", "windowingsystem") == "x11":
                    self.attributes("-zoomed", True)
                else:
                    self.state("zoomed")
            except tk.TclError:
                pass

    def _build(self) -> None:
        ScrollFrame.install_wheel(self)
        self.scroll = ScrollFrame(self)
        self.scroll.pack(fill="both", expand=True)
        root = ttk.Frame(self.scroll.body, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=3)
        root.columnconfigure(1, weight=2)
        root.rowconfigure(1, weight=1)

        left = ttk.Frame(root)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        right = ttk.Frame(root)
        right.grid(row=0, column=1, sticky="nsew")

        self.inputs: list[ttk.Widget] = []         # disabilitati durante la ricezione
        self.groups: dict[str, list[ttk.Widget]] = {"audio": [], "kiss": [], "wav": [], "none": []}

        # ---- sorgente
        src = ttk.LabelFrame(left, text=t("Sorgente del segnale"), padding=10)
        src.pack(fill="x")
        src.columnconfigure(2, weight=1)
        self.v_source = tk.StringVar(value="audio")

        def radio(text, value, row):
            w = ttk.Radiobutton(src, text=text, value=value, variable=self.v_source,
                                command=self._apply_states)
            w.grid(row=row, column=0, columnspan=4, sticky="w", pady=(6 if row else 0, 2))
            self.inputs.append(w)

        def label(text, row, col=1):
            ttk.Label(src, text=text).grid(row=row, column=col, sticky="w", padx=(22, 8), pady=2)

        def add(group, w, **grid):
            w.grid(**grid)
            self.inputs.append(w)
            self.groups[group].append(w)
            return w

        radio(t("Scheda audio, con il demodulatore interno"), "audio", 0)
        label(t("Dispositivo"), 1)
        self.cb_device = add("audio", ttk.Combobox(src, state="readonly", width=40),
                             row=1, column=2, sticky="ew", pady=2)
        add("audio", ttk.Button(src, text=t("Aggiorna elenco"), command=self._refresh_devices),
            row=1, column=3, padx=(6, 0))
        label(t("Campionamento"), 2)
        rate_row = ttk.Frame(src)
        rate_row.grid(row=2, column=2, columnspan=2, sticky="w", pady=2)
        self.cb_rate = ttk.Combobox(rate_row, values=RATES, width=8, state="readonly")
        self.cb_rate.pack(side="left")
        ttk.Label(rate_row, text=t("Hz      Canale")).pack(side="left", padx=(4, 8))
        self.cb_channel = ttk.Combobox(rate_row, values=CHANNELS, width=9, state="readonly")
        self.cb_channel.pack(side="left")
        for w in (self.cb_rate, self.cb_channel):
            self.inputs.append(w)
            self.groups["audio"].append(w)

        radio(t("Direwolf o TNC esterno (KISS su TCP)"), "kiss", 3)
        label(t("Indirizzo"), 4)
        kiss_row = ttk.Frame(src)
        kiss_row.grid(row=4, column=2, columnspan=2, sticky="w", pady=2)
        self.v_kiss_host = tk.StringVar()
        self.v_kiss_port = tk.StringVar()
        e_host = ttk.Entry(kiss_row, textvariable=self.v_kiss_host, width=18)
        e_host.pack(side="left")
        ttk.Label(kiss_row, text=t("Porta")).pack(side="left", padx=(10, 6))
        e_port = ttk.Entry(kiss_row, textvariable=self.v_kiss_port, width=7)
        e_port.pack(side="left")
        for w in (e_host, e_port):
            self.inputs.append(w)
            self.groups["kiss"].append(w)

        radio(t("File WAV registrato"), "wav", 5)
        label(t("File"), 6)
        self.v_wav = tk.StringVar()
        add("wav", ttk.Entry(src, textvariable=self.v_wav), row=6, column=2, sticky="ew", pady=2)
        add("wav", ttk.Button(src, text=t("Sfoglia…"), command=self._pick_wav), row=6, column=3, padx=(6, 0))
        self.v_realtime = tk.BooleanVar()
        add("wav", ttk.Checkbutton(src, text=t("Riproduci a velocità reale"), variable=self.v_realtime),
            row=7, column=2, sticky="w", pady=2)
        radio(t("Nessuna sorgente VHF (solo LoRa e/o APRS-IS)"), "none", 8)

        ttk.Separator(src).grid(row=9, column=0, columnspan=4, sticky="ew", pady=(10, 4))
        label(t("Demodulatore"), 10)
        dem_row = ttk.Frame(src)
        dem_row.grid(row=10, column=2, columnspan=2, sticky="w", pady=2)
        self.cb_profile = ttk.Combobox(dem_row, values=[t for _, t in PROFILE_CHOICES],
                                       width=21, state="readonly")
        self.cb_profile.pack(side="left")
        self.v_fix = tk.BooleanVar(value=True)
        cb_fix = ttk.Checkbutton(dem_row, text=t("Correggi i bit errati"), variable=self.v_fix)
        cb_fix.pack(side="left", padx=(12, 0))
        self.inputs += [self.cb_profile, cb_fix]

        # ---- stazione e filtri
        sta = ttk.LabelFrame(left, text=t("Stazione e filtri"), padding=10)
        sta.pack(fill="x", pady=(10, 0))
        sta.columnconfigure(1, weight=1)
        self.v_home = tk.StringVar()
        self.v_filters = tk.StringVar()
        self.v_logon = tk.BooleanVar()
        self.v_logfile = tk.StringVar()
        hint = lambda text, row: ttk.Label(sta, text=text, style="Hint.TLabel").grid(  # noqa: E731
            row=row, column=1, columnspan=2, sticky="w")
        # posizione
        ttk.Label(sta, text=t("Posizione ricevente")).grid(row=0, column=0, sticky="w", padx=(0, 8))
        e = ttk.Entry(sta, textvariable=self.v_home)
        e.grid(row=0, column=1, sticky="ew", pady=(2, 0))
        self.inputs.append(e)
        # resta attivo anche in ricezione: la posizione si aggiorna al volo
        ttk.Button(sta, text=t("Scegli sulla mappa…"), command=self._pick_home).grid(
            row=0, column=2, padx=(6, 0), pady=(2, 0), sticky="ew")
        hint(t("lat, lon in gradi decimali, es. 44.494, 11.343"), 1)
        # tipo di dispositivo e commento: modificabili anche durante la ricezione
        ttk.Label(sta, text=t("Tipo di stazione")).grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(4, 0))
        stf = ttk.Frame(sta)
        stf.grid(row=2, column=1, columnspan=2, sticky="w", pady=(4, 0))
        self.cb_stype = ttk.Combobox(stf, values=[t(v[0]) for v in STATION_TYPES.values()],
                                     state="readonly", width=24)
        self.cb_stype.pack(side="left")
        # su aprs.fi il tipo diventa il simbolo; il campo "Dispositivo" dipende dall'identificativo
        # del programma (TOCALL), non da questa scelta
        ttk.Label(stf, text=t("= simbolo su aprs.fi"), style="Hint.TLabel").pack(side="left", padx=(8, 0))
        self.cb_stype.bind("<<ComboboxSelected>>", lambda e: self._station_changed())
        ttk.Label(sta, text=t("Commento")).grid(row=3, column=0, sticky="w", padx=(0, 8), pady=(4, 0))
        self.v_comment = tk.StringVar()
        ttk.Entry(sta, textvariable=self.v_comment).grid(row=3, column=1, columnspan=2, sticky="ew", pady=(4, 0))
        self.l_comment = ttk.Label(sta, style="Hint.TLabel", wraplength=440, justify="left")
        self.l_comment.grid(row=4, column=1, columnspan=2, sticky="w")
        self.v_comment.trace_add("write", lambda *a: self._station_changed())
        ttk.Label(sta, text=t("Apparato")).grid(row=5, column=0, sticky="w", padx=(0, 8), pady=(4, 0))
        self.v_equip = tk.StringVar()
        ttk.Entry(sta, textvariable=self.v_equip).grid(row=5, column=1, columnspan=2, sticky="ew", pady=(4, 0))
        self.l_equip = ttk.Label(sta, style="Hint.TLabel", wraplength=440, justify="left")
        self.l_equip.grid(row=6, column=1, columnspan=2, sticky="w")
        self.v_equip.trace_add("write", lambda *a: self._station_changed())
        # filtri
        ttk.Label(sta, text=t("Mostra solo")).grid(row=7, column=0, sticky="w", padx=(0, 8), pady=(4, 0))
        e = ttk.Entry(sta, textvariable=self.v_filters)
        e.grid(row=7, column=1, columnspan=2, sticky="ew", pady=(4, 0))
        self.inputs.append(e)
        hint(t("nominativi o prefissi separati da virgola; vuoto = tutti"), 8)
        cb = ttk.Checkbutton(sta, text=t("Salva i pacchetti su file"), variable=self.v_logon,
                             command=self._apply_states)
        cb.grid(row=9, column=0, sticky="w", pady=(6, 0))
        self.e_logfile = ttk.Entry(sta, textvariable=self.v_logfile)
        self.e_logfile.grid(row=9, column=1, sticky="ew", pady=(6, 0))
        self.b_logfile = ttk.Button(sta, text=t("Sfoglia…"), command=self._pick_log)
        self.b_logfile.grid(row=9, column=2, padx=(6, 0), pady=(6, 0))
        self.inputs += [cb, self.e_logfile, self.b_logfile]

        # ---- mappa
        mp = ttk.LabelFrame(left, text=t("Mappa nel browser"), padding=10)
        ttk.Label(mp, text=t("Porta")).pack(side="left")
        self.v_port = tk.StringVar()
        e = ttk.Entry(mp, textvariable=self.v_port, width=7)
        e.pack(side="left", padx=(8, 14))
        self.v_lan = tk.BooleanVar()
        c = ttk.Checkbutton(mp, text=t("Visibile in rete locale"), variable=self.v_lan)
        c.pack(side="left")
        self.inputs += [e, c]

        # ---- comandi
        cmd = ttk.Frame(right)
        cmd.pack(fill="x", pady=(0, 10))
        self.b_start = ttk.Button(cmd, text=t("Avvia ricezione"), style="Big.TButton", command=self._toggle)
        self.b_start.pack(side="left")
        ttk.Button(cmd, text=t("Apri mappa"), command=self._open_map).pack(side="left", padx=(10, 0))

        # ---- stato
        stf = ttk.LabelFrame(right, text=t("Stato"), padding=10)
        stf.pack(fill="x")
        top = ttk.Frame(stf)
        top.pack(fill="x")
        self.dot = tk.Canvas(top, width=14, height=14, highlightthickness=0)
        self.dot.pack(side="left", padx=(0, 8))
        self.dot_id = self.dot.create_oval(2, 2, 12, 12, fill=self.pal["idle"], outline="")
        self.l_status = ttk.Label(top, text=t("Fermo"), wraplength=330)
        self.l_status.pack(side="left", fill="x")
        nums = ttk.Frame(stf)
        nums.pack(fill="x", pady=(10, 0))
        self.l_counts = {}
        for i, (key, text) in enumerate([("packets", t("Pacchetti")), ("frames", t("Frame validi")),
                                         ("stations", t("Stazioni"))]):
            nums.columnconfigure(i, weight=1)
            v = ttk.Label(nums, text="0", style="Value.TLabel")
            v.grid(row=0, column=i, sticky="w")
            cap = ttk.Label(nums, text=text, style="Hint.TLabel")
            cap.grid(row=1, column=i, sticky="w")
            if key == "frames":
                self.l_frames_cap = cap
            self.l_counts[key] = v
        self.l_last = ttk.Label(stf, text=t("Nessun pacchetto ricevuto"), style="Hint.TLabel")
        self.l_last.pack(anchor="w", pady=(8, 0))

        # ---- livello audio
        lv = ttk.LabelFrame(right, text=t("Livello audio"), padding=10)
        lv.pack(fill="x", pady=(10, 0))
        self.meter = tk.Canvas(lv, height=26, highlightthickness=0)
        self.meter.pack(fill="x")
        self.meter.bind("<Configure>", lambda e: self._draw_meter())
        self.l_level = ttk.Label(lv, text=t("Inattivo"))
        self.l_level.pack(anchor="w", pady=(6, 0))
        ttk.Label(lv, style="Hint.TLabel", wraplength=320, justify="left",
                  text=t("Il picco dei pacchetti deve restare nel verde.")).pack(anchor="w", pady=(4, 0))

        mp.pack(fill="x", pady=(10, 0))           # a sinistra, sotto la stazione: la tabella in basso ha più spazio

        isf = ttk.LabelFrame(right, text=t("APRS-IS"), padding=10)
        isf.pack(fill="x", pady=(10, 0))
        row = ttk.Frame(isf)
        row.pack(fill="x")
        self.v_is_on = tk.BooleanVar()
        cb = ttk.Checkbutton(row, text=t("Collegati ad APRS-IS"), variable=self.v_is_on)
        cb.pack(side="left")
        self.inputs.append(cb)
        b = ttk.Button(row, text=t("Impostazioni…"), command=self._aprsis_dialog)
        b.pack(side="right")
        self.inputs.append(b)
        self.l_is = ttk.Label(isf, text=t("Non collegato"), style="Hint.TLabel", wraplength=320, justify="left")
        self.l_is.pack(anchor="w", pady=(6, 0))

        # ---- LoRa APRS
        lof = ttk.LabelFrame(right, text=t("LoRa APRS"), padding=10)
        lof.pack(fill="x", pady=(10, 0))
        row = ttk.Frame(lof)
        row.pack(fill="x")
        self.v_lora_on = tk.BooleanVar()
        cb = ttk.Checkbutton(row, text=t("Ricevi da un modulo LoRa"), variable=self.v_lora_on)
        cb.pack(side="left")
        self.inputs.append(cb)
        b = ttk.Button(row, text=t("Impostazioni…"), command=self._lora_dialog)
        b.pack(side="right")
        self.inputs.append(b)
        self.l_lora = ttk.Label(lof, text=t("Non attivo"), style="Hint.TLabel", wraplength=320, justify="left")
        self.l_lora.pack(anchor="w", pady=(6, 0))

        # ---- Geopin
        gpf = ttk.LabelFrame(right, text=t("Geopin"), padding=10)
        gpf.pack(fill="x", pady=(10, 0))
        row = ttk.Frame(gpf)
        row.pack(fill="x")
        self.v_gp_on = tk.BooleanVar()
        cb = ttk.Checkbutton(row, text=t("Attiva"), variable=self.v_gp_on, command=self._geopin_toggled)
        cb.pack(side="left")
        self.inputs.append(cb)
        b = ttk.Button(row, text=t("Impostazioni…"), command=self._geopin_dialog)
        b.pack(side="right")
        self.inputs.append(b)
        self.b_gp_now = ttk.Button(row, text=t("Pubblica ora"), command=self._geopin_now)
        self.b_gp_now.pack(side="right", padx=(0, 6))
        self.l_gp = ttk.Label(gpf, text=t("Non attiva"), style="Hint.TLabel", wraplength=340, justify="left")
        self.l_gp.pack(anchor="w", pady=(6, 0))


        # ---- schede in basso
        nb = ttk.Notebook(root)
        nb.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(12, 0))
        self.nb = nb

        tab_st = ttk.Frame(nb, padding=6)
        nb.add(tab_st, text=t("Stazioni"))
        cols = [("id", t("Nominativo"), 110), ("kind", t("Tipo"), 110), ("pos", t("Posizione"), 150),
                ("dist", t("Distanza"), 112), ("speed", t("Velocità"), 80), ("alt", t("Quota"), 70),
                ("last", t("Ultimo"), 100), ("n", t("Pacchetti"), 82), ("orig", t("Ricezione"), 125),
                ("comment", t("Commento"), 168)]
        # height = righe minime: la tabella si allarga con la finestra, ma su schermi piccoli non spinge
        # fuori il resto del pannello
        self.tree = ttk.Treeview(tab_st, columns=[c[0] for c in cols], show="headings", selectmode="browse",
                                 height=2)
        for key, text, w in cols:
            self.tree.heading(key, text=text)
            self.tree.column(key, width=w, stretch=key == "comment",
                             anchor="e" if key in ("speed", "alt", "n") else "w")
        sb = ttk.Scrollbar(tab_st, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        tab_st.rowconfigure(0, weight=1)
        tab_st.columnconfigure(0, weight=1)
        bar = ttk.Frame(tab_st)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(bar, text=t("Mostra sulla mappa"), command=self._show_selected).pack(side="left")
        ttk.Label(bar, text=t("Doppio clic su una riga per aprirla sulla mappa"),
                  style="Hint.TLabel").pack(side="left", padx=10)
        ttk.Button(bar, text=t("Svuota stazioni"), command=self._clear).pack(side="right")
        self.tree.bind("<Double-1>", lambda e: self._show_selected())

        self._build_messages(nb)
        self._build_alerts(nb)
        self.t_packets = self._text_tab(nb, t("Pacchetti"))
        self.t_log = self._text_tab(nb, t("Registro"))

    # ------------------------------------------------------------ messaggi e avvisi

    def _build_messages(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text=t("Messaggi"))
        self.tab_msg = tab
        tab.columnconfigure(1, weight=1)
        tab.rowconfigure(0, weight=1)
        self.conv = ttk.Treeview(tab, columns=("peer", "last"), show="headings", selectmode="browse", height=2)
        self.conv.heading("peer", text=t("Stazione"))
        self.conv.heading("last", text=t("Ultimo"))
        self.conv.column("peer", width=110, stretch=False)
        self.conv.column("last", width=110, stretch=False)
        self.conv.grid(row=0, column=0, sticky="nsew")
        self.conv.bind("<<TreeviewSelect>>", lambda e: self._select_peer())
        right = ttk.Frame(tab)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.t_thread = tk.Text(right, wrap="word", height=4, state="disabled", relief="flat", borderwidth=0,
                                padx=8, pady=6)
        sb = ttk.Scrollbar(right, orient="vertical", command=self.t_thread.yview)
        self.t_thread.configure(yscrollcommand=sb.set)
        self.t_thread.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        bar = ttk.Frame(tab)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Label(bar, text=t("A")).pack(side="left")
        self.v_msg_to = tk.StringVar()
        ttk.Entry(bar, textvariable=self.v_msg_to, width=11).pack(side="left", padx=(6, 8))
        self.v_msg_text = tk.StringVar()
        e = ttk.Entry(bar, textvariable=self.v_msg_text)
        e.pack(side="left", fill="x", expand=True)
        e.bind("<Return>", lambda ev: self._send_message())
        self.l_msg_cnt = ttk.Label(bar, text="0/67", style="Hint.TLabel", width=6)
        self.l_msg_cnt.pack(side="left", padx=(6, 0))
        self.v_msg_text.trace_add("write", lambda *a: self._msg_text_changed())
        self.b_msg_send = ttk.Button(bar, text=t("Invia"), command=self._send_message)
        self.b_msg_send.pack(side="left", padx=(6, 0))
        ttk.Button(bar, text=t("Sulla mappa"), command=lambda: self._open_page("/#messaggi")).pack(side="left", padx=(6, 0))
        self.l_msg_state = ttk.Label(tab, style="Hint.TLabel", wraplength=900, justify="left")
        self.l_msg_state.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.after(200, self._refresh_messages)

    def _msg_text_changed(self) -> None:
        import unicodedata
        v = self.v_msg_text.get()
        clean = "".join(c for c in unicodedata.normalize("NFD", v) if unicodedata.category(c) != "Mn")
        clean = "".join(c for c in clean if " " <= c <= "~" and c not in "|~{")[:67]
        if clean != v:
            self.v_msg_text.set(clean)              # lettere accentate senza accento
            return
        self.l_msg_cnt.configure(text=f"{len(v)}/67")

    def _update_msg_state(self) -> None:
        m = self.engine.messages
        if not self.cfg.messages_enabled:
            txt = t("Messaggi disattivati (menu Strumenti).")
        elif not m.my_call:
            txt = t("I messaggi si ricevono e si inviano con il collegamento APRS-IS: imposta il nominativo e il passcode.")
        elif not m.can_send():
            txt = t("Messaggi per {calls}. Per inviare serve il collegamento APRS-IS verificato (nominativo e passcode).",
                    calls=", ".join(sorted(m.my_calls())))
        else:
            txt = t("Messaggi per {calls}. Invio tramite APRS-IS, con ritrasmissione fino alla conferma.",
                    calls=", ".join(sorted(m.my_calls())))
        if self.l_msg_state.cget("text") != txt:
            self.l_msg_state.configure(text=txt)
        can = bool(m.my_call) and m.can_send()
        self.b_msg_send.state(["!disabled"] if can else ["disabled"])

    def _refresh_messages(self) -> None:
        snap = self.engine.messages.snapshot()
        by: dict[str, list] = {}
        for msg in snap["messages"]:
            by.setdefault(msg["peer"], []).append(msg)
        order = sorted(by, key=lambda k: -by[k][-1]["t"])
        sel = self.msg_peer
        self.conv.delete(*self.conv.get_children())
        for peer in order:
            unread = sum(1 for x in by[peer] if x["dir"] == "in" and not x["read"])
            self.conv.insert("", "end", iid=peer, values=(peer + (f"  ({unread})" if unread else ""),
                                                           time.strftime("%d/%m %H:%M", time.localtime(by[peer][-1]["t"]))))
        if sel and self.conv.exists(sel):
            self.conv.selection_set(sel)
        self._show_thread(by.get(sel or "", []))
        n = snap["unread"]
        self.nb.tab(self.tab_msg, text=t("Messaggi") + (f" ({n})" if n else ""))
        self._update_msg_state()

    def _select_peer(self) -> None:
        sel = self.conv.selection()
        if not sel or sel[0] == self.msg_peer:
            return
        self.msg_peer = sel[0]
        self.v_msg_to.set(sel[0])
        self.engine.messages.mark_read(sel[0])
        self._refresh_messages()

    def _show_thread(self, msgs: list) -> None:
        st = {"in invio": t("in invio…"), "consegnato": t("consegnato"), "senza conferma": t("senza conferma"),
              "rifiutato": t("rifiutato")}
        w = self.t_thread
        w.configure(state="normal")
        w.delete("1.0", "end")
        w.tag_configure("out", justify="right", foreground=self.pal["accent"])
        w.tag_configure("meta", foreground=self.pal["muted"])
        for msg in msgs:
            tag = "out" if msg["dir"] == "out" else ""
            w.insert("end", msg["text"] + "\n", tag)
            meta = time.strftime("%d/%m %H:%M", time.localtime(msg["t"]))
            if msg["dir"] == "out":
                meta += " · " + st.get(msg["status"], msg["status"])
            w.insert("end", meta + "\n\n", (tag, "meta") if tag else "meta")
        if not msgs:
            w.insert("end", t("Scegli una conversazione a sinistra, oppure scrivi il destinatario e il testo qui sotto."), "meta")
        w.configure(state="disabled")
        w.see("end")

    def _send_message(self) -> None:
        to, text = self.v_msg_to.get().strip().upper(), self.v_msg_text.get().strip()
        if not to or not text:
            return
        try:
            self.engine.messages.send(to, text)
        except ValueError as e:
            messagebox.showerror(t("Messaggio non inviato"), str(e), parent=self)
            return
        self.v_msg_text.set("")
        self.msg_peer = to
        self._refresh_messages()

    def _messages_toggled(self) -> None:
        self.cfg.messages_enabled = self.v_msg_on.get()
        self._save_cfg_quiet()
        self.engine.configure(self.cfg)
        self._update_msg_state()
        if self.rx.running:
            messagebox.showinfo(t("Messaggi APRS"), t("La modifica vale dal prossimo avvio della ricezione."), parent=self)

    def _build_alerts(self, nb: ttk.Notebook) -> None:
        tab = ttk.Frame(nb, padding=6)
        nb.add(tab, text=t("Avvisi"))
        self.tab_al = tab
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        self.al_tree = ttk.Treeview(tab, columns=("time", "text"), show="headings", selectmode="browse", height=2)
        self.al_tree.heading("time", text=t("Ora"))
        self.al_tree.heading("text", text=t("Avviso"))
        self.al_tree.column("time", width=90, stretch=False)
        self.al_tree.column("text", width=600, stretch=True)
        sb = ttk.Scrollbar(tab, orient="vertical", command=self.al_tree.yview)
        self.al_tree.configure(yscrollcommand=sb.set)
        self.al_tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.al_tree.bind("<Double-1>", lambda e: self._alert_open())
        bar = ttk.Frame(tab)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        ttk.Button(bar, text=t("Regole…"), command=self._alerts_dialog).pack(side="left")
        self.v_al_sound = tk.BooleanVar(value=self.cfg.alert_sound)
        ttk.Checkbutton(bar, text=t("Suono e finestra"), variable=self.v_al_sound,
                        command=self._alert_sound_toggled).pack(side="left", padx=(10, 0))
        self.l_al_rules = ttk.Label(bar, style="Hint.TLabel")
        self.l_al_rules.pack(side="left", padx=(10, 0))
        ttk.Button(bar, text=t("Svuota"), command=self._alerts_clear).pack(side="right")
        self.nb.bind("<<NotebookTabChanged>>", lambda e: self._tab_changed())
        self._alert_rules_text()

    def _alert_rules_text(self) -> None:
        r = self.engine.alerts.rules()
        parts = []
        if r["watch"]:
            parts.append(t("{n} stazioni osservate", n=len(r["watch"])))
        if r["zones"]:
            parts.append(t("{n} zone", n=len(r["zones"])))
        if r["emergency"]:
            parts.append(t("emergenze"))
        if r["messages"]:
            parts.append(t("messaggi"))
        self.l_al_rules.configure(text=t("Attivi: {list}", list=", ".join(parts)) if parts else t("Nessuna regola attiva"))

    def _tab_changed(self) -> None:
        try:
            cur = self.nb.select()
        except tk.TclError:
            return
        if cur == str(self.tab_al):
            self.alerts_unseen = 0
            self.nb.tab(self.tab_al, text=t("Avvisi"))

    def _on_alert(self, a: dict) -> None:
        icon = ALERT_ICONS.get(a["kind"], "•")
        self.al_tree.insert("", 0, iid=str(a["n"]), values=(time.strftime("%H:%M:%S", time.localtime(a["t"])),
                                                              f"{icon}  {a['text']}"))
        kids = self.al_tree.get_children()
        if len(kids) > 300:
            self.al_tree.delete(*kids[300:])
        if self.nb.select() != str(self.tab_al):
            self.alerts_unseen += 1
            self.nb.tab(self.tab_al, text=t("Avvisi") + f" ({self.alerts_unseen})")
        logging.info("%s %s", t("Avviso:"), a["text"])
        if not self.v_al_sound.get():
            return
        self.bell()
        if a["level"] == "critical" or a["kind"] == "message":
            self._alert_popup(a)

    def _alert_popup(self, a: dict) -> None:
        """Finestra che non blocca il pannello; le emergenze restano finché non si chiudono."""
        win = tk.Toplevel(self)
        win.title(t("Avviso APRS"))
        win.configure(bg=self.pal["bg"])
        win.transient(self)
        win.attributes("-topmost", True)
        frm = ttk.Frame(win, padding=16)
        frm.pack(fill="both", expand=True)
        style = "Value.TLabel" if a["level"] == "critical" else "TLabel"
        ttk.Label(frm, text=a["text"], style=style, wraplength=420, justify="left").pack(anchor="w")
        ttk.Label(frm, text=time.strftime("%H:%M:%S", time.localtime(a["t"])), style="Hint.TLabel").pack(anchor="w", pady=(4, 0))
        row = ttk.Frame(frm)
        row.pack(anchor="e", pady=(12, 0))
        ttk.Button(row, text=t("Chiudi"), command=win.destroy).pack(side="right")
        target = "/#messaggi" if a["kind"] == "message" else "/#" + quote(a["call"])
        ttk.Button(row, text=t("Apri sulla mappa"), command=lambda: (self._open_page(target), win.destroy())).pack(side="right", padx=(0, 8))
        fit_to_screen(win, 460, 160, center_on=self)
        win.bind("<Escape>", lambda e: win.destroy())
        if a["level"] != "critical":
            win.after(30000, lambda: win.winfo_exists() and win.destroy())

    def _alert_open(self) -> None:
        sel = self.al_tree.selection()
        if not sel:
            return
        a = next((x for x in self.engine.alerts.snapshot()["alerts"] if str(x["n"]) == sel[0]), None)
        if a:
            self._open_page("/#messaggi" if a["kind"] == "message" else "/#" + quote(a["call"]))

    def _alerts_clear(self) -> None:
        self.engine.alerts.clear()
        self.store.push({"alerts_cleared": True})
        self.al_tree.delete(*self.al_tree.get_children())

    def _alert_sound_toggled(self) -> None:
        self.cfg.alert_sound = self.v_al_sound.get()
        self._save_cfg_quiet()

    def _save_rules(self, rules: dict) -> None:
        """Regole cambiate (dalla mappa o dalla finestra): si salvano nella configurazione."""
        self.cfg.alert_watch = ", ".join(rules["watch"])
        self.cfg.alert_zones = rules["zones"]
        self.cfg.alert_emergency, self.cfg.alert_messages = rules["emergency"], rules["messages"]
        self.cfg.alert_quiet_min = rules["quiet_min"]
        self._save_cfg_quiet()
        self._alert_rules_text()

    def _alerts_dialog(self) -> None:
        from .avvisi import clean_zones
        r = self.engine.alerts.rules()
        dlg, frm = self._dialog(t("Regole degli avvisi"))
        v_watch = tk.StringVar(value=", ".join(r["watch"]))
        v_quiet = tk.StringVar(value=str(r["quiet_min"]))
        v_em, v_msg = tk.BooleanVar(value=r["emergency"]), tk.BooleanVar(value=r["messages"])
        ttk.Label(frm, text=t("Stazioni da osservare")).grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(frm, textvariable=v_watch, width=48).grid(row=0, column=1, sticky="ew")
        ttk.Label(frm, style="Hint.TLabel", wraplength=420, justify="left", text=t(
            "Separate da virgole. Senza SSID valgono tutti gli SSID; con * tutti i nominativi che iniziano così.")
                  ).grid(row=1, column=1, sticky="w")
        ttk.Label(frm, text=t("Riavvisa dopo (minuti)")).grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        ttk.Entry(frm, textvariable=v_quiet, width=8).grid(row=2, column=1, sticky="w", pady=(8, 0))
        ttk.Checkbutton(frm, text=t("Segnale di emergenza Mic-E"), variable=v_em).grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))
        ttk.Checkbutton(frm, text=t("Messaggi per la mia stazione"), variable=v_msg).grid(row=4, column=0, columnspan=2, sticky="w", pady=(4, 0))
        ttk.Label(frm, text=t("Zone")).grid(row=5, column=0, sticky="nw", padx=(0, 8), pady=(12, 0))
        zt = ttk.Treeview(frm, columns=("name", "pos", "r", "calls"), show="headings", height=5, selectmode="browse")
        for k, txt, w in (("name", t("Nome"), 110), ("pos", t("Centro"), 150), ("r", "km", 50), ("calls", t("Stazioni"), 150)):
            zt.heading(k, text=txt)
            zt.column(k, width=w, stretch=k == "calls")
        zt.grid(row=5, column=1, sticky="ew", pady=(12, 0))
        zones = [dict(z) for z in r["zones"]]

        def fill():
            zt.delete(*zt.get_children())
            for i, z in enumerate(zones):
                zt.insert("", "end", iid=str(i), values=(z["name"], f"{z['lat']:.4f}, {z['lon']:.4f}", f"{z['radius_km']:g}",
                                                        ", ".join(z.get("calls") or []) or t("tutte")))
        fill()
        zf = ttk.Frame(frm)
        zf.grid(row=6, column=1, sticky="ew", pady=(6, 0))
        v_zn, v_zc, v_zr, v_zs = tk.StringVar(), tk.StringVar(value=self.v_home.get()), tk.StringVar(value="5"), tk.StringVar()
        for lbl, var, w in ((t("Nome"), v_zn, 12), (t("Centro lat, lon"), v_zc, 18), ("km", v_zr, 5), (t("Solo"), v_zs, 14)):
            ttk.Label(zf, text=lbl).pack(side="left", padx=(0, 3))
            ttk.Entry(zf, textvariable=var, width=w).pack(side="left", padx=(0, 8))

        def add_zone():
            try:
                lat, lon = (float(x) for x in v_zc.get().replace(" ", "").split(","))
                radius = float(v_zr.get().replace(",", "."))
            except ValueError:
                messagebox.showerror(t("Zone"), t("Scrivi il centro come lat, lon e il raggio in km."), parent=dlg)
                return
            z = clean_zones([{"name": v_zn.get() or t("Zona {n}", n=len(zones) + 1), "lat": lat, "lon": lon,
                              "radius_km": radius, "calls": v_zs.get()}])
            if not z:
                messagebox.showerror(t("Zone"), t("Zona non valida."), parent=dlg)
                return
            zones.append(z[0])
            v_zn.set("")
            fill()

        def del_zone():
            sel = zt.selection()
            if sel:
                zones.pop(int(sel[0]))
                fill()
        zb = ttk.Frame(frm)
        zb.grid(row=7, column=1, sticky="w", pady=(6, 0))
        ttk.Button(zb, text=t("Aggiungi la zona"), command=add_zone).pack(side="left")
        ttk.Button(zb, text=t("Togli la zona scelta"), command=del_zone).pack(side="left", padx=(6, 0))
        ttk.Label(frm, style="Hint.TLabel", wraplength=480, justify="left", text=t(
            "Le zone si disegnano più comodamente sulla mappa: scheda Avvisi, Regole, Aggiungi una zona dalla mappa.")
                  ).grid(row=8, column=0, columnspan=2, sticky="w", pady=(10, 0))

        def ok():
            try:
                quiet = max(1, int(v_quiet.get()))
            except ValueError:
                messagebox.showerror(t("Avvisi"), t("I minuti devono essere un numero."), parent=dlg)
                return
            self.engine.alerts.configure(v_watch.get(), zones, v_em.get(), v_msg.get(), quiet)
            rules = self.engine.alerts.rules()
            self.store.push({"alert_rules": rules})
            self._save_rules(rules)
            dlg.destroy()
        self._dialog_buttons(dlg, frm, 9, ok)

    # ------------------------------------------------------------ pannello web e lingua

    def _open_page(self, path: str) -> None:
        if not self.web.url:
            messagebox.showinfo(t("Mappa non attiva"), t("Avvia la ricezione o correggi la porta della mappa."), parent=self)
            return
        theme = "scuro" if self.effective_theme == "dark" else "chiaro"
        base, _, frag = path.partition("#")
        webbrowser.open(f"{self.web.url}{base or '/'}?tema={theme}" + (f"#{frag}" if frag else ""))

    def _panel_password_dialog(self) -> None:
        from .server import hash_password
        dlg, frm = self._dialog(t("Password del pannello web"))
        ttk.Label(frm, wraplength=440, justify="left", text=t(
            "Con la password, dalla rete locale si possono usare il pannello web (/pannello), l'invio dei messaggi "
            "e le regole degli avvisi. Senza password funzionano solo da questo computer. Serve anche "
            "\"Visibile in rete locale\" nella sezione della mappa.")).grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(frm, text=t("Stato: impostata") if self.cfg.panel_password else t("Stato: nessuna password"),
                  style="Hint.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        v1, v2 = tk.StringVar(), tk.StringVar()
        for r, (lbl, v) in enumerate(((t("Nuova password"), v1), (t("Ripetila"), v2)), start=2):
            ttk.Label(frm, text=lbl).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
            ttk.Entry(frm, textvariable=v, show="•", width=28).grid(row=r, column=1, sticky="w", pady=(8, 0))

        def set_pw(pw: str):
            self.cfg.panel_password = hash_password(pw) if pw else ""
            self.store.panel_password = self.cfg.panel_password
            self._save_cfg_quiet()
            dlg.destroy()

        def ok():
            if v1.get() != v2.get():
                messagebox.showerror(t("Password"), t("Le due password non coincidono."), parent=dlg)
                return
            if len(v1.get()) < 8:
                messagebox.showerror(t("Password"), t("La password deve avere almeno 8 caratteri."), parent=dlg)
                return
            set_pw(v1.get())
        ttk.Button(frm, text=t("Togli la password"), command=lambda: set_pw("")).grid(row=4, column=0, sticky="w", pady=(16, 0))
        self._dialog_buttons(dlg, frm, 4, ok)

    def _language_changed(self) -> None:
        self.cfg.language = self.v_lang.get()
        self._save_cfg_quiet()
        lang = set_language(self.cfg.language)
        set_language(lang)
        messagebox.showinfo("APRS Master", t("La nuova lingua si vede al prossimo avvio del programma. "
                                             "La mappa nel browser cambia subito, ricaricando la pagina."), parent=self)

    def _save_cfg_quiet(self) -> None:
        try:
            self.cfg.save()
        except OSError as e:
            logging.warning("Configurazione non salvata: %s", e)

    # ------------------------------------------------------------ tema

    def _build_menu(self) -> None:
        self.menubar = tk.Menu(self)
        self.menu_view = tk.Menu(self.menubar, tearoff=0)
        for value, label in THEME_CHOICES:
            self.menu_view.add_radiobutton(label=label, value=value, variable=self.v_theme,
                                           command=self._theme_changed)
        self.menu_view.add_separator()
        self.v_lang = tk.StringVar(value=self.cfg.language if self.cfg.language in LANGS else "auto")
        self.menu_lang = tk.Menu(self.menu_view, tearoff=0)
        for value, label in (("auto", t("Come il sistema")),) + tuple(LANGS.items()):
            self.menu_lang.add_radiobutton(label=label, value=value, variable=self.v_lang, command=self._language_changed)
        self.menu_view.add_cascade(label=t("Lingua") + " / Language", menu=self.menu_lang)
        self.menubar.add_cascade(label=t("Visualizza"), menu=self.menu_view)
        # ---- storico
        self.v_hist_on = tk.BooleanVar(value=self.cfg.history_enabled)
        self.v_hist_days = tk.IntVar(value=self.cfg.history_days)
        self.v_hist_load = tk.IntVar(value=self.cfg.history_load_hours)
        self.menu_hist = tk.Menu(self.menubar, tearoff=0)
        self.menu_hist.add_command(label=t("Esporta tracce GPX…"), command=self._export_gpx)
        self.menu_hist.add_separator()
        self.menu_hist.add_checkbutton(label=t("Salva lo storico dei pacchetti"), variable=self.v_hist_on,
                                       command=self._history_toggled)
        self.menu_hist_load = tk.Menu(self.menu_hist, tearoff=0)
        for hours, text in ((0, t("Nulla")), (6, t("Ultime 6 ore")), (24, t("Ultime 24 ore")), (168, t("Ultimi 7 giorni"))):
            self.menu_hist_load.add_radiobutton(label=text, value=hours, variable=self.v_hist_load,
                                                command=self._save_history_settings)
        self.menu_hist.add_cascade(label=t("All'avvio mostra sulla mappa"), menu=self.menu_hist_load)
        self.menu_hist_days = tk.Menu(self.menu_hist, tearoff=0)
        for days, text in ((30, t("30 giorni")), (90, t("90 giorni")), (365, t("1 anno")), (0, t("Per sempre"))):
            self.menu_hist_days.add_radiobutton(label=text, value=days, variable=self.v_hist_days,
                                                command=self._save_history_settings)
        self.menu_hist.add_cascade(label=t("Conserva i dati per"), menu=self.menu_hist_days)
        self.menu_hist.add_separator()
        self.menu_hist.add_command(label=t("Svuota lo storico…"), command=self._clear_history)
        self.menubar.add_cascade(label=t("Storico"), menu=self.menu_hist)

        # ---- mappe
        self.map_cfg = {k: getattr(self.cfg, k) for k in ("map_source", "map_url", "map_attribution", "mbtiles_path")}
        self.v_map_src = tk.StringVar(value=self.map_cfg["map_source"])
        self.v_cache_mb = tk.IntVar(value=self.cfg.map_cache_mb)
        self.menu_map = tk.Menu(self.menubar, tearoff=0)
        self.menu_map.add_radiobutton(label=t("OpenStreetMap (online)"), value="osm", variable=self.v_map_src,
                                      command=lambda: self._set_map_source("osm"))
        self.menu_map.add_radiobutton(label=t("Server di mappe con cache su disco…"), value="custom",
                                      variable=self.v_map_src, command=lambda: self._set_map_source("custom"))
        self.menu_map.add_radiobutton(label=t("Mappa offline da file MBTiles…"), value="mbtiles",
                                      variable=self.v_map_src, command=lambda: self._set_map_source("mbtiles"))
        self.menu_map.add_separator()
        self.menu_cache = tk.Menu(self.menu_map, tearoff=0)
        for mb, text in ((200, "200 MB"), (500, "500 MB"), (1000, "1 GB"), (2000, "2 GB")):
            self.menu_cache.add_radiobutton(label=text, value=mb, variable=self.v_cache_mb,
                                            command=self._cache_size_changed)
        self.menu_map.add_cascade(label=t("Dimensione massima della cache"), menu=self.menu_cache)
        self.menu_map.add_command(label=t("Svuota la cache delle mappe…"), command=self._clear_map_cache)
        self.menubar.add_cascade(label=t("Mappa"), menu=self.menu_map)

        # ---- strumenti: messaggi, avvisi, pannello web
        self.menu_tools = tk.Menu(self.menubar, tearoff=0)
        self.v_msg_on = tk.BooleanVar(value=self.cfg.messages_enabled)
        self.menu_tools.add_checkbutton(label=t("Messaggi APRS (tramite APRS-IS)"), variable=self.v_msg_on,
                                        command=self._messages_toggled)
        self.menu_tools.add_command(label=t("Regole degli avvisi…"), command=self._alerts_dialog)
        self.menu_tools.add_separator()
        self.menu_tools.add_command(label=t("Apri il pannello web"), command=lambda: self._open_page("/pannello"))
        self.menu_tools.add_command(label=t("Password del pannello web…"), command=self._panel_password_dialog)
        self.menubar.add_cascade(label=t("Strumenti"), menu=self.menu_tools)

        self.menu_help = tk.Menu(self.menubar, tearoff=0)
        self.menu_help.add_command(label=t("Licenza (GNU GPL v3)"), command=self._show_license)
        self.menu_help.add_command(label=t("Informazioni"), command=self._about)
        self.menubar.add_cascade(label=t("Aiuto"), menu=self.menu_help)
        self.config(menu=self.menubar)

    def _set_icon(self) -> None:
        try:
            self._icons = [tk.PhotoImage(file=str(STATIC / f"icona-{n}.png")) for n in (256, 64, 32)]
            self.iconphoto(True, *self._icons)
        except (tk.TclError, OSError):
            pass                       # senza icona il programma funziona comunque

    def _about(self) -> None:
        from .receiver import CONFIG_PATH
        from .messaggi import DB_PATH as MSG_DB
        messagebox.showinfo(
            t("Informazioni"),
            f"{APP_NAME} {__version__}\n{COPYRIGHT}\n\n{t(LICENSE_P1)}\n\n{t(LICENSE_P2)}\n\n"
            + t("Mappa © OpenStreetMap contributors.") + "\n\n"
            + t("Impostazioni:") + f"  {CONFIG_PATH}\n"
            + t("Storico:") + f"  {DB_PATH}\n"
            + t("Messaggi:") + f"  {MSG_DB}\n"
            + t("Coda per Geopin:") + f"  {Path.home() / '.aprs_mappa_geopin.db'}",
            parent=self)

    def _show_license(self) -> None:
        text = license_text()
        if text is None:
            if messagebox.askyesno(t("Licenza"), t("Il file della licenza non è stato trovato.\n"
                                   "Aprire il testo della GNU GPL v3 sul sito gnu.org?"), parent=self):
                webbrowser.open("https://www.gnu.org/licenses/gpl-3.0.html")
            return
        win = tk.Toplevel(self)
        win.title(t("Licenza: GNU General Public License v3"))
        win.configure(bg=self.pal["bg"])
        win.transient(self)
        fit_to_screen(win, 720, 600, center_on=self)
        frm = ttk.Frame(win, padding=10)
        frm.pack(fill="both", expand=True)
        txtw = tk.Text(frm, wrap="word", font=("Courier", 10), bg=self.pal["text_bg"], fg=self.pal["fg"],
                    relief="flat", padx=10, pady=8, selectbackground=self.pal["select"])
        sb = ttk.Scrollbar(frm, command=txtw.yview)
        txtw.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txtw.pack(side="left", fill="both", expand=True)
        txtw.insert("1.0", text)
        txtw.configure(state="disabled")
        ttk.Button(win, text=t("Chiudi"), command=win.destroy).pack(pady=(0, 10))
        win.bind("<Escape>", lambda e: win.destroy())

    def _theme_changed(self) -> None:
        self._apply_theme()
        self.cfg.theme = self.v_theme.get()
        try:
            self.cfg.save()
        except OSError:
            pass

    def _watch_system_theme(self) -> None:
        if self.v_theme.get() == "auto":
            self._apply_theme()
        self.after(15000, self._watch_system_theme)

    def _apply_theme(self, force: bool = False) -> None:
        choice = self.v_theme.get()
        name = ("dark" if system_prefers_dark() else "light") if choice == "auto" else choice
        if name == self.effective_theme and not force:
            return
        self.effective_theme = name
        p = self.pal = THEMES[name]
        for sf in ScrollFrame.registry:           # fondo dei contenitori scorrevoli (finestra e dialoghi)
            sf.set_background(p["bg"])
        st = ttk.Style(self)

        st.configure(".", background=p["bg"], foreground=p["fg"], bordercolor=p["line"],
                     lightcolor=p["bg"], darkcolor=p["bg"], troughcolor=p["track"],
                     fieldbackground=p["field"], selectbackground=p["select"],
                     selectforeground=p["select_fg"], insertcolor=p["fg"], arrowcolor=p["fg"],
                     focuscolor=p["accent"])
        st.map(".", foreground=[("disabled", p["disabled"])])
        st.configure("TFrame", background=p["bg"])
        st.configure("TLabel", background=p["bg"], foreground=p["fg"])
        st.configure("Hint.TLabel", foreground=p["muted"])
        st.configure("Value.TLabel", foreground=p["fg"])
        st.configure("TLabelframe", background=p["bg"], bordercolor=p["line"])
        st.configure("TLabelframe.Label", background=p["bg"], foreground=p["fg"])
        for b in ("TButton", "Big.TButton"):
            st.configure(b, background=p["button"], foreground=p["fg"], bordercolor=p["line"],
                         lightcolor=p["button"], darkcolor=p["button"])
            st.map(b, background=[("disabled", p["bg"]), ("pressed", p["button_active"]),
                                  ("active", p["button_active"])],
                   lightcolor=[("pressed", p["button_active"]), ("active", p["button_active"])],
                   darkcolor=[("pressed", p["button_active"]), ("active", p["button_active"])],
                   foreground=[("disabled", p["disabled"])])
        for c in ("TCheckbutton", "TRadiobutton"):
            st.configure(c, background=p["bg"], foreground=p["fg"],
                         indicatorbackground=p["field"], indicatorforeground=p["fg"])
            st.map(c, background=[("active", p["bg"])],
                   indicatorbackground=[("disabled", p["bg"]), ("pressed", p["button_active"])],
                   foreground=[("disabled", p["disabled"])])
        st.configure("TEntry", fieldbackground=p["field"], foreground=p["fg"])
        st.map("TEntry", fieldbackground=[("disabled", p["bg"]), ("readonly", p["bg"])],
               foreground=[("disabled", p["disabled"])])
        st.configure("TCombobox", fieldbackground=p["field"], foreground=p["fg"],
                     background=p["button"], arrowcolor=p["fg"])
        st.map("TCombobox", fieldbackground=[("disabled", p["bg"]), ("readonly", p["field"])],
               foreground=[("disabled", p["disabled"])],
               selectbackground=[("readonly", p["field"])], selectforeground=[("readonly", p["fg"])],
               background=[("active", p["button_active"])], arrowcolor=[("disabled", p["disabled"])])
        st.configure("TNotebook", background=p["bg"], bordercolor=p["line"])
        st.configure("TNotebook.Tab", background=p["button"], foreground=p["fg"],
                     bordercolor=p["line"], lightcolor=p["button"])
        st.map("TNotebook.Tab", background=[("selected", p["bg"])], lightcolor=[("selected", p["bg"])])
        st.configure("Treeview", background=p["field"], fieldbackground=p["field"], foreground=p["fg"],
                     bordercolor=p["line"])
        st.map("Treeview", background=[("selected", p["select"])], foreground=[("selected", p["select_fg"])])
        st.configure("Treeview.Heading", background=p["button"], foreground=p["fg"],
                     bordercolor=p["line"], lightcolor=p["button"], darkcolor=p["button"])
        st.map("Treeview.Heading", background=[("active", p["button_active"])])
        st.configure("TScrollbar", background=p["button"], troughcolor=p["bg"], bordercolor=p["line"],
                     arrowcolor=p["fg"], lightcolor=p["button"], darkcolor=p["button"])
        st.map("TScrollbar", background=[("active", p["button_active"]), ("disabled", p["bg"])])

        # widget Tk classici (non gestiti dagli stili ttk)
        self.configure(bg=p["bg"])
        for c in (self.dot, self.meter):
            c.configure(bg=p["bg"])
        for tw in (self.t_packets, self.t_log, self.t_thread):
            tw.configure(bg=p["text_bg"], fg=p["fg"], insertbackground=p["fg"],
                        selectbackground=p["select"], selectforeground=p["select_fg"])
        self.t_log.tag_configure("err", foreground=p["err"])
        self.t_log.tag_configure("warn", foreground=p["warn"])
        for m in (self.menubar, self.menu_view, self.menu_help, self.menu_hist,
                  self.menu_hist_load, self.menu_hist_days, self.menu_map, self.menu_cache):
            m.configure(bg=p["bg"], fg=p["fg"], activebackground=p["select"],
                        activeforeground=p["select_fg"], selectcolor=p["fg"])
        for cb in (self.cb_device, self.cb_rate, self.cb_channel, self.cb_profile, self.cb_stype):
            try:   # elenco a tendina delle combobox
                pop = self.tk.call("ttk::combobox::PopdownWindow", cb)
                self.tk.call(f"{pop}.f.l", "configure", "-background", p["field"], "-foreground", p["fg"],
                             "-selectbackground", p["select"], "-selectforeground", p["select_fg"])
            except tk.TclError:
                pass
        self._dark_titlebar(name == "dark")
        self._last_status = None
        self._update_status()
        self._draw_meter()

    def _dark_titlebar(self, dark: bool) -> None:
        """Barra del titolo scura su Windows 10/11 (ignorato altrove)."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            val = ctypes.c_int(1 if dark else 0)
            for attr in (20, 19):          # DWMWA_USE_IMMERSIVE_DARK_MODE (nuovo, vecchio)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(val),
                                                              ctypes.sizeof(val)) == 0:
                    break
        except Exception:
            pass

    def _text_tab(self, nb: ttk.Notebook, title: str) -> tk.Text:
        f = ttk.Frame(nb, padding=6)
        nb.add(f, text=title)
        txtw = tk.Text(f, wrap="none", height=4, font=("Courier", 10), state="disabled",
                    relief="flat", borderwidth=0)
        sy = ttk.Scrollbar(f, orient="vertical", command=txtw.yview)
        sx = ttk.Scrollbar(f, orient="horizontal", command=txtw.xview)
        txtw.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
        txtw.grid(row=0, column=0, sticky="nsew")
        sy.grid(row=0, column=1, sticky="ns")
        sx.grid(row=1, column=0, sticky="ew")
        f.rowconfigure(0, weight=1)
        f.columnconfigure(0, weight=1)
        return txtw

    # ------------------------------------------------------------ configurazione

    def _refresh_devices(self) -> None:
        self.devices = [("", DEFAULT_DEVICE)]
        try:
            self.devices += [(str(i), f"{i}: {name}") for i, name in list_input_devices()]
        except Exception as e:          # sounddevice/PortAudio mancanti
            logging.warning("Schede audio non disponibili (%s). Installa sounddevice "
                            "oppure usa Direwolf o un file WAV.", e)
        current = self._device_value() if self.cb_device.get() else self.cfg.device
        self.cb_device["values"] = [lbl for _, lbl in self.devices]
        self._select_device(current)

    def _device_value(self) -> str:
        lbl = self.cb_device.get()
        return next((v for v, l in self.devices if l == lbl), "")

    def _select_device(self, value: str) -> None:
        lbl = next((l for v, l in self.devices if v == value), None)
        if lbl is None and value:      # nome libero salvato in precedenza
            self.devices.append((value, value))
            self.cb_device["values"] = [l for _, l in self.devices]
            lbl = value
        self.cb_device.set(lbl or DEFAULT_DEVICE)

    def _load_cfg(self) -> None:
        c = self.cfg
        self.v_source.set(c.source)
        self._select_device(c.device)
        self.cb_rate.set(str(c.rate))
        self.cb_channel.set(CHANNELS[min(c.channel, 1)])
        self.v_kiss_host.set(c.kiss_host)
        self.v_kiss_port.set(str(c.kiss_port))
        self.v_wav.set(c.wav)
        self.v_realtime.set(c.realtime)
        self.cb_profile.set(dict(PROFILE_CHOICES).get(c.demod_profile, PROFILE_CHOICES[0][1]))
        self.v_fix.set(c.fix_bits)
        self.v_home.set(c.home)
        self.v_filters.set(", ".join(c.filters))
        self.v_logon.set(bool(c.log_file))
        self.v_logfile.set(c.log_file or str(Path.home() / "aprs_pacchetti.log"))
        self.v_port.set(str(c.web_port))
        self.v_lan.set(c.bind == "0.0.0.0")
        self.v_is_on.set(c.aprsis_enabled)
        self.cb_stype.set(t(STATION_TYPES.get(c.station_type, STATION_TYPES["igate_vhf"])[0]))
        self.v_comment.set(c.station_comment)
        self.v_equip.set(c.station_equipment)
        self.is_cfg = {k: getattr(c, k) for k in ("aprsis_call", "aprsis_passcode", "aprsis_server",
                                                   "aprsis_port", "aprsis_radius_km", "igate_enabled",
                                                   "aprsis_beacon")}
        self.v_lora_on.set(c.lora_enabled)
        self.lora_cfg = {k: getattr(c, k) for k in ("lora_mode", "lora_serial", "lora_baud", "lora_host", "lora_port")}
        self.v_gp_on.set(c.geopin_enabled)
        self.gp_cfg = {k: getattr(c, k) for k in ("geopin_url", "geopin_key", "geopin_interval_min")}
        self._apply_states()

    def _read_cfg(self) -> Config:
        """Legge e valida i campi; solleva ValueError con un messaggio chiaro."""
        def port(text, what):
            try:
                p = int(text)
            except ValueError:
                raise ValueError(t("{what}: inserisci un numero di porta.", what=what)) from None
            if not 1 <= p <= 65535:
                raise ValueError(t("{what}: la porta deve essere tra 1 e 65535.", what=what))
            return p

        c = Config(
            source=self.v_source.get(),
            device=self._device_value(),
            rate=int(self.cb_rate.get() or 48000),
            channel=CHANNELS.index(self.cb_channel.get()) if self.cb_channel.get() in CHANNELS else 0,
            kiss_host=self.v_kiss_host.get().strip() or "127.0.0.1",
            kiss_port=port(self.v_kiss_port.get(), t("Porta KISS")),
            wav=self.v_wav.get().strip(),
            realtime=self.v_realtime.get(),
            web_port=port(self.v_port.get(), t("Porta della mappa")),
            bind="0.0.0.0" if self.v_lan.get() else "127.0.0.1",
            home=self.v_home.get().strip(),
            filters=[f.strip().upper() for f in self.v_filters.get().split(",") if f.strip()],
            log_file=self.v_logfile.get().strip() if self.v_logon.get() else "",
            theme=self.v_theme.get(),
            history_enabled=self.v_hist_on.get(),
            history_days=self.v_hist_days.get(),
            history_load_hours=self.v_hist_load.get(),
            station_type=self._station_type_key(), station_comment=self.v_comment.get().strip(),
            station_equipment=self.v_equip.get().strip(),
            aprsis_enabled=self.v_is_on.get(),
            aprsis_call=self.is_cfg["aprsis_call"], aprsis_passcode=self.is_cfg["aprsis_passcode"],
            aprsis_server=self.is_cfg["aprsis_server"], aprsis_port=self.is_cfg["aprsis_port"],
            aprsis_radius_km=self.is_cfg["aprsis_radius_km"], igate_enabled=self.is_cfg["igate_enabled"],
            aprsis_beacon=self.is_cfg["aprsis_beacon"],
            lora_enabled=self.v_lora_on.get(), **self.lora_cfg,
            geopin_enabled=self.v_gp_on.get(), **self.gp_cfg,
            map_source=self.map_cfg["map_source"], map_url=self.map_cfg["map_url"],
            map_attribution=self.map_cfg["map_attribution"], mbtiles_path=self.map_cfg["mbtiles_path"],
            map_cache_mb=self.v_cache_mb.get(),
            demod_profile=next((k for k, t in PROFILE_CHOICES if t == self.cb_profile.get()), "completo"),
            fix_bits=self.v_fix.get(),
        )
        # impostazioni che non hanno un campo nel pannello: restano quelle salvate
        for k in ("messages_calls", "alert_watch", "alert_zones", "alert_emergency", "alert_messages",
                  "alert_quiet_min", "alert_sound", "language", "panel_password"):
            setattr(c, k, getattr(self.cfg, k))
        c.messages_enabled = self.v_msg_on.get() if hasattr(self, "v_msg_on") else self.cfg.messages_enabled
        try:
            c.home_tuple()
        except ValueError:
            raise ValueError(t("Posizione ricevente: usa il formato lat, lon "
                               "in gradi decimali, ad esempio 44.494, 11.343.")) from None
        err = comment_error(c.station_comment)
        if err:
            raise ValueError(t("Commento della stazione: ") + tx(err))
        err = equipment_error(c.station_equipment)
        if err:
            raise ValueError(t("Apparato: ") + tx(err))
        if c.aprsis_enabled and not valid_callsign(c.aprsis_call):
            raise ValueError(t("APRS-IS: indica il nominativo da usare in Impostazioni… (es. IZ2LPN-10)."))
        if c.aprsis_enabled and c.igate_enabled and not c.aprsis_passcode.strip().isdigit():
            raise ValueError(t("APRS-IS: l'iGate richiede il passcode del nominativo (Impostazioni…)."))
        if c.lora_enabled and c.lora_mode == "serial" and not c.lora_serial:
            raise ValueError(t("LoRa: scegli la porta seriale del modulo in Impostazioni…."))
        if c.lora_enabled and c.lora_mode == "tcp" and not c.lora_host:
            raise ValueError(t("LoRa: indica l'indirizzo del modulo in rete in Impostazioni…."))
        if c.source == "none" and not (c.lora_enabled or c.aprsis_enabled):
            raise ValueError(t("Senza sorgente VHF attiva LoRa o APRS-IS, altrimenti non c'è nulla da ricevere."))
        if c.geopin_enabled and not (c.geopin_url.strip() and c.geopin_key.strip()):
            raise ValueError(t("Geopin: indica indirizzo del portale e chiave del ricevitore in Impostazioni…."))
        if c.source == "wav" and not Path(c.wav).is_file():
            raise ValueError(t("Scegli un file WAV esistente con il pulsante Sfoglia."))
        if self.v_logon.get() and not c.log_file:
            raise ValueError(t("Indica il file in cui salvare i pacchetti."))
        return c

    def _apply_states(self) -> None:
        running = self.rx.running
        for w in self.inputs:
            w.state(["disabled"] if running else ["!disabled"])
        if not running:
            for src, ws in self.groups.items():
                for w in ws:
                    w.state(["!disabled"] if src == self.v_source.get() else ["disabled"])
            on = self.v_logon.get()
            for w in (self.e_logfile, self.b_logfile):
                w.state(["!disabled"] if on else ["disabled"])
        for w in (self.cb_device, self.cb_rate, self.cb_channel, self.cb_profile):
            if "disabled" not in w.state():
                w.state(["readonly"])

    def _pick_wav(self) -> None:
        p = filedialog.askopenfilename(title=t("Scegli un file WAV"),
                                       filetypes=[("File WAV", "*.wav"), ("Tutti i file", "*.*")])
        if p:
            self.v_wav.set(p)

    def _pick_log(self) -> None:
        p = filedialog.asksaveasfilename(title=t("File in cui salvare i pacchetti"),
                                         defaultextension=".log", initialfile="aprs_pacchetti.log",
                                         filetypes=[("Log", "*.log *.txt"), ("Tutti i file", "*.*")])
        if p:
            self.v_logfile.set(p)

    # ------------------------------------------------------------ azioni

    def _start_web(self, cfg: Config) -> bool:
        try:
            self.web.ensure(cfg.bind, cfg.web_port)
            logging.info("Mappa disponibile su %s", self.web.url)
            return True
        except OSError as e:
            logging.error("La porta %d per la mappa non è utilizzabile (%s). "
                          "Scegline un'altra.", cfg.web_port, e)
            return False

    def _toggle(self) -> None:
        if self.rx.running:
            self.b_start.state(["disabled"])
            self.rx.stop()
            self.b_start.state(["!disabled"])
            logging.info("Ricezione fermata")
            return
        try:
            cfg = self._read_cfg()
        except ValueError as e:
            messagebox.showerror(t("Controlla la configurazione"), str(e), parent=self)
            return
        if not self._start_web(cfg):
            messagebox.showerror(t("Mappa non avviabile"),
                                 t("La porta {port} è già in uso. Scegline un'altra.", port=cfg.web_port), parent=self)
            return
        self.store.set_home(cfg.home_tuple())
        self.cfg = cfg
        try:
            cfg.save()
        except OSError as e:
            logging.warning("Configurazione non salvata: %s", e)
        self._geopin_apply(cfg)
        self.engine.configure(cfg)
        try:
            self.rx.start(cfg)
        except (OSError, RuntimeError) as e:
            messagebox.showerror(t("Impossibile avviare"), tx(str(e)), parent=self)
            return
        self._refresh_station_rows()

    def _clear(self) -> None:
        self.store.clear()

    # ------------------------------------------------------------ stazione

    def _station_type_key(self) -> str:
        label = self.cb_stype.get()
        return next((k for k, v in STATION_TYPES.items() if t(v[0]) == label), "igate_vhf")

    def _station_changed(self) -> None:
        """Aggiorna subito il commento d'esempio, il segnaposto sulla mappa e
        l'annuncio dell'iGate (al prossimo invio), senza fermare la ricezione."""
        text = self.v_comment.get()
        equip = self.v_equip.get() if hasattr(self, "v_equip") else ""
        err = comment_error(text.strip())
        n = len(text.strip())
        self.l_comment.configure(
            foreground=self.pal["err" if err else "muted"],
            text=tx(err) if err else (t("{n}/43 caratteri; su aprs.fi: Commento", n=n) if n else
                                      t("vuoto = \"{c}\"; su aprs.fi: Commento", c=default_comment())))
        eerr = equipment_error(equip.strip())
        m = len(equip.strip())
        self.l_equip.configure(
            foreground=self.pal["err" if eerr else "muted"],
            text=tx(eerr) if eerr else (t("{n}/62 caratteri; su aprs.fi: Ultimo rapporto", n=m) if m else
                                        t("es. Yaesu FT-2980 + Direwolf; su aprs.fi: Ultimo rapporto")))
        if err or eerr:
            return
        stype = self._station_type_key()
        call = self.is_cfg["aprsis_call"] if hasattr(self, "is_cfg") else ""
        self.store.set_station(stype, text, call, equip)
        c = self.rx.aprsis
        if c is not None:
            c.station_type, c.comment, c.equipment = stype, text.strip(), equip.strip()

    # ------------------------------------------------------------ mappe

    def _make_tiles(self, c) -> TileService:
        ts = TileService(c.map_source, c.map_url, c.map_attribution, c.mbtiles_path, c.map_cache_mb,
                        contact=c.aprsis_call.split("-")[0] if c.aprsis_call else "")
        if ts.error:
            logging.error("Mappe: %s. Uso OpenStreetMap.", ts.error)
        return ts

    def _apply_map_cfg(self) -> None:
        self.cfg.map_source = self.map_cfg["map_source"]
        for k, v in self.map_cfg.items():
            setattr(self.cfg, k, v)
        self.cfg.map_cache_mb = self.v_cache_mb.get()
        self.tiles = self._make_tiles(self.cfg)
        if self.tiles.error:
            self.map_cfg["map_source"] = self.cfg.map_source = "osm"
            messagebox.showerror(t("Mappe"), tx(self.tiles.error), parent=self)
        self.v_map_src.set(self.tiles.mode)
        self.store.set_tiles(self.tiles)
        try:
            self.cfg.save()
        except OSError:
            pass
        logging.info("Sorgente delle mappe: %s", {"osm": "OpenStreetMap", "custom": "server con cache",
                                                  "mbtiles": "file MBTiles"}[self.tiles.mode])

    def _set_map_source(self, mode: str) -> None:
        self.v_map_src.set(self.map_cfg["map_source"])     # torna indietro se si annulla
        if mode == "osm":
            self.map_cfg["map_source"] = "osm"
            self._apply_map_cfg()
        elif mode == "mbtiles":
            p = filedialog.askopenfilename(parent=self, title=t("Scegli una mappa offline MBTiles"),
                                           filetypes=[("Mappe MBTiles", "*.mbtiles"), ("Tutti i file", "*.*")])
            if p:
                self.map_cfg.update(map_source="mbtiles", mbtiles_path=p)
                self._apply_map_cfg()
        else:
            self._custom_map_dialog()

    def _custom_map_dialog(self) -> None:
        dlg, frm = self._dialog(t("Server di mappe con cache su disco"))
        ttk.Label(frm, wraplength=460, justify="left", text=(
            t("Indirizzo di un server di tile raster, con {z}, {x} e {y}: ad esempio un tuo server o un "
            "fornitore che consente la conservazione delle mappe. Le tile viste vengono salvate su disco "
            "per almeno 7 giorni e restano disponibili anche senza rete.\n\n"
            "Non usare tile.openstreetmap.org: la sua policy non consente l'uso offline. Per "
            "OpenStreetMap scegli \"OpenStreetMap (online)\"."))).grid(row=0, column=0, columnspan=2, sticky="w")
        v_url = tk.StringVar(value=self.map_cfg["map_url"])
        v_att = tk.StringVar(value=self.map_cfg["map_attribution"])
        for r, (lbl, var) in enumerate((("Indirizzo", v_url), ("Attribuzione", v_att)), start=1):
            ttk.Label(frm, text=lbl).grid(row=r, column=0, sticky="w", pady=(10, 0), padx=(0, 8))
            ttk.Entry(frm, textvariable=var, width=56).grid(row=r, column=1, sticky="ew", pady=(10, 0))
        ttk.Label(frm, style="Hint.TLabel", text=t("Testo dei diritti richiesto dal fornitore, mostrato sulla mappa")
                  ).grid(row=3, column=1, sticky="w")

        def ok():
            url = v_url.get().strip()
            if "tile.openstreetmap.org" in url:
                messagebox.showerror(t("Mappe"), t("La policy di tile.openstreetmap.org non consente la cache per "
                                     "l'uso offline. Scegli \"OpenStreetMap (online)\"."), parent=dlg)
                return
            self.map_cfg.update(map_source="custom", map_url=url, map_attribution=v_att.get().strip())
            dlg.destroy()
            self._apply_map_cfg()
        self._dialog_buttons(dlg, frm, 4, ok)

    def _cache_size_changed(self) -> None:
        self.cfg.map_cache_mb = self.tiles.cache_mb = self.v_cache_mb.get()
        threading.Thread(target=self.tiles.prune, daemon=True).start()
        try:
            self.cfg.save()
        except OSError:
            pass

    def _clear_map_cache(self) -> None:
        mb = self.tiles.cache_size() / 1e6
        if messagebox.askyesno(t("Cache delle mappe"), t("Cancellare le mappe salvate ({mb} MB)?", mb=f"{mb:.1f}")
                               + "\n" + t("Cartella: {dir}", dir=CACHE_DIR), parent=self):
            self.tiles.clear()
            logging.info("Cache delle mappe svuotata")

    # ------------------------------------------------------------ APRS-IS

    def _dialog(self, title: str):
        dlg = tk.Toplevel(self)
        dlg.title(title)
        dlg.transient(self)
        dlg.configure(bg=self.pal["bg"])
        # su schermi piccoli anche le finestre di impostazione si scorrono
        sf = ScrollFrame(dlg)
        sf.set_background(self.pal["bg"])
        sf.pack(fill="both", expand=True)
        dlg._scroll = sf
        frm = ttk.Frame(sf.body, padding=16)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        return dlg, frm

    def _dialog_buttons(self, dlg, frm, row: int, ok) -> None:
        btns = ttk.Frame(frm)
        btns.grid(row=row, column=0, columnspan=3, sticky="e", pady=(16, 0))
        ttk.Button(btns, text=t("Annulla"), command=dlg.destroy).pack(side="right")
        ttk.Button(btns, text=t("OK"), command=ok).pack(side="right", padx=(0, 8))
        self._place_dialog(dlg)
        dlg.grab_set()

    def _place_dialog(self, dlg) -> None:
        """Misura del contenuto, ridotta allo schermo e centrata sul pannello (sempre visibile)."""
        sf = getattr(dlg, "_scroll", None)
        if sf is not None:
            cw, ch = sf.content_size()
            w, h = fit_to_screen(dlg, cw, ch, center_on=self)
            if h < ch:                             # spazio per la barra verticale
                w, h = fit_to_screen(dlg, cw + sf.vsb.winfo_reqwidth() + 2, ch, center_on=self)
            dlg.minsize(min(w, 320), min(h, 240))
        else:
            dlg.update_idletasks()
            fit_to_screen(dlg, dlg.winfo_reqwidth(), dlg.winfo_reqheight(), center_on=self)

    def _lora_dialog(self) -> None:
        from .lora import BAUD_RATES, list_serial_ports
        c = self.lora_cfg
        dlg, frm = self._dialog(t("Impostazioni LoRa APRS"))
        v_mode = tk.StringVar(value=c["lora_mode"])
        ports = list_serial_ports()
        labels = [d for _, d in ports]
        v_port = tk.StringVar(value=next((d for p, d in ports if p == c["lora_serial"]), c["lora_serial"]))
        v_baud = tk.StringVar(value=str(c["lora_baud"]))
        v_host, v_tport = tk.StringVar(value=c["lora_host"]), tk.StringVar(value=str(c["lora_port"]))

        ttk.Radiobutton(frm, text=t("Porta seriale USB"), value="serial", variable=v_mode).grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(frm, text=t("Porta")).grid(row=1, column=0, sticky="w", padx=(22, 8), pady=(6, 0))
        cb_port = ttk.Combobox(frm, textvariable=v_port, values=labels, width=40)
        cb_port.grid(row=1, column=1, sticky="w", pady=(6, 0))

        def refresh():
            nonlocal ports
            ports = list_serial_ports()
            cb_port.configure(values=[d for _, d in ports])
        ttk.Button(frm, text=t("Aggiorna"), command=refresh).grid(row=1, column=2, padx=(6, 0), pady=(6, 0))
        ttk.Label(frm, text=t("Velocità")).grid(row=2, column=0, sticky="w", padx=(22, 8), pady=(6, 0))
        ttk.Combobox(frm, textvariable=v_baud, values=[str(b) for b in BAUD_RATES], width=10).grid(
            row=2, column=1, sticky="w", pady=(6, 0))
        ttk.Label(frm, style="Hint.TLabel", text=t("baud: 115200 per la maggior parte dei firmware ESP32")).grid(
            row=3, column=1, columnspan=2, sticky="w")
        if not ports:
            try:
                import serial  # noqa: F401
                msg = t("Nessuna porta seriale trovata: collega il modulo e premi Aggiorna.")
            except ImportError:
                msg = t("Per la porta seriale serve pyserial:  pip install pyserial")
            ttk.Label(frm, text=msg, style="Hint.TLabel").grid(row=4, column=1, columnspan=2, sticky="w")

        ttk.Radiobutton(frm, text=t("Rete (TCP)"), value="tcp", variable=v_mode).grid(
            row=5, column=0, columnspan=3, sticky="w", pady=(14, 0))
        ttk.Label(frm, text=t("Indirizzo")).grid(row=6, column=0, sticky="w", padx=(22, 8), pady=(6, 0))
        ttk.Entry(frm, textvariable=v_host, width=30).grid(row=6, column=1, sticky="w", pady=(6, 0))
        ttk.Label(frm, text=t("Porta")).grid(row=7, column=0, sticky="w", padx=(22, 8), pady=(6, 0))
        ttk.Entry(frm, textvariable=v_tport, width=10).grid(row=7, column=1, sticky="w", pady=(6, 0))
        ttk.Label(frm, style="Hint.TLabel", wraplength=480, justify="left", text=(
            t("Il programma riconosce da solo i pacchetti scritti dal firmware: testo APRS (anche dentro i "
            "messaggi di diagnostica) oppure protocollo KISS. Le stazioni ricevute via LoRa sono distinte "
            "da quelle VHF nel pannello, sulla mappa e nella copertura. Se l'iGate è attivo, inoltra ad "
            "APRS-IS anche i pacchetti LoRa."))).grid(row=8, column=0, columnspan=3, sticky="w", pady=(14, 0))

        def ok():
            mode = v_mode.get()
            port = v_port.get().strip()
            port = next((p for p, d in ports if d == port), port.split(" - ")[0])
            try:
                baud, tport = int(v_baud.get()), int(v_tport.get())
            except ValueError:
                messagebox.showerror(t("LoRa"), t("Velocità e porta TCP devono essere numeri."), parent=dlg)
                return
            if mode == "serial" and not port:
                messagebox.showerror(t("LoRa"), t("Scegli la porta seriale del modulo."), parent=dlg)
                return
            if mode == "tcp" and not v_host.get().strip():
                messagebox.showerror(t("LoRa"), t("Indica l'indirizzo del modulo in rete."), parent=dlg)
                return
            self.lora_cfg.update(lora_mode=mode, lora_serial=port, lora_baud=baud,
                                 lora_host=v_host.get().strip(), lora_port=tport)
            self.v_lora_on.set(True)
            dlg.destroy()
            try:
                self._read_cfg().save()
            except (ValueError, OSError):
                pass
        self._dialog_buttons(dlg, frm, 9, ok)

    def _aprsis_dialog(self) -> None:
        c = self.is_cfg
        dlg, frm = self._dialog(t("Impostazioni APRS-IS"))
        v = {"call": tk.StringVar(value=c["aprsis_call"]), "pass": tk.StringVar(value=c["aprsis_passcode"]),
             "server": tk.StringVar(value=c["aprsis_server"]), "port": tk.StringVar(value=str(c["aprsis_port"])),
             "radius": tk.StringVar(value=str(c["aprsis_radius_km"])), "igate": tk.BooleanVar(value=c["igate_enabled"]),
             "beacon": tk.BooleanVar(value=c["aprsis_beacon"])}
        rows = [(t("Nominativo-SSID"), "call", t("es. IZ2LPN-10 (-10 è l'SSID consueto per gli iGate)")),
                ("Passcode", "pass", t("serve per annunciare la stazione, per l'iGate e per inviare messaggi; senza, sola ricezione")),
                (t("Server"), "server", t("rotate.aprs2.net sceglie un server vicino")),
                (t("Porta"), "port", ""),
                (t("Raggio confronto (km)"), "radius", t("stazioni ricevute dalla rete intorno alla posizione ricevente; 0 = nessuna"))]
        r = 0
        for lbl, key, hint in rows:
            ttk.Label(frm, text=lbl).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
            cell = ttk.Frame(frm)
            cell.grid(row=r, column=1, sticky="w", pady=(8, 0))
            e = ttk.Entry(cell, textvariable=v[key], width=28, show="•" if key == "pass" else "")
            e.pack(side="left")
            if key == "pass":
                def calc():
                    call = v["call"].get().strip()
                    if not valid_callsign(call):
                        messagebox.showinfo(t("Passcode"), t("Scrivi prima un nominativo valido."), parent=dlg)
                        return
                    v["pass"].set(str(aprsis_passcode(call)))
                ttk.Button(cell, text=t("Calcola"), command=calc).pack(side="left", padx=(6, 0))
            r += 1
            if hint:
                ttk.Label(frm, text=hint, style="Hint.TLabel", wraplength=330, justify="left").grid(
                    row=r, column=1, sticky="w")
                r += 1
        ttk.Checkbutton(frm, text=t("Annuncia la stazione: posizione, tipo e apparato ogni 30 minuti (visibile su aprs.fi)"),
                        variable=v["beacon"]).grid(row=r, column=0, columnspan=3, sticky="w", pady=(14, 0))
        ttk.Checkbutton(frm, text=t("iGate: inoltra ad APRS-IS i pacchetti ricevuti via radio"),
                        variable=v["igate"]).grid(row=r + 1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(frm, style="Hint.TLabel", wraplength=480, justify="left", text=(
            t("L'annuncio usa il nominativo-SSID, la posizione ricevente e il tipo di dispositivo; serve il "
            "passcode. L'iGate in sola ricezione inoltra i pacchetti sentiti via radio seguendo le regole "
            "APRS-IS (esclusi NOGATE, RFONLY, TCPIP, TCPXX e le interrogazioni). Non trasmette mai via radio. "
            "Funziona solo con scheda audio, TNC o LoRa, non con i file registrati. APRS-IS è riservato ai "
            "radioamatori: usa il tuo nominativo e il suo passcode."))).grid(row=r + 2, column=0, columnspan=3,
                                                                         sticky="w", pady=(4, 0))
        r += 1

        def ok():
            call = v["call"].get().strip().upper()
            try:
                port, radius = int(v["port"].get()), int(v["radius"].get())
            except ValueError:
                messagebox.showerror(t("APRS-IS"), t("Porta e raggio devono essere numeri."), parent=dlg)
                return
            if call and not valid_callsign(call):
                messagebox.showerror(t("APRS-IS"), t("Nominativo non valido (es. IZ2LPN-10)."), parent=dlg)
                return
            if v["igate"].get() and not v["pass"].get().strip().isdigit():
                messagebox.showerror(t("APRS-IS"), t("Per l'iGate serve il passcode (usa Calcola)."), parent=dlg)
                return
            if v["beacon"].get() and call and not v["pass"].get().strip().isdigit():
                messagebox.showerror(t("APRS-IS"), t("Per annunciare la stazione serve il passcode (usa Calcola), "
                                     "oppure togli la spunta da \"Annuncia la stazione\"."), parent=dlg)
                return
            self.is_cfg.update(aprsis_call=call, aprsis_passcode=v["pass"].get().strip(),
                               aprsis_server=v["server"].get().strip() or "rotate.aprs2.net",
                               aprsis_port=port, aprsis_radius_km=max(0, radius), igate_enabled=v["igate"].get(),
                               aprsis_beacon=v["beacon"].get())
            if call:
                self.v_is_on.set(True)
            dlg.destroy()
            self._station_changed()            # il nominativo compare sul segnaposto
            try:
                self._read_cfg().save()
            except (ValueError, OSError):
                pass
        self._dialog_buttons(dlg, frm, r + 2, ok)

    # ------------------------------------------------------------ Geopin

    def _geopin_apply(self, cfg: Config) -> None:
        """Avvia, riconfigura o ferma il pubblicatore secondo la configurazione."""
        from .geopin import GeopinPublisher, publish_url, receiver_status
        want = (cfg.geopin_enabled and cfg.geopin_url.strip() and cfg.geopin_key.strip())
        p = self.publisher
        if p is not None and want and (p.url, p.key, p.interval_s) == (
                publish_url(cfg.geopin_url), cfg.geopin_key.strip(), max(1, cfg.geopin_interval_min) * 60):
            self.rx.publisher = p
            return
        if p is not None:
            p.stop(final=False)
            self.publisher = self.rx.publisher = None
        if want:
            try:
                self.publisher = GeopinPublisher(cfg.geopin_url, cfg.geopin_key, cfg.geopin_interval_min,
                                                 status_fn=lambda: receiver_status(self.rx, self.cfg))
            except Exception as e:             # coda su disco non apribile
                logging.error("Geopin non disponibile: %s", e)
                return
            self.publisher.start()
            self.rx.publisher = self.publisher
            logging.info("Geopin: pubblicazione ogni %d minuti su %s", cfg.geopin_interval_min, self.publisher.url)

    def _geopin_toggled(self) -> None:
        if self.v_gp_on.get() and not (self.gp_cfg["geopin_url"] and self.gp_cfg["geopin_key"]):
            self.v_gp_on.set(False)
            self._geopin_dialog()
            return
        try:
            cfg = self._read_cfg()
        except ValueError as e:
            # senza avviso la casella resterebbe spuntata con la pubblicazione ferma
            self.v_gp_on.set(False)
            messagebox.showerror(t("Geopin non attivato"), t("Prima correggi la configurazione:") + f"\n{e}", parent=self)
            return
        self.cfg.geopin_enabled = cfg.geopin_enabled
        try:
            cfg.save()
        except OSError:
            pass
        self._geopin_apply(cfg)

    def _geopin_now(self) -> None:
        if self.publisher is not None:
            self.publisher.publish_now()

    def _geopin_dialog(self) -> None:
        from .geopin import INTERVALS, GeopinError, GeopinPublisher
        c = self.gp_cfg
        dlg, frm = self._dialog(t("Impostazioni Geopin"))
        v_url, v_key = tk.StringVar(value=c["geopin_url"]), tk.StringVar(value=c["geopin_key"])
        choices = [t("{m} minuto", m=m) if m == 1 else t("{m} minuti", m=m) for m in INTERVALS]
        cur = c["geopin_interval_min"] if c["geopin_interval_min"] in INTERVALS else 5
        v_int = tk.StringVar(value=choices[INTERVALS.index(cur)])
        # il codice di collegamento di Geopin contiene indirizzo e chiave: sopra i campi
        top = ttk.Frame(frm.master, padding=(16, 16, 16, 0))
        top.pack(before=frm, fill="x")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text=t("Codice di collegamento")).grid(row=0, column=0, sticky="w", padx=(0, 8))
        v_code = tk.StringVar()
        e_code = ttk.Entry(top, textvariable=v_code, width=36)
        e_code.grid(row=0, column=1, sticky="ew")

        def use_code(*_):
            from .geopin import decode_link
            d = decode_link(v_code.get())
            if d is None:
                if v_code.get().strip():
                    messagebox.showerror(t("Geopin"), t("Codice di collegamento Geopin non valido."), parent=dlg)
                return
            v_url.set(d[0])
            v_key.set(d[1])
            v_code.set("")
        ttk.Button(top, text=t("Usa"), command=use_code).grid(row=0, column=2, padx=(6, 0))
        e_code.bind("<Return>", use_code)
        e_code.bind("<<Paste>>", lambda e: dlg.after(50, use_code))
        ttk.Label(top, text=t("Il codice di collegamento si copia dalla pagina Il mio account di Geopin, quando crei la chiave del ricevitore."),
                  style="Hint.TLabel", wraplength=440, justify="left").grid(row=1, column=1, columnspan=2, sticky="w")
        ttk.Separator(top).grid(row=2, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        ttk.Label(frm, text=t("Indirizzo del portale")).grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Entry(frm, textvariable=v_url, width=44).grid(row=0, column=1, sticky="w")
        ttk.Label(frm, text=t("es. https://geopin.esempio.it/"), style="Hint.TLabel").grid(row=1, column=1, sticky="w")
        ttk.Label(frm, text=t("Chiave del ricevitore")).grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        cell = ttk.Frame(frm)
        cell.grid(row=2, column=1, sticky="w", pady=(8, 0))
        e_key = ttk.Entry(cell, textvariable=v_key, width=36, show="•")
        e_key.pack(side="left")
        v_show = tk.BooleanVar(value=False)
        ttk.Checkbutton(cell, text=t("Mostra"), variable=v_show,
                        command=lambda: e_key.configure(show="" if v_show.get() else "•")).pack(side="left", padx=(6, 0))
        ttk.Label(frm, text=t("si crea sul portale, nella pagina Il mio account → Ricevitori"),
                  style="Hint.TLabel").grid(row=3, column=1, sticky="w")
        ttk.Label(frm, text=t("Pubblica ogni")).grid(row=4, column=0, sticky="w", padx=(0, 8), pady=(8, 0))
        cb = ttk.Combobox(frm, textvariable=v_int, values=choices, width=12, state="readonly")
        cb.grid(row=4, column=1, sticky="w", pady=(8, 0))

        l_test = ttk.Label(frm, text="", style="Hint.TLabel", wraplength=480, justify="left")

        def test():
            l_test.configure(text=t("Prova in corso…"))
            url, key = v_url.get().strip(), v_key.get().strip()
            result: list[str] = []

            def work():
                p = None
                try:
                    p = GeopinPublisher(url, key)
                    result.append(tx(p.test()))
                except (GeopinError, OSError) as e:
                    result.append(t("Collegamento non riuscito: {e}", e=tx(str(e))))
                finally:
                    if p is not None:
                        p.stop(final=False)

            def wait():
                if result:
                    if l_test.winfo_exists():
                        l_test.configure(text=result[0])
                else:
                    dlg.after(200, wait)
            threading.Thread(target=work, daemon=True).start()
            wait()

        row = ttk.Frame(frm)
        row.grid(row=5, column=0, columnspan=3, sticky="w", pady=(14, 0))
        ttk.Button(row, text=t("Prova collegamento"), command=test).pack(side="left")

        def open_portal():
            u = v_url.get().strip()
            if u:
                webbrowser.open(u if "://" in u else "https://" + u)
        ttk.Button(row, text=t("Apri il portale"), command=open_portal).pack(side="left", padx=(6, 0))
        l_test.grid(row=6, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(frm, style="Hint.TLabel", wraplength=480, justify="left", text=(
            t("Il programma pubblica su Geopin tutto ciò che riceve (VHF, LoRa e APRS-IS) a intervalli "
            "regolari. I pacchetti restano in coda sul computer finché il portale non li riceve, anche "
            "se il programma viene chiuso o la rete manca. I pacchetti dei file WAV non vengono "
            "pubblicati."))).grid(row=7, column=0, columnspan=3, sticky="w", pady=(14, 0))

        def ok():
            url, key = v_url.get().strip(), v_key.get().strip()
            if (url or key) and not (url and key):
                messagebox.showerror(t("Geopin"), t("Servono sia l'indirizzo del portale sia la chiave."), parent=dlg)
                return
            self.gp_cfg.update(geopin_url=url, geopin_key=key,
                               geopin_interval_min=INTERVALS[choices.index(v_int.get())])
            self.v_gp_on.set(bool(url and key))
            dlg.destroy()
            try:
                cfg = self._read_cfg()
            except ValueError as e:
                messagebox.showwarning(t("Geopin"), t("Impostazioni salvate, ma la pubblicazione partirà solo dopo aver "
                                       "corretto la configurazione:") + f"\n{e}", parent=self)
                return
            try:
                cfg.save()
            except OSError as e:
                logging.warning("Configurazione non salvata: %s", e)
            self.cfg.geopin_enabled, self.cfg.geopin_url = cfg.geopin_enabled, cfg.geopin_url
            self.cfg.geopin_key, self.cfg.geopin_interval_min = cfg.geopin_key, cfg.geopin_interval_min
            self._geopin_apply(cfg)
        self._dialog_buttons(dlg, frm, 8, ok)

    # ------------------------------------------------------------ storico

    def _open_history(self) -> History | None:
        try:
            h = History()
            removed = h.prune(self.cfg.history_days)
            if removed:
                logging.info("Storico: rimossi %d pacchetti più vecchi di %d giorni",
                             removed, self.cfg.history_days)
            return h
        except Exception as e:      # disco pieno, file bloccato...: si continua senza
            logging.error("Storico non disponibile: %s", e)
            return None

    def _save_history_settings(self) -> None:
        self.cfg.history_enabled = self.v_hist_on.get()
        self.cfg.history_days = self.v_hist_days.get()
        self.cfg.history_load_hours = self.v_hist_load.get()
        if self.history is not None:
            self.history.prune(self.cfg.history_days)
        try:
            self.cfg.save()
        except OSError:
            pass

    def _history_toggled(self) -> None:
        if self.v_hist_on.get() and self.history is None:
            self.history = self._open_history()
            if self.history is None:
                self.v_hist_on.set(False)
        elif not self.v_hist_on.get() and self.history is not None:
            self.history.close()
            self.history = None
        self.store.history = self.history
        self._save_history_settings()

    def _clear_history(self) -> None:
        if self.history is None:
            messagebox.showinfo(t("Storico"), t("Lo storico è disattivato."), parent=self)
            return
        n = self.history.count()
        if messagebox.askyesno(t("Svuota lo storico"),
                               t("Cancellare definitivamente tutti i {n} pacchetti salvati?", n=n) + "\n"
                               + t("Le stazioni sulla mappa restano finché non premi \"Svuota stazioni\"."),
                               icon="warning", parent=self):
            self.history.clear()
            logging.info("Storico svuotato")

    def _export_gpx(self) -> None:
        if self.history is None:
            messagebox.showinfo(t("Esporta GPX"), t("Attiva lo storico dal menu Storico: l'esportazione "
                                "usa le posizioni salvate."), parent=self)
            return
        selected = list(self.tree.selection())
        dlg, frm = self._dialog(t("Esporta tracce GPX"))
        ttk.Label(frm, text=t("Periodo")).grid(row=0, column=0, sticky="w")
        v_period = tk.IntVar(value=24)
        for i, (hours, text) in enumerate(((1, t("Ultima ora")), (24, t("Ultime 24 ore")),
                                           (168, t("Ultimi 7 giorni")), (0, t("Tutto lo storico")))):
            ttk.Radiobutton(frm, text=text, value=hours, variable=v_period).grid(
                row=1 + i, column=0, sticky="w", padx=(12, 0))
        ttk.Label(frm, text=t("Stazioni")).grid(row=5, column=0, sticky="w", pady=(12, 0))
        v_only = tk.BooleanVar(value=bool(selected))
        ttk.Radiobutton(frm, text=t("Tutte"), value=False, variable=v_only).grid(row=6, column=0, sticky="w", padx=(12, 0))
        rb = ttk.Radiobutton(frm, text=t("Solo quelle selezionate nella tabella ({n})", n=len(selected)),
                             value=True, variable=v_only)
        rb.grid(row=7, column=0, sticky="w", padx=(12, 0))
        if not selected:
            rb.state(["disabled"])

        def do_export():
            hours = v_period.get()
            ids = selected if v_only.get() else None
            since = time.time() - hours * 3600 if hours else None
            tracks = self.history.tracks(since=since, stations=ids)
            if not tracks:
                messagebox.showinfo(t("Esporta GPX"), t("Nessuna posizione salvata nel periodo scelto."), parent=dlg)
                return
            base = safe_filename(ids[0]) if ids and len(ids) == 1 else "tracce_aprs"
            path = filedialog.asksaveasfilename(
                parent=dlg, title=t("Salva tracce GPX"), defaultextension=".gpx",
                initialfile=f"{base}_{time.strftime('%Y%m%d')}.gpx",
                filetypes=[("Tracce GPX", "*.gpx"), ("Tutti i file", "*.*")])
            if not path:
                return
            gpx, st = to_gpx(tracks, f"APRS Master {__version__}")
            try:
                Path(path).write_text(gpx, encoding="utf-8")
            except OSError as e:
                messagebox.showerror(t("Esporta GPX"), t("Salvataggio non riuscito: {e}", e=e), parent=dlg)
                return
            dlg.destroy()
            logging.info("Esportato %s: %d tracce (%d punti), %d stazioni fisse",
                         path, st["tracks"], st["points"], st["waypoints"])
            messagebox.showinfo(t("Esporta GPX"),
                                t("Salvate {n} tracce ({p} punti) e {w} stazioni fisse.",
                                  n=st["tracks"], p=st["points"], w=st["waypoints"]), parent=self)

        btns = ttk.Frame(frm)
        btns.grid(row=8, column=0, sticky="e", pady=(16, 0))
        ttk.Button(btns, text=t("Annulla"), command=dlg.destroy).pack(side="right")
        ttk.Button(btns, text=t("Esporta…"), command=do_export).pack(side="right", padx=(0, 8))
        self._place_dialog(dlg)
        dlg.grab_set()

    def _open_map(self, station: str | None = None, pick: bool = False) -> None:
        if not self.web.url:
            messagebox.showinfo(t("Mappa non attiva"), t("Avvia la ricezione o correggi la porta della mappa."),
                                parent=self)
            return
        query = "tema=" + ("scuro" if self.effective_theme == "dark" else "chiaro")
        if pick:
            query += "&scegli=1"
        webbrowser.open(f"{self.web.url}/?{query}" + (f"#{quote(station)}" if station else ""))

    def _pick_home(self) -> None:
        """Apre la mappa in modalità scelta: la posizione confermata nel browser
        torna qui tramite l'evento 'home' con picked=True."""
        if not self.web.url:
            try:
                port = int(self.v_port.get())
            except ValueError:
                port = 0
            if not (1 <= port <= 65535) or not self._start_web(
                    Config(web_port=port, bind="0.0.0.0" if self.v_lan.get() else "127.0.0.1")):
                messagebox.showerror(t("Mappa non avviabile"),
                                     t("Imposta una porta libera per la mappa e riprova."), parent=self)
                return
        try:   # mostra sulla mappa la posizione già scritta nel campo, se valida
            current = Config(home=self.v_home.get().strip()).home_tuple()
            if current and current != self.store.home:
                self.store.set_home(current)
        except ValueError:
            pass
        self._open_map(pick=True)
        logging.info("Scegli la posizione sulla mappa nel browser e premi \"Usa questa posizione\"")

    def _show_selected(self) -> None:
        sel = self.tree.selection()
        if sel:
            self._open_map(sel[0])

    def _close(self) -> None:
        try:
            self._read_cfg().save()
        except (ValueError, OSError):
            pass
        self.rx.stop(timeout=2)
        if self.publisher is not None:
            self.configure(cursor="watch")
            self.update_idletasks()
            self.publisher.stop(final=True, timeout=8)      # invia ciò che è rimasto in coda
        self.store.unsubscribe(self.events)
        self.engine.close()
        self.web.stop()
        if self.history is not None:
            self.history.close()
        self.destroy()

    # ------------------------------------------------------------ aggiornamenti

    def _poll(self) -> None:
        for _ in range(300):
            try:
                msg = json.loads(self.events.get_nowait())
            except queue.Empty:
                break
            self._on_event(msg)
        for _ in range(300):
            try:
                level, line = self.logq.get_nowait()
            except queue.Empty:
                break
            tag = "err" if level >= logging.ERROR else "warn" if level >= logging.WARNING else ""
            self._append(self.t_log, line, tag)
        for _ in range(100):
            try:
                kind, ev = self.uiq.get_nowait()
            except queue.Empty:
                break
            if kind == "alert":
                self._on_alert(ev)
            elif kind == "message":
                self._refresh_messages()
            elif kind == "rules":
                self._save_rules(ev)
        self._update_status()
        self._update_meter()
        self.after(100, self._poll)

    def _on_event(self, m: dict) -> None:
        if m.get("reload"):        # posizioni ricaricate dallo storico
            with self.store.lock:
                stations = [dict(st) for st in self.store.stations.values()]
            for st in sorted(stations, key=lambda d: d["last_heard"]):
                self.stations[st["id"]] = st
                self._station_row(st, move_top=True)
            return
        if m.get("reset"):
            self.stations.clear()
            self.tree.delete(*self.tree.get_children())
            self._set_text(self.t_packets, "")
            self.shown_packets, self.last_packet = 0, None
            self.l_last.configure(text=t("Nessun pacchetto ricevuto"))
            return
        if "home" in m:
            if m.get("picked") and m["home"]:
                lat, lon = m["home"]
                self.v_home.set(f"{lat:.5f}, {lon:.5f}")
                self.cfg.home = self.v_home.get()
                try:
                    self.cfg.save()
                except OSError:
                    pass
                logging.info("Posizione ricevente scelta sulla mappa: %.5f, %.5f", lat, lon)
                self.lift()
            self._refresh_station_rows()
            return
        p = m.get("packet")
        if p:
            self.shown_packets = m.get("total", self.shown_packets + 1)
            self.last_packet = p["t"]
            hh = time.strftime("%H:%M:%S", time.localtime(p["t"]))
            tag = {"lora": "[LoRa] ", "is": t("[Rete] ")}.get(p.get("origin"), "")
            self._append(self.t_packets, f"{hh}  {tag}{printable(p['raw'])}    [{t(p['kind'])}]")
            self.l_last.configure(text=t("Ultimo pacchetto alle {h}, {call}", h=hh, call=p["source"]))
        st = m.get("station")
        if st:
            self.stations[st["id"]] = st
            self._station_row(st, move_top=True)

    def _station_row(self, d: dict, move_top: bool = False) -> None:
        home = self.store.home
        dist = (f"{haversine(home, (d['lat'], d['lon'])):.1f} km {bearing(home, (d['lat'], d['lon'])):.0f}°"
                if home else "")
        orig = " + ".join(n for n, k in (("VHF", "rf_last"), ("LoRa", "lora_last"), ("Internet", "is_last"))
                          if d.get(k)) or "VHF"
        vals = (d["id"], t(d.get("mic_e_status") or d["kind"]), f"{d['lat']:.5f}, {d['lon']:.5f}", dist,
                f"{d['speed_kmh']:.0f} km/h" if d.get("speed_kmh") else "",
                f"{d['altitude_m']:.0f} m" if d.get("altitude_m") is not None else "",
                ago(d["last_heard"]), d["packets"], orig, printable(d.get("comment") or ""))
        if self.tree.exists(d["id"]):
            self.tree.item(d["id"], values=vals)
            if move_top:
                self.tree.move(d["id"], "", 0)
        else:
            self.tree.insert("", 0, iid=d["id"], values=vals)

    def _refresh_station_rows(self) -> None:
        for d in self.stations.values():
            self._station_row(d)

    def _refresh_ages(self) -> None:
        for sid, d in self.stations.items():
            if self.tree.exists(sid):
                self.tree.set(sid, "last", ago(d["last_heard"]))
        if self.last_packet:
            hh = time.strftime("%H:%M:%S", time.localtime(self.last_packet))
            self.l_last.configure(text=t("Ultimo pacchetto alle {h}, {call}", h=hh, call=ago(self.last_packet)))
        self.after(5000, self._refresh_ages)

    def _update_status(self) -> None:
        running = self.rx.running
        if running:
            text, color = tx(self.rx.status), self.pal["ok"]
        elif self.rx.error:
            text, color = t("Errore: {e}", e=tx(self.rx.error)), self.pal["err"]
        elif self.rx.finished_wav:
            text, color = t("File WAV decodificato"), self.pal["idle"]
        else:
            text, color = t("Fermo"), self.pal["idle"]
        if self._last_status != (text, color):
            self._last_status = (text, color)
            self.l_status.configure(text=text)
            self.dot.itemconfigure(self.dot_id, fill=color)
        if running != self.was_running:
            self.was_running = running
            self.b_start.configure(text=t("Ferma ricezione") if running else t("Avvia ricezione"))
            self._apply_states()
        self.l_counts["packets"].configure(text=str(self.shown_packets))
        fx = self.rx.fixed
        lf = self.rx.handler.lora_frames if (running and self.rx.handler) else 0
        self.l_counts["frames"].configure(text=f"{self.rx.frames}" + (f" + {lf}" if lf else "")
                                          + ("  " + t("({n} corretti)", n=fx) if fx else ""))
        cap = t("Frame VHF + LoRa") if lf else t("Frame validi")
        if self.l_frames_cap.cget("text") != cap:
            self.l_frames_cap.configure(text=cap)
        lo = self.rx.lora if running else None
        if lo is None:
            ltxt = t("Attivo all'avvio della ricezione") if self.v_lora_on.get() else t("Non attivo")
        else:
            ltxt = tx(lo.status) + ("   " + t("Pacchetti ricevuti: {n}", n=lo.packets) if lo.connected else "")
        if self.l_lora.cget("text") != ltxt:
            self.l_lora.configure(text=ltxt)
        self.l_counts["stations"].configure(text=str(len(self.stations)))
        c = self.rx.aprsis if running else None
        if c is None:
            txt = t("Attivo all'avvio della ricezione") if self.v_is_on.get() else t("Non collegato")
        else:
            txt = tx(c.status)
            if c.connected:
                txt += "\n" + t("Ricevuti dalla rete: {n}", n=c.received)
                if c.igate:
                    txt += "   " + t("Inoltrati (iGate): {n}", n=c.gated)
                if c.beacon_info:
                    txt += "\n" + tx(c.beacon_info)
                if c.igate_note:
                    txt += "\n" + tx(c.igate_note)
        if self.l_is.cget("text") != txt:
            self.l_is.configure(text=txt)
        self._gp_tick += 1
        if self._gp_tick % 10 == 1:            # una volta al secondo basta
            self._update_geopin()
        if self._gp_tick % 20 == 2:
            self._update_msg_state()

    def _update_geopin(self) -> None:
        p = self.publisher
        if p is None:
            txt = t("Non attiva")
        else:
            txt = tx(p.status)
            pend = p.pending
            parts = []
            if p.busy:
                parts.append(t("pubblicazione in corso…"))
            elif p.next_at:
                parts.append(t("prossima alle {h}", h=time.strftime("%H:%M", time.localtime(p.next_at))))
            parts.append(t("in coda: {n}", n=pend))
            txt += "\n" + ", ".join(parts).capitalize()
        if self.l_gp.cget("text") != txt:
            self.l_gp.configure(text=txt)
        self.b_gp_now.state(["!disabled"] if p is not None and not p.busy else ["disabled"])


    def _update_meter(self) -> None:
        active = self.rx.running and self.cfg.source in ("audio", "wav")
        if active:
            db = 20 * math.log10(max(self.rx.peak, 1e-5))
            self.level_db = max(db, self.level_db - 1.5)       # discesa lenta del picco
        else:
            self.level_db = -90.0
        self._draw_meter()
        if not active:
            txt = ((t("Nessuna sorgente audio") if self.cfg.source == "none" else t("Non disponibile con Direwolf/TNC"))
                   if self.rx.running else t("Inattivo"))
        elif self.level_db > -3:
            txt = t("{db} dBFS: troppo alto, abbassa il volume", db=f"{self.level_db:.0f}")
        elif self.level_db > -30:
            txt = t("{db} dBFS: livello corretto", db=f"{self.level_db:.0f}")
        else:
            txt = t("{db} dBFS: silenzio o volume troppo basso", db=f"{self.level_db:.0f}")
        if self.l_level.cget("text") != txt:
            self.l_level.configure(text=txt)

    def _draw_meter(self) -> None:
        c = self.meter
        w, h = max(c.winfo_width(), 50), 26
        lo = -60.0

        def x(db):
            return (min(max(db, lo), 0) - lo) / -lo * w
        c.delete("all")
        c.create_rectangle(0, 4, w, h - 4, fill=self.pal["track"], outline="")
        c.create_rectangle(x(-30), 4, x(-3), h - 4, fill=self.pal["zone_ok"], outline="")
        c.create_rectangle(x(-3), 4, w, h - 4, fill=self.pal["zone_hi"], outline="")
        if self.level_db > lo:
            color = self.pal["err"] if self.level_db > -3 else self.pal["ok"] if self.level_db > -30 else self.pal["low"]
            c.create_rectangle(0, 8, x(self.level_db), h - 8, fill=color, outline="")
        for db in (-50, -40, -30, -20, -10, -3):
            c.create_line(x(db), 0, x(db), 4, fill=self.pal["tick"])

    # ------------------------------------------------------------ testo

    @staticmethod
    def _append(t: tk.Text, line: str, tag: str = "", max_lines: int = 2000) -> None:
        at_end = t.yview()[1] > 0.98
        t.configure(state="normal")
        t.insert("end", line + "\n", tag)
        excess = int(t.index("end-1c").split(".")[0]) - max_lines
        if excess > 0:
            t.delete("1.0", f"{excess + 1}.0")
        t.configure(state="disabled")
        if at_end:
            t.see("end")

    @staticmethod
    def _set_text(t: tk.Text, text: str) -> None:
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("1.0", text)
        t.configure(state="disabled")


def main() -> None:
    App().mainloop()


if __name__ == "__main__":
    main()
