# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Messaggi APRS (APRS 1.0.1, capitolo 14).

- Ricevuti: tutti i messaggi sentiti (radio, LoRa, APRS-IS); quelli per la propria stazione (il
  nominativo-SSID di APRS-IS o il nominativo senza SSID) si conservano e, se hanno un numero, si
  confermano subito con "ack". Lo stesso messaggio ripetuto (digipeater, più ricevitori, il mittente
  che ritrasmette perché non ha visto la conferma) si conta una volta ma si riconferma sempre.
- Inviati: tramite APRS-IS, con il collegamento verificato (passcode). Ogni messaggio ha un numero e
  si ritrasmette dopo 30 s, 1, 2 e 4 minuti finché non arriva la conferma; poi resta "senza conferma".
- Bollettini (BLN...): l'ultimo per mittente e numero.

I messaggi si conservano in un piccolo database SQLite (~/.aprs_mappa_messaggi.db).
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable

from .aprsis import TOCALL, valid_callsign

log = logging.getLogger("messaggi")
DB_PATH = Path.home() / ".aprs_mappa_messaggi.db"
TEXT_MAX = 67                         # lunghezza massima del testo di un messaggio
RETRY_S = [30, 60, 120, 240]          # ritrasmissioni senza conferma; poi si rinuncia
DUP_S = 30 * 60                       # stesso mittente, numero e testo: duplicato
HEARD_MAX = 300


def text_error(text: str) -> str | None:
    from .lingua import t
    if not text.strip():
        return t("Scrivi il testo del messaggio.")
    if len(text) > TEXT_MAX:
        return t("Il messaggio è troppo lungo: {n} caratteri su {max}.", n=len(text), max=TEXT_MAX)
    bad = sorted({c for c in text if not (" " <= c <= "~") or c in "|~{"})
    if bad:
        return t("Il messaggio contiene caratteri non ammessi in APRS: {c}. Usa lettere senza accenti.",
                 c=" ".join(bad))
    return None


def addressee_error(to: str) -> str | None:
    from .lingua import t
    to = to.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{1,6}(-[0-9A-Z]{1,2})?", to) or len(to) > 9:
        return t("Destinatario non valido: scrivi un nominativo, con l'SSID se serve (es. IZ2LPN-9).")
    return None


def message_line(my_call: str, to: str, text: str, msg_id: str | None = None) -> str:
    """Riga APRS-IS di un messaggio (destinatario completato a 9 caratteri)."""
    body = text + (f"{{{msg_id}" if msg_id else "")
    return f"{my_call.upper()}>{TOCALL},TCPIP*::{to.upper():<9}:{body}"


class MessageCenter:
    def __init__(self, db_path: str | Path = DB_PATH):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(db_path), check_same_thread=False)
        self.db.execute("""CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT,
            t REAL NOT NULL, dir TEXT NOT NULL, peer TEXT NOT NULL, me TEXT NOT NULL, text TEXT NOT NULL,
            msgid TEXT, status TEXT NOT NULL DEFAULT '', tries INTEGER NOT NULL DEFAULT 0, next_t REAL,
            origin TEXT NOT NULL DEFAULT '', read INTEGER NOT NULL DEFAULT 0)""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS bulletins (src TEXT NOT NULL, dst TEXT NOT NULL,
            t REAL NOT NULL, text TEXT NOT NULL, origin TEXT NOT NULL DEFAULT '', PRIMARY KEY (src, dst))""")
        self.db.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
        self.db.commit()
        self.my_call = ""                 # nominativo-SSID usato per APRS-IS (es. IZ2LPN-10)
        self.extra_calls: list[str] = []  # altri destinatari considerati "miei"
        self.sender: Callable[[str], bool] | None = None   # invia una riga ad APRS-IS
        self.can_send: Callable[[], bool] = lambda: False
        self.on_event: Callable[[dict], None] | None = None
        self.heard: list[dict] = []       # ultimi messaggi sentiti tra altre stazioni
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ configurazione
    def configure(self, my_call: str, extra: list[str] | None = None) -> None:
        with self.lock:
            self.my_call = my_call.strip().upper()
            self.extra_calls = [c.strip().upper() for c in (extra or []) if c.strip()]

    def my_calls(self) -> set[str]:
        out = set(self.extra_calls)
        if self.my_call:
            out |= {self.my_call, self.my_call.split("-")[0]}
        return out

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, daemon=True, name="messaggi")
            self._thread.start()

    def close(self) -> None:
        self._stop.set()
        with self.lock:
            self.db.close()

    # ------------------------------------------------------------ ricezione
    def handle(self, source: str, msg: dict, origin: str, t: float | None = None) -> None:
        """Messaggio decodificato da aprs.parse (pkt.message)."""
        now = time.time() if t is None else t
        source = source.upper()
        kind, to = msg.get("type"), (msg.get("to") or "").upper()
        if kind == "tlm_def":
            return
        if kind == "bulletin":
            with self.lock:
                self.db.execute("INSERT OR REPLACE INTO bulletins (src, dst, t, text, origin) VALUES (?, ?, ?, ?, ?)",
                                (source, to, now, msg.get("text", ""), origin))
                self.db.commit()
            self._emit({"type": "bulletin", "from": source, "to": to, "text": msg.get("text", ""), "t": now})
            return
        mine = to in self.my_calls()
        if not mine:
            with self.lock:
                self.heard.append({"t": now, "from": source, "to": to, "text": msg.get("text", ""),
                                   "type": kind, "origin": origin})
                del self.heard[:-HEARD_MAX]
            return
        if kind in ("ack", "rej"):
            self._ack(source, msg.get("id") or "", kind == "ack")
            return
        if source == self.my_call:
            return                          # un proprio messaggio rimandato dalla rete
        msg_id = msg.get("id")
        text = msg.get("text", "")
        with self.lock:
            dup = self.db.execute("SELECT id FROM messages WHERE dir = 'in' AND peer = ? AND text = ? AND "
                                  "COALESCE(msgid, '') = ? AND t > ?",
                                  (source, text, msg_id or "", now - DUP_S)).fetchone()
            if not dup:
                cur = self.db.execute("INSERT INTO messages (t, dir, peer, me, text, msgid, status, origin) "
                                      "VALUES (?, 'in', ?, ?, ?, ?, 'ricevuto', ?)",
                                      (now, source, to, text, msg_id, origin))
                self.db.commit()
                row_id = cur.lastrowid
        if msg_id:                           # la conferma si manda anche per le ripetizioni
            self._send(message_line(self.my_call or to, source, f"ack{msg_id}"))
        if not dup:
            self._emit({"type": "message", "id": row_id, "from": source, "to": to, "text": text, "t": now,
                        "origin": origin})

    def _ack(self, peer: str, msg_id: str, ok: bool) -> None:
        with self.lock:
            row = self.db.execute("SELECT id, text FROM messages WHERE dir = 'out' AND peer = ? AND msgid = ? "
                                  "AND status IN ('in invio', 'senza conferma')", (peer, msg_id)).fetchone()
            if not row:
                return
            status = "consegnato" if ok else "rifiutato"
            self.db.execute("UPDATE messages SET status = ?, next_t = NULL WHERE id = ?", (status, row[0]))
            self.db.commit()
        self._emit({"type": "status", "id": row[0], "status": status, "to": peer, "text": row[1]})

    # ------------------------------------------------------------ invio
    def send(self, to: str, text: str) -> dict:
        """Accoda e invia un messaggio; solleva ValueError con un testo da mostrare."""
        from .lingua import t
        to, text = to.strip().upper(), text.strip()
        err = addressee_error(to) or text_error(text)
        if err:
            raise ValueError(err)
        if not valid_callsign(self.my_call):
            raise ValueError(t("Per inviare serve il collegamento APRS-IS con il proprio nominativo."))
        if not self.can_send():
            raise ValueError(t("Per inviare serve il collegamento APRS-IS verificato (nominativo e passcode)."))
        with self.lock:
            row = self.db.execute("SELECT v FROM meta WHERE k = 'next_id'").fetchone()
            n = int(row[0]) if row else 1
            msg_id = str(n)
            self.db.execute("INSERT OR REPLACE INTO meta (k, v) VALUES ('next_id', ?)", (str(n % 99999 + 1),))
            now = time.time()
            cur = self.db.execute("INSERT INTO messages (t, dir, peer, me, text, msgid, status, tries, next_t, read) "
                                  "VALUES (?, 'out', ?, ?, ?, ?, 'in invio', 1, ?, 1)",
                                  (now, to, self.my_call, text, msg_id, now + RETRY_S[0]))
            self.db.commit()
            row_id = cur.lastrowid
        self._send(message_line(self.my_call, to, text, msg_id))
        item = {"type": "sent", "id": row_id, "to": to, "text": text, "t": now, "status": "in invio"}
        self._emit(item)
        return item

    def _send(self, line: str) -> bool:
        if self.sender is None or not self.can_send():
            return False
        try:
            return bool(self.sender(line))
        except Exception:
            log.exception("invio del messaggio non riuscito")
            return False

    def tick(self, now: float | None = None) -> None:
        """Ritrasmissioni dei messaggi senza conferma."""
        now = time.time() if now is None else now
        with self.lock:
            rows = self.db.execute("SELECT id, peer, text, msgid, tries FROM messages WHERE dir = 'out' "
                                   "AND status = 'in invio' AND next_t IS NOT NULL AND next_t <= ?", (now,)).fetchall()
        for rid, peer, text, msg_id, tries in rows:
            if tries > len(RETRY_S):
                with self.lock:
                    self.db.execute("UPDATE messages SET status = 'senza conferma', next_t = NULL WHERE id = ?", (rid,))
                    self.db.commit()
                self._emit({"type": "status", "id": rid, "status": "senza conferma", "to": peer, "text": text})
                continue
            self._send(message_line(self.my_call, peer, text, msg_id))
            nxt = now + (RETRY_S[tries] if tries < len(RETRY_S) else RETRY_S[-1])
            with self.lock:
                self.db.execute("UPDATE messages SET tries = tries + 1, next_t = ? WHERE id = ?", (nxt, rid))
                self.db.commit()

    def _loop(self) -> None:
        while not self._stop.wait(5):
            try:
                self.tick()
            except sqlite3.ProgrammingError:
                return
            except Exception:
                log.exception("ritrasmissione dei messaggi non riuscita")

    # ------------------------------------------------------------ lettura
    def mark_read(self, peer: str | None = None) -> None:
        with self.lock:
            if peer:
                self.db.execute("UPDATE messages SET read = 1 WHERE dir = 'in' AND peer = ?", (peer.upper(),))
            else:
                self.db.execute("UPDATE messages SET read = 1 WHERE dir = 'in'")
            self.db.commit()
        self._emit({"type": "read"})

    def unread(self) -> int:
        with self.lock:
            return int(self.db.execute("SELECT COUNT(*) FROM messages WHERE dir = 'in' AND read = 0").fetchone()[0])

    def snapshot(self, limit: int = 300) -> dict:
        with self.lock:
            rows = self.db.execute("SELECT id, t, dir, peer, me, text, msgid, status, origin, read FROM messages "
                                   "ORDER BY t DESC, id DESC LIMIT ?", (limit,)).fetchall()
            bl = self.db.execute("SELECT src, dst, t, text, origin FROM bulletins ORDER BY t DESC LIMIT 100").fetchall()
            heard = list(self.heard[-100:])
        msgs = [{"id": r[0], "t": r[1], "dir": r[2], "peer": r[3], "me": r[4], "text": r[5], "msgid": r[6],
                 "status": r[7], "origin": r[8], "read": bool(r[9])} for r in reversed(rows)]
        return {"me": self.my_call, "calls": sorted(self.my_calls()), "can_send": bool(self.can_send()),
                "messages": msgs, "unread": sum(1 for m in msgs if m["dir"] == "in" and not m["read"]),
                "bulletins": [{"from": b[0], "to": b[1], "t": b[2], "text": b[3], "origin": b[4]} for b in bl],
                "heard": list(reversed(heard))}

    def _emit(self, ev: dict) -> None:
        if self.on_event:
            try:
                self.on_event(ev)
            except Exception:
                log.exception("notifica del messaggio non riuscita")
