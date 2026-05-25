#!/bin/bash
cd "$(dirname "$0")"
echo ""
echo " Claude Monitor - Simulazione locale"
echo " ====================================="
echo ""

# Controlla Python
if ! command -v python3 &>/dev/null; then
    echo " ERRORE: Python3 non trovato."
    exit 1
fi

# Installa dipendenze
echo " Installazione dipendenze..."
pip3 install fastapi uvicorn -q

echo ""
echo " Avvio server su http://localhost:8080"
echo " Premi Ctrl+C per fermare"
echo ""

python3 simulate.py
