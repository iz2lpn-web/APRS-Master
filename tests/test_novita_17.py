# APRS Master by IZ2LPN op. Giorgio - test delle novità della versione 1.7
# SPDX-License-Identifier: GPL-3.0-or-later
import ast
import base64
import json
import re
import socket
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from aprs_mappa import aprs
from aprs_mappa.avvisi import AlertEngine, call_matches, clean_zones
from aprs_mappa.messaggi import MessageCenter, message_line
from aprs_mappa.server import StationStore, check_password, hash_password
from aprs_mappa.telemetria import TelemetryBook

PKG = Path(aprs.__file__).parent


def pkt(line):
    return aprs.parse_tnc2(line)


class TestParserNovita(unittest.TestCase):
    def test_meteo_con_posizione(self):
        p = pkt("WX1>APRS:@092345z4528.00N/00915.00E_220/004g005t077r001p002P003h50b10132")
        self.assertTrue(p.has_position)
        self.assertEqual(p.wx["temp"], 25.0)
        self.assertEqual(p.wx["wind_dir"], 220)
        self.assertEqual(p.wx["wind"], 6.4)
        self.assertEqual(p.wx["gust"], 8.0)
        self.assertEqual(p.wx["hum"], 50)
        self.assertEqual(p.wx["press"], 1013.2)
        self.assertEqual(p.wx["rain_1h"], 0.3)

    def test_meteo_senza_posizione_e_campi_mancanti(self):
        p = pkt("WX1>APRS:_10090556c...s004g...t-04r...h00b.....")
        self.assertEqual(p.kind, "Meteo")
        self.assertFalse(p.has_position)
        self.assertEqual(p.wx["temp"], -20.0)
        self.assertIsNone(p.wx["wind_dir"])
        self.assertEqual(p.wx["hum"], 100)            # h00 = 100%
        self.assertIsNone(p.wx["press"])

    def test_posizione_normale_senza_meteo(self):
        self.assertIsNone(pkt("N0CALL>APRS:!4500.00N/00900.00E>t077 non e meteo").wx)

    def test_telemetria(self):
        p = pkt("TL1>APRS:T#005,199,000,255,073,123,01101001")
        self.assertEqual(p.kind, "Telemetria")
        self.assertEqual(p.telemetry, {"seq": "005", "analog": [199.0, 0.0, 255.0, 73.0, 123.0], "bits": "01101001"})

    def test_messaggi(self):
        m = pkt("IZ1XX>APRS::IZ2LPN-10:Ciao{12").message
        self.assertEqual(m, {"to": "IZ2LPN-10", "text": "Ciao", "id": "12", "type": "msg"})
        self.assertEqual(pkt("IZ1XX>APRS::IZ2LPN   :ack3").message["type"], "ack")
        self.assertEqual(pkt("IZ1XX>APRS::IZ2LPN   :rej3").message["id"], "3")
        m = pkt("IZ1XX>APRS::IZ2LPN   :testo{AB}C1").message      # formato "reply-ack"
        self.assertEqual((m["text"], m["id"]), ("testo", "AB"))
        b = pkt("IZ1XX>APRS::BLN1     :Contest domenica")
        self.assertEqual((b.kind, b.message["type"]), ("Bollettino", "bulletin"))
        d = pkt("TL1>APRS::TL1      :PARM.Batt,Temp")
        self.assertEqual((d.kind, d.message["type"]), ("Definizione telemetria", "tlm_def"))

    def test_definizioni_telemetria(self):
        self.assertEqual(aprs.telemetry_definition("UNIT.V,C")[1], ["V", "C"])
        k, eq = aprs.telemetry_definition("EQNS.0,0.075,0,0,1,-40")
        self.assertEqual(k, "EQNS")
        self.assertEqual(eq[:2], [[0, 0.075, 0], [0, 1, -40]])
        self.assertEqual(len(eq), 5)
        self.assertEqual(aprs.telemetry_definition("BITS.11110000,Progetto X"), ("BITS", ["11110000", "Progetto X"]))

    def test_tnc2(self):
        self.assertIsNone(aprs.parse_tnc2("senza intestazione"))
        self.assertEqual(aprs.parse_tnc2("A1BC>APRS,WIDE1-1:!4500.00N/00900.00E-").source, "A1BC")


class TestTelemetria(unittest.TestCase):
    def test_conversione_con_le_definizioni(self):
        b = TelemetryBook()
        raw = {"seq": "1", "analog": [200, 50, None, 1, 2], "bits": "10000000"}
        self.assertEqual(b.scaled("TL1", raw)["analog"][0]["value"], 200)     # senza definizioni: grezzo
        b.define("TL1", "PARM.Batt,Temp,,,,Porta")
        b.define("tl1", "UNIT.V,C,,,,aperta")
        b.define("TL1", "EQNS.0,0.075,0,0,1,-40,0,1,0,0,1,0,0,1,0")
        b.define("TL1", "BITS.01111111,Stazione")
        v = b.scaled("TL1", raw)
        self.assertEqual([a["name"] for a in v["analog"]], ["Batt", "Temp", "A4", "A5"])
        self.assertEqual(v["analog"][0]["value"], 15.0)
        self.assertEqual(v["analog"][1]["value"], 10.0)
        self.assertEqual(v["analog"][0]["unit"], "V")
        self.assertFalse(v["bits"][0]["on"])           # BITS dice che 0 è "attivo" per il primo bit
        self.assertEqual(v["bits"][0]["name"], "Porta")
        self.assertEqual(v["project"], "Stazione")
        self.assertTrue(v["defined"])


class TestMessaggi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mc = MessageCenter(Path(self.tmp.name) / "m.db")
        self.mc.configure("IZ2LPN-10", ["IZ2LPN-9"])
        self.sent, self.events = [], []
        self.mc.sender = lambda line: self.sent.append(line) or True
        self.mc.can_send = lambda: True
        self.mc.on_event = self.events.append

    def tearDown(self):
        self.mc.close()
        self.tmp.cleanup()

    def test_riga_aprs_is(self):
        self.assertEqual(message_line("IZ2LPN-10", "iz1xx", "Ciao", "7"), "IZ2LPN-10>APZRAP,TCPIP*::IZ1XX    :Ciao{7")

    def test_ricezione_conferma_e_doppioni(self):
        msg = {"to": "IZ2LPN", "text": "Ciao", "id": "12", "type": "msg"}
        self.mc.handle("IZ1XX", msg, "rf")
        self.mc.handle("IZ1XX", msg, "is")             # lo stesso, ripetuto
        self.assertEqual(self.sent, ["IZ2LPN-10>APZRAP,TCPIP*::IZ1XX    :ack12"] * 2)
        snap = self.mc.snapshot()
        self.assertEqual(len(snap["messages"]), 1)
        self.assertEqual(snap["unread"], 1)
        self.assertEqual([e["type"] for e in self.events], ["message"])
        self.mc.mark_read("IZ1XX")
        self.assertEqual(self.mc.unread(), 0)

    def test_messaggi_altrui_e_bollettini(self):
        self.mc.handle("IZ1XX", {"to": "IK2ZZZ", "text": "x", "id": "1", "type": "msg"}, "is")
        self.mc.handle("IZ1XX", {"to": "BLN1", "text": "Contest", "id": None, "type": "bulletin"}, "is")
        self.mc.handle("IZ1XX", {"to": "BLN1", "text": "Contest rinviato", "id": None, "type": "bulletin"}, "is")
        snap = self.mc.snapshot()
        self.assertEqual(self.sent, [])
        self.assertEqual(len(snap["messages"]), 0)
        self.assertEqual(snap["heard"][0]["to"], "IK2ZZZ")
        self.assertEqual([b["text"] for b in snap["bulletins"]], ["Contest rinviato"])

    def test_invio_conferma_e_rifiuto(self):
        a = self.mc.send("iz1xx", "Ciao")
        self.assertEqual(self.sent[-1], "IZ2LPN-10>APZRAP,TCPIP*::IZ1XX    :Ciao{1")
        self.mc.handle("IZ1XX", {"to": "IZ2LPN-10", "text": "", "id": "1", "type": "ack"}, "is")
        self.assertEqual(self.mc.snapshot()["messages"][-1]["status"], "consegnato")
        self.mc.send("IZ1XX", "Ancora")
        self.mc.handle("IZ1XX", {"to": "IZ2LPN-10", "text": "", "id": "2", "type": "rej"}, "is")
        self.assertEqual(self.mc.snapshot()["messages"][-1]["status"], "rifiutato")
        self.assertEqual(a["status"], "in invio")

    def test_ritrasmissioni_e_rinuncia(self):
        self.mc.send("IZ1XX", "Ci sei?")
        t0 = time.time()
        for dt in (31, 92, 213, 454, 700):
            self.mc.tick(t0 + dt)
        self.assertEqual(len(self.sent), 5)             # primo invio + 4 ritrasmissioni
        self.assertEqual(self.mc.snapshot()["messages"][-1]["status"], "senza conferma")

    def test_controlli_del_testo(self):
        for to, text in (("IZ1XX", ""), ("IZ1XX", "x" * 68), ("IZ1XX", "città"), ("IZ1XX", "a{b"), ("NON VALIDO", "ok")):
            with self.assertRaises(ValueError):
                self.mc.send(to, text)
        self.mc.can_send = lambda: False
        with self.assertRaises(ValueError):
            self.mc.send("IZ1XX", "ok")
        self.assertEqual(self.sent, [])

    def test_numeri_persistenti(self):
        self.mc.send("IZ1XX", "uno")
        self.mc.close()
        mc = MessageCenter(Path(self.tmp.name) / "m.db")
        mc.configure("IZ2LPN-10")
        sent = []
        mc.sender, mc.can_send = (lambda line: sent.append(line) or True), (lambda: True)
        mc.send("IZ1XX", "due")
        self.assertTrue(sent[0].endswith("{2"))
        self.assertEqual(len(mc.snapshot()["messages"]), 2)
        self.mc = mc


class TestAvvisi(unittest.TestCase):
    def test_nominativi(self):
        self.assertTrue(call_matches("IZ2LPN-9", ["IZ2LPN"]))
        self.assertFalse(call_matches("IZ2LPN-9", ["IZ2LPN-7"]))
        self.assertTrue(call_matches("IU2ABC", ["IU2*"]))
        self.assertFalse(call_matches("IZ2LPNX", ["IZ2LPN"]))

    def test_stazione_osservata_e_assenza(self):
        e = AlertEngine()
        e.configure("IZ2LPN", quiet_min=30)
        p = pkt("IZ2LPN-9>APRS:!4500.00N/00900.00E>")
        self.assertEqual(len(e.check(p, "rf", 1000)), 1)
        self.assertEqual(e.check(p, "rf", 1000 + 600), [])
        self.assertIn("di nuovo", e.check(p, "rf", 1000 + 600 + 1801)[0]["text"])

    def test_zone(self):
        e = AlertEngine()
        e.configure(zones=[{"name": "Casa", "lat": 45.0, "lon": 9.0, "radius_km": 2},
                           {"name": "Solo", "lat": 45.0, "lon": 9.0, "radius_km": 2, "calls": "IZ2ABC"}])
        dentro, fuori = "!4500.00N/00900.00E>", "!4510.00N/00900.00E>"
        self.assertEqual(e.check(pkt("IK2X>APRS:" + dentro), "rf", 1), [])       # prima posizione: nessun avviso
        out = e.check(pkt("IK2X>APRS:" + fuori), "rf", 2)
        self.assertEqual([a["kind"] for a in out], ["zone_out"])
        self.assertEqual([a["kind"] for a in e.check(pkt("IK2X>APRS:" + dentro), "rf", 3)], ["zone_in"])
        out = e.check(pkt("IZ2ABC>APRS:" + dentro), "rf", 4)                   # zona con stazioni scelte
        self.assertEqual([(a["kind"], a["zone"]) for a in out], [("zone_in", "Solo")])
        self.assertEqual(clean_zones([{"name": "x", "lat": 99, "lon": 0, "radius_km": 1}, {"lat": "a"}]), [])

    def test_emergenza_e_messaggio(self):
        e = AlertEngine()
        e.configure()
        got = []
        e.on_alert = got.append
        p = aprs.parse("IU2MIC", "452X12", b"`%(>l\x1c\x1c>/Aiuto")
        self.assertEqual(p.mic_e_status, "EMERGENZA")
        e.check(p, "rf", 10)
        e.check(p, "rf", 20)                            # non ripete subito
        self.assertEqual([(a["kind"], a["level"]) for a in got], [("emergency", "critical")])
        e.message({"type": "message", "from": "IZ1XX", "text": "ciao", "t": 30})
        self.assertEqual(got[-1]["kind"], "message")
        e.configure(emergency=False, messages=False)
        self.assertEqual(e.check(p, "rf", 10_000), [])
        self.assertIsNone(e.message({"type": "message", "from": "IZ1XX", "text": "ciao"}))


class TestArchivioMeteoTelemetria(unittest.TestCase):
    def test_meteo_resta_e_si_aggancia(self):
        st = StationStore()
        st.add(pkt("WX1>APRS:@092345z4528.00N/00915.00E_220/004g005t077r000p000P000h50b10132"), "x", t=100)
        st.add(pkt("WX1>APRS:!4528.00N/00915.00E_comment"), "x", t=200)      # posizione senza dati meteo
        self.assertEqual(st.stations["WX1"]["wx"]["temp"], 25.0)
        st.add(pkt("WX1>APRS:_10090556c220s004g005t068r000p000P000h55b10120"), "x", t=300)
        self.assertEqual(st.stations["WX1"]["wx"]["temp"], 20.0)
        d = st.weather_detail("WX1", hours=24 * 365 * 100)
        self.assertEqual(len(d["series"]), 2)
        self.assertEqual(d["last"]["lat"], 45.466667)

    def test_storico_con_meteo_e_messaggi(self):
        from aprs_mappa.storico import History
        h = History(":memory:")
        st = StationStore(history=h)
        lines = ["WX1>APRS:@092345z4528.00N/00915.00E_220/004g005t077r000p000P000h50b10132",
                 "WX1>APRS:_10090556c220s004g005t068r000p000P000h55b10120",
                 "IZ1XX>APRS::IZ2LPN   :Ciao{1", "TL1>APRS:T#005,199,000,255,073,123,01101001"]
        now = time.time()
        for i, line in enumerate(lines):
            st.add(pkt(line), line, t=now - 100 + i * 40)
        self.assertEqual(h.count(), 4)
        self.assertEqual([round(w[2]["temp"]) for w in h.weather()], [25, 20])
        st2 = StationStore(history=h)
        st2.load_history(1)
        self.assertEqual(st2.stations["WX1"]["wx"]["temp"], 20.0)
        self.assertEqual(len(st2.weather_detail("WX1")["series"]), 2)
        h.close()

    def test_telemetria_con_definizioni_successive(self):
        st = StationStore()
        st.add(pkt("TL1>APRS:T#005,199,000,255,073,123,01101001"), "x")
        st.add(pkt("TL1>APRS::TL1      :EQNS.0,0.075,0,0,1,-40,0,1,0,0,1,0,0,1,0"), "x")
        d = st.telemetry_detail("tl1")
        self.assertEqual(d["values"]["analog"][0]["value"], 14.925)
        self.assertEqual(d["series"][0][2], 14.925)

    def test_ascoltatori_fuori_dal_lock(self):
        st = StationStore()
        seen = []
        st.listeners.append(lambda p, raw, origin, t: seen.append((p.source, st.lock.locked())))
        st.add(pkt("A1BC>APRS:!4500.00N/00900.00E-"), "x")
        st.add(pkt("A1BC>APRS:!4500.00N/00900.00E-"), "x", broadcast=False)     # ricarica dallo storico
        self.assertEqual(seen, [("A1BC", False)])


class TestServerNovita(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from aprs_mappa.receiver import Config, Receiver
        from aprs_mappa.servizio import Engine
        cls.tmp = tempfile.TemporaryDirectory()
        cls.store = StationStore()
        cls.engine = Engine(cls.store, Receiver(cls.store), Path(cls.tmp.name) / "m.db")
        cls.engine.configure(Config(aprsis_call="IZ2LPN-10"))
        cls.sent = []
        cls.engine.messages.sender = lambda line: cls.sent.append(line) or True
        cls.engine.messages.can_send = lambda: True
        from http.server import ThreadingHTTPServer
        from aprs_mappa.server import make_handler
        import threading
        cls.srv = ThreadingHTTPServer(("0.0.0.0", 0), make_handler(cls.store))
        cls.srv.daemon_threads = True
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.port = cls.srv.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.engine.close()
        cls.tmp.cleanup()

    def get(self, path, base=None, auth=None):
        req = urllib.request.Request((base or self.base) + path)
        if auth:
            req.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def post(self, path, body, base=None, auth=None):
        req = urllib.request.Request((base or self.base) + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        if auth:
            req.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def test_messaggi_meteo_telemetria_e_avvisi(self):
        for line in ("IZ1XX>APRS,TCPIP*::IZ2LPN-10:Prova{5", "WX9>APRS:_10090556c220s004g005t068r000p000P000h55b10120",
                     "TL9>APRS:T#001,1,2,3,4,5,00000000"):
            self.store.add(pkt(line), line, origin="is")
        self.assertTrue(self.get("/api/whoami")["write"])
        m = self.get("/api/messaggi")
        self.assertEqual(m["messages"][-1]["text"], "Prova")
        self.assertIn("IZ2LPN-10>APZRAP,TCPIP*::IZ1XX    :ack5", self.sent)
        r = self.post("/api/messaggi", {"to": "IZ1XX", "text": "Risposta"})
        self.assertEqual(r["status"], "in invio")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/api/messaggi", {"to": "IZ1XX", "text": "àèì"})
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(self.get("/api/meteo?id=WX9")["last"]["wx"]["temp"], 20.0)
        self.assertEqual(self.get("/api/telemetria?id=TL9")["values"]["analog"][0]["value"], 1)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get("/api/meteo?id=NESSUNO")
        self.assertEqual(cm.exception.code, 404)
        rules = self.post("/api/avvisi", {"watch": "IZ1*", "zones": [{"name": "Z", "lat": 45, "lon": 9, "radius_km": 3}]})
        self.assertEqual(rules["watch"], ["IZ1*"])
        self.assertEqual(self.get("/api/avvisi")["rules"]["zones"][0]["name"], "Z")
        st = json.loads(urllib.request.urlopen(self.base + "/api/state", timeout=5).read())
        self.assertEqual(st["features"], {"messages": True, "alerts": True, "panel": False})
        self.assertEqual(st["weather"][0]["id"], "WX9")
        lingua = self.get("/api/lingua?lang=en")
        self.assertEqual(lingua["lang"], "en")
        self.assertEqual(lingua["dict"]["Messaggi"], "Messages")

    def test_accesso_dalla_rete_con_password(self):
        try:                                   # indirizzo della scheda di rete (nessun pacchetto parte)
            u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            u.connect(("192.0.2.1", 9))
            ip = u.getsockname()[0]
            u.close()
        except OSError:
            ip = "127.0.0.1"
        if ip.startswith("127."):
            self.skipTest("nessun indirizzo di rete oltre a localhost")
        remote = f"http://{ip}:{self.port}"
        try:
            self.assertFalse(self.get("/api/whoami", remote)["write"])
        except OSError:
            self.skipTest("indirizzo di rete non raggiungibile")
        self.get("/api/messaggi", remote)                 # leggere si può: i messaggi APRS sono pubblici
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.post("/api/messaggi", {"to": "IZ1XX", "text": "x"}, remote)
        self.assertEqual(cm.exception.code, 403)          # senza password: solo da questo computer
        self.store.panel_password = hash_password("segreta123")
        try:
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.post("/api/messaggi", {"to": "IZ1XX", "text": "x"}, remote, auth="a:sbagliata")
            self.assertEqual(cm.exception.code, 401)
            self.assertIn("Basic", cm.exception.headers.get("WWW-Authenticate", ""))
            self.assertEqual(self.post("/api/messaggi", {"to": "IZ1XX", "text": "ok"}, remote, auth="a:segreta123")["to"], "IZ1XX")
            self.assertTrue(self.get("/api/whoami", remote, auth="a:segreta123")["write"])
        finally:
            self.store.panel_password = ""

    def test_hash_della_password(self):
        h = hash_password("segreta123")
        self.assertTrue(h.startswith("pbkdf2_sha256$"))
        self.assertTrue(check_password("segreta123", h))
        self.assertFalse(check_password("segreta124", h))
        self.assertFalse(check_password("x", "rotto"))
        self.assertNotEqual(h, hash_password("segreta123"))        # sale casuale


class TestServizioEGeopin(unittest.TestCase):
    def test_codice_di_collegamento(self):
        from aprs_mappa.geopin import decode_link, encode_link
        c = encode_link("https://geopin.esempio.it/", "gp_" + "a" * 40)
        self.assertTrue(c.startswith("geopin1:"))
        self.assertEqual(decode_link("  " + c + "\n"), ("https://geopin.esempio.it/", "gp_" + "a" * 40))
        # come lo crea Geopin in PHP: base64url senza "="
        php = "geopin1:" + base64.urlsafe_b64encode(json.dumps({"u": "https://x.it/", "k": "k+/="}).encode()).decode().rstrip("=")
        self.assertEqual(decode_link(php), ("https://x.it/", "k+/="))
        for bad in ("", "gp_123", "geopin1:!!!", "geopin1:" + base64.urlsafe_b64encode(b'{"u":""}').decode()):
            self.assertIsNone(decode_link(bad))

    def test_configurazione_dal_pannello(self):
        from aprs_mappa.receiver import Config
        from aprs_mappa.servizio import apply_changes, validate
        c = Config(aprsis_passcode="12345")
        n = apply_changes(c, {"source": "kiss", "kiss_port": "8002", "fix_bits": "false", "aprsis_passcode": "",
                              "filters": "IZ2, IK2", "alert_zones": [{"name": "A", "lat": 1, "lon": 2, "radius_km": 3}],
                              "panel_password": "non si cambia da qui", "web_port": 1})
        self.assertEqual((n.source, n.kiss_port, n.fix_bits, n.aprsis_passcode), ("kiss", 8002, False, "12345"))
        self.assertEqual(n.filters, ["IZ2", "IK2"])
        self.assertEqual(n.alert_zones[0]["name"], "A")
        self.assertEqual((n.panel_password, n.web_port), ("", 8080))
        self.assertIsNone(validate(n))
        self.assertIsNotNone(validate(Config(source="none")))
        self.assertIsNotNone(validate(Config(home="200,0")))
        with self.assertRaises(ValueError):
            apply_changes(c, {"kiss_port": "abc"})

    def test_servizio_comandi(self):
        from aprs_mappa.receiver import Config
        from aprs_mappa.servizio import Service, systemd_unit
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "cfg.json"
            Config(source="kiss", kiss_port=1, history_enabled=False, web_port=0).save(path)
            s = Service(path, Path(d) / "m.db")
            try:
                st = s.status()
                self.assertFalse(st["running"])
                self.assertNotIn("aprsis_passcode", st["config"])
                s.command({"action": "password", "password": "segreta123"})
                self.assertTrue(check_password("segreta123", Config.load(path).panel_password))
                with self.assertRaises(ValueError):
                    s.command({"action": "password", "password": "corta"})
                from aprs_mappa.geopin import encode_link
                s.command({"action": "geopin_code", "code": encode_link("https://g.it/", "k1")})
                c = Config.load(path)
                self.assertEqual((c.geopin_enabled, c.geopin_url, c.geopin_key), (True, "https://g.it/", "k1"))
                self.assertTrue(check_password("segreta123", c.panel_password))      # resta dopo un salvataggio
                with self.assertRaises(ValueError):
                    s.command({"action": "save", "config": {"source": "none"}})
                with self.assertRaises(ValueError):
                    s.command({"action": "sconosciuto"})
                s._save_rules({"watch": ["IZ2*"], "zones": [], "emergency": False, "messages": True, "quiet_min": 5})
                self.assertEqual(Config.load(path).alert_watch, "IZ2*")
            finally:
                s.stop()
                s.engine.close()
        unit = systemd_unit("/etc/aprs.json", "pi")
        self.assertIn("User=pi", unit)
        self.assertIn("--servizio --config /etc/aprs.json", unit)
        self.assertIn("Restart=on-failure", unit)

    def test_filtro_aprs_is_per_i_messaggi(self):
        import threading
        from aprs_mappa.receiver import Config, Receiver
        lines = []
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)

        def serve():
            c, _ = srv.accept()
            lines.append(c.recv(4096).decode())
            c.close()
        threading.Thread(target=serve, daemon=True).start()
        cfg = Config(source="none", aprsis_enabled=True, aprsis_call="IZ2LPN-10", aprsis_passcode="22714",
                     aprsis_server="127.0.0.1", aprsis_port=srv.getsockname()[1], home="45,9",
                     messages_calls="IZ2LPN-9", aprsis_beacon=False)
        rx = Receiver(StationStore())
        rx.start(cfg)
        t0 = time.time()
        while not lines and time.time() - t0 < 3:
            time.sleep(0.05)
        rx.stop()
        srv.close()
        self.assertIn("pass 22714 ", lines[0])
        self.assertIn("filter r/45.000/9.000/100 g/IZ2LPN-10/IZ2LPN/IZ2LPN-9", lines[0])


class TestLingua(unittest.TestCase):
    def tearDown(self):
        from aprs_mappa.lingua import set_language
        set_language("it")

    def test_tutti_i_testi_tradotti(self):
        """Ogni t("...") del codice e ogni testo marcato nelle pagine ha la traduzione inglese."""
        from aprs_mappa.lingua_en import EN
        missing = set()
        for f in PKG.glob("*.py"):
            if f.name == "lingua_en.py":
                continue
            for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("t", "tr") and n.args
                        and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)
                        and any(c.isalpha() for c in n.args[0].value) and n.args[0].value not in EN):
                    missing.add(f"{f.name}: {n.args[0].value}")
        for page in ("index.html", "pannello.html"):
            src = (PKG / "static" / page).read_text(encoding="utf-8")
            for m in re.finditer(r'\btt\("((?:[^"\\]|\\.)*)"', src):
                s = json.loads('"' + m.group(1) + '"')
                if s not in EN:
                    missing.add(f"{page}: {s}")
            for m in re.finditer(r'<(\w+)[^>]*\sdata-t(?:\s[^>]*)?>([^<]+)</\1>', src):
                s = " ".join(m.group(2).split())
                if s not in EN:
                    missing.add(f"{page}: {s}")
        self.assertEqual(sorted(missing), [])

    def test_segnaposto_coerenti(self):
        from aprs_mappa.lingua_en import EN
        bad = [k for k, v in EN.items() if sorted(re.findall(r"\{\w+\}", k)) != sorted(re.findall(r"\{\w+\}", v))]
        self.assertEqual(bad, [])

    def test_testi_composti(self):
        from aprs_mappa.lingua import set_language, t, tx
        self.assertEqual(t("{n} s fa", n=3), "3 s fa")
        set_language("en")
        self.assertEqual(t("{n} s fa", n=3), "3 s ago")
        self.assertEqual(tx("Collegato a euro.aprs2.net in sola ricezione (senza passcode)"),
                         "Connected to euro.aprs2.net, receive only (no passcode)")
        self.assertEqual(tx("Pubblicati 12 pacchetti alle 10:20"), "Uploaded 12 packets at 10:20")
        self.assertEqual(tx("Fermo\nNon collegato"), "Stopped\nNot connected")
        self.assertEqual(tx("testo che non c'è"), "testo che non c'è")


if __name__ == "__main__":
    unittest.main()
