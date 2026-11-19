# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Parser del campo informativo APRS (APRS Protocol Reference 1.0.1 + 1.2).

Supporta: posizioni non compresse e compresse (con o senza timestamp),
Mic-E, oggetti, item, NMEA $GPRMC/$GPGGA grezzo, pacchetti di terze parti,
dati meteo (con e senza posizione), telemetria (T#) e messaggi, conferme,
rifiuti e bollettini, comprese le definizioni della telemetria (PARM, UNIT, EQNS, BITS).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict

KNOT_KMH = 1.852
FT_M = 0.3048


@dataclass
class AprsPacket:
    source: str
    kind: str                      # descrizione del tipo di pacchetto
    lat: float | None = None
    lon: float | None = None
    symbol_table: str = "/"
    symbol_code: str = "/"
    course: int | None = None      # gradi
    speed_kmh: float | None = None
    altitude_m: float | None = None
    comment: str = ""
    object_name: str | None = None
    mic_e_status: str | None = None
    wx: dict | None = None         # dati meteo già convertiti (°C, km/h, mm, hPa, %)
    telemetry: dict | None = None  # {"seq", "analog": [5 valori grezzi], "bits": "01010101"}
    message: dict | None = None    # {"to", "text", "id", "type": msg|ack|rej|bulletin|tlm_def}

    @property
    def has_position(self) -> bool:
        return self.lat is not None and self.lon is not None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- utilità

def _base91(s: str) -> int:
    v = 0
    for c in s:
        v = v * 91 + (ord(c) - 33)
    return v


def _valid(lat: float, lon: float) -> bool:
    return -90 <= lat <= 90 and -180 <= lon <= 180


def _comment_extensions(pkt: AprsPacket, comment: str) -> str:
    """Estrae corso/velocità (ccc/sss) e quota (/A=dddddd) dal commento."""
    m = re.match(r"^(\d{3})/(\d{3})", comment)
    if m:
        crs, spd = int(m.group(1)), int(m.group(2))
        if 1 <= crs <= 360:
            pkt.course = crs % 360
            pkt.speed_kmh = round(spd * KNOT_KMH, 1)
        comment = comment[7:]
    m = re.search(r"/A=(-?\d{5,6})", comment)
    if m:
        pkt.altitude_m = round(int(m.group(1)) * FT_M, 1)
        comment = comment[:m.start()] + comment[m.end():]
    return comment.strip()


# ---------------------------------------------------------------- posizioni

def _uncompressed(pkt: AprsPacket, body: str) -> bool:
    # DDMM.mmN / DDDMM.mmE $   (19 caratteri)
    if len(body) < 19:
        return False
    lat_s, table, lon_s, code = body[0:8], body[8], body[9:18], body[18]
    # ambiguità di posizione: le cifre sostituite da spazi -> centro dell'area
    lat_s = lat_s.replace(" ", "5", 1).replace(" ", "0")
    lon_s = lon_s.replace(" ", "5", 1).replace(" ", "0")
    try:
        lat = int(lat_s[0:2]) + float(lat_s[2:7]) / 60
        lon = int(lon_s[0:3]) + float(lon_s[3:8]) / 60
    except ValueError:
        return False
    if lat_s[7] in "Ss":
        lat = -lat
    elif lat_s[7] not in "Nn":
        return False
    if lon_s[8] in "Ww":
        lon = -lon
    elif lon_s[8] not in "Ee":
        return False
    if not _valid(lat, lon):
        return False
    pkt.lat, pkt.lon = round(lat, 6), round(lon, 6)
    pkt.symbol_table, pkt.symbol_code = table, code
    pkt.comment = _comment_extensions(pkt, body[19:])
    return True


def _compressed(pkt: AprsPacket, body: str) -> bool:
    # T YYYY XXXX $ cs t   (13 caratteri)
    if len(body) < 13:
        return False
    table, y, x, code, c, s, t = body[0], body[1:5], body[5:9], body[9], body[10], body[11], body[12]
    if not all(33 <= ord(ch) <= 123 for ch in y + x):
        return False
    lat = 90 - _base91(y) / 380926
    lon = -180 + _base91(x) / 190463
    if not _valid(lat, lon):
        return False
    pkt.lat, pkt.lon = round(lat, 6), round(lon, 6)
    # le lettere a-j indicano un overlay numerico 0-9 sulla tabella alternativa
    pkt.symbol_table = str("abcdefghij".index(table)) if table in "abcdefghij" else table
    pkt.symbol_code = code
    if c != " ":
        ctype = (ord(t) - 33) >> 3 & 0x03
        if ctype == 2:                          # origine GGA: cs = quota
            pkt.altitude_m = round(1.002 ** _base91(c + s) * FT_M, 1)
        elif "!" <= c <= "z":                   # corso / velocità
            pkt.course = ((ord(c) - 33) * 4) % 360
            pkt.speed_kmh = round((1.08 ** (ord(s) - 33) - 1) * KNOT_KMH, 1)
    pkt.comment = _comment_extensions(pkt, body[13:])
    return True


def _position(pkt: AprsPacket, body: str) -> bool:
    if not body:
        return False
    if body[0].isdigit() or body[0] == " ":
        return _uncompressed(pkt, body)
    return _compressed(pkt, body)


# ---------------------------------------------------------------- Mic-E

_MICE_STD = {7: "Off duty", 6: "In viaggio", 5: "In servizio", 4: "Di ritorno",
             3: "Impegnato", 2: "Speciale", 1: "Priorità", 0: "EMERGENZA"}


def _mic_e(pkt: AprsPacket, dest: str, info: str) -> bool:
    dest = dest.split("-")[0].rstrip("*")
    if len(dest) < 6 or len(info) < 9:
        return False
    digits, msg_bits, custom = [], [], False
    for i, ch in enumerate(dest[:6]):
        if "0" <= ch <= "9":
            d, bit = ord(ch) - 48, 0
        elif "A" <= ch <= "J":
            d, bit, custom = ord(ch) - 65, 1, True
        elif "P" <= ch <= "Y":
            d, bit = ord(ch) - 80, 1
        elif ch in "KLZ":
            d, bit = 0, (1 if ch in "KZ" else 0)
            custom = custom or ch == "K"
        else:
            return False
        digits.append(d)
        if i < 3:
            msg_bits.append(bit)

    north = "P" <= dest[3] <= "Z"
    lon_offset = "P" <= dest[4] <= "Z"
    west = "P" <= dest[5] <= "Z"

    lat = digits[0] * 10 + digits[1] + (digits[2] * 10 + digits[3] + (digits[4] * 10 + digits[5]) / 100) / 60
    if not north:
        lat = -lat

    b = [ord(c) - 28 for c in info[1:7]]
    lon_deg = b[0] + (100 if lon_offset else 0)
    if 180 <= lon_deg <= 189:
        lon_deg -= 80
    elif 190 <= lon_deg <= 199:
        lon_deg -= 190
    lon_min = b[1] - 60 if b[1] >= 60 else b[1]
    lon = lon_deg + (lon_min + b[2] / 100) / 60
    if west:
        lon = -lon
    if not _valid(lat, lon):
        return False

    sp, dc, se = b[3], b[4], b[5]
    speed = sp * 10 + dc // 10
    course = (dc % 10) * 100 + se
    if speed >= 800:
        speed -= 800
    if course >= 400:
        course -= 400

    pkt.kind = "Mic-E"
    pkt.lat, pkt.lon = round(lat, 6), round(lon, 6)
    pkt.symbol_code, pkt.symbol_table = info[7], info[8]
    pkt.speed_kmh = round(speed * KNOT_KMH, 1)
    pkt.course = course % 360 if course else None
    code = msg_bits[0] << 2 | msg_bits[1] << 1 | msg_bits[2]
    pkt.mic_e_status = f"Custom {code}" if custom else _MICE_STD[code]

    comment = info[9:]
    m = re.search(r"(.{3})\}", comment)          # quota: xxx} in base 91
    if m and all(33 <= ord(c) <= 123 for c in m.group(1)):
        pkt.altitude_m = float(_base91(m.group(1)) - 10000)
        comment = comment[:m.start()] + comment[m.end():]
    # togli eventuali byte di tipo apparato (es. "`", "]", ">") in testa
    pkt.comment = comment.lstrip("`'>]").strip()
    return True


# ---------------------------------------------------------------- NMEA

def _nmea_coord(val: str, hemi: str, deg_len: int) -> float:
    v = int(val[:deg_len]) + float(val[deg_len:]) / 60
    return -v if hemi in "SW" else v


def _nmea(pkt: AprsPacket, info: str) -> bool:
    f = info.split("*")[0].split(",")
    try:
        if f[0] == "$GPRMC" and f[2] == "A":
            pkt.lat = _nmea_coord(f[3], f[4], 2)
            pkt.lon = _nmea_coord(f[5], f[6], 3)
            if f[7]:
                pkt.speed_kmh = round(float(f[7]) * KNOT_KMH, 1)
            if f[8]:
                pkt.course = int(float(f[8])) % 360
        elif f[0] == "$GPGGA" and f[6] != "0":
            pkt.lat = _nmea_coord(f[2], f[3], 2)
            pkt.lon = _nmea_coord(f[4], f[5], 3)
            if f[9]:
                pkt.altitude_m = float(f[9])
        else:
            return False
    except (IndexError, ValueError):
        return False
    pkt.kind = "NMEA " + f[0][1:]
    pkt.symbol_table, pkt.symbol_code = "/", ">"
    pkt.lat, pkt.lon = round(pkt.lat, 6), round(pkt.lon, 6)
    return _valid(pkt.lat, pkt.lon)


# ---------------------------------------------------------------- meteo

# campi meteo APRS (lettera -> larghezza), nell'ordine della specifica
_WX_FIELDS = {"c": 3, "s": 3, "g": 3, "t": 3, "r": 3, "p": 3, "P": 3, "h": 2, "b": 5, "L": 3, "l": 3}


def weather_fields(w: str) -> dict | None:
    """Dati meteo da una sequenza di campi (es. "c220s004g005t077r000h50b10132"), già convertiti.
    I campi mancanti ("..." o spazi) restano None. None se non c'è nessun dato meteo."""
    v: dict[str, int | None] = {}
    i = 0
    while i < len(w) and w[i] in _WX_FIELDS and w[i] not in v:
        k, n = w[i], _WX_FIELDS[w[i]]
        raw = w[i + 1:i + 1 + n]
        if len(raw) < n:
            break
        v[k] = int(raw) if re.fullmatch(r"-?\d+", raw) else None
        i += 1 + n
    if not any(x is not None for x in v.values()) or not {"t", "s", "b", "h"} & set(v):
        return None
    mph = lambda x: None if x is None else round(x * 1.609344, 1)
    inch = lambda x: None if x is None else round(x * 0.254, 1)
    t, h, c, b = v.get("t"), v.get("h"), v.get("c"), v.get("b")
    out = {
        "temp": None if t is None or t < -99 or t > 200 else round((t - 32) * 5 / 9, 1),
        "wind_dir": c % 360 if c is not None and c <= 360 else None,
        "wind": mph(v.get("s")), "gust": mph(v.get("g")),
        "rain_1h": inch(v.get("r")), "rain_24h": inch(v.get("p")), "rain_mid": inch(v.get("P")),
        "hum": None if h is None else (100 if h == 0 else min(100, h)),
        "press": round(b / 10, 1) if b else None,
    }
    return out if any(x is not None for x in out.values()) else None


def _position_weather(body: str) -> dict | None:
    """Meteo dopo una posizione con il simbolo "_" (stazione meteo)."""
    if body[:1].isdigit() or body[:1] == " ":
        if len(body) < 19 or body[18] != "_":
            return None
        w = body[19:]
        m = re.match(r"^([0-9. ]{3})/([0-9. ]{3})", w)        # vento: direzione/velocità
        if m:
            w = "c" + m.group(1) + "s" + m.group(2) + w[7:]
        return weather_fields(w)
    if len(body) < 13 or body[9] != "_":
        return None
    return weather_fields(body[13:])


# ---------------------------------------------------------------- telemetria e messaggi

def _telemetry(info: str) -> dict | None:
    """T#sss,aaa,aaa,aaa,aaa,aaa,bbbbbbbb (valori analogici grezzi e 8 bit digitali)."""
    m = re.match(r"^T#(MIC|\d{1,3}),?([^,]*),([^,]*),([^,]*),([^,]*),([^,]*),([01]{8})?", info)
    if not m:
        return None
    vals = []
    for x in m.groups()[1:6]:
        try:
            vals.append(float(x))
        except ValueError:
            vals.append(None)
    if all(v is None for v in vals):
        return None
    return {"seq": m.group(1), "analog": vals, "bits": m.group(7) or ""}


def _message(info: str) -> dict | None:
    """:DESTINATARIO:testo{numero  (destinatario di 9 caratteri, completato con spazi)."""
    if len(info) < 11 or info[10] != ":":
        return None
    to = info[1:10].strip().upper()
    body = info[11:]
    m = re.fullmatch(r"(ack|rej)([A-Za-z0-9}]{1,5})\s*", body)
    if m:
        return {"to": to, "text": "", "id": m.group(2).split("}")[0], "type": m.group(1)}
    text, msg_id = body, None
    m = re.match(r"^(.*)\{([A-Za-z0-9]{1,5})(\}[A-Za-z0-9]{0,2})?\s*$", body)
    if m:
        text, msg_id = m.group(1), m.group(2)
    kind = "msg"
    if re.match(r"^(PARM|UNIT|EQNS|BITS)\.", text):
        kind = "tlm_def"
    elif to.startswith("BLN") or to.startswith("NWS"):
        kind = "bulletin"
    return {"to": to, "text": text.rstrip(), "id": msg_id, "type": kind}


def telemetry_definition(text: str) -> tuple[str, list] | None:
    """PARM./UNIT./EQNS./BITS. -> (tipo, valori)."""
    m = re.match(r"^(PARM|UNIT|EQNS|BITS)\.(.*)$", text.strip())
    if not m:
        return None
    kind, rest = m.group(1), m.group(2)
    parts = [x.strip() for x in rest.split(",")]
    if kind == "EQNS":
        nums = []
        for x in parts[:15]:
            try:
                nums.append(float(x))
            except ValueError:
                nums.append(0.0)
        while len(nums) < 15:
            nums.append(0.0)
        return kind, [nums[i:i + 3] for i in range(0, 15, 3)]
    if kind == "BITS":
        return kind, [parts[0][:8] if parts else "11111111", ",".join(parts[1:]).strip()]
    return kind, parts


# ---------------------------------------------------------------- ingresso

_OTHER = {":": "Messaggio", ">": "Stato", "T": "Telemetria", "_": "Meteo",
          "<": "Capacità", "?": "Query", "{": "Formato utente", "#": "Meteo (Peet)",
          "*": "Meteo (Peet)"}


def parse(source: str, dest: str, info_bytes: bytes, depth: int = 0) -> AprsPacket:
    info = info_bytes.decode("latin-1", errors="replace").rstrip("\r\n")
    pkt = AprsPacket(source=source, kind="Sconosciuto")
    if not info:
        return pkt
    dti = info[0]

    if dti in "!=":
        pkt.kind = "Posizione"
        _position(pkt, info[1:])
        if pkt.symbol_code == "_":
            pkt.wx = _position_weather(info[1:])
    elif dti in "/@":
        pkt.kind = "Posizione con orario"
        _position(pkt, info[8:])
        if pkt.symbol_code == "_":
            pkt.wx = _position_weather(info[8:])
    elif dti == ";" and len(info) >= 18:
        pkt.object_name = info[1:10].strip()
        pkt.kind = "Oggetto" if info[10] == "*" else "Oggetto cancellato"
        _position(pkt, info[18:])
    elif dti == ")":
        m = re.match(r"\)([^!_]{3,9})[!_]", info)
        if m:
            pkt.object_name = m.group(1).strip()
            pkt.kind = "Item"
            _position(pkt, info[m.end():])
    elif dti in "`'\x1c\x1d":
        _mic_e(pkt, dest, info)
    elif dti == "$":
        _nmea(pkt, info)
    elif dti == "}" and depth < 2:
        # terze parti: SRC>DST,PATH:info incapsulato
        m = re.match(r"\}([^>]+)>([^,:]+)[^:]*:(.*)", info, re.S)
        if m:
            inner = parse(m.group(1), m.group(2), m.group(3).encode("latin-1"), depth + 1)
            inner.kind += " (terze parti)"
            return inner
    elif dti == "_":
        pkt.kind = "Meteo"
        pkt.wx = weather_fields(info[9:])                     # dopo _MMDDHHMM
        pkt.comment = info[1:].strip()
    elif dti == "T":
        pkt.kind = "Telemetria"
        pkt.telemetry = _telemetry(info)
        pkt.comment = info[1:].strip()
    elif dti == ":":
        pkt.message = _message(info)
        kinds = {"ack": "Conferma", "rej": "Rifiuto", "bulletin": "Bollettino", "tlm_def": "Definizione telemetria"}
        pkt.kind = kinds.get((pkt.message or {}).get("type", ""), "Messaggio")
        pkt.comment = info[1:].strip()
    elif dti in _OTHER:
        pkt.kind = _OTHER[dti]
        pkt.comment = info[1:].strip()
    else:
        # la specifica ammette '!' entro i primi 40 caratteri senza identificatore
        i = info.find("!", 0, 40)
        if i > 0 and _position(pkt, info[i + 1:]):
            pkt.kind = "Posizione"
        else:
            pkt.comment = info
    return pkt


def parse_tnc2(line: str) -> AprsPacket | None:
    """Pacchetto in formato testo SRC>DST,PATH:info (APRS-IS, LoRa, storico)."""
    head, sep, info = line.partition(":")
    if not sep or ">" not in head:
        return None
    source, rest = head.split(">", 1)
    return parse(source.strip(), rest.split(",", 1)[0].strip(), info.encode("latin-1", errors="replace"))
