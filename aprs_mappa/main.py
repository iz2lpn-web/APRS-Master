#!/usr/bin/env python3
# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Ricevitore APRS con mappa OpenStreetMap - riga di comando.
Per il pannello di controllo grafico usa:  aprs-mappa-gui

Esempi:
  aprs-mappa --source audio                     # demodulatore interno, scheda audio
  aprs-mappa --list-devices
  aprs-mappa --source kiss --kiss-port 8001      # Direwolf in ascolto KISS TCP
  aprs-mappa --source wav --wav registrazione.wav
  aprs-mappa --source audio --filter N0CALL-9 --home 44.49,11.34
Poi apri http://localhost:8080

Come servizio senza finestra (Raspberry Pi), con il pannello di controllo nel browser:
  aprs-mappa --servizio                          # configurazione salvata (~/.aprs_mappa.json)
  aprs-mappa --panel-password                    # password per il pannello dalla rete locale
  aprs-mappa --installa-servizio                 # avvio automatico con systemd
Poi apri http://INDIRIZZO-DEL-RASPBERRY:8080/pannello
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
import webbrowser
from pathlib import Path

from . import COPYRIGHT, LICENSE_NAME, __version__
from .receiver import CONFIG_PATH, Config, Receiver, list_input_devices
from .server import StationStore, WebServer
from .storico import DB_PATH, History

log = logging.getLogger("aprs")


def _out(text: str) -> None:
    # negli eseguibili "a finestra" (Windows) stdout può non esistere
    if sys.stdout is not None:
        print(text, flush=True)


def export_gpx(a) -> int:
    from .storico import to_gpx
    if not Path(a.history_db).is_file():
        _out(f"Storico non trovato: {a.history_db}")
        return 1
    h = History(a.history_db)
    since = time.time() - a.hours * 3600 if a.hours > 0 else None
    tracks = h.tracks(since=since, stations=[c.upper() for c in a.station] or None)
    h.close()
    if not tracks:
        _out("Nessuna posizione nel periodo richiesto.")
        return 1
    gpx, st = to_gpx(tracks, f"APRS Master {__version__}")
    Path(a.export_gpx).write_text(gpx, encoding="utf-8")
    _out(f"Scritto {a.export_gpx}: {st['tracks']} tracce ({st['points']} punti), "
         f"{st['waypoints']} stazioni fisse.")
    return 0


def service_commands(a) -> int:
    """--panel-password, --installa-servizio, --servizio."""
    from .lingua import set_language
    path = Path(a.config)
    cfg = Config.load(path)
    if a.geopin_code:
        cfg.geopin_enabled, cfg.geopin_url, cfg.geopin_key = True, a.geopin_url, a.geopin_key
        cfg.save(path)
        _out(f"Geopin collegato: {a.geopin_url}")
    if a.lang:
        cfg.language = a.lang
        cfg.save(path)
    set_language(cfg.language)
    if a.panel_password is not None:
        from .server import hash_password
        pw = a.panel_password
        if pw == "":
            import getpass
            pw = getpass.getpass("Nuova password del pannello (almeno 8 caratteri, vuota = toglila): ")
            if pw and getpass.getpass("Ripetila: ") != pw:
                _out("Le due password non coincidono.")
                return 1
        if pw == "-":
            pw = ""
        if pw and len(pw) < 8:
            _out("La password deve avere almeno 8 caratteri.")
            return 1
        cfg.panel_password = hash_password(pw) if pw else ""
        cfg.save(path)
        _out("Password del pannello impostata." if pw else "Password del pannello tolta: "
             "il pannello si comanda solo dal computer stesso.")
        if not a.servizio:
            return 0
    if a.installa_servizio:
        from .servizio import systemd_unit
        unit = systemd_unit(path.resolve())
        target = Path("/etc/systemd/system/aprs-master.service")
        try:
            target.write_text(unit, encoding="utf-8")
        except OSError:
            local = Path("aprs-master.service")
            local.write_text(unit, encoding="utf-8")
            _out(f"Scritto {local.resolve()}. Per installarlo (serve l'amministratore):\n"
                 f"  sudo cp {local.resolve()} {target}\n"
                 "  sudo systemctl daemon-reload && sudo systemctl enable --now aprs-master")
            return 0
        _out(f"Scritto {target}. Per avviarlo ora e a ogni accensione:\n"
             "  sudo systemctl daemon-reload && sudo systemctl enable --now aprs-master")
        return 0
    if not path.is_file():
        cfg.save(path)
        _out(f"Creata la configurazione {path}: completala dal pannello web.")
    from .servizio import Service
    Service(path).run()
    return 0


def self_test() -> bool:
    """Controlli rapidi usati anche dalla compilazione automatica: verifica che
    nell'installazione o nell'eseguibile ci sia tutto il necessario."""
    import numpy as np
    from .afsk import AFSKModulator, make_demodulator
    from .ax25 import encode_frame
    from .server import STATIC
    from . import aprs
    ok = True

    def check(name, cond):
        nonlocal ok
        ok = ok and bool(cond)
        _out(f"{'OK  ' if cond else 'ERR '} {name}")

    fs = 48000
    mod = AFSKModulator(fs, amplitude=0.3)
    frames = [encode_frame("N0CALL-9", "APZ001", ["WIDE1-1"], f"!44{i}0.00N/01120.00E>test".encode())
              for i in range(3)]
    sig = np.concatenate([np.zeros(fs // 4)] + [np.concatenate([mod.modulate(f), np.zeros(fs // 4)])
                                                 for f in frames])
    sig = sig + np.random.default_rng(0).normal(0, 0.02, sig.size)
    got = []
    d = make_demodulator(fs, got.append)
    for i in range(0, sig.size, 4800):
        d.process(sig[i:i + 4800])
    d.flush()
    check("demodulatore AFSK", got == frames)
    p = aprs.parse("N0CALL", "APZ001", b"!4429.64N/01120.58E>")
    check("parser APRS", p.has_position and abs(p.lat - 44.494) < 1e-3)
    try:
        h = History(":memory:")
        h.record(time.time(), p, "x")
        check("storico SQLite", len(h.positions()) == 1)
        h.close()
    except Exception as e:
        check(f"storico SQLite ({e})", False)
    check("pagina della mappa, Leaflet e icone", all((STATIC / f).is_file() for f in
          ("index.html", "icona-256.png", "leaflet/leaflet.js", "leaflet/leaflet.css")))
    from .aprsis import gate_line, passcode
    from .ax25 import AX25Frame
    fr = AX25Frame(dest="APZ001", source="N0CALL", path=["NOGATE"], info=b"!x")
    from .lora import StreamParser
    got = []
    StreamParser(got.append).feed(b"Rx: <\xff\x01N0CALL-7>APLRT1,WIDE1-1:!4500.00N/00900.00E>LoRa\r\n")
    check("lettura pacchetti LoRa", got == ["N0CALL-7>APLRT1,WIDE1-1:!4500.00N/00900.00E>LoRa"])
    try:
        from serial.tools import list_ports  # noqa: F401
        check("pyserial (modulo LoRa su porta seriale)", True)
    except ImportError:
        check("pyserial (modulo LoRa su porta seriale)", False)
    try:
        import certifi  # noqa: F401
        check("certifi (certificati HTTPS per Geopin e mappe)", True)
    except ImportError:
        check("certifi (certificati HTTPS per Geopin e mappe)", False)
    check("regole iGate APRS-IS", passcode("N0CALL") == 13023 and gate_line(fr, "N0CALL-10") is None)
    try:
        from .geopin import GeopinQueue, publish_url
        q = GeopinQueue(":memory:")
        q.add(time.time(), "rf", "N0CALL>APZ001:!x")
        check("coda Geopin", q.count() == 1 and publish_url("geopin.it").endswith("/api/publish.php"))
        q.close()
    except Exception as e:
        check(f"coda Geopin ({e})", False)
    try:
        from .messaggi import MessageCenter
        from .servizio import Engine
        st = StationStore()
        e = Engine(st, Receiver(st), ":memory:")
        e.configure(Config(aprsis_call="N0CALL-10"))
        sent = []
        e.messages.sender, e.messages.can_send = (lambda line: sent.append(line) or True), (lambda: True)
        line = "N0CALL-1>APRS,TCPIP*::N0CALL-10:prova{7"
        st.add(aprs.parse_tnc2(line), line, origin="is")
        check("messaggi APRS (ricezione e conferma)",
              sent == ["N0CALL-10>APZRAP,TCPIP*::N0CALL-1 :ack7"] and e.messages.unread() == 1)
        e.close()
        assert isinstance(e.messages, MessageCenter)
    except Exception as ex:
        check(f"messaggi APRS ({ex})", False)
    wx = aprs.parse("N0CALL", "APRS", b"_10090556c220s004g005t077r000p000P000h50b10132")
    tl = aprs.parse("N0CALL", "APRS", b"T#005,199,000,255,073,123,01101001")
    check("meteo e telemetria", wx.wx and wx.wx["temp"] == 25.0 and tl.telemetry and tl.telemetry["seq"] == "005")
    check("pagine della mappa e del pannello", all((STATIC / f).is_file() for f in
          ("pannello.html", "grafici.js", "markercluster/leaflet.markercluster.js")))
    from .lingua_en import EN
    check("traduzione inglese", len(EN) > 300)
    from . import license_text
    lic = license_text()
    check("testo della licenza GPL v3", lic is not None and "Version 3, 29 June 2007" in lic)
    try:
        import tkinter  # noqa: F401
        check("Tkinter", True)
    except ImportError:
        check("Tkinter (serve solo per il pannello grafico)", False)
    _out("Autotest superato" if ok else "Autotest NON superato")
    return ok


def main() -> None:
    ap = argparse.ArgumentParser(prog="aprs-mappa", description="Ricevitore APRS con mappa OpenStreetMap",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["audio", "kiss", "wav", "none"], default="audio",
                    help="audio = demodulatore interno; kiss = TNC/Direwolf; wav = file; "
                         "none = nessuna sorgente VHF (solo LoRa e/o APRS-IS)")
    ap.add_argument("--device", default="", help="scheda audio (indice o parte del nome)")
    ap.add_argument("--rate", type=int, default=48000, help="frequenza di campionamento")
    ap.add_argument("--channel", type=int, default=0, help="canale audio (0 = sinistro)")
    ap.add_argument("--list-devices", action="store_true", help="elenca le schede audio")
    ap.add_argument("--kiss-host", default="127.0.0.1")
    ap.add_argument("--kiss-port", type=int, default=8001)
    ap.add_argument("--wav", default="", help="file WAV da decodificare")
    ap.add_argument("--realtime", action="store_true", help="WAV a velocità reale (demo)")
    ap.add_argument("--web-port", type=int, default=8080)
    ap.add_argument("--bind", default="127.0.0.1", help="0.0.0.0 per accesso dalla rete locale")
    ap.add_argument("--home", default="", help="posizione stazione ricevente 'lat,lon'")
    ap.add_argument("--filter", action="append", default=[],
                    help="mostra solo nominativi che iniziano così (ripetibile)")
    ap.add_argument("--log", default="", help="salva tutti i pacchetti grezzi su file")
    ap.add_argument("--demod", choices=["completo", "leggero"], default="completo",
                    help="profilo del demodulatore interno (leggero = meno CPU)")
    ap.add_argument("--no-fix", action="store_true", help="disattiva la correzione dei bit errati")
    ap.add_argument("--no-browser", action="store_true")
    h = ap.add_argument_group("storico")
    h.add_argument("--no-history", action="store_true", help="non salvare lo storico dei pacchetti")
    h.add_argument("--history-db", default=str(DB_PATH), help=f"file dello storico (predefinito {DB_PATH})")
    h.add_argument("--history-days", type=int, default=90, help="giorni da conservare (0 = per sempre)")
    h.add_argument("--load-hours", type=int, default=24, help="ore di storico mostrate all'avvio")
    h.add_argument("--export-gpx", metavar="FILE", help="esporta le tracce dallo storico ed esci")
    h.add_argument("--station", action="append", default=[], help="con --export-gpx: solo questa stazione (ripetibile)")
    h.add_argument("--hours", type=float, default=0, help="con --export-gpx: ultime N ore (0 = tutto)")
    lg = ap.add_argument_group("LoRa APRS (modulo esterno, in parallelo alla sorgente VHF)")
    lg.add_argument("--lora-serial", default="", help="porta seriale del modulo LoRa (es. COM5, /dev/ttyUSB0)")
    lg.add_argument("--lora-baud", type=int, default=115200)
    lg.add_argument("--lora-tcp", default="", metavar="HOST:PORTA", help="modulo LoRa in rete (TCP)")
    sg = ap.add_argument_group("stazione ricevente")
    sg.add_argument("--station-type", choices=["igate_vhf", "igate_lora", "fissa", "portatile"], default="igate_vhf",
                    help="tipo di dispositivo: determina il simbolo APRS dell'annuncio e sulla mappa")
    sg.add_argument("--station-comment", default="", help="commento dell'annuncio (max 43 caratteri, senza accenti)")
    sg.add_argument("--station-equipment", default="",
                    help="descrizione libera dell'apparato, inviata come stato (max 62 caratteri)")
    i = ap.add_argument_group("APRS-IS")
    i.add_argument("--aprsis-call", default="", help="nominativo-SSID per APRS-IS (es. IZ2LPN-10); attiva il collegamento")
    i.add_argument("--aprsis-pass", default="", help="passcode APRS-IS (serve per annunciare la stazione e per l'iGate)")
    i.add_argument("--aprsis-server", default="rotate.aprs2.net")
    i.add_argument("--aprsis-port", type=int, default=14580)
    i.add_argument("--aprsis-radius", type=int, default=100,
                   help="km intorno a --home dei pacchetti ricevuti da APRS-IS per il confronto (0 = nessuno)")
    i.add_argument("--igate", action="store_true", help="inoltra ad APRS-IS i pacchetti ricevuti via radio (iGate RX)")
    i.add_argument("--no-beacon", action="store_true",
                   help="non annunciare la stazione su APRS-IS (con il passcode l'annuncio è automatico)")
    g = ap.add_argument_group("Geopin (pubblicazione sul portale)")
    g.add_argument("--geopin-url", default="", help="indirizzo del portale Geopin; attiva la pubblicazione")
    g.add_argument("--geopin-key", default="", help="chiave del ricevitore, creata sul portale")
    g.add_argument("--geopin-interval", type=int, default=5, help="minuti tra una pubblicazione e l'altra")
    g.add_argument("--geopin-test", action="store_true", help="prova collegamento e chiave ed esci")
    m = ap.add_argument_group("mappe")
    m.add_argument("--map-url", default="", help="server di mappe con cache su disco: https://.../{z}/{x}/{y}.png")
    m.add_argument("--map-attribution", default="", help="attribuzione da mostrare per --map-url")
    m.add_argument("--mbtiles", default="", help="mappa offline da file MBTiles raster")
    m.add_argument("--map-cache-mb", type=int, default=500, help="dimensione massima della cache delle mappe")
    sv = ap.add_argument_group("servizio (senza finestra, con pannello web)")
    sv.add_argument("--servizio", action="store_true",
                    help="avvia con la configurazione salvata e il pannello di controllo su /pannello")
    sv.add_argument("--config", default=str(CONFIG_PATH), help=f"file della configurazione (predefinito {CONFIG_PATH})")
    sv.add_argument("--panel-password", nargs="?", const="", default=None, metavar="PASSWORD",
                    help="imposta la password del pannello web ed esci (senza valore la chiede; '-' la toglie)")
    sv.add_argument("--installa-servizio", action="store_true",
                    help="scrive il file systemd per l'avvio automatico (Linux) ed esci")
    sv.add_argument("--geopin-code", default="", metavar="CODICE",
                    help="codice di collegamento mostrato da Geopin (indirizzo e chiave insieme)")
    sv.add_argument("--lang", choices=["auto", "it", "en"], default=None, help="lingua dell'interfaccia")
    ap.add_argument("--self-test", action="store_true",
                    help="verifica rapida dell'installazione (demodulatore, storico, mappa) ed esci")
    ap.add_argument("--version", action="version",
                    version=f"%(prog)s {__version__}\n{COPYRIGHT}\nLicenza: {LICENSE_NAME}. "
                            "Questo programma è fornito SENZA ALCUNA GARANZIA.")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")

    from .lingua import set_language
    if a.self_test:
        sys.exit(0 if self_test() else 1)
    if a.geopin_code:
        from .geopin import decode_link
        d = decode_link(a.geopin_code)
        if d is None:
            ap.error("--geopin-code: codice di collegamento Geopin non valido")
        a.geopin_url, a.geopin_key = d
    if a.panel_password is not None or a.installa_servizio or a.servizio:
        sys.exit(service_commands(a))
    set_language(a.lang or Config.load().language)
    if a.export_gpx:
        sys.exit(export_gpx(a))
    if a.geopin_url and not a.geopin_key:
        ap.error("--geopin-url richiede --geopin-key")
    if a.geopin_test:
        from .geopin import GeopinError, GeopinPublisher
        if not a.geopin_url:
            ap.error("--geopin-test richiede --geopin-url e --geopin-key")
        p = GeopinPublisher(a.geopin_url, a.geopin_key)
        try:
            _out(p.test())
            ok = True
        except GeopinError as e:
            _out(f"Collegamento non riuscito: {e}")
            ok = False
        p.stop(final=False)
        sys.exit(0 if ok else 1)
    if a.list_devices:
        for i, name in list_input_devices():
            print(f"{i:3d}  {name}")
        return
    if a.source == "wav" and not a.wav:
        ap.error("--source wav richiede --wav FILE")

    cfg = Config(source=a.source, device=a.device, rate=a.rate, channel=a.channel,
                 kiss_host=a.kiss_host, kiss_port=a.kiss_port, wav=a.wav, realtime=a.realtime,
                 web_port=a.web_port, bind=a.bind, home=a.home, filters=a.filter, log_file=a.log,
                 demod_profile=a.demod, fix_bits=not a.no_fix,
                 aprsis_enabled=bool(a.aprsis_call), aprsis_call=a.aprsis_call, aprsis_passcode=a.aprsis_pass,
                 aprsis_server=a.aprsis_server, aprsis_port=a.aprsis_port, aprsis_radius_km=a.aprsis_radius,
                 igate_enabled=a.igate, aprsis_beacon=not a.no_beacon,
                 station_type=a.station_type, station_comment=a.station_comment.strip(),
                 station_equipment=a.station_equipment.strip(),
                 lora_enabled=bool(a.lora_serial or a.lora_tcp),
                 lora_mode="tcp" if a.lora_tcp else "serial", lora_serial=a.lora_serial, lora_baud=a.lora_baud,
                 lora_host=a.lora_tcp.rpartition(":")[0], lora_port=int(a.lora_tcp.rpartition(":")[2] or 8001)
                 if a.lora_tcp.rpartition(":")[2].isdigit() else 8001)
    if a.lora_tcp and not a.lora_tcp.rpartition(":")[2].isdigit():
        ap.error("--lora-tcp va scritto come HOST:PORTA, es. 192.168.1.50:8001")
    if a.source == "none" and not (a.lora_serial or a.lora_tcp or a.aprsis_call):
        ap.error("--source none richiede --lora-serial, --lora-tcp o --aprsis-call")
    from .aprsis import comment_error, equipment_error
    err = comment_error(a.station_comment.strip())
    if err:
        ap.error("--station-comment: " + err)
    err = equipment_error(a.station_equipment.strip())
    if err:
        ap.error("--station-equipment: " + err)
    if a.igate:
        from .aprsis import valid_callsign
        if not valid_callsign(a.aprsis_call) or not a.aprsis_pass:
            ap.error("--igate richiede --aprsis-call con un nominativo valido e --aprsis-pass")
    try:
        home = cfg.home_tuple()
    except ValueError:
        ap.error("--home deve essere nel formato lat,lon (es. 44.49,11.34)")

    history = None
    if not a.no_history:
        try:
            history = History(a.history_db)
            history.prune(a.history_days)
        except Exception as e:
            log.error("Storico non disponibile (%s): continuo senza", e)
            history = None
    from .mappe import TileService
    mode = "mbtiles" if a.mbtiles else "custom" if a.map_url else "osm"
    tiles = TileService(mode, a.map_url, a.map_attribution, a.mbtiles, a.map_cache_mb,
                        contact=a.aprsis_call.split("-")[0])
    if tiles.error:
        log.error("Mappe: %s. Uso OpenStreetMap.", tiles.error)
    store = StationStore(home, history=history, tiles=tiles)
    store.set_station(a.station_type, a.station_comment, a.aprsis_call, a.station_equipment)
    if history is not None:
        n = store.load_history(a.load_hours)
        if n:
            log.info("Ricaricate dallo storico %d posizioni delle ultime %d ore", n, a.load_hours)
    web = WebServer(store)
    web.ensure(cfg.bind, cfg.web_port)
    log.info("Mappa disponibile su %s", web.url)
    if not a.no_browser:
        threading.Timer(1.0, webbrowser.open, (web.url,)).start()

    rx = Receiver(store)
    from .servizio import Engine
    saved = Config.load(Path(a.config))
    for k in ("alert_watch", "alert_zones", "alert_emergency", "alert_messages", "alert_quiet_min",
              "messages_calls", "panel_password"):
        setattr(cfg, k, getattr(saved, k))       # avvisi e password restano quelli salvati
    store.panel_password = cfg.panel_password
    engine = Engine(store, rx)
    engine.configure(cfg)
    engine.callbacks.append(lambda kind, ev: log.info("%s: %s", "Avviso" if kind == "alert" else "Messaggio",
                                                      ev.get("text", "")))
    publisher = None
    if a.geopin_url:
        from .geopin import GeopinPublisher, receiver_status
        publisher = GeopinPublisher(a.geopin_url, a.geopin_key, a.geopin_interval,
                                    status_fn=lambda: receiver_status(rx, cfg))
        publisher.start()
        rx.publisher = publisher
        log.info("Geopin: pubblicazione ogni %d minuti su %s", a.geopin_interval, publisher.url)
    rx.start(cfg)
    try:
        while rx.running:
            time.sleep(0.5)
        if publisher is not None:              # ricezione finita: si pubblica solo mentre è attiva
            log.info("Geopin: invio dei pacchetti rimasti…")
            publisher.stop(final=True)
            publisher = rx.publisher = None
        if rx.finished_wav:
            log.info("La mappa resta disponibile (Ctrl+C per uscire)")
            threading.Event().wait()
    except KeyboardInterrupt:
        rx.stop()
        print("\nUscita.")
    finally:
        if publisher is not None:
            log.info("Geopin: invio dei pacchetti rimasti…")
            publisher.stop(final=True)


if __name__ == "__main__":
    main()
