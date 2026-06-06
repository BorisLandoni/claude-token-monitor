#!/usr/bin/env bash
# Claude Monitor — Raspberry Pi installer
# Usage: bash install.sh
# Tested on: Raspberry Pi OS (Bookworm, Bullseye)

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="$REPO_DIR/backend"
SETUP_DIR="$REPO_DIR/setup"
VENV_DIR="$BACKEND_DIR/.venv"
SERVICE_USER="${SUDO_USER:-$USER}"
HOME_DIR=$(eval echo "~$SERVICE_USER")
AUTOSTART_DIR="$HOME_DIR/.config/autostart"
PORT=8080

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║     Claude Monitor — RPi Setup           ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── 1. System packages ────────────────────────────────────────────────────────
echo "[1/5] Installazione pacchetti di sistema..."
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends \
  python3-pip python3-venv python3-full \
  chromium-browser \
  fonts-noto fonts-noto-cjk \
  xdotool unclutter \
  2>/dev/null || true

# Detect Chromium path
CHROMIUM_PATH=""
for p in /usr/bin/chromium-browser /usr/bin/chromium /snap/bin/chromium; do
  if [ -x "$p" ]; then
    CHROMIUM_PATH="$p"
    break
  fi
done
echo "    Chromium: ${CHROMIUM_PATH:-non trovato}"

# ── 2. Python virtual environment ────────────────────────────────────────────
echo "[2/5] Creazione virtual environment Python..."
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$BACKEND_DIR/requirements.txt" -q
echo "    Dipendenze Python installate."

# ── 3. systemd service (backend) ─────────────────────────────────────────────
echo "[3/5] Installazione servizio systemd..."
SERVICE_FILE="/etc/systemd/system/claude-monitor.service"
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Claude Monitor Backend
After=network-online.target
Wants=network-online.target

[Service]
User=$SERVICE_USER
WorkingDirectory=$BACKEND_DIR
Environment=PORT=$PORT
ExecStart=$VENV_DIR/bin/uvicorn server:app --host 0.0.0.0 --port $PORT
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable claude-monitor
sudo systemctl restart claude-monitor
echo "    Servizio claude-monitor attivo sulla porta $PORT."

# ── 5. Kiosk startup script ───────────────────────────────────────────────────
echo "[4/5] Configurazione kiosk..."
KIOSK_SCRIPT="$SETUP_DIR/start-kiosk.sh"
cat > "$KIOSK_SCRIPT" <<KIOSK
#!/usr/bin/env bash
# Kiosk launcher — attende il backend poi apre Chromium a schermo intero

# Disabilita screen saver e risparmio energetico
xset s off 2>/dev/null || true
xset s noblank 2>/dev/null || true
xset -dpms 2>/dev/null || true

# Nascondi il cursore dopo 1 secondo di inattività
unclutter -idle 1 -root &

# Attendi che il backend sia pronto
for i in \$(seq 1 20); do
  curl -sf http://localhost:$PORT/health > /dev/null 2>&1 && break
  sleep 1
done

# Avvia Chromium in modalità kiosk
exec ${CHROMIUM_PATH:-chromium-browser} \\
  --kiosk \\
  --app=http://localhost:$PORT \\
  --no-first-run \\
  --disable-infobars \\
  --disable-session-crashed-bubble \\
  --disable-translate \\
  --noerrdialogs \\
  --check-for-update-interval=604800 \\
  --touch-events=enabled \\
  --enable-features=OverlayScrollbar \\
  --disable-features=TranslateUI \\
  "http://localhost:$PORT"
KIOSK
chmod +x "$KIOSK_SCRIPT"

# LXDE / Openbox autostart
mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_DIR/claude-kiosk.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Claude Monitor Kiosk
Exec=$KIOSK_SCRIPT
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
DESKTOP
echo "    Kiosk configurato (avvio automatico al login desktop)."

# ── 6. Done ───────────────────────────────────────────────────────────────────
echo "[5/5] Installazione completata!"
echo ""
echo "  ┌─────────────────────────────────────────────────┐"
echo "  │  Claude Monitor è in esecuzione!                │"
echo "  │                                                 │"
echo "  │  Dashboard: http://localhost:$PORT              │"
echo "  │  Dal tuo PC: http://$(hostname -I | awk '{print $1}'):$PORT      │"
echo "  │                                                 │"
echo "  │  Prossimi passi:                                │"
echo "  │  1. Apri http://$(hostname -I | awk '{print $1}'):$PORT         │"
echo "  │  2. Vai in IMPOSTAZIONI                         │"
echo "  │  3. Inserisci le credenziali Claude.ai          │"
echo "  │  4. Riavvia il Raspberry Pi per il kiosk        │"
echo "  └─────────────────────────────────────────────────┘"
echo ""
echo "  Comandi utili:"
echo "    sudo systemctl status claude-monitor   # stato servizio"
echo "    sudo journalctl -u claude-monitor -f   # log in tempo reale"
echo "    sudo systemctl restart claude-monitor  # riavvia"
echo ""
