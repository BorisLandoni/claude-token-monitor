@echo off
cd /d "%~dp0"
echo.
echo  Claude Monitor - Simulazione locale
echo  =====================================
echo.

REM Controlla che Python sia installato
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  ERRORE: Python non trovato.
    echo  Scaricalo da https://python.org e riprova.
    pause
    exit /b 1
)

REM Installa dipendenze se mancano
echo  Installazione dipendenze...
pip install fastapi uvicorn -q

echo.
echo  Avvio server su http://localhost:8080
echo  Premi Ctrl+C per fermare
echo.

python simulate.py
pause
