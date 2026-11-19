# Richiesta dell'identificativo APRS (TOCALL) per APRS Master

Su aprs.fi il campo **Dispositivo** di una stazione si ricava solo dal destinatario dei suoi pacchetti
(il "tocall"), cercato nel database pubblico [aprs-deviceid](https://github.com/aprsorg/aprs-deviceid)
curato da OH7LZB. Finché un programma è in sviluppo usa un identificativo sperimentale `APZ???`
(APRS Master usa `APZRAP`) e aprs.fi mostra "Unknown / Experimental".

Per far comparire "APRS Master" serve un identificativo assegnato. Lo chiede l'autore del programma,
non gli utenti, aprendo una segnalazione su GitHub (serve un account gratuito):
https://github.com/aprsorg/aprs-deviceid/issues

Prima di inviarla conviene verificare che il prefisso sia ancora libero con lo strumento
[TOCALL Search](https://aprsorg.github.io/aprs-deviceid-web/). Al 2 novembre 2026 `APMST?` non risulta
assegnato e non ricade in nessun prefisso o carattere jolly esistente. Il regolamento
([ALLOCATING.md](https://github.com/aprsorg/aprs-deviceid/blob/main/ALLOCATING.md)) assegna gli
identificativi a software distribuito ad altri, come APRS Master. I gestori di solito rispondono entro
una settimana e possono proporre un identificativo diverso da quello chiesto.

Quando arriva l'assegnazione basta cambiare una riga in `aprs_mappa/aprsis.py`:

```python
TOCALL = "APMST1"            # identificativo assegnato da aprs-deviceid
```

e pubblicare una nuova versione. aprs.fi riconosce il nuovo identificativo dal primo annuncio.

## Testo da incollare nella segnalazione

**Titolo:** `Tocall request: APRS Master by IZ2LPN`

**Testo:**

````
Hello,

I'm the author of APRS Master, a free (GPL-3.0) APRS receiver and receive-only iGate
for Windows, Linux and macOS, written in Python. It decodes AFSK 1200 from a sound card,
or uses Direwolf/KISS TNCs and LoRa APRS modules, shows stations on an OpenStreetMap map
and can gate received packets to APRS-IS (receive-only, it never transmits on RF).
It is distributed to other users as source code and as ready-made executables.

Packets sent to APRS-IS by the program are its own position beacon (with the station
symbol chosen by the user) and a status packet. During development it used APZRAP.

I would like to request the following allocation (a 5-letter prefix, so that the last
digit can identify future variants):

 - tocall: APMST?
   vendor: IZ2LPN
   model: APRS Master
   class: software
   os: Multiple

If APMST? is not suitable, any other identifier you prefer is fine.

Thank you and 73,
Giorgio IZ2LPN
````

Facoltativo: si può aggiungere una riga `contact:` con un indirizzo email tecnico, che diventa
pubblico nel database.
