#!/usr/bin/env bash
# ============================================================
#  Claude Token Monitor — Setup automatico per Raspberry Pi
#  Testato su: Raspberry Pi OS Bookworm (64-bit) / Bullseye
#  Uso:  bash setup-rpi.sh
#        NON_INTERACTIVE=1 bash setup-rpi.sh   (da UI)
# ============================================================
set -euo pipefail

REPO_URL="https://github.com/BorisLandoni/claude-token-monitor.git"
INSTALL_DIR="$HOME/claude-token-monitor"
SERVICE_NAME="claude-monitor"
PORT=8080
NON_INTERACTIVE=${NON_INTERACTIVE:-0}

# ── Colori ───────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

step()  { echo -e "\n${CYAN}${BOLD}[•] $*${RESET}"; }
ok()    { echo -e "  ${GREEN}✓ $*${RESET}"; }
warn()  { echo -e "  ${YELLOW}⚠ $*${RESET}"; }
err()   { echo -e "  ${RED}✗ $*${RESET}"; }
banner(){ echo -e "\n${BOLD}$*${RESET}"; }

# ── Banner ────────────────────────────────────────────────────
clear
echo -e "${CYAN}${BOLD}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║     Claude Token Monitor — Setup RPi         ║"
echo "  ║     https://github.com/BorisLandoni/         ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${RESET}"
echo "  Questo script installa tutto il necessario:"
echo "  Node.js, Claude Code, Python, il monitor e il"
echo "  servizio di avvio automatico."
echo ""
if [ "$NON_INTERACTIVE" = "0" ]; then
  echo "  Premi INVIO per continuare o Ctrl+C per annullare."
  read -r
fi

# ── 1. Aggiornamento sistema ──────────────────────────────────
step "Aggiornamento pacchetti di sistema..."
sudo apt-get update -qq
sudo apt-get install -y -qq \
    git curl wget python3 python3-pip python3-venv \
    libglib2.0-0 libnss3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
    libasound2 libwayland-client0 2>/dev/null || true
ok "Pacchetti di sistema installati"

# ── 2. Node.js (via NodeSource, versione LTS) ─────────────────
step "Installazione Node.js LTS..."
if command -v node &>/dev/null && node --version | grep -qE '^v(18|20|22)'; then
    ok "Node.js già installato: $(node --version)"
else
    curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
    sudo apt-get install -y -qq nodejs
    ok "Node.js installato: $(node --version)"
fi

# ── 3. Claude Code ────────────────────────────────────────────
step "Installazione Claude Code (claude CLI)..."
ARCH=$(uname -m)
if command -v claude &>/dev/null; then
    ok "Claude Code già installato: $(claude --version 2>/dev/null || echo 'ok')"
elif [ "$ARCH" = "armv7l" ] || [ "$ARCH" = "armv6l" ]; then
    warn "Architettura 32-bit ($ARCH) non supportata da Claude Code."
    warn "Il monitor funziona ugualmente se copi ~/.claude/.credentials.json"
    warn "da un PC dove Claude Code è già installato (via scp)."
else
    # Assicura che npm sia disponibile (NodeSource a volte non lo include)
    if ! command -v npm &>/dev/null; then
        sudo apt-get install -y -qq npm
    fi
    NPM_BIN=$(command -v npm)
    sudo "$NPM_BIN" install -g @anthropic-ai/claude-code --quiet
    # Esegui postinstall se necessario
    CLAUDE_MOD="$(npm root -g 2>/dev/null)/@anthropic-ai/claude-code/install.cjs"
    if [ -f "$CLAUDE_MOD" ] && ! command -v claude &>/dev/null; then
        node "$CLAUDE_MOD" 2>/dev/null || true
    fi
    ok "Claude Code installato"
fi

# ── 4. Login Claude Code ──────────────────────────────────────
step "Login a Claude Code..."
CRED_FILE="$HOME/.claude/.credentials.json"

if [ -f "$CRED_FILE" ]; then
    ok "Credenziali Claude Code già presenti — login saltato"
elif [ "$NON_INTERACTIVE" = "1" ]; then
    warn "Credenziali non trovate."
    warn "Opzione A (64-bit): apri un terminale e digita: claude login"
    warn "Opzione B (32-bit): copia ~/.claude/.credentials.json dal PC via scp"
else
    echo ""
    echo -e "  ${YELLOW}Devi effettuare il login a Claude Code.${RESET}"
    echo "  Si aprirà il browser: accedi con il tuo account Anthropic."
    echo "  Una volta completato il login, torna qui."
    echo ""
    claude login
    if [ -f "$CRED_FILE" ]; then
        ok "Login completato — credenziali salvate"
    else
        warn "File credenziali non trovato dopo il login."
        warn "Il monitor potrebbe non funzionare. Prova: claude login"
    fi
fi

# ── 5. Clone/aggiornamento repository ────────────────────────
step "Download Claude Token Monitor da GitHub..."
if [ -d "$INSTALL_DIR/.git" ]; then
    ok "Repository già presente — aggiornamento in corso..."
    git -C "$INSTALL_DIR" pull --quiet
else
    git clone --quiet "$REPO_URL" "$INSTALL_DIR"
    ok "Repository clonato in $INSTALL_DIR"
fi

# ── 6. Ambiente Python virtuale + dipendenze ──────────────────
step "Creazione ambiente Python e installazione dipendenze..."
VENV_DIR="$INSTALL_DIR/rpi-monitor/backend/.venv"
python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet \
    fastapi "uvicorn[standard]" httpx playwright pydantic
ok "Dipendenze Python installate"

# ── 7. Playwright browser ─────────────────────────────────────
step "Installazione browser Playwright (Chromium, ~150MB)..."
"$VENV_DIR/bin/python" -m playwright install chromium
ok "Playwright Chromium installato"

# ── 8. Script di avvio ────────────────────────────────────────
step "Creazione script di avvio..."
cat > "$INSTALL_DIR/start.sh" <<EOF
#!/usr/bin/env bash
# Avvia il backend Claude Token Monitor
cd "$INSTALL_DIR/rpi-monitor/backend"
exec "$VENV_DIR/bin/python" server.py
EOF
chmod +x "$INSTALL_DIR/start.sh"
ok "Script $INSTALL_DIR/start.sh creato"

# ── 9. Servizio systemd (autostart al boot) ───────────────────
step "Configurazione servizio systemd ($SERVICE_NAME)..."
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Claude Token Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR/rpi-monitor/backend
ExecStart=$VENV_DIR/bin/python server.py
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

sleep 2
if systemctl is-active --quiet "$SERVICE_NAME"; then
    ok "Servizio avviato e abilitato all'avvio"
else
    warn "Il servizio non risulta attivo. Controlla: journalctl -u $SERVICE_NAME -n 30"
fi

# ── 10. Display 5" HDMI 800x480 touch (lcdwiki) ─────────────
step "Configurazione display 5\" HDMI 800x480..."

# Percorso config.txt cambia tra Bullseye e Bookworm
if [ -f /boot/firmware/config.txt ]; then
    CONFIG_FILE="/boot/firmware/config.txt"
elif [ -f /boot/config.txt ]; then
    CONFIG_FILE="/boot/config.txt"
else
    CONFIG_FILE=""
fi

DISPLAY_CONFIGURED=0
if [ -n "$CONFIG_FILE" ]; then
    if grep -q "hdmi_cvt 800 480" "$CONFIG_FILE" 2>/dev/null; then
        ok "Risoluzione display già configurata in $CONFIG_FILE"
        DISPLAY_CONFIGURED=1
    else
        sudo tee -a "$CONFIG_FILE" > /dev/null <<'DISPLAY_CONF'

# ── Claude Monitor — 5" HDMI Display 800x480 (lcdwiki) ───────
max_usb_current=1
hdmi_group=2
hdmi_mode=87
hdmi_cvt 800 480 60 6 0 0 0
hdmi_drive=1
display_rotate=0
DISPLAY_CONF
        ok "Risoluzione 800x480 aggiunta a $CONFIG_FILE"
        DISPLAY_CONFIGURED=1
    fi

    # Touch screen
    # Il display lcdwiki 5" HDMI può avere touch USB (plug-and-play)
    # oppure touch resistivo via GPIO (serve overlay ADS7846).
    # Rilevamento automatico: se presente USB HID touch → nessuna config.
    # In caso di touch GPIO, il blocco commentato sotto va decommentato.
    if ! grep -q "ads7846\|claude_touch" "$CONFIG_FILE" 2>/dev/null; then
        sudo tee -a "$CONFIG_FILE" > /dev/null <<'TOUCH_CONF'
# Touch GPIO (ADS7846) — decommenta SOLO se il touch non funziona via USB:
#dtoverlay=ads7846,cs=1,penirq=25,penirq_pull=2,speed=50000,keep_vref_on=0,swapxy=0,pmax=255,xohms=150,xmin=200,xmax=3900,ymin=200,ymax=3900
# claude_touch=configured
TOUCH_CONF
        ok "Overlay touch aggiunto (commentato — attivo solo se necessario)"
    fi
else
    warn "config.txt non trovato — configura manualmente la risoluzione"
fi

# Rotazione schermo per orientamento landscape (Wayland / X11)
# Imposta la rotazione a livello di display manager se necessario
if [ "$DISPLAY_CONFIGURED" = "1" ]; then
    # Crea script di calibrazione touch da eseguire al primo avvio
    TOUCH_CAL="$INSTALL_DIR/touch-calibrate.sh"
    cat > "$TOUCH_CAL" <<'TOUCHSCRIPT'
#!/usr/bin/env bash
# Calibrazione touch screen 5" lcdwiki — esegui una volta dopo il riavvio
# Richiede: sudo apt-get install -y xinput-calibrator
if command -v xinput_calibrator &>/dev/null; then
    xinput_calibrator
else
    echo "Installa con: sudo apt-get install -y xinput-calibrator"
    echo "Poi esegui: xinput_calibrator"
fi
TOUCHSCRIPT
    chmod +x "$TOUCH_CAL"
    ok "Script calibrazione touch salvato in $TOUCH_CAL"
fi

# ── 11. Kiosk Chromium (modalità display) ────────────────────
step "Configurazione Chromium kiosk (opzionale)..."

AUTOSTART_DIR="$HOME/.config/autostart"
AUTOSTART_FILE="$AUTOSTART_DIR/claude-monitor-kiosk.desktop"

mkdir -p "$AUTOSTART_DIR"
cat > "$AUTOSTART_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=Claude Monitor Kiosk
Exec=bash -c 'sleep 5 && chromium-browser --kiosk --noerrdialogs --disable-infobars --no-first-run --disable-session-crashed-bubble --app=http://localhost:$PORT'
X-GNOME-Autostart-enabled=true
EOF
ok "Kiosk configurato in $AUTOSTART_FILE"
echo "    (Il browser si aprirà in modalità kiosk al prossimo avvio del desktop)"

# ── 12. Firewall: porta 8080 ──────────────────────────────────
if command -v ufw &>/dev/null; then
    step "Apertura porta $PORT nel firewall..."
    sudo ufw allow "$PORT/tcp" &>/dev/null || true
    ok "Porta $PORT aperta"
fi

# ── Fine ──────────────────────────────────────────────────────
LOCAL_IP=$(hostname -I 2>/dev/null | awk '{print $1}')

echo ""
echo -e "${GREEN}${BOLD}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║           Setup completato!                  ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${RESET}"
echo -e "  Dashboard locale: ${CYAN}http://localhost:$PORT${RESET}"
[ -n "$LOCAL_IP" ] && echo -e "  Da altri device:  ${CYAN}http://$LOCAL_IP:$PORT${RESET}"
echo ""
echo "  Comandi utili:"
echo "    sudo systemctl status $SERVICE_NAME   # stato server"
echo "    sudo systemctl restart $SERVICE_NAME  # riavvia server"
echo "    journalctl -u $SERVICE_NAME -f        # log in tempo reale"
echo ""
echo -e "  ${YELLOW}Il servizio si avvia automaticamente ad ogni riaccensione.${RESET}"
echo ""
