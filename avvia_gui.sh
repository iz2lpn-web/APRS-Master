#!/bin/sh
# APRS Master by IZ2LPN op. Giorgio - avvio del pannello di controllo (Linux e macOS)
# Al primo avvio crea l'ambiente Python nella cartella .venv e installa le dipendenze.
cd "$(dirname "$0")" || exit 1

# ./avvia_gui.sh --collegamento : aggiunge "APRS Master" al menu delle applicazioni
if [ "$1" = "--collegamento" ]; then
    dir="$(pwd)"
    dest="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    mkdir -p "$dest"
    cat > "$dest/aprs-master.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=APRS Master
Comment=Ricevitore APRS con mappa OpenStreetMap
Exec="$dir/avvia_gui.sh"
Path=$dir
Icon=$dir/strumenti/icone/icona-256.png
Terminal=false
Categories=HamRadio;Network;
DESKTOP
    echo "Collegamento creato: $dest/aprs-master.desktop"
    exit 0
fi

if [ ! -x .venv/bin/python ]; then
    if ! command -v python3 >/dev/null 2>&1; then
        echo "Python 3 non trovato. Installa Python 3.9 o successivo."
        exit 1
    fi
    if ! python3 -c "import tkinter" >/dev/null 2>&1; then
        echo "Manca Tkinter, necessario per la finestra del programma."
        echo "  Debian/Ubuntu:  sudo apt install python3-tk python3-venv libportaudio2"
        echo "  Fedora:         sudo dnf install python3-tkinter portaudio"
        echo "  macOS:          usa Python da python.org (Tkinter incluso)"
        exit 1
    fi
    echo "Prima installazione: preparo l'ambiente Python, attendi..."
    python3 -m venv .venv || {
        echo "Creazione dell'ambiente non riuscita (su Debian/Ubuntu: sudo apt install python3-venv)."
        rm -rf .venv; exit 1; }
    .venv/bin/python -m pip install --upgrade pip >/dev/null
    if ! .venv/bin/python -m pip install -r requirements.txt; then
        echo "Installazione non riuscita: controlla la connessione a Internet e riprova."
        rm -rf .venv; exit 1
    fi
    echo "Installazione completata."
fi
exec .venv/bin/python -m aprs_mappa "$@"
