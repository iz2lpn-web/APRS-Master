# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Finestre che si adattano allo schermo.

ScrollFrame è un contenitore con barre di scorrimento verticale e orizzontale che
compaiono solo quando servono: se la finestra è più grande del contenuto, il
contenuto si allarga a riempirla (le colonne e la tabella si estendono come
prima); se è più piccola, il contenuto mantiene la sua misura minima e si scorre.

fit_to_screen() sceglie la misura iniziale di una finestra in base al contenuto
reale (che cresce con la scala dei caratteri di Windows, 125-150%) e all'area
utile dello schermo.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

# widget che scorrono da soli: la rotella sopra di loro non muove la finestra
_SELF_SCROLLING = {"Treeview", "Text", "Listbox", "TCombobox", "TSpinbox", "Spinbox", "Scale", "TScale",
                   "Canvas"}


class ScrollFrame(ttk.Frame):
    """Contenitore scorrevole: i widget si mettono in .body."""

    registry: list["ScrollFrame"] = []

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0)
        self.vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.hsb = ttk.Scrollbar(self, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=self.vsb.set, xscrollcommand=self.hsb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.body = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window(0, 0, window=self.body, anchor="nw")
        self._shown = (False, False)
        self.bind("<Configure>", lambda e: self._layout())
        self.body.bind("<Configure>", lambda e: self._layout())
        ScrollFrame.registry.append(self)
        self.bind("<Destroy>", self._forget, add="+")

    def _forget(self, event) -> None:
        if event.widget is self and self in ScrollFrame.registry:
            ScrollFrame.registry.remove(self)

    def set_background(self, color: str) -> None:
        self.canvas.configure(background=color)

    def content_size(self) -> tuple[int, int]:
        """Misura minima del contenuto, barre escluse."""
        self.body.update_idletasks()
        return self.body.winfo_reqwidth(), self.body.winfo_reqheight()

    def _layout(self) -> None:
        # si decide sullo spazio totale del contenitore, non su quello del canvas: altrimenti le barre,
        # una volta comparse, ruberebbero lo spazio che le rende necessarie e non sparirebbero più
        W, H = self.winfo_width(), self.winfo_height()
        if W <= 1 or H <= 1:
            return
        rw, rh = self.body.winfo_reqwidth(), self.body.winfo_reqheight()
        bw, bh = self.vsb.winfo_reqwidth(), self.hsb.winfo_reqheight()
        need_v = rh > H
        need_h = rw > W - (bw if need_v else 0)
        if need_h and not need_v:
            need_v = rh > H - bh
            need_h = rw > W - (bw if need_v else 0)
        need = (need_v, need_h)
        if need != self._shown:
            self._shown = need
            if need_v:
                self.vsb.grid(row=0, column=1, sticky="ns")
            else:
                self.vsb.grid_remove()
                self.canvas.yview_moveto(0)
            if need_h:
                self.hsb.grid(row=1, column=0, sticky="ew")
            else:
                self.hsb.grid_remove()
                self.canvas.xview_moveto(0)
        cw = W - (bw if need_v else 0)
        ch = H - (bh if need_h else 0)
        # il contenuto riempie lo spazio disponibile, ma non scende sotto la sua misura minima
        self.canvas.itemconfigure(self._win, width=max(cw, rw), height=max(ch, rh))
        self.canvas.configure(scrollregion=(0, 0, max(cw, rw), max(ch, rh)))

    @property
    def scrollable(self) -> tuple[bool, bool]:
        return self._shown

    # ------------------------------------------------------------ rotella del mouse

    @classmethod
    def install_wheel(cls, root: tk.Misc) -> None:
        """Una sola volta per l'applicazione: la rotella scorre il contenitore sotto il puntatore.
        Shift + rotella (o la rotella orizzontale) scorre in orizzontale."""
        root.bind_all("<MouseWheel>", lambda e: cls._wheel(e, horizontal=bool(e.state & 0x0001)), add="+")
        root.bind_all("<Shift-MouseWheel>", lambda e: cls._wheel(e, horizontal=True), add="+")
        root.bind_all("<Button-4>", lambda e: cls._wheel(e, horizontal=bool(e.state & 0x0001), step=-1), add="+")
        root.bind_all("<Button-5>", lambda e: cls._wheel(e, horizontal=bool(e.state & 0x0001), step=1), add="+")
        root.bind_all("<Shift-Button-4>", lambda e: cls._wheel(e, horizontal=True, step=-1), add="+")
        root.bind_all("<Shift-Button-5>", lambda e: cls._wheel(e, horizontal=True, step=1), add="+")

    @classmethod
    def _wheel(cls, event, horizontal: bool = False, step: int | None = None) -> None:
        w = event.widget
        if not isinstance(w, tk.Misc):                 # es. l'elenco a tendina di una combobox
            return
        try:
            if w.winfo_class() in _SELF_SCROLLING:
                return
        except tk.TclError:
            return
        path = str(w)
        target = None
        for sf in cls.registry:                       # il contenitore più interno che contiene il widget
            body = str(sf.body)
            if path == body or path.startswith(body + ".") or path == str(sf.canvas):
                if target is None or len(str(sf.body)) > len(str(target.body)):
                    target = sf
        if target is None:
            return
        vert, horiz = target.scrollable
        if step is None:
            d = event.delta
            if abs(d) >= 120:                         # Windows: multipli di 120
                step = -int(d / 120)
            else:                                     # macOS: valori piccoli
                step = -1 if d > 0 else (1 if d < 0 else 0)
        if not step:
            return
        if horizontal and horiz:
            target.canvas.xview_scroll(step * 2, "units")
        elif not horizontal and vert:
            target.canvas.yview_scroll(step * 2, "units")
        elif not horizontal and horiz:                # solo la barra orizzontale: la rotella va in orizzontale
            target.canvas.xview_scroll(step * 2, "units")


def work_area(win: tk.Misc) -> tuple[int, int, int, int]:
    """Area utile dello schermo (x, y, larghezza, altezza), escluse barra delle applicazioni e pannelli.
    Su Windows la si legge dal sistema; altrove si stima lasciando un margine."""
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    if win.tk.call("tk", "windowingsystem") == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            r = wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(r), 0):   # SPI_GETWORKAREA
                return r.left, r.top, r.right - r.left, r.bottom - r.top
        except Exception:
            pass
        return 0, 0, sw, sh - 48
    if win.tk.call("tk", "windowingsystem") == "aqua":
        return 0, 25, sw, sh - 25 - 70                 # barra dei menu e Dock
    return 0, 0, sw, sh - 60                           # pannelli di Linux


def fit_to_screen(win: tk.Misc, want_w: int, want_h: int, frame_w: int = 16, frame_h: int = 40,
                  center_on: tk.Misc | None = None) -> tuple[int, int]:
    """Dimensiona e posiziona la finestra: la misura desiderata, ridotta all'area utile dello schermo
    (bordi e barra del titolo compresi), centrata sullo schermo o sulla finestra indicata."""
    ax, ay, aw, ah = work_area(win)
    w = max(320, min(want_w, aw - frame_w))
    h = max(240, min(want_h, ah - frame_h))
    if center_on is not None and center_on.winfo_ismapped():
        x = center_on.winfo_rootx() + (center_on.winfo_width() - w) // 2
        y = center_on.winfo_rooty() + (center_on.winfo_height() - h) // 3
    else:
        x = ax + (aw - frame_w - w) // 2
        y = ay + max(0, (ah - frame_h - h) // 3)
    # mai fuori dall'area utile: la barra del titolo deve restare raggiungibile
    x = min(max(x, ax), ax + max(0, aw - w - frame_w))
    y = min(max(y, ay), ay + max(0, ah - h - frame_h))
    win.geometry(f"{w}x{h}+{x}+{y}")
    return w, h
