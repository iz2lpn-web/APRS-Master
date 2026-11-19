# APRS Master by IZ2LPN op. Giorgio - ricevitore APRS con mappa OpenStreetMap
# Copyright (C) 2026 IZ2LPN op. Giorgio
# SPDX-License-Identifier: GPL-3.0-or-later
"""Connessioni HTTPS verificate, robuste come quelle di un browser.

- Alle autorità di sistema si aggiungono, se il modulo è installato, quelle di
  certifi (le stesse di Firefox): utile dove Python non trova l'archivio di
  sistema (Python di python.org su macOS, alcuni eseguibili pacchettizzati).
- Se un server presenta la catena incompleta (manca il certificato intermedio,
  errore frequente sugli hosting), il certificato intermedio viene scaricato
  dall'indirizzo indicato nel certificato stesso (estensione Authority
  Information Access, "CA Issuers"), come fanno i browser, e la connessione
  viene ritentata. La verifica resta completa: l'intermedio scaricato vale solo
  se si concatena a un'autorità fidata.
- Gli errori di certificato diventano messaggi chiari (scaduto, autofirmato,
  nome sbagliato, catena incompleta) con l'indicazione di cosa fare.
La verifica non viene mai disattivata.
"""
from __future__ import annotations

import logging
import socket
import ssl
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit

log = logging.getLogger("rete")

_lock = threading.Lock()
_extra_pem: list[str] = []          # certificati intermedi scaricati (PEM), validi per tutta la sessione
_aia_tried: set[str] = set()        # server per cui il recupero è già stato tentato
EXTRA_CAFILE: str | None = None     # autorità aggiuntiva (solo per i test)

# OID 1.3.6.1.5.5.7.48.2 (id-ad-caIssuers) codificato in DER
_OID_CA_ISSUERS = bytes.fromhex("06082b06010505073002")


def ssl_context() -> ssl.SSLContext:
    """Contesto TLS con verifica completa: sistema + certifi + intermedi recuperati."""
    ctx = ssl.create_default_context()
    # Python 3.13 accetta di default le "catene parziali": un certificato caricato qui sotto varrebbe come
    # autorità anche da solo. Gli intermedi scaricati devono invece concatenarsi a un'autorità vera.
    partial = getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
    if partial:
        ctx.verify_flags &= ~partial
    try:
        import certifi                  # facoltativo
        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:
        pass
    if EXTRA_CAFILE:
        ctx.load_verify_locations(cafile=EXTRA_CAFILE)
    with _lock:
        for pem in _extra_pem:
            try:
                ctx.load_verify_locations(cadata=pem)
            except ssl.SSLError:
                pass
    return ctx


def _ca_issuers_urls(der: bytes) -> list[str]:
    """Indirizzi "CA Issuers" dell'estensione AIA di un certificato DER.
    Dopo l'OID segue il nome generale [6] (tag 0x86) con l'URI in IA5String."""
    urls = []
    i = der.find(_OID_CA_ISSUERS)
    while i >= 0:
        j = i + len(_OID_CA_ISSUERS)
        if j + 2 <= len(der) and der[j] == 0x86:
            n, k = der[j + 1], j + 2
            if n & 0x80:                                 # lunghezza in forma lunga
                nb = n & 0x7F
                n = int.from_bytes(der[k:k + nb], "big")
                k += nb
            uri = der[k:k + n].decode("ascii", "replace")
            if uri.lower().startswith(("http://", "https://")):
                urls.append(uri)
        i = der.find(_OID_CA_ISSUERS, i + 1)
    return urls


def _server_cert_der(host: str, port: int, timeout: float) -> bytes | None:
    """Certificato del server, letto senza verifica solo per cercarvi l'indirizzo dell'intermedio."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            with ctx.wrap_socket(s, server_hostname=host) as t:
                return t.getpeercert(binary_form=True)
    except OSError:
        return None


def _fetch_intermediates(host: str, port: int, timeout: float) -> bool:
    """Scarica gli intermedi indicati dal certificato del server (al massimo 3 livelli)."""
    key = f"{host}:{port}"
    with _lock:
        if key in _aia_tried:
            return False
        _aia_tried.add(key)
    der = _server_cert_der(host, port, timeout)
    added = False
    for _level in range(3):
        urls = _ca_issuers_urls(der or b"")
        if not urls:
            break
        try:
            with urllib.request.urlopen(urls[0], timeout=timeout) as r:   # di norma http, per standard
                data = r.read(65536)
        except (OSError, ValueError):
            break
        try:
            pem = ssl.DER_cert_to_PEM_cert(data) if not data.lstrip().startswith(b"-----") else data.decode("ascii")
        except (ValueError, UnicodeDecodeError):
            break
        with _lock:
            if pem not in _extra_pem:
                _extra_pem.append(pem)
                added = True
        log.info("Certificato intermedio di %s recuperato da %s", host, urls[0])
        try:
            der = ssl.PEM_cert_to_DER_cert(pem)
        except ValueError:
            break
    return added


def explain(e: BaseException, host: str = "") -> str:
    """Spiegazione in italiano di un errore di certificato o di rete."""
    reason = getattr(e, "reason", e)
    if isinstance(reason, ssl.SSLCertVerificationError):
        msg = (reason.verify_message or str(reason)).lower()
        where = f" di {host}" if host else ""
        if "expired" in msg:
            return f"il certificato HTTPS{where} è scaduto: va rinnovato dal pannello dell'hosting"
        if "self-signed" in msg or "self signed" in msg:
            return (f"il certificato HTTPS{where} è autofirmato: il portale deve usare un certificato "
                    "rilasciato da un'autorità (ad esempio Let's Encrypt, gratuito su quasi tutti gli hosting)")
        if "hostname mismatch" in msg or "doesn't match" in msg or "not valid for" in msg:
            return (f"il certificato HTTPS non è valido per {host or 'questo indirizzo'}: controlla l'indirizzo "
                    "del portale (con o senza www) o attiva il certificato per quel nome sull'hosting")
        if "local issuer" in msg or "unable to get issuer" in msg:
            return (f"certificato HTTPS{where} non verificabile: sul server manca il certificato intermedio "
                    "(catena incompleta). Va installata la catena completa (\"fullchain\") dal pannello "
                    "dell'hosting; la si può controllare su https://www.ssllabs.com/ssltest/")
        if "not yet valid" in msg:
            return "certificato HTTPS non ancora valido: controlla data e ora del computer"
        return f"certificato HTTPS{where} non valido ({reason.verify_message or reason})"
    return str(reason)


def urlopen(req: urllib.request.Request | str, timeout: float):
    """Come urllib.request.urlopen, con contesto robusto e recupero degli intermedi."""
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    try:
        return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())
    except urllib.error.URLError as e:
        r = e.reason
        if not (isinstance(r, ssl.SSLCertVerificationError) and "issuer" in (r.verify_message or "")):
            raise
        u = urlsplit(url)
        if u.scheme != "https" or not u.hostname or not _fetch_intermediates(u.hostname, u.port or 443, timeout):
            raise
        return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())
