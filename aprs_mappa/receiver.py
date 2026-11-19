# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Motore di ricezione: configurazione, gestione dei pacchetti e sorgenti
(scheda audio, KISS TCP, WAV) in un thread che si può avviare e fermare.
Usato sia dalla GUI (gui.py) sia dalla riga di comando (main.py)."""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import wave
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import numpy as np

from . import aprs
from .afsk import make_demodulator
from .ax25 import AX25Frame, decode_frame
from .kiss import run_kiss_tcp
from .server import StationStore

log = logging.getLogger("aprs")
CONFIG_PATH = Path.home() / ".aprs_mappa.json"


@dataclass
class Config:
    source: str = "audio"            # audio | kiss | wav
    device: str = ""                 # indice o nome; vuoto = predefinita
    rate: int = 48000
    channel: int = 0
    kiss_host: str = "127.0.0.1"
    kiss_port: int = 8001
    wav: str = ""
    realtime: bool = False
    web_port: int = 8080
    bind: str = "127.0.0.1"
    home: str = ""                   # "lat,lon"
    filters: list[str] = field(default_factory=list)
    log_file: str = ""
    theme: str = "auto"              # auto | light | dark (solo GUI)
    demod_profile: str = "completo"  # completo | leggero
    fix_bits: bool = True            # correzione dei bit errati
    history_enabled: bool = True     # storico persistente (SQLite)
    history_days: int = 90           # giorni da conservare; 0 = per sempre
    history_load_hours: int = 24     # ore ricaricate sulla mappa all'avvio
    # stazione ricevente
    station_type: str = "igate_vhf"  # vedi aprsis.STATION_TYPES
    station_comment: str = ""        # commento dell'annuncio (vuoto = predefinito)
    station_equipment: str = ""      # descrizione libera dell'apparato (pacchetto di stato)
    # Geopin: pubblicazione dei pacchetti ricevuti sul portale
    geopin_enabled: bool = False
    geopin_url: str = ""             # indirizzo del portale, es. https://geopin.esempio.it/
    geopin_key: str = ""             # chiave del ricevitore, creata sul portale
    geopin_interval_min: int = 5     # ogni quanti minuti pubblicare
    # APRS-IS
    aprsis_enabled: bool = False     # collegamento ad APRS-IS
    aprsis_call: str = ""            # nominativo-SSID usato per il collegamento (es. IZ2LPN-10)
    aprsis_passcode: str = ""
    aprsis_server: str = "rotate.aprs2.net"
    aprsis_port: int = 14580
    aprsis_radius_km: int = 100      # raggio dei pacchetti ricevuti per il confronto; 0 = nessuno
    igate_enabled: bool = False      # inoltro dei pacchetti radio ad APRS-IS
    aprsis_beacon: bool = True       # annuncia la stazione (posizione e stato) ogni 30 minuti
    # LoRa APRS (modulo esterno, in parallelo alla sorgente VHF)
    lora_enabled: bool = False
    lora_mode: str = "serial"        # serial | tcp
    lora_serial: str = ""            # es. COM5 o /dev/ttyUSB0
    lora_baud: int = 115200
    lora_host: str = ""
    lora_port: int = 8001
    # mappe
    map_source: str = "osm"          # osm | custom | mbtiles
    map_url: str = ""                # server personalizzato: https://.../{z}/{x}/{y}.png
    map_attribution: str = ""
    mbtiles_path: str = ""
    map_cache_mb: int = 500
    # messaggi APRS (inviati tramite APRS-IS, serve il passcode)
    messages_enabled: bool = True
    messages_calls: str = ""          # altri destinatari considerati propri, separati da virgole
    # avvisi
    alert_watch: str = ""             # nominativi osservati, separati da virgole (IZ2* = prefisso)
    alert_zones: list = field(default_factory=list)   # [{"name","lat","lon","radius_km","calls"}]
    alert_emergency: bool = True      # segnale di emergenza Mic-E
    alert_messages: bool = True       # messaggio per la propria stazione
    alert_quiet_min: int = 30         # una stazione osservata riavvisa dopo questa assenza
    alert_sound: bool = True          # suono e finestra nella GUI
    # interfaccia e servizio
    language: str = "auto"            # auto | it | en
    panel_password: str = ""          # hash della password del pannello web ("" = solo da locale)

    def home_tuple(self) -> tuple[float, float] | None:
        if not self.home.strip():
            return None
        lat, lon = (float(v) for v in self.home.replace(" ", "").split(","))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("coordinate fuori intervallo")
        return lat, lon

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            known = {f.name for f in fields(cls)}
            return cls(**{k: v for k, v in data.items() if k in known})
        except (OSError, ValueError, TypeError):
            return cls()


class PacketHandler:
    def __init__(self, store: StationStore, call_filter: list[str], logfile: str | None,
                 gate=None):
        self.store = store
        self.gate = gate         # funzione che inoltra un frame radio ad APRS-IS (iGate) o None
        self.lora_gate = None    # idem per i pacchetti LoRa
        self.publish = None      # funzione (t, origine, riga) che accoda per Geopin, o None
        self.filter = [c.strip().upper() for c in call_filter if c.strip()]
        self.logfile = open(logfile, "a", encoding="utf-8") if logfile else None
        self.seen: dict[bytes, float] = {}
        self.frames = 0          # frame validi (CRC ok), compresi duplicati e filtrati
        self.lora_frames = 0     # pacchetti ricevuti dal modulo LoRa

    def close(self) -> None:
        if self.logfile:
            self.logfile.close()
            self.logfile = None

    def _duplicate(self, frame: AX25Frame) -> bool:
        """Scarta lo stesso pacchetto ripetuto dai digipeater entro 30 s."""
        key = (frame.source + frame.dest).encode() + frame.info
        now = time.time()
        self.seen = {k: t for k, t in self.seen.items() if now - t < 30}
        dup = key in self.seen
        self.seen[key] = now
        return dup

    def __call__(self, frame: AX25Frame | None) -> None:
        if frame is None:
            return
        self.frames += 1
        raw = frame.tnc2()
        if self.logfile:
            self.logfile.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {raw}\n")
            self.logfile.flush()
        if self.gate is not None:
            # l'iGate inoltra ogni ricezione, anche le ripetizioni dei digipeater:
            # i duplicati li scarta il server (indicazioni di aprsc per gli iGate RX)
            self.gate(frame)
        if self.filter and not any(frame.source.upper().startswith(c) for c in self.filter):
            return
        if self._duplicate(frame):
            return
        pkt = aprs.parse(frame.source, frame.dest, frame.info)
        pos = f" -> {pkt.lat:.5f}, {pkt.lon:.5f}" if pkt.has_position else ""
        log.info("%s  [%s%s]", raw, pkt.kind, pos)
        self.store.add(pkt, raw)
        self._publish("rf", raw)

    def _publish(self, origin: str, raw: str) -> None:
        if self.publish is not None:
            self.publish(time.time(), origin, raw)

    def lora_line(self, line: str) -> None:
        """Pacchetto TNC2 ricevuto dal modulo LoRa."""
        from .lora import tnc2_to_frame
        frame = tnc2_to_frame(line)
        if frame is None:
            return
        self.lora_frames += 1
        if self.logfile:
            self.logfile.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [LoRa] {line}\n")
            self.logfile.flush()
        if self.lora_gate is not None:
            self.lora_gate(frame)
        if self.filter and not any(frame.source.upper().startswith(c) for c in self.filter):
            return
        key = b"lora:" + line.encode("latin-1", errors="replace")
        now = time.time()
        self.seen = {k: t for k, t in self.seen.items() if now - t < 30}
        if key in self.seen:
            return
        self.seen[key] = now
        pkt = aprs.parse(frame.source, frame.dest, frame.info)
        pos = f" -> {pkt.lat:.5f}, {pkt.lon:.5f}" if pkt.has_position else ""
        log.info("[LoRa] %s  [%s%s]", line, pkt.kind, pos)
        self.store.add(pkt, line, origin="lora")
        self._publish("lora", line)

    def internet_line(self, line: str) -> None:
        """Pacchetto ricevuto da APRS-IS (per il confronto con la radio)."""
        pkt = aprs.parse_tnc2(line)
        if pkt is None:
            return
        source = line.split(">", 1)[0]
        if self.filter and not any(source.upper().startswith(c) for c in self.filter):
            return
        self.store.add(pkt, line, origin="is")
        self._publish("is", line)


def list_input_devices() -> list[tuple[int, str]]:
    """[(indice, descrizione)] delle schede audio con ingressi. Solleva
    ImportError/OSError se sounddevice o PortAudio non sono disponibili."""
    import sounddevice as sd
    apis = sd.query_hostapis()
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            out.append((i, f"{d['name']} ({apis[d['hostapi']]['name']})"))
    return out


class Receiver:
    def __init__(self, store: StationStore):
        self.store = store
        self.thread: threading.Thread | None = None
        self.stop_evt = threading.Event()
        self.handler: PacketHandler | None = None
        self.peak = 0.0              # picco del blocco audio più recente (0..1)
        self.status = "Fermo"
        self.error: str | None = None
        self.finished_wav = False
        self.demod = None            # demodulatore interno in uso (statistiche)
        self.aprsis = None           # aprsis.AprsIsClient se il collegamento è attivo
        self.lora = None             # lora.LoraSource se il modulo LoRa è attivo
        self.publisher = None        # geopin.GeopinPublisher, gestito da chi crea il ricevitore

    @property
    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    @property
    def fixed(self) -> int:
        """Frame recuperati con la correzione dei bit (demodulatore interno)."""
        return self.demod.stats["fixed"] if self.demod else 0

    @property
    def frames(self) -> int:
        return self.handler.frames if self.handler else 0

    def start(self, cfg: Config) -> None:
        if self.running:
            raise RuntimeError("ricezione già attiva")
        self.stop_evt.clear()
        self.error, self.peak, self.finished_wav = None, 0.0, False
        self.demod = None
        self.aprsis = None
        gate = None
        if cfg.aprsis_enabled:
            from .aprsis import AprsIsClient, valid_callsign
            home = cfg.home_tuple()
            flt = (f"r/{home[0]:.3f}/{home[1]:.3f}/{cfg.aprsis_radius_km}"
                   if home and cfg.aprsis_radius_km > 0 else "")
            igate = cfg.igate_enabled and (cfg.source in ("audio", "kiss") or cfg.lora_enabled)
            if cfg.igate_enabled and cfg.source == "wav":
                log.warning("iGate: i pacchetti del file registrato non vengono inoltrati ad APRS-IS")
            # il passcode serve sia all'iGate sia ad annunciare la stazione: senza, il server
            # accetta solo la ricezione e la stazione non compare su aprs.fi
            # e ai messaggi: per inviarli il collegamento deve essere verificato
            pcode = cfg.aprsis_passcode if (igate or cfg.aprsis_beacon or cfg.messages_enabled) else "-1"
            if cfg.messages_enabled and valid_callsign(cfg.aprsis_call):
                # i messaggi per la propria stazione arrivano anche da lontano (filtro "g/")
                call = cfg.aprsis_call.strip().upper()
                calls = [call] + ([call.split("-")[0]] if "-" in call else [])
                calls += [c.strip().upper() for c in cfg.messages_calls.split(",") if c.strip()]
                flt = (flt + " g/" + "/".join(dict.fromkeys(calls))).strip()
            self.aprsis = AprsIsClient(cfg.aprsis_call, pcode,
                                       cfg.aprsis_server, cfg.aprsis_port, flt, igate, home,
                                       station_type=cfg.station_type, comment=cfg.station_comment,
                                       equipment=cfg.station_equipment, beacon=cfg.aprsis_beacon)
            self.aprsis.home_fn = lambda: self.store.home     # segue la posizione scelta sulla mappa
            if cfg.igate_enabled and not igate and cfg.source != "wav":
                self.aprsis.igate_note = "iGate non attivo: serve una sorgente radio (scheda audio, Direwolf o LoRa)"
            if igate and cfg.source in ("audio", "kiss"):
                gate = self.aprsis.gate            # VHF: solo da radio in diretta, mai da file
        self.handler = PacketHandler(self.store, cfg.filters, cfg.log_file or None, gate)
        if self.publisher is not None:
            # i pacchetti di un file registrato non sono ricezioni in diretta: non si pubblicano
            self.publisher.publish_rf = cfg.source != "wav"
            self.handler.publish = self.publisher.add
        if self.aprsis and self.aprsis.igate and cfg.lora_enabled:
            self.handler.lora_gate = self.aprsis.gate
        if self.aprsis:
            self.aprsis.on_line = self.handler.internet_line
            self.aprsis.start()
        self.lora = None
        if cfg.lora_enabled:
            from .lora import LoraSource
            self.lora = LoraSource(cfg.lora_mode, self.handler.lora_line, cfg.lora_serial, cfg.lora_baud,
                                   cfg.lora_host, cfg.lora_port)
            self.lora.start()
        self.thread = threading.Thread(target=self._run, args=(cfg,), daemon=True)
        self.thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self.stop_evt.set()
        if self.thread:
            self.thread.join(timeout)
        if self.aprsis:
            self.aprsis.stop()
        if self.lora:
            self.lora.stop()

    def _run(self, cfg: Config) -> None:
        source = {"audio": self._audio, "kiss": self._kiss, "wav": self._wav,
                  "none": self._none}[cfg.source]
        try:
            source(cfg)
            if not self.error:
                self.status = "Fermo"
        except Exception as e:                    # mostrato nella GUI
            self.error = f"{type(e).__name__}: {e}"
            self.status = "Errore"
            log.error("Ricezione interrotta: %s", self.error)
        finally:
            if self.handler:
                self.handler.close()
            if self.aprsis:                       # la ricezione è finita: anche APRS-IS e LoRa
                self.aprsis.stop()
            if self.lora:
                self.lora.stop()

    def _none(self, cfg: Config) -> None:
        """Nessuna sorgente VHF: restano attivi LoRa e/o APRS-IS."""
        self.status = "Solo LoRa" if cfg.lora_enabled else "Solo APRS-IS"
        while not self.stop_evt.wait(0.5):
            pass

    def _frame_cb(self, has_fcs: bool):
        return lambda b: self.handler(decode_frame(b, has_fcs=has_fcs))

    # ---------------------------------------------------------- sorgenti

    def _audio(self, cfg: Config) -> None:
        import sounddevice as sd
        demod = self.demod = make_demodulator(cfg.rate, self._frame_cb(True), cfg.demod_profile, cfg.fix_bits)
        blocks: queue.Queue = queue.Queue(maxsize=200)

        def cb(indata, frames, t, status):
            if status:
                log.warning("audio: %s", status)
            try:
                blocks.put_nowait(indata[:, cfg.channel].copy())
            except queue.Full:
                log.warning("audio: elaborazione troppo lenta, blocco perso")

        dev = cfg.device.strip()
        device = int(dev) if dev.isdigit() else (dev or None)
        with sd.InputStream(device=device, samplerate=cfg.rate, channels=cfg.channel + 1,
                            dtype="float32", blocksize=cfg.rate // 10, callback=cb):
            self.status = "In ascolto (scheda audio)"
            log.info("In ascolto sulla scheda audio %s a %d Hz", device if device is not None else "predefinita", cfg.rate)
            while not self.stop_evt.is_set():
                try:
                    blk = blocks.get(timeout=0.3)
                except queue.Empty:
                    continue
                self.peak = float(np.max(np.abs(blk)))
                demod.process(blk)

    def _kiss(self, cfg: Config) -> None:
        self.status = f"KISS {cfg.kiss_host}:{cfg.kiss_port}"
        run_kiss_tcp(cfg.kiss_host, cfg.kiss_port, self._frame_cb(False),
                     stop=self.stop_evt.is_set)

    def _wav(self, cfg: Config) -> None:
        with wave.open(cfg.wav, "rb") as w:
            if w.getsampwidth() != 2:
                raise ValueError("il WAV deve essere PCM a 16 bit")
            rate, nch = w.getframerate(), w.getnchannels()
            demod = self.demod = make_demodulator(rate, self._frame_cb(True), cfg.demod_profile, cfg.fix_bits)
            ch = min(cfg.channel, nch - 1)
            chunk = rate // 10 if cfg.realtime else rate
            self.status = "Decodifica file WAV"
            log.info("Decodifico %s (%d Hz, %d canali)", cfg.wav, rate, nch)
            while not self.stop_evt.is_set():
                data = w.readframes(chunk)
                if not data:
                    break
                x = np.frombuffer(data, dtype="<i2").reshape(-1, nch)[:, ch].astype(np.float32) / 32768
                self.peak = float(np.max(np.abs(x)))
                demod.process(x)
                if cfg.realtime:
                    time.sleep(chunk / rate)
            demod.flush()
        self.peak = 0.0
        self.finished_wav = not self.stop_evt.is_set()
        if self.finished_wav:
            log.info("Fine del file WAV")
