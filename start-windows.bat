@echo off
setlocal
cd /d "%~dp0rpi-monitor\backend"

echo.
echo  Claude Monitor — Avvio su Windows
echo  ====================================
echo.

REM Controlla Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERRORE: Python non trovato.
    echo  Scaricalo da https://python.org e riprova.
    pause & exit /b 1
)

REM Installa dipendenze Python
echo  [1/3] Installazione dipendenze Python...
pip install fastapi "uvicorn[standard]" httpx playwright pydantic -q
if %errorlevel% neq 0 (
    echo  ERRORE: pip fallito. Controlla la connessione.
    pause & exit /b 1
)

REM Installa browser Playwright (solo la prima volta, ~150MB)
echo  [2/3] Installazione browser Playwright (solo prima volta)...
python -m playwright install chromium
if %errorlevel% neq 0 (
    echo  ERRORE: playwright install fallito.
    pause & exit /b 1
)

REM Apri il browser dopo 3 secondi
echo  [3/3] Avvio server su http://localhost:8080 ...
echo.
echo  Apri il browser su: http://localhost:8080
echo  Premi Ctrl+C per fermare il server.
echo.

REM Apre il browser in background dopo 3s
start /b cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:8080"

REM Avvia il server (blocca finche non si chiude)
python server.py

pause
