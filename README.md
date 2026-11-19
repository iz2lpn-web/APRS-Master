# APRS Master by IZ2LPN op. Giorgio 1.7.0

Riceve i pacchetti APRS (AFSK 1200 baud, AX.25) trasmessi da un beacon, li decodifica e mostra le
stazioni in tempo reale su una mappa OpenStreetMap nel browser. Si gestisce da un pannello di controllo
grafico oppure dalla riga di comando.

Il segnale arriva in uno di tre modi:

- dalla **scheda audio** collegata alla radio, con il demodulatore integrato;
- da **Direwolf** o da un altro TNC tramite KISS su TCP;
- da un **file WAV** registrato.

Oltre alla mappa: messaggi APRS, avvisi (stazioni osservate, zone, emergenze Mic-E), stazioni
meteo con grafici, telemetria, riproduzione degli spostamenti, e un modo di funzionamento come
servizio senza finestra (Raspberry Pi) comandato da un pannello nel browser. Interfaccia in
italiano e in inglese.

## Installazione e avvio

Serve Python 3.9 o successivo.

### Il modo più semplice: avviatore a doppio clic

| Sistema | File da aprire |
|---|---|
| Windows | `avvia_gui.bat` |
| Linux | `avvia_gui.sh` |
| macOS | `avvia_gui.command` |

Al primo avvio l'avviatore crea un ambiente Python nella cartella `.venv` e scarica le dipendenze
(numpy, sounddevice e pyserial). Serve Internet e ci vuole circa un minuto. Dagli avvii successivi il pannello
si apre subito. Per reinstallare da zero cancella la cartella `.venv`.

Prerequisiti per sistema:

- **Windows**: Python da [python.org](https://www.python.org/downloads/) (non quello del Microsoft Store),
  con l'opzione "Add python.exe to PATH" spuntata durante l'installazione.
- **Linux**: Tkinter, venv e PortAudio. Su Debian/Ubuntu:
  `sudo apt install python3-tk python3-venv libportaudio2`. Su Fedora:
  `sudo dnf install python3-tkinter portaudio`.
- **macOS**: Python da python.org, che include Tkinter. Al primo avvio di `avvia_gui.command`
  potrebbe servire clic destro e poi Apri per superare il controllo di Gatekeeper.

### Con pip

```
pip install aprs_mappa-1.7.0-py3-none-any.whl
aprs-mappa-gui                 # pannello di controllo
aprs-mappa --help              # riga di comando
```
Dalla cartella del pacchetto funziona anche `pip install .`.

### Eseguibile autonomo, senza Python sul computer di destinazione

```
python -m pip install pyinstaller numpy sounddevice
python strumenti/crea_eseguibile.py
```
Il risultato è in `dist/APRSMaster`: copia la cartella intera. PyInstaller crea eseguibili solo per
il sistema su cui gira, quindi per ottenere la versione Windows va lanciato su Windows.

Su Linux, `./avvia_gui.sh --collegamento` aggiunge "APRS Master", con la sua icona, al menu
delle applicazioni.

## Collegamento alla radio

Collega l'uscita audio della radio (altoparlante o presa DATA a 1200 bps) all'ingresso line o mic
della scheda audio, o a un'interfaccia audio USB. In Europa la frequenza APRS è 144,800 MHz.

Per regolare il volume usa l'indicatore di livello nel pannello: durante un pacchetto il picco deve
restare nella fascia verde. Un audio distorto peggiora la decodifica più del rumore.

## Uso del pannello di controllo

- **Sorgente del segnale**: scegli scheda audio (con elenco dei dispositivi, campionamento e canale),
  Direwolf/TNC (indirizzo e porta) o file WAV. Le impostazioni si bloccano durante la ricezione.
- **Posizione ricevente**: scrivi le coordinate oppure premi **Scegli sulla mappa…**. Nel browser
  puoi fare clic sul punto (il segnaposto si trascina), cercare una località, scrivere coordinate
  (`44.494, 11.343` oppure `44,494 11,343`) o un locatore Maidenhead (`JN54pl`), oppure usare la
  posizione del browser. **Usa questa posizione** compila il campo nel pannello; funziona anche a
  ricezione avviata e aggiorna subito distanze e azimut.
- **Tipo di stazione**: iGate RX VHF, iGate RX LoRa, stazione fissa o stazione portatile. Determina
  il simbolo APRS della propria stazione (`R&`, `L&`, `/-`, `/;`) nell'annuncio e sulla mappa; su
  aprs.fi è l'icona della stazione (il campo "Dispositivo" di aprs.fi è un'altra cosa, vedi più avanti).
- **Commento**: testo dell'annuncio di posizione (su aprs.fi: "Commento") e del
  segnaposto della propria stazione sulla mappa. Al massimo 43 caratteri, senza lettere accentate né `|`.
  Se è vuoto si usa "iGate RX - APRS Master" con la versione. Tipo e commento si possono cambiare
  anche durante la ricezione: valgono dal prossimo annuncio, inviato ogni 30 minuti.
- **Apparato**: descrizione libera dell'attrezzatura (ad esempio "Yaesu FT-2980 + Direwolf, X-50 a
  12 m"), al massimo 62 caratteri senza lettere accentate né `|`. L'iGate la invia ad APRS-IS come
  pacchetto di stato insieme a ogni annuncio di posizione: aprs.fi la mostra come "Ultimo rapporto"
  (in inglese "Last status"). Compare anche nel segnaposto della propria stazione sulla mappa.
- Sulla mappa la propria stazione mostra sempre accanto al simbolo il **nominativo con l'SSID** usato
  per APRS-IS (ad esempio IZ2LPN-10), con il bordo arancione. Senza nominativo APRS-IS compare solo il
  simbolo. In tutte le etichette l'SSID è evidenziato in blu.
- **Geopin**: pubblicazione delle stazioni ricevute sul portale Geopin (vedi più avanti).
- **Mostra solo**: nominativi o prefissi separati da virgola, per seguire solo il proprio beacon.
- **Salva i pacchetti su file**: registro testuale di tutti i pacchetti ricevuti.
- **Mappa nel browser**: porta del server e accesso dalla rete locale (per vedere la mappa da un
  tablet o un telefono). La posizione ricevente si può cambiare solo dal computer stesso.
- **Stazioni**: tabella con distanza, velocità, quota e ultimo ascolto; doppio clic per aprire la
  stazione sulla mappa.
- **Demodulatore**: "Completo" è il profilo consigliato e usa circa il 10% di un core. "Leggero"
  ne usa circa il 2% ed è pensato per computer poco potenti come un Raspberry Pi; decodifica
  meno pacchetti con radio molto sbilanciate. "Correggi i bit errati" recupera pacchetti con
  un singolo errore: sono conteggiati accanto ai frame validi.
- **Visualizza**: tema chiaro, scuro o come il sistema; la mappa aperta dal pannello usa lo stesso tema.
- **Dimensione della finestra**: all'avvio il pannello prende la misura che serve al suo contenuto,
  entro l'area utile dello schermo, e si apre sempre tutto visibile, anche con la scala dei caratteri
  di Windows al 125-150%. Se la finestra è più piccola del pannello compaiono le barre di scorrimento
  verticale e orizzontale; la rotella del mouse scorre il pannello e Shift + rotella lo scorre in
  orizzontale, mentre sopra la tabella delle stazioni e i registri scorre loro. Lo stesso vale per
  le finestre di impostazione.
- **Storico**: vedi la sezione successiva.

## Mappe e uso senza Internet

Dal menu **Mappa** del pannello si sceglie da dove arrivano le mappe:

- **OpenStreetMap (online)**, la scelta predefinita. Il browser scarica le mappe e le conserva nella
  propria cache come prevede la [policy di OpenStreetMap](https://operations.osmfoundation.org/policies/tiles/).
  La stessa policy non consente l'uso offline delle sue mappe e sconsiglia di metterci davanti una
  cache propria: per questo il programma non salva su disco le mappe di tile.openstreetmap.org.
- **Server di mappe con cache su disco**: l'indirizzo di un server di tile raster (con `{z}`, `{x}`
  e `{y}`), ad esempio un tuo server o un fornitore che consente di conservare le mappe; verifica le
  sue condizioni d'uso. Le mappe viste vengono salvate per almeno 7 giorni e restano disponibili
  quando la rete manca. Dimensione massima della cache (predefinita 500 MB) e cancellazione sono
  nello stesso menu.
- **Mappa offline da file MBTiles**: un file `.mbtiles` raster (PNG, JPEG o WebP), ad esempio creato
  con QGIS o scaricato da un servizio che lo consente. Nessun accesso alla rete.

La libreria della mappa (Leaflet) è inclusa nel programma e i simboli APRS vengono scaricati una
sola volta e conservati: con una mappa offline, la pagina funziona senza Internet.

## La mappa nel browser

La pagina della mappa (http://localhost:8080) ha tre schede: **Stazioni**, **Messaggi** e
**Avvisi**. Le funzioni della mappa sono le stesse di Geopin:

- **Filtri** per tipo di stazione (mobili, fisse, meteo, digipeater e iGate, oggetti) e per
  ricezione (radio diretta, tramite digipeater, LoRa, solo Internet). La scelta resta nel browser.
- **Raggruppa**: con la mappa lontana le stazioni vicine diventano un cerchio con il numero; un clic
  lo apre. **Etichette**: i nominativi che si sovrapporrebbero si nascondono (restano quelli della
  stazione scelta, delle emergenze e dei più recenti) e ricompaiono al passaggio del mouse.
- **Riproduci**: le stazioni rifanno gli spostamenti delle tracce in memoria (le ultime ore ricaricate
  dallo storico più la ricezione in corso), con velocità regolabile e cursore del tempo.
- **Stazioni meteo**: la temperatura compare accanto al nominativo, il riquadro della stazione
  riassume vento, umidità, pressione e pioggia, e **Grafici meteo** mostra temperatura, vento e
  raffiche, pressione, umidità e pioggia (6 ore - 30 giorni con lo storico). Valgono sia i rapporti
  meteo con posizione sia quelli senza (`_` con data), che si agganciano alla stazione già sulla mappa.
- **Telemetria** (`T#`): valori con nome, unità e conversione secondo le definizioni PARM, UNIT,
  EQNS e BITS che la stazione manda come messaggi, con un grafico per canale.
- **Emergenza Mic-E**: la stazione è evidenziata in rosso sulla mappa e in cima all'elenco.

## Messaggi APRS

Con il collegamento ad APRS-IS (nominativo e passcode) il programma riceve e invia messaggi:

- i messaggi per il proprio nominativo-SSID, o per il nominativo senza SSID, si raccolgono nella
  scheda **Messaggi** del pannello e della mappa, divisi per conversazione. Quelli con un numero si
  confermano subito (`ack`), anche quando il mittente li ripete; le ripetizioni non si contano due
  volte. Altri destinatari propri si aggiungono nel pannello web (es. `IZ2LPN-9`);
- l'invio passa da APRS-IS: al server si chiede anche di inoltrare i messaggi destinati a noi
  (filtro `g/`), da qualunque distanza. Ogni messaggio ha un numero e si ritrasmette dopo 30 s, 1, 2
  e 4 minuti finché non arriva la conferma; lo stato (in invio, consegnato, senza conferma,
  rifiutato) compare accanto al testo. Il programma non trasmette mai via radio;
- testo fino a 67 caratteri, senza accenti (le lettere accentate perdono l'accento da sole);
- **Sentiti**: i messaggi tra altre stazioni ricevuti via radio o rete; **Bollettini**: l'ultimo
  bollettino di ogni mittente (`BLN…`).

I messaggi si conservano in `~/.aprs_mappa_messaggi.db`. Si disattivano dal menu **Strumenti**.

## Avvisi

Scheda **Avvisi** del pannello e della mappa, pulsante **Regole**:

- **stazioni da osservare**, separate da virgole: senza SSID valgono tutti gli SSID, con `*` tutti i
  nominativi che iniziano così (`IU2*`). L'avviso arriva quando la stazione si sente per la prima
  volta o dopo un'assenza (30 minuti, regolabile);
- **zone**: cerchi con nome e raggio, disegnati con un clic sulla mappa. Avvisano quando una stazione
  entra o esce; con un elenco di stazioni valgono solo per quelle (che avvisano anche alla prima
  posizione dentro la zona);
- **emergenza Mic-E** e **messaggi per la mia stazione**, attivi per tutti.

Nel pannello gli avvisi suonano e i messaggi e le emergenze aprono una finestra (si spegne con
**Suono e finestra**); l'emergenza resta finché non si chiude. Nella mappa c'è un suono e, con
**Notifiche del browser**, la notifica anche con la pagina in secondo piano.

## Servizio senza finestra e pannello web (Raspberry Pi)

Su un computer sempre acceso, anche senza schermo:

```
aprs-mappa --panel-password          # sceglie la password per usare il pannello dalla rete
aprs-mappa --servizio                # avvia con la configurazione salvata
```

Il servizio usa la stessa configurazione del pannello grafico (`~/.aprs_mappa.json`, oppure quella
indicata con `--config FILE`); se non esiste la crea. Con `"bind": "0.0.0.0"` nella configurazione
(la casella **Visibile in rete locale** del pannello grafico) la mappa e il **pannello web**,
all'indirizzo `http://INDIRIZZO:8080/pannello`, si aprono da un altro computer o dal telefono.

Il pannello web mostra lo stato (ricezione, APRS-IS, LoRa, Geopin, temperatura del processore, spazio
libero), avvia, ferma e riavvia la ricezione e cambia tutta la configurazione: sorgente, stazione,
APRS-IS, messaggi, LoRa, Geopin (anche con il codice di collegamento), storico e lingua. Il
passcode APRS-IS non viene mai rimandato alla pagina: si scrive solo per cambiarlo.

**Chi può cambiare cosa.** Guardare la mappa, gli avvisi e i messaggi si può da qualunque computer
che raggiunge la pagina (i messaggi APRS sono pubblici). Inviare messaggi, cambiare le regole degli
avvisi, scegliere la posizione ricevente e usare il pannello web si può dal computer stesso oppure,
dalla rete, con la password del pannello (il browser la chiede). La password si conserva solo come
hash PBKDF2; dopo 10 tentativi sbagliati in 10 minuti un indirizzo deve aspettare. Viaggia senza
cifratura sulla rete locale: non esporre la porta su Internet. La password si imposta anche dal
pannello grafico (Strumenti → Password del pannello web) e dal pannello web.

Avvio automatico con systemd:

```
aprs-mappa --installa-servizio       # scrive /etc/systemd/system/aprs-master.service (o qui, se non può)
sudo systemctl daemon-reload && sudo systemctl enable --now aprs-master
journalctl -u aprs-master -f         # registro
```

Un esempio completo è in `esempi/aprs-master.service`. L'utente del servizio è nei gruppi `audio`
(scheda audio) e `dialout` (modulo LoRa su porta seriale).

## Lingua

Menu **Visualizza → Lingua / Language**: italiano, inglese o come il sistema. Il pannello cambia al
prossimo avvio; la mappa e il pannello web usano la stessa lingua (con `?lang=en` nell'indirizzo se
ne sceglie un'altra per una sola pagina). Da riga di comando: `--lang en`. Il registro tecnico resta
in italiano.

## LoRa APRS

LoRa APRS (in Europa 433,775 MHz) usa una modulazione diversa dall'APRS VHF e una radio FM non può
riceverlo: serve un modulo LoRa, ad esempio una scheda ESP32 con firmware LoRa APRS (iGate o
tracker). Il programma lo legge **in parallelo** alla sorgente VHF, così si ricevono le due bande
insieme.

Nel riquadro **LoRa APRS** del pannello, **Impostazioni…** permette di scegliere:
- **porta seriale USB** (es. `COM5` su Windows, `/dev/ttyUSB0` su Linux) e velocità, di solito 115200 baud;
- **rete (TCP)**: indirizzo e porta del modulo, se il firmware offre un'uscita in rete.

Il formato viene riconosciuto da solo: pacchetti APRS in testo, anche in mezzo ai messaggi di
diagnostica del firmware e con l'intestazione LoRa `<\xff\x01`, oppure protocollo KISS (con dentro
AX.25 o testo). Le righe che non sono pacchetti vengono ignorate.

Le stazioni ricevute via LoRa sono distinte da quelle VHF:
- nella colonna **Ricezione** del pannello ("VHF", "LoRa", "Internet" e combinazioni) e
  nell'elenco dei pacchetti;
- sulla mappa, con l'etichetta bordata di viola e i contatori separati per banda;
- nella **copertura**, che si calcola per una banda alla volta (scelta VHF/LoRa).

Se l'iGate APRS-IS è attivo, inoltra anche i pacchetti LoRa, con le stesse regole. Per usare solo
LoRa, scegli la sorgente "Nessuna sorgente VHF". La porta seriale richiede la libreria `pyserial`,
installata dagli avviatori.

## Mappa di copertura

Il pulsante **Copertura** nella mappa mostra, per il periodo scelto, dove si trovano le stazioni
ricevute via radio:
- celle verdi: stazioni ricevute **direttamente**;
- celle arancioni: stazioni ricevute solo **tramite digipeater**;
- linea tratteggiata (se è impostata la posizione ricevente): la distanza massima di ricezione
  diretta per ogni direzione, a settori di 10°.

Un pacchetto è "diretto" quando nessun digipeater lo ha ripetuto (nessun `*` nel percorso). Gli
oggetti e i pacchetti arrivati da Internet non contano, perché la loro posizione non è quella di
chi ha trasmesso.

## APRS-IS: iGate e confronto con la rete

Nel riquadro **APRS-IS** del pannello, **Impostazioni…** chiede nominativo-SSID (es. `IZ2LPN-10`),
passcode (il pulsante Calcola lo ricava dal nominativo), server e raggio del confronto.

- **Confronto con APRS-IS**: il programma riceve dalla rete i pacchetti delle stazioni entro il raggio
  scelto intorno alla posizione ricevente. Ogni stazione risulta ricevuta "Radio", "Internet" o
  "Radio + Internet" (colonna Ricezione del pannello, etichette sulla mappa). Le stazioni sentite
  solo dalla rete appaiono semitrasparenti e si nascondono con "Solo via radio". È utile per capire
  cosa si perde via radio e quanto si sente rispetto agli iGate vicini.
- **Annuncia la stazione** (attivo in modo predefinito): ogni 30 minuti, e la prima volta pochi secondi
  dopo il collegamento, il programma invia ad APRS-IS la posizione ricevente con il nominativo-SSID e
  il simbolo del tipo di dispositivo, più lo stato con la descrizione dell'apparato. È ciò che fa
  comparire la stazione su aprs.fi, anche senza iGate. Servono il **passcode** e la **posizione
  ricevente**; se manca qualcosa il riquadro APRS-IS lo dice ("Stazione non annunciata: …"),
  altrimenti mostra "Stazione annunciata come IZ2LPN-10 alle …". Una posizione scelta sulla mappa a
  ricezione avviata vale dall'annuncio successivo. Senza passcode il collegamento è in sola ricezione.
- **iGate in sola ricezione**: inoltra ad APRS-IS i pacchetti ricevuti via radio seguendo le regole
  di [aprs-is.net](https://aprs-is.net/IGateDetails.aspx). Sono esclusi i pacchetti con NOGATE,
  RFONLY, TCPIP o TCPXX, le interrogazioni e i pacchetti di terze parti già passati da Internet.
  Il percorso non viene modificato: si aggiunge solo `qAO` e il nominativo dell'iGate. Non
  trasmette mai via radio. Serve una sorgente radio in diretta (scheda audio, Direwolf o LoRa): con
  "Nessuna sorgente VHF" e senza LoRa, o con i file WAV, l'iGate resta spento e il riquadro lo segnala.

APRS-IS è riservato ai radioamatori: usa il tuo nominativo e il suo passcode.

### Come appare la stazione su aprs.fi

| Voce su aprs.fi | Da dove arriva |
|---|---|
| Nominativo e posizione | nominativo-SSID APRS-IS e posizione ricevente |
| Icona | Tipo di stazione |
| Commento | Commento (vuoto = "iGate RX - APRS Master" con la versione) |
| Ultimo rapporto | Apparato |
| Dispositivo | identificativo del programma (TOCALL), non impostabile dal pannello |

Il campo **Dispositivo** aprs.fi lo ricava solo dal destinatario dei pacchetti, cercato nel database
pubblico [aprs-deviceid](https://github.com/aprsorg/aprs-deviceid). APRS Master usa per ora
l'identificativo sperimentale `APZRAP`, quindi aprs.fi mostra "Experimental". Per vedere
"APRS Master" serve un identificativo assegnato: in [RICHIESTA_TOCALL.md](RICHIESTA_TOCALL.md) ci sono
la procedura e il testo pronto della richiesta.

## Storico ed esportazione GPX

Ogni pacchetto ricevuto viene salvato in un piccolo database (`~/.aprs_mappa_storico.db`, il
percorso esatto è in Aiuto → Informazioni). All'avvio la mappa mostra le stazioni e le tracce
delle ultime 24 ore, quindi chiudere il programma non fa più perdere nulla.

Dal menu **Storico** del pannello:
- **Esporta tracce GPX…**: scegli il periodo e se esportare tutte le stazioni o solo quelle
  selezionate nella tabella. Le stazioni in movimento diventano tracce, quelle ferme punti.
  Una pausa di oltre un'ora apre un nuovo segmento di traccia. Il file si apre con Google Earth,
  QGIS, OsmAnd, Garmin BaseCamp e la maggior parte dei navigatori;
- quante ore mostrare all'avvio, per quanto tempo conservare i dati (predefinito 90 giorni),
  disattivazione dello storico e cancellazione completa.

Sulla mappa nel browser, il riquadro di ogni stazione ha il link **Scarica traccia GPX**
(ultime 24 ore). Funziona anche da un tablet in rete locale.

Da riga di comando:
```
aprs-mappa --export-gpx traccia.gpx --station N0CALL-9 --hours 48
aprs-mappa --export-gpx tutto.gpx
```

Le impostazioni sono salvate in `~/.aprs_mappa.json`. Il percorso esatto è indicato in Aiuto → Informazioni.

## Geopin: pubblicazione sul portale

Geopin è il portale web su cui più stazioni riceventi pubblicano ciò che ricevono. Ogni
installazione di APRS Master pubblica con la chiave di un **ricevitore**, creata dal proprio
account sul portale (pagina **Il mio account → Ricevitori → Aggiungi ricevitore**).

Nel pannello, riquadro **Geopin → Impostazioni…**. Il modo più rapido è il **codice di
collegamento** (`geopin1:…`) che Geopin 1.5.1 o successivo mostra accanto alla chiave appena creata:
incollato nel primo campo, compila da solo indirizzo e chiave. Altrimenti:

- **indirizzo del portale**, ad esempio `https://geopin.esempio.it/`;
- **chiave del ricevitore**, che inizia con `gp_`;
- **Pubblica ogni**: 1, 5, 15, 30 o 60 minuti (predefinito 5).

**Prova collegamento** verifica indirizzo e chiave senza inviare nulla. Con la casella
**Pubblica sul portale** attiva, il programma pubblica tutto ciò che riceve (VHF, LoRa e APRS-IS) a
intervalli regolari. **Pubblica ora** invia subito senza aspettare l'intervallo. Il riquadro mostra
l'esito dell'ultima pubblicazione, l'ora della prossima e quanti pacchetti sono in coda.

Come funziona:

- ogni pacchetto ricevuto entra in una coda su disco (`~/.aprs_mappa_geopin.db`, il percorso esatto
  è in Aiuto → Informazioni). Se la rete manca, il portale non risponde o il programma viene
  chiuso, i pacchetti restano lì e partono alla pubblicazione successiva;
- l'invio è compresso e a gruppi di 2000 pacchetti. Ogni pacchetto ha un numero progressivo, così
  un invio ripetuto dopo un errore di rete non crea doppioni sul portale;
- dopo un errore il programma riprova entro un minuto, poi a intervalli crescenti, mai oltre
  l'intervallo scelto;
- insieme ai pacchetti viaggiano lo stato della ricezione, la posizione ricevente e come si presenta
  la stazione (nominativo-SSID, simbolo, commento, apparato): sul portale il ricevitore risulta attivo
  ed è evidenziato sulla mappa come in APRS Master, e la posizione serve per distanze e copertura;
- alla chiusura il programma tenta un'ultima pubblicazione (al massimo qualche secondo);
- i pacchetti dei **file WAV** non vengono pubblicati, perché non sono ricezioni in diretta.

La pubblicazione è indipendente dalla ricezione: resta attiva anche a ricezione ferma, così gli
arretrati partono comunque e il portale sa che il programma è aperto.

Da riga di comando:
```
aprs-mappa --source kiss --home 44.49,11.34 --geopin-url https://geopin.esempio.it/ --geopin-key gp_...
aprs-mappa --geopin-url https://geopin.esempio.it/ --geopin-key gp_... --geopin-test
aprs-mappa --geopin-code geopin1:...                       # indirizzo e chiave insieme
aprs-mappa --servizio --geopin-code geopin1:...            # li salva nella configurazione del servizio
```

**Se la prova di collegamento dà un errore di certificato.** Il programma verifica sempre il
certificato HTTPS del portale e non disattiva mai la verifica, perché la chiave viaggia nella
richiesta. Come un browser, se il server non invia il certificato intermedio (catena incompleta,
frequente sugli hosting) lo recupera da solo dall'indirizzo indicato nel certificato. Negli altri
casi il messaggio dice cosa non va: certificato scaduto, autofirmato, emesso per un altro nome
(controlla l'indirizzo, con o senza www) o catena incompleta senza modo di recuperarla. Nella pagina
Amministrazione di Geopin il pulsante **Verifica certificato HTTPS** controlla il server.

Per pubblicare anche lo storico raccolto prima di usare Geopin, caricalo dal portale (pagina Il mio
account → Importa lo storico).

## Riga di comando

```
aprs-mappa --source audio --filter N0CALL-9 --home 44.49,11.34
aprs-mappa --list-devices
aprs-mappa --source kiss --kiss-port 8001
aprs-mappa --source wav --wav esempi/test_aprs.wav --realtime
```
Senza installare il pacchetto si usa `python -m aprs_mappa` seguito dalle stesse opzioni. Lanciato
senza opzioni apre il pannello. La mappa è su http://localhost:8080.

| Opzione | Significato |
|---|---|
| `--source audio\|kiss\|wav` | sorgente del segnale |
| `--device`, `--rate`, `--channel` | scheda audio (indice o parte del nome), campionamento, canale |
| `--kiss-host`, `--kiss-port` | indirizzo del TNC KISS (predefinito 127.0.0.1:8001) |
| `--wav`, `--realtime` | file da decodificare, a velocità reale |
| `--home lat,lon` | posizione ricevente per distanze e azimut |
| `--filter CALL` | solo i nominativi che iniziano così (ripetibile) |
| `--log FILE` | salva i pacchetti grezzi |
| `--web-port`, `--bind` | porta della mappa; `--bind 0.0.0.0` per la rete locale |
| `--demod completo\|leggero`, `--no-fix` | profilo del demodulatore, correzione dei bit |
| `--no-history`, `--history-days N`, `--load-hours N` | storico: disattivazione, durata, ore mostrate all'avvio |
| `--export-gpx FILE [--station CALL] [--hours N]` | esporta le tracce dallo storico ed esce |
| `--station-type igate_vhf\|igate_lora\|fissa\|portatile`, `--station-comment TESTO`, `--station-equipment TESTO` | tipo di dispositivo, commento e apparato della stazione |
| `--lora-serial PORTA [--lora-baud N]`, `--lora-tcp HOST:PORTA` | modulo LoRa APRS in parallelo alla sorgente VHF |
| `--source none` | nessuna sorgente VHF (solo LoRa e/o APRS-IS) |
| `--aprsis-call CALL [--aprsis-pass N] [--igate]` | collegamento ad APRS-IS, iGate RX |
| `--aprsis-server`, `--aprsis-port`, `--aprsis-radius KM` | server e raggio del confronto |
| `--map-url URL [--map-attribution T]`, `--mbtiles FILE`, `--map-cache-mb N` | sorgente delle mappe |
| `--no-beacon` | con `--aprsis-pass`: non annunciare la stazione su APRS-IS |
| `--geopin-url URL --geopin-key CHIAVE [--geopin-interval MIN]` | pubblicazione sul portale Geopin |
| `--geopin-test` | con `--geopin-url` e `--geopin-key`: prova collegamento e chiave ed esce |
| `--geopin-code CODICE` | codice di collegamento di Geopin, al posto di `--geopin-url` e `--geopin-key` |
| `--servizio [--config FILE]` | servizio senza finestra con la configurazione salvata e il pannello web |
| `--panel-password [PASSWORD]` | imposta la password del pannello web (senza valore la chiede, `-` la toglie) |
| `--installa-servizio` | scrive il file per systemd |
| `--lang auto\|it\|en` | lingua |
| `--self-test` | verifica rapida dell'installazione |

## Prova senza radio

Apri il pannello, scegli **File WAV registrato** e seleziona `esempi/test_aprs.wav`. Il file contiene
un beacon in movimento (posizioni non compresse, compresse e Mic-E), un digipeater e un oggetto, con
rumore aggiunto. Per creare altri file di prova:
```
python -m aprs_mappa.genera_test -o prova.wav --snr 6
```

## Confronto del demodulatore su una registrazione

```
python strumenti/banco_demodulatore.py --wav registrazione.wav
```
Decodifica la registrazione con il demodulatore della versione 1.0, con il profilo leggero e con
quello completo. Mostra quanti pacchetti trova ciascuno, quanti grazie alla correzione e quanta
CPU usano. Con `--simula` ripete la prova su segnali simulati con de-enfasi, pre-enfasi e rumore.
Una registrazione di qualche minuto di traffico reale (WAV PCM 16 bit, mono, 44,1 o 48 kHz) è il
modo migliore per capire come si comporta con la tua radio.

## Direwolf

Il demodulatore integrato è adeguato per segnali discreti. In condizioni difficili Direwolf decodifica
di più. In `esempi/direwolf.conf` c'è una configurazione minima: avvia `direwolf -c direwolf.conf`, poi
nel pannello scegli "Direwolf o TNC esterno" con porta 8001.

## Compilazione automatica (GitHub Actions)

Il file `.github/workflows/compilazione.yml` si attiva da solo quando il progetto è in un
repository GitHub. A ogni push sul ramo `main` e a ogni pull request:
- esegue i test su Windows, macOS e Linux, e con Python 3.9, 3.12 e 3.13;
- apre e chiude il pannello grafico su Linux (con un display virtuale);
- crea l'eseguibile per Windows (x64), macOS (Apple Silicon) e Linux (x64), ne verifica il
  funzionamento con `--self-test` e lo rende scaricabile fra gli "artifact" dell'esecuzione;
- crea il wheel e l'archivio dei sorgenti e prova a installarli.

Per pubblicare una versione basta un tag con lo stesso numero di `aprs_mappa/__init__.py`:
```
git tag v1.7.0
git push origin v1.7.0
```
Il workflow crea una release GitHub con eseguibili, wheel e note prese da `CHANGELOG.md`.
Se il tag non corrisponde alla versione nel codice, la pubblicazione si ferma con un errore.

Note:
- gli eseguibili non sono firmati: al primo avvio Windows (SmartScreen) e macOS (Gatekeeper)
  chiedono conferma;
- su macOS viene creata solo la versione per Apple Silicon. Sui Mac Intel si usa `avvia_gui.command`.

## Test

```
python -m unittest discover -s tests -v
```

## Struttura

| Percorso | Contenuto |
|---|---|
| `aprs_mappa/gui.py` | pannello di controllo (Tkinter) |
| `aprs_mappa/main.py` | riga di comando |
| `aprs_mappa/receiver.py` | motore di ricezione e configurazione |
| `aprs_mappa/afsk.py` | demodulatore e modulatore AFSK, HDLC |
| `aprs_mappa/ax25.py` | frame AX.25 e CRC |
| `aprs_mappa/aprs.py` | parser APRS |
| `aprs_mappa/kiss.py` | client KISS TCP |
| `aprs_mappa/server.py` | archivio stazioni, server web, eventi in tempo reale |
| `aprs_mappa/static/index.html` | mappa (Leaflet + OpenStreetMap) |
| `aprs_mappa/genera_test.py` | generatore di WAV di prova |
| `esempi/` | WAV di prova e configurazione di Direwolf |
| `aprs_mappa/storico.py` | storico SQLite, copertura, esportazione GPX |
| `aprs_mappa/aprsis.py` | APRS-IS: iGate in sola ricezione e confronto |
| `aprs_mappa/lora.py` | ricezione LoRa APRS da porta seriale o rete |
| `aprs_mappa/mappe.py` | sorgenti delle mappe, cache su disco, MBTiles |
| `aprs_mappa/scorrimento.py` | finestre adattate allo schermo e barre di scorrimento |
| `aprs_mappa/rete.py` | connessioni HTTPS verificate: certifi, recupero degli intermedi, messaggi d'errore |
| `aprs_mappa/geopin.py` | pubblicazione sul portale Geopin: coda su disco e invio a intervalli, codice di collegamento |
| `aprs_mappa/messaggi.py` | messaggi APRS: archivio, conferme, invio con ritrasmissione, bollettini |
| `aprs_mappa/avvisi.py` | avvisi: stazioni osservate, zone, emergenze, messaggi |
| `aprs_mappa/telemetria.py` | definizioni e conversione della telemetria |
| `aprs_mappa/servizio.py` | collegamento di messaggi e avvisi; servizio senza finestra e pannello web |
| `aprs_mappa/lingua.py`, `lingua_en.py` | lingua dell'interfaccia e dizionario inglese |
| `aprs_mappa/static/pannello.html` | pannello di controllo nel browser |
| `aprs_mappa/static/grafici.js` | grafici SVG di meteo e telemetria (gli stessi di Geopin) |
| `strumenti/icone/` | icona del programma: disegni SVG e file ICO, ICNS, PNG |
| `strumenti/crea_icone.py` | rigenera le icone dai disegni SVG (serve cairosvg) |
| `strumenti/crea_eseguibile.py` | creazione dell'eseguibile con PyInstaller |
| `strumenti/banco_demodulatore.py` | confronto del demodulatore su registrazioni o simulazioni |
| `.github/workflows/` | compilazione automatica |
| `tests/` | test automatici |

## Servizi esterni

La mappa usa le tile di OpenStreetMap, i simboli APRS di [hessu/aprs-symbols](https://github.com/hessu/aprs-symbols)
e, per la ricerca delle località, Nominatim. Tutti richiedono Internet sul computer che apre la mappa
e sono soggetti alle rispettive policy d'uso ([tile](https://operations.osmfoundation.org/policies/tiles/),
[Nominatim](https://operations.osmfoundation.org/policies/nominatim/)): vanno bene per uso personale.
La ricezione e la decodifica funzionano anche senza Internet. La pubblicazione su Geopin, se attivata,
invia i pacchetti ricevuti al portale indicato nelle impostazioni.

## Licenza

Copyright (C) 2026 IZ2LPN op. Giorgio

APRS Master è software libero: puoi ridistribuirlo e/o modificarlo secondo i termini della
GNU General Public License pubblicata dalla Free Software Foundation, versione 3 della licenza
o (a tua scelta) qualsiasi versione successiva (`GPL-3.0-or-later`).

Il programma è distribuito nella speranza che sia utile, ma SENZA ALCUNA GARANZIA, nemmeno la
garanzia implicita di COMMERCIABILITÀ o di IDONEITÀ PER UN PARTICOLARE SCOPO. Il testo completo
è nel file `LICENSE` e nel pannello, in Aiuto → Licenza.

In pratica: chi riceve il programma, anche come eseguibile, deve ricevere anche questa licenza e
poter avere il codice sorgente; le versioni modificate che vengono distribuite devono restare sotto
GPL. Gli eseguibili creati dalla compilazione automatica includono già il file `LICENSE`.

### Componenti di terze parti

Tutti con licenze compatibili con la GPL:

| Componente | Uso | Licenza |
|---|---|---|
| NumPy | elaborazione del segnale | BSD-3-Clause |
| pyserial | modulo LoRa su porta seriale | BSD-3-Clause |
| sounddevice, PortAudio | scheda audio (PortAudio è incluso negli eseguibili Windows e macOS) | MIT |
| Python, Tcl/Tk | inclusi negli eseguibili | PSF, licenza in stile BSD |
| Leaflet 1.9.4 | mappa nel browser, inclusa in `aprs_mappa/static/leaflet/` con la sua licenza | BSD-2-Clause |
| Leaflet.markercluster | raggruppamento delle stazioni, incluso in `aprs_mappa/static/markercluster/` con la sua licenza | MIT |
| Dati e tile di OpenStreetMap | mappa, caricati dalla rete | ODbL; © OpenStreetMap contributors |
| Simboli APRS di hessu/aprs-symbols | scaricati al primo uso e conservati in locale, non distribuiti | vedi il [repository](https://github.com/hessu/aprs-symbols) |

L'icona del programma è un disegno originale creato per questo progetto ed è distribuita con la
stessa licenza del programma.
