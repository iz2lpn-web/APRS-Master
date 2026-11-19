# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Test automatici.  Esecuzione dalla cartella principale:
    python -m unittest discover -s tests -v
"""
import json
import os
import math
import time
import unittest
import urllib.error
import urllib.request

import numpy as np

from aprs_mappa import aprs
from aprs_mappa.afsk import AFSKDemodulator, AFSKModulator
from aprs_mappa.ax25 import crc16_x25, decode_frame, encode_frame
from aprs_mappa.genera_test import compressed, mic_e, uncompressed
from aprs_mappa.kiss import _unescape
from aprs_mappa.server import StationStore, WebServer


class TestAX25(unittest.TestCase):
    def test_crc_valore_di_controllo(self):
        # valore di controllo standard di CRC-16/X-25
        self.assertEqual(crc16_x25(b"123456789"), 0x906E)

    def test_codifica_e_decodifica(self):
        raw = encode_frame("N0CALL-9", "APZ001", ["WIDE1-1*", "WIDE2-1"], b"!test")
        f = decode_frame(raw)
        self.assertEqual((f.source, f.dest, f.path, f.info),
                         ("N0CALL-9", "APZ001", ["WIDE1-1*", "WIDE2-1"], b"!test"))
        self.assertEqual(f.tnc2(), "N0CALL-9>APZ001,WIDE1-1*,WIDE2-1:!test")

    def test_crc_errato_scartato(self):
        raw = bytearray(encode_frame("N0CALL", "APZ001", [], b"!test"))
        raw[5] ^= 0x10
        self.assertIsNone(decode_frame(bytes(raw)))

    def test_kiss_escape(self):
        self.assertEqual(_unescape(b"a\xdb\xdcb\xdb\xddc"), b"a\xc0b\xdbc")


class TestParserAPRS(unittest.TestCase):
    def test_non_compressa_con_rotta_e_quota(self):
        p = aprs.parse("N0CALL", "APZ001", b"!4429.64N/01120.58E>090/036/A=001000 Ciao")
        self.assertAlmostEqual(p.lat, 44.494, places=4)
        self.assertAlmostEqual(p.lon, 11.343, places=3)
        self.assertEqual((p.symbol_table, p.symbol_code, p.course), ("/", ">", 90))
        self.assertAlmostEqual(p.speed_kmh, 66.7, places=1)
        self.assertAlmostEqual(p.altitude_m, 304.8, places=1)
        self.assertEqual(p.comment, "Ciao")

    def test_emisfero_sud_ovest(self):
        p = aprs.parse("N0CALL", "APZ001", b"=3351.00S/15112.00W-")
        self.assertLess(p.lat, 0)
        self.assertLess(p.lon, 0)

    def test_compressa(self):
        p = aprs.parse("N0CALL", "APZ001", compressed(44.5, 11.3, "/", ">", "x").encode())
        self.assertAlmostEqual(p.lat, 44.5, places=4)
        self.assertAlmostEqual(p.lon, 11.3, places=4)

    def test_mic_e(self):
        dest, info = mic_e(44.494, 11.343, 25, 180, ">", "/", "test")
        p = aprs.parse("N0CALL-9", dest, info.encode("latin-1"))
        self.assertEqual(p.kind, "Mic-E")
        self.assertAlmostEqual(p.lat, 44.494, places=3)
        self.assertAlmostEqual(p.lon, 11.343, places=3)
        self.assertEqual(p.course, 180)
        self.assertAlmostEqual(p.speed_kmh, 25 * 1.852, places=1)
        self.assertEqual(p.mic_e_status, "In viaggio")

    def test_oggetto(self):
        p = aprs.parse("N0CALL", "APZ001", b";RIFUGIO  *111111z4430.00N/01120.00E;Nota")
        self.assertEqual((p.kind, p.object_name), ("Oggetto", "RIFUGIO"))
        self.assertAlmostEqual(p.lon, 11.3333, places=3)

    def test_nmea_gprmc(self):
        p = aprs.parse("N0CALL", "GPS", b"$GPRMC,123519,A,4429.64,N,01120.58,E,010.0,084.4,230394,,*6A")
        self.assertAlmostEqual(p.lat, 44.494, places=3)
        self.assertEqual(p.course, 84)

    def test_terze_parti(self):
        p = aprs.parse("IGATE", "APZ001", b"}N0CALL-5>APZ001,TCPIP:!4429.64N/01120.58E-")
        self.assertEqual(p.source, "N0CALL-5")
        self.assertTrue(p.has_position)

    def test_tipi_senza_posizione(self):
        self.assertEqual(aprs.parse("A", "B", b">In onda").kind, "Stato")
        self.assertEqual(aprs.parse("A", "B", b":N0CALL   :ciao").kind, "Messaggio")
        self.assertFalse(aprs.parse("A", "B", b"spazzatura").has_position)


class TestDemodulatore(unittest.TestCase):
    def _segnale(self, rate, snr_db, n=5):
        mod = AFSKModulator(rate, amplitude=0.3)
        rng = np.random.default_rng(7)
        frames, parts = [], [np.zeros(rate // 2)]
        for i in range(n):
            info = uncompressed(44.4 + i / 100, 11.3, "/", ">", 90, 10, 100, f" n{i}")
            f = encode_frame("N0CALL-9", "APZ001", ["WIDE1-1"], info.encode())
            frames.append(f[:-2])
            parts += [mod.modulate(f), np.zeros(rate // 2)]
        sig = np.concatenate(parts)
        rms = math.sqrt(np.mean(sig[sig != 0] ** 2))
        return frames, sig + rng.normal(0, rms / 10 ** (snr_db / 20), sig.size)

    def _decodifica(self, rate, sig, block):
        out = []
        demod = AFSKDemodulator(rate, lambda b: out.append(b[:-2]) if decode_frame(b) else None)
        for i in range(0, len(sig), block):
            demod.process(sig[i:i + block])
        return out

    def test_frequenze_di_campionamento(self):
        for rate in (48000, 44100, 22050):
            with self.subTest(rate=rate):
                frames, sig = self._segnale(rate, 10)
                self.assertEqual(self._decodifica(rate, sig, rate // 10), frames)

    def test_blocchi_piccoli(self):
        # la suddivisione in blocchi non deve perdere pacchetti
        frames, sig = self._segnale(48000, 10)
        self.assertEqual(self._decodifica(48000, sig, 333), frames)


class TestCorrezione(unittest.TestCase):
    def test_coppia_adiacente_e_bit_singolo(self):
        from aprs_mappa.ax25 import _flip, correct_frame
        f = encode_frame("N0CALL-9", "APZ001", ["WIDE1-1"], b"!4429.64N/01120.58E>prova")
        for bits in ((100,), (200, 201), (len(f) * 8 - 3,)):
            with self.subTest(bits=bits):
                r = correct_frame(_flip(f, *bits))
                self.assertIsNotNone(r)
                self.assertEqual(r[0], f)

    def test_nessuna_falsa_correzione_su_rumore(self):
        from aprs_mappa.ax25 import correct_frame
        rng = np.random.default_rng(3)
        fake = [correct_frame(rng.integers(0, 256, int(rng.integers(18, 120))).astype(np.uint8).tobytes())
                for _ in range(3000)]
        self.assertEqual(sum(r is not None for r in fake), 0)

    def test_demodulatore_con_de_enfasi(self):
        # de-enfasi RC 750 us: il demodulatore 1.0 la decodificava male
        fs = 48000
        mod = AFSKModulator(fs, amplitude=0.3)
        frames, parts = [], [np.zeros(fs // 2)]
        for i in range(4):
            f = encode_frame("N0CALL-9", "APZ001", [], f"!44{i}0.00N/01120.00E>de-enfasi".encode())
            frames.append(f)
            parts += [mod.modulate(f), np.zeros(fs // 2)]
        sig = np.concatenate(parts)
        fr = np.fft.rfftfreq(sig.size, 1 / fs)
        fc = 1 / (2 * math.pi * 750e-6)
        y = np.fft.irfft(np.fft.rfft(sig) / (1 + 1j * fr / fc) * (1 + 1j * 1200 / fc), sig.size)
        y += np.random.default_rng(1).normal(0, 0.03, y.size)
        for profile in ("completo", "leggero"):
            with self.subTest(profile=profile):
                from aprs_mappa.afsk import make_demodulator
                got = []
                d = make_demodulator(fs, got.append, profile)
                for i in range(0, len(y), 4800):
                    d.process(y[i:i + 4800])
                d.flush()
                self.assertEqual(got, frames)      # ognuno una sola volta, nell'ordine


class TestStorico(unittest.TestCase):
    def setUp(self):
        import tempfile
        from aprs_mappa.storico import History
        self.dir = tempfile.TemporaryDirectory()
        self.h = History(f"{self.dir.name}/storico.db")

    def tearDown(self):
        self.h.close()
        self.dir.cleanup()

    def _pos(self, call, lat, t, comment="", obj=None):
        p = aprs.AprsPacket(source=call, kind="Posizione", lat=lat, lon=11.3,
                            comment=comment, object_name=obj, altitude_m=100.0)
        self.h.record(t, p, f"{call}>APZ001:!...")

    def test_tracce_punti_e_segmenti(self):
        import xml.etree.ElementTree as ET
        from aprs_mappa.storico import to_gpx
        t0 = time.time() - 7200
        for i in range(5):
            self._pos("N0CALL-9", 44.40 + i / 100, t0 + i * 60)
        self._pos("N0CALL-9", 44.50, t0 + 4 * 60 + 3700)   # pausa > 1 h: nuovo segmento
        self._pos("N0CALL-10", 44.53, t0, comment="Digi \x1c<prova> & co")
        self.h.record(t0, aprs.AprsPacket(source="X", kind="Stato"), "X>APZ001:>ciao")
        tracks = self.h.tracks()
        self.assertEqual(set(tracks), {"N0CALL-9", "N0CALL-10"})
        gpx, st = to_gpx(tracks, "test")
        self.assertEqual((st["tracks"], st["waypoints"], st["points"]), (1, 1, 6))
        ns = {"g": "http://www.topografix.com/GPX/1/1"}
        root = ET.fromstring(gpx)                       # XML ben formato
        self.assertEqual(len(root.findall(".//g:trkseg", ns)), 2)
        self.assertEqual(root.find(".//g:wpt/g:desc", ns).text, "Digi <prova> & co")
        self.assertEqual(root.find(".//g:trkpt/g:ele", ns).text, "100.0")

    def test_filtri_e_pulizia(self):
        now = time.time()
        self._pos("VECCHIO", 44.0, now - 40 * 86400)
        self._pos("NUOVO", 44.1, now - 60)
        self.assertEqual(list(self.h.tracks(since=now - 3600)), ["NUOVO"])
        self.assertEqual(list(self.h.tracks(stations=["VECCHIO"])), ["VECCHIO"])
        self.assertEqual(self.h.prune(30), 1)
        self.assertEqual(self.h.count(), 1)

    def test_ricarica_nella_mappa(self):
        now = time.time()
        for i in range(3):
            self._pos("N0CALL-9", 44.40 + i / 100, now - 300 + i)
        self._pos("RIFUGIO", 44.5, now - 100, obj="RIFUGIO")
        store = StationStore(history=self.h)
        self.assertEqual(store.load_history(24), 4)
        self.assertEqual(len(store.stations["N0CALL-9"]["track"]), 3)
        self.assertEqual(self.h.count(), 4)             # la ricarica non duplica lo storico
        store.add(aprs.parse("N0CALL", "APZ001", b"!4429.64N/01120.58E-"), "raw")
        self.assertEqual(self.h.count(), 5)             # i nuovi pacchetti invece sì


class TestLicenzaEIcone(unittest.TestCase):
    ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent

    def test_licenza_gpl3_integrale(self):
        import hashlib
        import aprs_mappa
        data = (self.ROOT / "LICENSE").read_bytes()
        # impronta del file COPYING ufficiale di gnu.org (GPL v3, 29 giugno 2007)
        self.assertEqual(hashlib.md5(data).hexdigest(), "1ebbd3e34237af26da5dc08a4e440464")
        self.assertEqual(aprs_mappa.license_text(), data.decode("utf-8"))
        self.assertIn("SENZA ALCUNA GARANZIA", aprs_mappa.NOTICE)

    def test_intestazioni_spdx(self):
        for f in list(self.ROOT.glob("aprs_mappa/*.py")) + list(self.ROOT.glob("strumenti/*.py")):
            with self.subTest(file=f.name):
                head = f.read_text(encoding="utf-8")[:400]
                self.assertIn("SPDX-License-Identifier: GPL-3.0-or-later", head)

    def test_file_delle_icone(self):
        icone = self.ROOT / "strumenti" / "icone"
        self.assertEqual((icone / "icona.ico").read_bytes()[:4], b"\x00\x00\x01\x00")
        self.assertEqual((icone / "icona.icns").read_bytes()[:4], b"icns")
        static = self.ROOT / "aprs_mappa" / "static"
        for n in (32, 64, 256):
            self.assertEqual((static / f"icona-{n}.png").read_bytes()[:8], b"\x89PNG\r\n\x1a\n")
        self.assertIn("<svg", (static / "icona.svg").read_text(encoding="utf-8"))


class TestAprsIs(unittest.TestCase):
    def F(self, path, info, src="N0CALL-9"):
        from aprs_mappa.ax25 import AX25Frame
        return AX25Frame(dest="APZ001", source=src, path=path, info=info)

    def test_passcode_e_nominativi(self):
        from aprs_mappa.aprsis import passcode, valid_callsign
        self.assertEqual(passcode("N0CALL"), 13023)            # valore di riferimento noto
        self.assertEqual(passcode("n0call-9"), 13023)          # SSID e maiuscole non contano
        self.assertTrue(valid_callsign("IZ2LPN-10"))
        for bad in ("", "ABC", "IZ2LPN-16", "IZ2LPN-X", "TOOLONG1"):
            self.assertFalse(valid_callsign(bad), bad)

    def test_annuncio_tipo_e_commento(self):
        from aprs_mappa.aprsis import beacon_line, comment_error, default_comment
        self.assertEqual(beacon_line("IZ2LPN-10", 45.5, 9.25, "igate_lora", "LoRa RX"),
                         "IZ2LPN-10>APZRAP,TCPIP*:!4530.00NL00915.00E&LoRa RX")
        self.assertEqual(beacon_line("IZ2LPN-10", -33.5, -70.75, "fissa", "Casa"),
                         "IZ2LPN-10>APZRAP,TCPIP*:!3330.00S/07045.00W-Casa")
        self.assertTrue(beacon_line("IZ2LPN-10", 45, 9).endswith("R00900.00E&" + default_comment()))
        # arrotondamento: 45.999999 diventa 46°00.00', non 45°60.00'
        self.assertIn("!4600.00N", beacon_line("IZ2LPN-10", 45.999999, 9))
        self.assertIsNone(comment_error("iGate RX 144.800 - Direwolf"))
        for bad in ("Città", "a|b", "x" * 44):
            self.assertIsNotNone(comment_error(bad), bad)

    def test_stato_con_apparato(self):
        from aprs_mappa.aprsis import equipment_error, status_line
        self.assertEqual(status_line("iz2lpn-10", " Yaesu FT-2980 + Direwolf "),
                         "IZ2LPN-10>APZRAP,TCPIP*:>Yaesu FT-2980 + Direwolf")
        self.assertIsNone(equipment_error("x" * 62))
        for bad in ("x" * 63, "Kenwood | Raspberry", "Antenna sul tetto più alto"):
            self.assertIsNotNone(equipment_error(bad), bad)

    def test_regole_di_inoltro(self):
        from aprs_mappa.aprsis import gate_line
        g = lambda path, info: gate_line(self.F(path, info), "IZ2LPN-10")
        self.assertEqual(g(["IR2UFV*", "WIDE2-1"], b"!x"), "N0CALL-9>APZ001,IR2UFV*,WIDE2-1,qAO,IZ2LPN-10:!x")
        for path in (["NOGATE"], ["RFONLY"], ["TCPIP*"], ["TCPXX*"]):
            self.assertIsNone(g(path, b"!x"), path)
        self.assertIsNone(g([], b"?APRS?"))
        self.assertIsNone(g([], b"}A>B,TCPIP,IGATE*:!x"))           # tornerebbe in rete
        self.assertEqual(g([], b"}A-1>B,WIDE1-1:!x"), "A-1>B,WIDE1-1,qAO,IZ2LPN-10:!x")
        self.assertEqual(g([], b">testo\rcoda"), "N0CALL-9>APZ001,qAO,IZ2LPN-10:>testo")

    def test_ricevuto_direttamente(self):
        from aprs_mappa.storico import heard_direct
        self.assertTrue(heard_direct("A>B,WIDE1-1,WIDE2-1:!x"))
        self.assertFalse(heard_direct("A>B,IR2UFV*,WIDE2-1:!x"))
        self.assertIsNone(heard_direct("A>B:}C>D,TCPIP,E*:!x"))

    def test_confronto_radio_internet(self):
        store = StationStore()
        p = aprs.parse("N0CALL-9", "APZ001", b"!4429.64N/01120.58E>")
        store.add(p, "radio")
        store.add(p, "rete", origin="is")
        q = aprs.parse("IK2SOLO", "APRS", b"!4510.00N/00920.00E-")
        store.add(q, "rete", origin="is")
        st = store.stations
        self.assertTrue(st["N0CALL-9"]["rf_last"] and st["N0CALL-9"]["is_last"])
        self.assertEqual(st["N0CALL-9"]["packets"], 1)       # il doppione dalla rete non si conta
        self.assertTrue(st["IK2SOLO"]["is_last"] and not st["IK2SOLO"]["rf_last"])


class TestMappe(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.dir.cleanup()

    def test_mbtiles_offline(self):
        import sqlite3
        from aprs_mappa.mappe import TileService
        path = f"{self.dir.name}/mappa.mbtiles"
        c = sqlite3.connect(path)
        c.executescript("CREATE TABLE metadata (name TEXT, value TEXT);"
                        "CREATE TABLE tiles (zoom_level INT, tile_column INT, tile_row INT, tile_data BLOB);")
        c.executemany("INSERT INTO metadata VALUES (?, ?)",
                      [("format", "png"), ("maxzoom", "12"), ("attribution", "Mappa di prova")])
        c.execute("INSERT INTO tiles VALUES (3, 4, 2, ?)", (b"\x89PNG-tile",))   # riga TMS 2 = y 5 a z 3
        c.commit(); c.close()
        t = TileService("mbtiles", mbtiles=path, cache_dir=self.dir.name)
        self.assertEqual(t.error, "")
        self.assertEqual(t.tile(3, 4, 5), (b"\x89PNG-tile", "image/png"))
        self.assertIsNone(t.tile(3, 4, 2))
        cfg = t.client_config()
        self.assertEqual((cfg["maxZoom"], cfg["attribution"]), (12, "Mappa di prova"))

    def test_url_non_valido_e_file_mancante(self):
        from aprs_mappa.mappe import TileService
        self.assertTrue(TileService("custom", url="https://esempio.it/mappa.png").error)
        t = TileService("mbtiles", mbtiles=f"{self.dir.name}/manca.mbtiles", cache_dir=self.dir.name)
        self.assertTrue(t.error)
        self.assertEqual(t.mode, "osm")

    def test_cache_su_disco(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from aprs_mappa.mappe import TileService
        hits = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                hits.append(self.headers.get("User-Agent"))
                self.send_response(200)
                self.send_header("Cache-Control", "max-age=10")
                self.end_headers()
                self.wfile.write(b"\x89PNG-dal-server")

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}/{{z}}/{{x}}/{{y}}.png"
        t = TileService("custom", url=url, cache_dir=self.dir.name)
        self.assertEqual(t.tile(2, 1, 1)[0], b"\x89PNG-dal-server")
        self.assertEqual(t.tile(2, 1, 1)[0], b"\x89PNG-dal-server")
        self.assertEqual(len(hits), 1)                 # la seconda volta dalla cache
        self.assertTrue(hits[0].startswith("APRSMaster/"))
        srv.shutdown(); srv.server_close()
        f = next(__import__("pathlib").Path(self.dir.name).rglob("1.tile"))
        os.utime(f, (time.time(), time.time() - 1))    # scaduta, e il server non c'è più
        self.assertEqual(t.tile(2, 1, 1)[0], b"\x89PNG-dal-server")


class TestLora(unittest.TestCase):
    def test_formati_dei_firmware(self):
        from aprs_mappa.lora import extract_tnc2
        P = "N0CALL-7>APLRT1,WIDE1-1:!4500.00N/00900.00E>x"
        for line in (P, "<\xff\x01" + P, "12:00:01 LoRa Rx ---> " + P + "\r\n"):
            self.assertEqual(extract_tnc2(line), P, line)
        for line in ("RSSI: -97 dBm SNR: 6", "WiFi connesso a 192.168.1.5:80", "abcN0CALL-7>APLRT1:!x"):
            self.assertIsNone(extract_tnc2(line), line)

    def test_flusso_testo_e_kiss(self):
        from aprs_mappa.lora import StreamParser
        got = []
        p = StreamParser(got.append)
        p.feed(b"avvio...\r\nN0CALL-7>APLRT1:!45"); p.feed(b"00.00N/00900.00E>a\r\n")
        self.assertEqual(got, ["N0CALL-7>APLRT1:!4500.00N/00900.00E>a"])
        got.clear()
        p = StreamParser(got.append)
        ax = encode_frame("N0CALL-8", "APLRT1", ["WIDE1-1"], b"!4500.00N/00900.00E>b", with_fcs=False)
        p.feed(b"\xc0\x00" + ax + b"\xc0\xc0\x00<\xff\x01N0CALL-9>APLRT1:!4500.00N/00900.00E>c\xc0")
        self.assertEqual(got, ["N0CALL-8>APLRT1,WIDE1-1:!4500.00N/00900.00E>b",
                               "N0CALL-9>APLRT1:!4500.00N/00900.00E>c"])

    def test_porta_seriale(self):
        try:
            import pty
            import serial  # noqa: F401
        except ImportError:
            self.skipTest("serve pyserial e un sistema con pty")
        from aprs_mappa.lora import LoraSource
        master, slave = pty.openpty()
        got = []
        src = LoraSource("serial", got.append, serial_port=os.ttyname(slave), baud=115200)
        src.start()
        time.sleep(0.6)
        os.write(master, b"Rx: N0CALL-7>APLRT1:!4500.00N/00900.00E>seriale\r\n")
        for _ in range(30):
            if got:
                break
            time.sleep(0.1)
        src.stop()
        os.close(master); os.close(slave)
        self.assertEqual(got, ["N0CALL-7>APLRT1:!4500.00N/00900.00E>seriale"])
        self.assertEqual(src.packets, 1)

    def test_vhf_e_lora_distinti(self):
        from aprs_mappa.receiver import PacketHandler
        store = StationStore()
        h = PacketHandler(store, [], None)
        h.lora_line("N0CALL-7>APLRT1,WIDE1-1:!4500.00N/00900.00E>LoRa")
        h(decode_frame(encode_frame("N0CALL-9", "APZ001", [], b"!4429.64N/01120.58E>VHF")))
        self.assertTrue(store.stations["N0CALL-7"]["lora_last"] and not store.stations["N0CALL-7"]["rf_last"])
        self.assertTrue(store.stations["N0CALL-9"]["rf_last"] and not store.stations["N0CALL-9"]["lora_last"])
        self.assertEqual(h.lora_frames, 1)


class TestServer(unittest.TestCase):
    def setUp(self):
        self.store = StationStore()
        self.web = WebServer(self.store)
        self.web.ensure("127.0.0.1", 18765)

    def tearDown(self):
        self.web.stop()

    def test_stato_e_posizione_scelta(self):
        q = self.store.subscribe()
        self.store.add(aprs.parse("N0CALL", "APZ001", b"!4429.64N/01120.58E-"), "N0CALL>APZ001:!...")
        state = json.load(urllib.request.urlopen("http://127.0.0.1:18765/api/state?x=1"))
        self.assertEqual(state["stations"][0]["id"], "N0CALL")

        req = urllib.request.Request("http://127.0.0.1:18765/api/home", method="POST",
                                     data=json.dumps({"lat": 44.5, "lon": 11.3}).encode(),
                                     headers={"Content-Type": "application/json"})
        self.assertEqual(urllib.request.urlopen(req).status, 200)
        self.assertEqual(self.store.home, (44.5, 11.3))
        events = [json.loads(q.get(timeout=1)) for _ in range(2)]
        self.assertTrue(events[1]["picked"])

    def test_posizione_non_valida(self):
        req = urllib.request.Request("http://127.0.0.1:18765/api/home", method="POST",
                                     data=b'{"lat": 120, "lon": 0}')
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 400)

    def test_stazione_nella_mappa(self):
        self.store.set_station("igate_lora", "LoRa RX di prova", "iz2lpn-10")
        st = json.load(urllib.request.urlopen("http://127.0.0.1:18765/api/state"))["station_info"]
        self.assertEqual((st["table"], st["code"], st["comment"], st["call"]),
                         ("L", "&", "LoRa RX di prova", "IZ2LPN-10"))

    def test_pagina_mappa(self):
        html = urllib.request.urlopen("http://127.0.0.1:18765/?tema=scuro").read().decode()
        self.assertIn("APRS Master by IZ2LPN op. Giorgio", html)
        self.assertIn('href="/icona.svg"', html)
        r = urllib.request.urlopen("http://127.0.0.1:18765/icona.svg")
        self.assertEqual(r.headers["Content-Type"], "image/svg+xml")
        r = urllib.request.urlopen("http://127.0.0.1:18765/icona-32.png")
        self.assertEqual(r.read()[:4], b"\x89PNG")
        with self.assertRaises(urllib.error.HTTPError):       # niente accesso ad altri file
            urllib.request.urlopen("http://127.0.0.1:18765/../server.py")


class FintoGeopin:
    """Portale Geopin finto: registra i pacchetti per numero progressivo, come quello vero."""

    def __init__(self, key="gp_" + "a" * 40):
        import gzip
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.key, self.seen, self.status, self.fail = key, {}, None, 0
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                if self.path != "/api/publish.php":
                    self.send_response(404); self.end_headers(); return
                if self.headers.get("Authorization") != f"Bearer {outer.key}":
                    return self._json(403, {"error": "chiave non valida"})
                if outer.fail:
                    outer.fail -= 1
                    return self._json(500, {"error": "errore del database"})
                body = self.rfile.read(int(self.headers["Content-Length"]))
                d = json.loads(gzip.decompress(body))
                for pk in d["packets"]:
                    outer.seen.setdefault(pk["seq"], pk)        # come il portale: i doppioni non contano
                outer.status = d["status"]
                self._json(200, {"ok": True, "accepted": len(d["packets"]), "receiver": {"name": "Casa", "owner": "N0CALL"}})

            def _json(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


class TestGeopin(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()
        self.q = os.path.join(self.tmp, "coda.db")
        self.portal = FintoGeopin()

    def tearDown(self):
        self.portal.close()

    def make(self, **kw):
        from aprs_mappa.geopin import GeopinPublisher
        return GeopinPublisher(self.portal.url, self.portal.key, 5, queue_path=self.q, **kw)

    def test_indirizzo(self):
        from aprs_mappa.geopin import publish_url
        self.assertEqual(publish_url("geopin.esempio.it"), "https://geopin.esempio.it/api/publish.php")
        self.assertEqual(publish_url("https://x.it/geo/"), "https://x.it/geo/api/publish.php")
        self.assertEqual(publish_url("https://x.it/api/publish.php"), "https://x.it/api/publish.php")

    def test_prova_collegamento(self):
        from aprs_mappa.geopin import GeopinError, GeopinPublisher
        p = self.make()
        self.assertIn('"Casa"', p.test())
        p.stop(final=False)
        bad = GeopinPublisher(self.portal.url, "gp_" + "b" * 40, queue_path=os.path.join(self.tmp, "x.db"))
        with self.assertRaisesRegex(GeopinError, "chiave"):
            bad.test()
        bad.stop(final=False)

    def test_coda_persistente_e_nessun_doppione(self):
        p = self.make(status_fn=lambda: {"running": True})
        for i in range(5):
            p.add(time.time(), "rf", f"N0CALL-{i}>APZ001:!4500.00N/00900.00E-x")
        p.queue.close()
        p = self.make()                          # riapertura: la coda è ancora lì
        self.assertEqual(p.pending, 5)
        self.portal.fail = 1                     # primo invio fallito: resta tutto in coda
        from aprs_mappa.geopin import GeopinError
        with self.assertRaises(GeopinError):
            p._publish_all()
        self.assertEqual(p.pending, 5)
        p._publish_all()
        self.assertEqual(p.pending, 0)
        self.assertEqual(sorted(self.portal.seen), [1, 2, 3, 4, 5])
        p.add(time.time(), "is", "N0NET>APRS,TCPIP*:!4500.00N/00900.00E-y")
        p._publish_all()
        self.assertEqual(sorted(self.portal.seen), [1, 2, 3, 4, 5, 6])
        p.stop(final=False)

    def test_invio_a_gruppi_e_pubblica_ora(self):
        from aprs_mappa import geopin
        p = self.make(status_fn=lambda: {"source": "Direwolf/TNC"})
        for i in range(geopin.BATCH + 300):
            p.add(time.time(), "is", f"N{i % 9}TST>APRS:!4500.00N/00900.00E-{i}")
        p.next_at = time.time() + 3600           # l'intervallo non è ancora scaduto…
        p.start()
        p.publish_now()                          # …ma si può pubblicare subito
        t0 = time.time()
        while p.pending and time.time() - t0 < 10:
            time.sleep(0.05)
        time.sleep(0.2)
        self.assertEqual(len(self.portal.seen), geopin.BATCH + 300)
        self.assertEqual(self.portal.status["source"], "Direwolf/TNC")
        self.assertTrue(p.ok)
        p.stop(final=True)
        self.assertTrue(self.portal.status.get("stopped"))

    def test_stato_con_la_stazione_ricevente(self):
        # Geopin evidenzia la stazione ricevente con nominativo-SSID e simbolo
        from aprs_mappa.geopin import receiver_status
        from aprs_mappa.receiver import Config, Receiver
        cfg = Config(source="none", home="44.5,11.3")
        store = StationStore(cfg.home_tuple())
        store.set_station("igate_vhf", "", "iz2lpn-10", "FT-2980")
        st = receiver_status(Receiver(store), cfg)
        self.assertEqual(st["station"]["call"], "IZ2LPN-10")
        self.assertEqual((st["station"]["table"], st["station"]["code"]), ("R", "&"))
        self.assertEqual(st["station"]["equipment"], "FT-2980")
        store.set_home((45.0, 9.0), picked=True)          # scelta sulla mappa: vale quella
        self.assertEqual(receiver_status(Receiver(store), cfg)["home"], (45.0, 9.0))

    def test_ricevitore_pubblica_tutte_le_origini(self):
        from aprs_mappa.receiver import PacketHandler
        p = self.make()
        h = PacketHandler(StationStore(), [], None)
        h.publish = p.add
        h(decode_frame(encode_frame("N0CALL-9", "APZ001", [], b"!4429.64N/01120.58E>VHF")))
        h.lora_line("N0CALL-7>APLRT1,WIDE1-1:!4500.00N/00900.00E>LoRa")
        h.internet_line("N0NET>APRS,TCPIP*,qAC,T2ITALY:!4501.00N/00901.00E-rete")
        rows = p.queue.batch()
        self.assertEqual([r[2] for r in rows], ["rf", "lora", "is"])
        p.publish_rf = False                     # file WAV: la radio non si pubblica
        h(decode_frame(encode_frame("N0CALL-8", "APZ001", [], b"!4429.64N/01120.58E>WAV")))
        self.assertEqual(p.pending, 3)
        p.stop(final=False)


class TestMappaEtichette(unittest.TestCase):
    def test_ssid_evidenziato_e_propria_stazione(self):
        from aprs_mappa.server import STATIC
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('function labelHTML(id)', html)          # SSID in evidenza sulle etichette
        self.assertIn('<span class="lbl">${labelHTML(d.id)}', html)
        self.assertIn('class="mk own"', html)                   # nominativo della stazione ricevente
        self.assertIn('${labelHTML(si.call)}', html)
        store = StationStore((45.0, 9.0))
        store.set_station("igate_vhf", "", "iz2lpn-10")
        self.assertEqual(store.station["call"], "IZ2LPN-10")

    def test_simboli_dalla_cdn_se_il_download_fallisce(self):
        import http.client

        class Senza:                            # servizio mappe senza rete
            def symbol_sheet(self, n):
                return None
        web = WebServer(StationStore(tiles=Senza()))
        web.ensure("127.0.0.1", 18766)
        try:
            c = http.client.HTTPConnection("127.0.0.1", 18766, timeout=5)
            c.request("GET", "/simboli/aprs-symbols-24-1.png")
            r = c.getresponse()
            self.assertEqual(r.status, 302)
            self.assertIn("cdn.jsdelivr.net/gh/hessu/aprs-symbols", r.getheader("Location"))
            c.request("GET", "/simboli/altro.png")
            r2 = c.getresponse(); r2.read()
            self.assertEqual(r2.status, 404)
        finally:
            web.stop()

class FintoAprsIs:
    """Server APRS-IS finto: verifica il passcode come quello vero e registra le righe ricevute."""

    def __init__(self):
        import socket
        import threading
        from aprs_mappa.aprsis import passcode
        self.lines, self.srv = [], socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen()
        self.port = self.srv.getsockname()[1]

        def serve():
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            c.sendall(b"# aprsc finto\r\n")
            buf = b""
            c.settimeout(0.2)
            while True:
                try:
                    data = c.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    return
                if not data:
                    return
                buf += data
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.rstrip(b"\r").decode("latin-1")
                    self.lines.append(line)
                    if line.startswith("user "):
                        call, pw = line.split()[1], line.split()[3]
                        ok = pw == str(passcode(call))
                        c.sendall(f"# logresp {call} {'verified' if ok else 'unverified'}, server T2FINTO\r\n".encode())
        threading.Thread(target=serve, daemon=True).start()

    def close(self):
        self.srv.close()


class TestAnnuncioStazione(unittest.TestCase):
    """La stazione deve comparire su aprs.fi anche senza iGate, se c'è il passcode."""

    def run_client(self, pcode, home=(44.494, 11.343), igate=False, beacon=True, wait=1.5):
        from aprs_mappa.aprsis import AprsIsClient
        srv = FintoAprsIs()
        c = AprsIsClient("IZ2LPN-10", pcode, "127.0.0.1", srv.port, igate=igate, home=home, beacon=beacon,
                         station_type="igate_vhf", equipment="Yaesu FT-2980 + Direwolf")
        c.beacon_delay_s = 0.2
        c.start()
        t0 = time.time()
        while time.time() - t0 < wait and not any(">APZRAP,TCPIP*:!" in x for x in srv.lines):
            time.sleep(0.05)
        time.sleep(0.2)
        info, self.status = c.beacon_info, c.status          # prima di scollegarsi
        c.stop()
        srv.close()
        return c, srv.lines, info

    def test_annuncio_senza_igate(self):
        from aprs_mappa.aprsis import passcode
        c, lines, info = self.run_client(str(passcode("IZ2LPN")))
        self.assertTrue(c.last_beacon)
        pos = [x for x in lines if x.startswith("IZ2LPN-10>APZRAP,TCPIP*:!")]
        self.assertEqual(len(pos), 1)
        self.assertRegex(pos[0], r"!4429\.64NR01120\.58E&")          # simbolo iGate RX: tabella R, codice &
        self.assertIn("IZ2LPN-10>APZRAP,TCPIP*:>Yaesu FT-2980 + Direwolf", lines)
        self.assertIn("annunciata come IZ2LPN-10", info)

    def test_senza_passcode_nessun_annuncio(self):
        c, lines, info = self.run_client("-1", wait=0.8)
        self.assertFalse(any(">APZRAP" in x for x in lines))
        self.assertIn("sola ricezione", self.status)
        self.assertIn("serve un passcode valido", info)

    def test_passcode_sbagliato(self):
        c, lines, info = self.run_client("12345", wait=0.8)
        self.assertFalse(any(">APZRAP" in x for x in lines))
        self.assertIn("passcode non accettato", info)

    def test_senza_posizione(self):
        from aprs_mappa.aprsis import passcode
        c, lines, info = self.run_client(str(passcode("IZ2LPN")), home=None, wait=0.8)
        self.assertFalse(any(">APZRAP" in x for x in lines))
        self.assertIn("manca la posizione", info)

    def test_annuncio_disattivato(self):
        from aprs_mappa.aprsis import passcode
        c, lines, info = self.run_client(str(passcode("IZ2LPN")), beacon=False, wait=0.8)
        self.assertFalse(any(">APZRAP" in x for x in lines))

    def test_il_ricevitore_invia_il_passcode_anche_senza_igate(self):
        # era il difetto: senza iGate il programma si collegava con "pass -1"
        from aprs_mappa.aprsis import passcode
        from aprs_mappa.receiver import Config, Receiver
        srv = FintoAprsIs()
        pw = str(passcode("IZ2LPN"))
        cfg = Config(source="none", aprsis_enabled=True, aprsis_call="IZ2LPN-10", aprsis_passcode=pw,
                     aprsis_server="127.0.0.1", aprsis_port=srv.port, home="44.494,11.343", igate_enabled=False)
        rx = Receiver(StationStore(cfg.home_tuple()))
        rx.start(cfg)
        t0 = time.time()
        while time.time() - t0 < 3 and not (rx.aprsis and rx.aprsis.verified):
            time.sleep(0.05)
        login = next((x for x in srv.lines if x.startswith("user ")), "")
        verified = rx.aprsis.verified
        rx.stop()
        srv.close()
        self.assertIn(f"pass {pw} ", login + " ")
        self.assertTrue(verified)
        # con l'annuncio e i messaggi disattivati e senza iGate si torna alla sola ricezione
        srv = FintoAprsIs()
        cfg.aprsis_port, cfg.aprsis_beacon, cfg.messages_enabled = srv.port, False, False
        rx = Receiver(StationStore(cfg.home_tuple()))
        rx.start(cfg)
        t0 = time.time()
        while time.time() - t0 < 3 and not any(x.startswith("user ") for x in srv.lines):
            time.sleep(0.05)
        rx.stop()
        srv.close()
        self.assertIn("pass -1 ", next(x for x in srv.lines if x.startswith("user ")))

    def test_posizione_scelta_a_ricezione_avviata(self):
        from aprs_mappa.aprsis import AprsIsClient, passcode
        srv = FintoAprsIs()
        store = StationStore(None)
        c = AprsIsClient("IZ2LPN-10", str(passcode("IZ2LPN")), "127.0.0.1", srv.port, home=None)
        c.home_fn = lambda: store.home
        c.beacon_delay_s = 0.6
        c.start()
        time.sleep(0.3)
        store.set_home((45.5, 9.25), picked=True)      # scelta sulla mappa dopo l'avvio
        t0 = time.time()
        while time.time() - t0 < 3 and not c.last_beacon:
            time.sleep(0.05)
        c.stop()
        srv.close()
        pos = [x for x in srv.lines if x.startswith("IZ2LPN-10>APZRAP,TCPIP*:!")]
        self.assertTrue(pos and "4530.00N" in pos[0] and "00915.00E" in pos[0], pos)


def _pki(tmp, aia_port):
    """Piccola infrastruttura di prova: radice -> intermedio -> certificati del server."""
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import AuthorityInformationAccessOID, NameOID
    now = datetime.datetime.now(datetime.timezone.utc)
    day = datetime.timedelta(days=1)

    def name(cn):
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])

    def make(cn, key, issuer_name, issuer_key, ca=False, sans=None, aia=None, nb=now - day, na=now + 30 * day):
        b = (x509.CertificateBuilder().subject_name(name(cn)).issuer_name(issuer_name)
             .public_key(key.public_key()).serial_number(x509.random_serial_number())
             .not_valid_before(nb).not_valid_after(na)
             .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
             # identificativi delle chiavi: Python 3.13 (VERIFY_X509_STRICT) li richiede, come i certificati veri
             .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
             .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False))
        if ca:
            b = b.add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        if sans:
            b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in sans]), critical=False)
        if aia:
            b = b.add_extension(x509.AuthorityInformationAccess([x509.AccessDescription(
                AuthorityInformationAccessOID.CA_ISSUERS, x509.UniformResourceIdentifier(aia))]), critical=False)
        return b.sign(issuer_key, hashes.SHA256())

    k = {n: ec.generate_private_key(ec.SECP256R1()) for n in ("root", "inter", "leaf", "self")}
    root = make("Radice di prova", k["root"], name("Radice di prova"), k["root"], ca=True)
    inter = make("Intermedio di prova", k["inter"], root.subject, k["root"], ca=True)
    aia = f"http://127.0.0.1:{aia_port}/intermedio.der"
    certs = {
        "aia": make("localhost", k["leaf"], inter.subject, k["inter"], sans=["localhost"], aia=aia),
        "noaia": make("localhost", k["leaf"], inter.subject, k["inter"], sans=["localhost"]),
        "scaduto": make("localhost", k["leaf"], inter.subject, k["inter"], sans=["localhost"],
                        nb=now - 60 * day, na=now - 2 * day),
        "nome": make("altro.esempio", k["leaf"], inter.subject, k["inter"], sans=["altro.esempio"]),
        "autofirmato": make("localhost", k["self"], name("localhost"), k["self"], sans=["localhost"]),
    }
    pem = lambda c: c.public_bytes(serialization.Encoding.PEM)
    keypem = lambda key: key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption())
    open(f"{tmp}/root.pem", "wb").write(pem(root))
    open(f"{tmp}/intermedio.der", "wb").write(inter.public_bytes(serialization.Encoding.DER))
    for n, c in certs.items():
        open(f"{tmp}/{n}.pem", "wb").write(pem(c))
        open(f"{tmp}/{n}_full.pem", "wb").write(pem(c) + pem(inter))
        open(f"{tmp}/{n}.key", "wb").write(keypem(k["self"] if n == "autofirmato" else k["leaf"]))
    return f"{tmp}/root.pem"


try:
    import cryptography  # noqa: F401
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


@unittest.skipUnless(HAS_CRYPTO, "serve il modulo cryptography per creare certificati di prova")
class TestCertificatiHttps(unittest.TestCase):
    """Portale HTTPS con certificati sbagliati o catena incompleta: deve funzionare come in un browser,
    o dare un messaggio chiaro, senza mai disattivare la verifica."""

    @classmethod
    def setUpClass(cls):
        import tempfile
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        cls.tmp = tempfile.mkdtemp()
        tmp = cls.tmp

        class Aia(BaseHTTPRequestHandler):           # serve l'intermedio, come fanno le autorità
            def log_message(self, *a):
                pass

            def do_GET(self):
                data = open(f"{tmp}/intermedio.der", "rb").read()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        cls.aia = ThreadingHTTPServer(("127.0.0.1", 0), Aia)
        threading.Thread(target=cls.aia.serve_forever, daemon=True).start()
        cls.root = _pki(tmp, cls.aia.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.aia.shutdown()
        cls.aia.server_close()

    def setUp(self):
        from aprs_mappa import rete
        rete.EXTRA_CAFILE = self.root                  # la radice di prova fa da autorità "di sistema"
        rete._extra_pem.clear()
        rete._aia_tried.clear()

    def tearDown(self):
        from aprs_mappa import rete
        rete.EXTRA_CAFILE = None
        rete._extra_pem.clear()
        rete._aia_tried.clear()

    def portal(self, cert):
        """Portale HTTPS finto che presenta il certificato indicato."""
        import ssl
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class P(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                data = json.dumps({"ok": True, "receiver": {"name": "Casa", "owner": "IZ2LPN"}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), P)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(f"{self.tmp}/{cert}.pem", f"{self.tmp}/{cert.replace('_full', '')}.key")
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"https://localhost:{srv.server_port}/"

    def prova(self, cert):
        from aprs_mappa.geopin import GeopinError, GeopinPublisher
        import tempfile
        p = GeopinPublisher(self.portal(cert), "gp_" + "a" * 40, queue_path=os.path.join(tempfile.mkdtemp(), "c.db"))
        try:
            return p.test()
        except GeopinError as e:
            return "ERRORE: " + str(e)
        finally:
            p.stop(final=False)

    def test_catena_completa(self):
        self.assertIn("Collegamento riuscito", self.prova("aia_full"))

    def test_catena_incompleta_recuperata_come_un_browser(self):
        self.assertIn("Collegamento riuscito", self.prova("aia"))

    def test_catena_incompleta_senza_indirizzo_intermedio(self):
        r = self.prova("noaia")
        self.assertIn("manca il certificato intermedio", r)
        self.assertIn("fullchain", r)

    def test_certificato_scaduto(self):
        self.assertIn("è scaduto", self.prova("scaduto_full"))

    def test_nome_sbagliato(self):
        self.assertIn("non è valido per localhost", self.prova("nome_full"))

    def test_autofirmato(self):
        self.assertIn("autofirmato", self.prova("autofirmato"))

    def test_intermedio_non_fidato_non_basta(self):
        # un intermedio scaricato che non si concatena a un'autorità fidata non apre la connessione
        from aprs_mappa import rete
        rete.EXTRA_CAFILE = None
        self.assertTrue(self.prova("aia").startswith("ERRORE"))


def _tk_root():
    try:
        import tkinter as tk
        r = tk.Tk()
        r.withdraw()
        return r
    except Exception:
        return None


class TestFinestraAdattabile(unittest.TestCase):
    """Su schermi piccoli il pannello si scorre invece di finire fuori dallo schermo."""

    def setUp(self):
        self.root = _tk_root()
        if self.root is None:
            self.skipTest("serve uno schermo (anche virtuale) per Tkinter")

    def tearDown(self):
        import gc
        self.root.destroy()
        del self.root
        gc.collect()          # l'interprete Tcl va liberato qui, nel thread principale

    def test_barre_solo_quando_servono(self):
        from tkinter import ttk
        from aprs_mappa.scorrimento import ScrollFrame
        self.root.deiconify()
        sf = ScrollFrame(self.root)
        sf.pack(fill="both", expand=True)
        ttk.Frame(sf.body, width=800, height=600).pack()
        self.root.geometry("1000x800")
        self.root.update()
        self.assertEqual(sf.scrollable, (False, False))
        self.assertEqual(sf.body.winfo_width(), 1000)          # il contenuto riempie la finestra
        self.root.geometry("500x400")
        self.root.update()
        self.assertEqual(sf.scrollable, (True, True))
        self.assertGreaterEqual(sf.body.winfo_width(), 800)    # e non scende sotto la sua misura
        self.root.geometry("820x400")
        self.root.update()
        self.assertEqual(sf.scrollable[0], True)

    def test_finestra_dentro_lo_schermo(self):
        from aprs_mappa.scorrimento import fit_to_screen, work_area
        ax, ay, aw, ah = work_area(self.root)
        w, h = fit_to_screen(self.root, 5000, 5000)
        self.assertLessEqual(w, aw)
        self.assertLessEqual(h, ah)


if __name__ == "__main__":
    unittest.main()
