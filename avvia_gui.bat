@echo off
rem APRS Master by IZ2LPN op. Giorgio - avvio del pannello di controllo (Windows)
rem Al primo avvio crea l'ambiente Python nella cartella .venv e installa le dipendenze.
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" goto avvio

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (where python >nul 2>nul && set "PY=python")
if not defined PY goto nopython

echo Prima installazione: preparo l'ambiente Python, attendi...
%PY% -m venv .venv || goto errore
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto errore
echo Installazione completata.

:avvio
start "" ".venv\Scripts\pythonw.exe" -m aprs_mappa
exit /b 0

:nopython
echo Python non trovato.
echo Installa Python 3.9 o successivo da https://www.python.org/downloads/
echo e spunta "Add python.exe to PATH" durante l'installazione.
pause
exit /b 1

:errore
echo.
echo Installazione non riuscita. Controlla che:
echo  - il computer sia connesso a Internet;
echo  - Python sia installato da https://www.python.org/downloads/ (non dal Microsoft Store).
if exist ".venv" rmdir /s /q ".venv"
pause
exit /b 1
