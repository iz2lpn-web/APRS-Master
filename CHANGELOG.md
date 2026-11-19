# Registro delle modifiche

## 1.7.0 (novembre 2026)

- **Messaggi APRS**: ricezione dei messaggi per la propria stazione con conferma automatica, invio
  tramite APRS-IS con numero e ritrasmissione fino alla conferma (stato consegnato, senza conferma,
  rifiutato), messaggi sentiti tra altre stazioni e bollettini. Scheda Messaggi nel pannello e nella
  mappa; archivio in `~/.aprs_mappa_messaggi.db`; filtro APRS-IS `g/` per ricevere i messaggi da
  qualunque distanza
- **Avvisi**: stazioni osservate (con prefissi e SSID), zone disegnate sulla mappa (entrata e
  uscita), emergenza Mic-E, messaggi per la propria stazione. Suono e finestra nel pannello, suono e
  notifiche del browser nella mappa
- **Meteo**: rapporti meteo con e senza posizione, temperatura accanto al nominativo, riassunto nel
  riquadro e grafici di temperatura, vento, pressione, umidità e pioggia (dallo storico fino a 30 giorni)
- **Telemetria**: valori convertiti secondo le definizioni PARM, UNIT, EQNS e BITS, con grafici
- **Mappa allineata a Geopin**: filtri per tipo e ricezione, raggruppamento delle stazioni vicine,
  etichette che non si sovrappongono, riproduzione degli spostamenti, grafici, emergenze in evidenza
- **Servizio senza finestra** (`--servizio`) per Raspberry Pi e computer sempre accesi, con il
  **pannello web** `/pannello`: stato, avvio e arresto, tutta la configurazione. Password del
  pannello (hash PBKDF2, blocco dopo i tentativi sbagliati) per usarlo dalla rete; senza password
  inviare messaggi, cambiare regole e posizione si può solo dal computer stesso.
  `--installa-servizio` scrive il file per systemd (esempio in `esempi/`)
- **Codice di collegamento Geopin** (`geopin1:…`, da Geopin 1.5.1): indirizzo e chiave in una riga,
  nella finestra Geopin, nel pannello web e con `--geopin-code`
- **Interfaccia in inglese**: pannello, mappa e pannello web (menu Visualizza → Lingua, `--lang`)
- Sicurezza: con Python 3.13 la verifica dei certificati HTTPS accettava una catena che finiva su un
  certificato intermedio presente tra le autorità di sistema (opzione `VERIFY_X509_PARTIAL_CHAIN`
  attiva per impostazione predefinita). Ora la catena deve arrivare sempre a un'autorità radice

## 1.6.6 (novembre 2026)

- La finestra si adatta allo schermo: la misura iniziale si calcola sul contenuto reale (che cresce
  con la scala dei caratteri di Windows al 125-150%) e sull'area utile dello schermo, esclusa la barra
  delle applicazioni, e la finestra si apre sempre tutta visibile. Su schermi grandi la tabella delle
  stazioni è più alta
- Barre di scorrimento verticale e orizzontale nella finestra principale e nelle finestre di
  impostazione, visibili solo quando la finestra è più piccola del pannello. La rotella del mouse
  scorre il pannello (Shift + rotella in orizzontale); sopra la tabella e i registri scorre loro
- La finestra si può rimpicciolire fino a 480×360; se il pannello non entra nello schermo si apre
  ingrandita

## 1.6.5 (novembre 2026)

- Geopin: insieme allo stato il programma invia come si presenta la stazione ricevente (nominativo-SSID,
  simbolo del tipo di stazione, commento e apparato) e la posizione aggiornata, anche se scelta sulla
  mappa a ricezione avviata. Geopin 1.1.5 la evidenzia sulla mappa come fa APRS Master

## 1.6.4 (novembre 2026)

- Corretto: la prova di collegamento a Geopin (e la pubblicazione) fallivano con "certificate verify
  failed" quando il portale invia il certificato senza il certificato intermedio (catena incompleta,
  frequente sugli hosting), anche se il browser apre il sito senza avvisi. Ora, come i browser, il
  programma recupera l'intermedio dall'indirizzo indicato nel certificato (estensione AIA) e ritenta.
  La verifica resta completa e non viene mai disattivata
- Alle autorità di sistema si aggiungono quelle di certifi (nuova dipendenza, inclusa anche negli
  eseguibili): risolve lo stesso errore dove Python non trova l'archivio dei certificati, come con il
  Python di python.org su macOS
- Messaggi chiari per i certificati sbagliati: scaduto, autofirmato, emesso per un altro nome, catena
  incompleta (con l'indicazione di cosa correggere sull'hosting)
- Lo stesso vale per lo scaricamento di mappe e simboli

## 1.6.3 (novembre 2026)

- Pannello: "Tipo dispositivo" diventa "Tipo di stazione", con l'indicazione che determina il simbolo
  sulla mappa e su aprs.fi; Commento e Apparato dicono con quale voce compaiono su aprs.fi
  (Commento, Ultimo rapporto)
- README: tabella "Come appare la stazione su aprs.fi". Il campo "Dispositivo" di aprs.fi dipende
  dall'identificativo del programma (TOCALL): APRS Master usa ancora quello sperimentale `APZRAP`
  ("Experimental"). Nuovo file RICHIESTA_TOCALL.md con la procedura e il testo per chiederne uno
  assegnato

## 1.6.2 (novembre 2026)

- Corretto: la stazione non compariva su aprs.fi senza l'iGate attivo. Il passcode veniva inviato ad
  APRS-IS solo con l'iGate, quindi il collegamento restava non verificato e l'annuncio della stazione
  (posizione con nominativo-SSID e stato dell'apparato) non partiva mai. Ora l'annuncio è un'opzione
  a sé, "Annuncia la stazione", attiva in modo predefinito: basta il passcode, anche senza iGate
- Corretto: una posizione ricevente scelta sulla mappa a ricezione avviata non veniva usata per
  l'annuncio fino al riavvio della ricezione
- Il riquadro APRS-IS dice se e quando la stazione è stata annunciata, o perché no (passcode mancante
  o non accettato, posizione mancante), distingue la sola ricezione senza passcode da un passcode
  sbagliato e segnala l'iGate spento per mancanza di una sorgente radio
- Riga di comando: `--no-beacon`

## 1.6.1 (novembre 2026)

- Il programma si chiama ora **APRS Master by IZ2LPN op. Giorgio**: barra del titolo, mappa nel
  browser, finestra Informazioni, commento predefinito dell'iGate, eseguibile (`APRSMaster`),
  collegamento nel menu di Linux e identificativo verso APRS-IS e i server di mappe. Restano invariati
  il nome del pacchetto (`aprs_mappa`), il comando `aprs-mappa`, le impostazioni e lo storico, quindi
  non si perde nulla. Su Linux il vecchio collegamento "Ricezione APRS" si può togliere con
  `rm ~/.local/share/applications/ricezione-aprs.desktop` e ricreare con `./avvia_gui.sh --collegamento`
- Pannello: il riquadro "Mappa nel browser" passa nella colonna sinistra; la tabella delle stazioni
  in basso è più alta
- Corretto: attivando Geopin con un altro campo della configurazione non valido la casella restava
  spuntata ma la pubblicazione non partiva, senza avviso. Ora compare il messaggio con il campo da
  correggere (anche dopo l'OK nelle impostazioni di Geopin)

## 1.6.0 (novembre 2026)

- Pubblicazione sul portale Geopin: il programma invia al portale tutto ciò che riceve (VHF, LoRa e
  APRS-IS) a intervalli regolari (1, 5, 15, 30 o 60 minuti), con la chiave del ricevitore creata sul
  portale. Riquadro Geopin nel pannello con "Pubblica ora", impostazioni e "Prova collegamento"
- Coda su disco (`~/.aprs_mappa_geopin.db`): nessun pacchetto perso se la rete manca o il programma
  viene chiuso; numerazione progressiva contro i doppioni; invio compresso a gruppi. Lo stato della
  ricezione e la posizione ricevente viaggiano con i pacchetti; i file WAV non vengono pubblicati
- Mappa: etichette dei nominativi più leggibili (carattere più grande, fondo pieno, bordo marcato) con
  l'SSID evidenziato in blu; le stazioni sentite solo da Internet hanno il simbolo attenuato ma
  l'etichetta leggibile
- Mappa: la propria stazione mostra sempre il nominativo con l'SSID accanto al simbolo
- Riga di comando: `--geopin-url`, `--geopin-key`, `--geopin-interval`, `--geopin-test`

## 1.5.1 (ottobre 2026)

- Definizione della stazione: nuovo campo **Apparato** per una descrizione libera dell'attrezzatura
  (massimo 62 caratteri). L'iGate la invia ad APRS-IS come pacchetto di stato insieme a ogni annuncio
  di posizione, e compare nel segnaposto della propria stazione sulla mappa. Modificabile anche
  durante la ricezione
- Riga di comando: `--station-equipment`

## 1.5.0 (ottobre 2026)

- Definizione della stazione: nuovi campi **Tipo dispositivo** (iGate RX VHF, iGate RX LoRa,
  stazione fissa, stazione portatile) e **Commento**, usati nell'annuncio di posizione dell'iGate
  verso APRS-IS e nel segnaposto della propria stazione sulla mappa (simbolo APRS, nominativo, tipo
  e commento). Modificabili anche durante la ricezione; il commento viene controllato (massimo 43
  caratteri, solo caratteri ammessi in APRS)
- Riga di comando: `--station-type` e `--station-comment`
- Corretto: arrotondamento delle coordinate nell'annuncio dell'iGate (ad esempio 45,999999° veniva
  scritto 45°60,00' invece di 46°00,00')

## 1.4.0 (ottobre 2026)

- Ricezione LoRa APRS da un modulo esterno (porta seriale USB o rete TCP), in parallelo alla
  sorgente VHF. Riconoscimento automatico del formato: testo APRS anche dentro i messaggi di
  diagnostica del firmware, intestazione LoRa `<\xff\x01`, protocollo KISS con AX.25 o testo
- Stazioni distinte per banda: colonna Ricezione (VHF, LoRa, Internet e combinazioni), etichetta
  viola e contatori per banda sulla mappa, registro dei pacchetti con l'origine
- Copertura calcolata per banda, con scelta VHF/LoRa
- L'iGate inoltra anche i pacchetti LoRa (con le stesse regole); i pacchetti dei file WAV
  restano esclusi anche quando LoRa è attivo
- Nuova sorgente "Nessuna sorgente VHF" per usare solo LoRa e/o APRS-IS
- Pannello: riquadro LoRa con impostazioni ed elenco delle porte seriali; finestra più alta
- Nuova dipendenza: pyserial

## 1.3.1 (ottobre 2026)

- Nome del programma in testata: "APRS Receiver by IZ2LPN op. Giorgio" (barra del titolo,
  intestazione della mappa e della scheda del browser, finestra Informazioni, intestazione dei sorgenti)
- Corretto: la riga del confronto con APRS-IS compariva nella mappa anche senza collegamento

## 1.3.0 (ottobre 2026)

- Copyright: IZ2LPN op. Giorgio
- Mappe: server di mappe personalizzato con cache su disco (almeno 7 giorni, funziona anche senza
  rete) e mappe offline da file MBTiles; Leaflet incluso e simboli APRS conservati in locale.
  Le mappe di tile.openstreetmap.org restano online, come richiede la loro policy
- Mappa di copertura: celle delle stazioni ricevute direttamente o tramite digipeater e distanza
  massima di ricezione diretta per direzione
- APRS-IS: iGate in sola ricezione secondo le regole aprs-is.net (qAO, esclusioni NOGATE, RFONLY,
  TCPIP, TCPXX, terze parti e interrogazioni; nessuna modifica al percorso) con annuncio della
  posizione come iGate RX
- Confronto con APRS-IS: stazioni ricevute via radio, solo dalla rete o in entrambi i modi, nel
  pannello e sulla mappa; filtro "Solo via radio"
- Lo storico registra l'origine di ogni pacchetto (radio o Internet); gli archivi delle versioni
  precedenti vengono aggiornati automaticamente

## 1.2.0 (ottobre 2026)

- Licenza GNU GPL v3 o successiva (GPL-3.0-or-later): file `LICENSE`, intestazione SPDX in ogni
  sorgente, metadati del pacchetto, avviso di licenza nel pannello (Aiuto → Informazioni) e testo
  completo consultabile in Aiuto → Licenza; la licenza è inclusa anche negli eseguibili
- Icona del programma: finestra del pannello, eseguibili Windows (.ico) e macOS (.icns), scheda del
  browser e collegamento nel menu delle applicazioni Linux (`./avvia_gui.sh --collegamento`)
- `--version` mostra copyright e licenza; l'autotest verifica anche licenza e icone

## 1.1.0 (ottobre 2026)

Demodulatore
- Sette "slicer" in parallelo con pesi diversi fra i due toni: compensano lo sbilanciamento
  dovuto a de-enfasi o pre-enfasi della radio. Con la de-enfasi tipica (750 µs) a SNR 9 dB
  i pacchetti decodificati passano dal 20-40% circa al 95-100% (banco di prova simulato)
- Secondo ramo di demodulazione con pre-enfasi, per radio con de-enfasi marcata
- Correzione degli errori su 1 bit o 2 bit adiacenti (l'errore tipico della codifica NRZI),
  accettata solo se il bit corretto era fra le decisioni più incerte del frame: sul banco di
  prova, con segnale debole, i pacchetti recuperati aumentano di circa il 70% senza produrne di falsi
- Profilo "leggero" per computer poco potenti; correzione disattivabile
- Contatore dei frame recuperati con la correzione
- `strumenti/banco_demodulatore.py` per confrontare le versioni su una registrazione reale

Storico
- Archivio persistente dei pacchetti (SQLite) con durata configurabile
- All'avvio la mappa mostra le stazioni e le tracce delle ultime ore
- Esportazione delle tracce in GPX dal pannello (menu Storico), dalla mappa (riquadro di ogni
  stazione) e dalla riga di comando (`--export-gpx`)

Compilazione
- GitHub Actions: test su Windows, macOS e Linux (Python 3.9-3.13), eseguibili autonomi con
  autotest e release automatica quando si pubblica un tag
- Nuova opzione `--self-test` per verificare un'installazione o un eseguibile

## 1.0.0 (ottobre 2026)

Prima versione completa.

- Demodulatore AFSK Bell 202 (1200 baud) integrato, dalla scheda audio o da file WAV
- Collegamento a Direwolf o ad altri TNC tramite KISS su TCP, con riconnessione automatica
- Decodifica APRS: posizioni non compresse e compresse, Mic-E, oggetti, item, NMEA, terze parti;
  rotta, velocità, quota e stato Mic-E
- Mappa OpenStreetMap nel browser, aggiornata in tempo reale: simboli APRS, tracce, distanza e
  azimut dalla stazione ricevente, elenco stazioni e registro dei pacchetti
- Pannello di controllo grafico: configurazione, avvio e arresto, indicatore del livello audio,
  tabella delle stazioni, registro; impostazioni salvate
- Scelta della posizione ricevente sulla mappa: clic, ricerca di località, coordinate o locatore Maidenhead
- Tema chiaro, scuro o come il sistema, applicato sia al pannello sia alla mappa
- Avviatori per Windows, Linux e macOS; wheel installabile; script per creare l'eseguibile
- Test automatici
