# Guida Completa — Claude Monitor su Raspberry Pi 4

Installazione passo-passo del monitor Claude.ai su Raspberry Pi 4 con display touch da 5" HDMI (800×480).  
Il sistema legge automaticamente l'utilizzo del tuo account Claude.ai e lo mostra su un display fisico dedicato.

---

## Indice

1. [Hardware necessario](#1-hardware-necessario)
2. [Installazione Raspberry Pi OS](#2-installazione-raspberry-pi-os)
3. [Collegamento del display](#3-collegamento-del-display)
4. [Prima accensione e WiFi](#4-prima-accensione-e-wifi)
5. [Accesso remoto SSH](#5-accesso-remoto-ssh)
6. [Installazione del software](#6-installazione-del-software)
7. [Primo accesso a Claude.ai](#7-primo-accesso-a-claudeai)
8. [Verifica che tutto funzioni](#8-verifica-che-tutto-funzioni)
9. [Impostazioni e personalizzazione](#9-impostazioni-e-personalizzazione)
10. [Gestione del servizio](#10-gestione-del-servizio)
11. [Aggiornamenti](#11-aggiornamenti)
12. [Risoluzione problemi](#12-risoluzione-problemi)

---

## 1. Hardware necessario

| Componente | Modello consigliato | Note |
|---|---|---|
| Single-board computer | **Raspberry Pi 3 Model B+** (usato nel prototipo) — o Pi 4 / 5 | Sul 3B+ il display si innesta a filo sull'HDMI full-size. Il Pi 4/5 è più veloce ma ha micro-HDMI. |
| Display touch HDMI | [5" 800×480 resistivo — Futuranet](https://futuranet.it/prodotto/display-touch-screen-5-800x480-pixel/) | Touch resistivo via GPIO (ADS7846). Si appoggia sul connettore a 40 pin del Pi. |
| Ponticello / cavo HDMI | **incluso con il display** | Sul Pi 3B+ il ponticello HDMI si collega diretto; sul Pi 4/5 serve adattatore micro-HDMI → HDMI. |
| MicroSD | **16 GB Classe 10** minimo, 32 GB consigliato | Samsung Endurance o SanDisk Endurance |
| Alimentatore | **USB-C 27W / 5.1V 3A** (Pi 4/5) o **microUSB 5V 2.5A** (Pi 3B+) | Evita problemi di sotto-tensione |
| Case (opzionale) | Case compatibile con display su GPIO | Per montaggio fisso |

> **Nota touch**: il pannello è **resistivo** e il touch viaggia sul **connettore GPIO a 40 pin** (controller ADS7846), da cui il display ricava anche l'alimentazione. Non è un touch USB plug-and-play: la configurazione (overlay `ads7846` + calibrazione X11) è fatta automaticamente da `setup-rpi.sh`.
> **Nota HDMI**: il Raspberry Pi 4/5 ha **due porte micro-HDMI**; usa la porta **HDMI 0** (vicina all'USB-C) con un adattatore micro-HDMI → HDMI. Il Pi 3B+ ha l'HDMI full-size, quindi il ponticello incluso si collega direttamente.

---

## 2. Installazione Raspberry Pi OS

### 2.1 Scarica Raspberry Pi Imager

1. Vai su **https://www.raspberrypi.com/software/**
2. Scarica e installa **Raspberry Pi Imager** per Windows (o macOS/Linux)
3. Inserisci la microSD nel PC (adattatore SD o USB reader)

### 2.2 Configura l'immagine

Apri Raspberry Pi Imager e configura:

| Campo | Valore |
|---|---|
| **Device** | Raspberry Pi 4 |
| **OS** | Raspberry Pi OS (64-bit) — con desktop (Bookworm) |
| **Storage** | La tua microSD |

Clicca **"NEXT"** → poi **"EDIT SETTINGS"** per la configurazione avanzata:

**Tab "GENERAL":**
- ✅ Hostname: `raspberrypi` (o un nome a piacere, es. `claude-monitor`)
- ✅ Username: `pi` — Password: scegli una password sicura (la userai per SSH)
- ✅ Wireless LAN: inserisci **SSID e password del tuo WiFi** (risparmia un passaggio dopo l'avvio)
- ✅ Wireless LAN country: `IT`
- ✅ Timezone: `Europe/Rome`
- ✅ Keyboard layout: `it`

**Tab "SERVICES":**
- ✅ Enable SSH → **Use password authentication**

Clicca **"SAVE"** → **"YES"** → **"YES"** (confermando la sovrascrittura della SD).

Attendi il completamento (~3-5 minuti).

---

## 3. Collegamento del display

Con il Raspberry Pi **spento**:

1. **Appoggia il display sul connettore GPIO a 40 pin** del Raspberry (fornisce 5 V + segnale touch resistivo)
2. **Ponticello/cavo HDMI** (display → Pi): sul **Pi 3B+** si innesta diretto sull'HDMI full-size; sul **Pi 4/5** usa la porta **HDMI 0** con adattatore micro-HDMI → HDMI
3. **Alimentatore** → porta di alimentazione del Pi (USB-C su Pi 4/5, microUSB su Pi 3B+)

> Se il display non si accende, prova a portare la risoluzione a 800×480: modifica `/boot/firmware/config.txt` aggiungendo:
> ```
> hdmi_group=2
> hdmi_mode=87
> hdmi_cvt=800 480 60 6 0 0 0
> hdmi_drive=1
> ```

---

## 4. Prima accensione e WiFi

### 4.1 Con WiFi pre-configurato nell'Imager

Se hai inserito le credenziali WiFi nell'Imager (passo 2.2), il RPi si connette automaticamente al WiFi dopo il primo avvio. Non occorre fare altro.

### 4.2 Connessione WiFi via interfaccia grafica (se non configurato nell'Imager)

1. Accendi il RPi con display collegato
2. Sul desktop: clicca sull'icona WiFi in alto a destra (barra delle applicazioni)
3. Seleziona la tua rete e inserisci la password

### 4.3 Connessione WiFi da terminale (raspi-config)

Se sei connesso via SSH o hai un terminale:

```bash
sudo raspi-config
```

Naviga: **System Options → Wireless LAN → inserisci SSID e password**

Oppure, direttamente con `nmcli`:

```bash
sudo nmcli device wifi connect "NomeRete" password "TuaPassword"
```

Verifica la connessione:

```bash
hostname -I
# Dovresti vedere un indirizzo IP locale, es. 192.168.1.XX
```

### 4.4 WiFi da riga di comando (wpa_supplicant, metodo alternativo)

```bash
sudo nano /etc/wpa_supplicant/wpa_supplicant.conf
```

Aggiungi in fondo:

```
network={
    ssid="NomeRete"
    psk="TuaPassword"
    key_mgmt=WPA-PSK
}
```

Salva (`Ctrl+O`, `Enter`, `Ctrl+X`) e riavvia il networking:

```bash
sudo systemctl restart networking
```

---

## 5. Accesso remoto SSH

SSH ti permette di gestire il RPi dal tuo PC senza tastiera/mouse collegati.

### 5.1 Trovare l'indirizzo IP del RPi

**Metodo A** — direttamente sul RPi (o tramite display):
```bash
hostname -I
```

**Metodo B** — dal tuo router: cerca i dispositivi connessi nella pagina di amministrazione del router (di solito 192.168.1.1 o 192.168.0.1).

**Metodo C** — con mDNS (funziona su Windows 10+ e macOS):
```
raspberrypi.local
```

### 5.2 Connettersi via SSH da Windows

Apri **Prompt dei comandi** o **PowerShell**:

```bash
ssh pi@raspberrypi.local
# oppure con IP diretto:
ssh pi@192.168.1.XX
```

Inserisci la password scelta al passo 2.2. Al primo accesso accetta l'impronta digitale digitando `yes`.

### 5.3 Connettersi via SSH da Android/iOS

Usa l'app **Termius** (gratuita) oppure **JuiceSSH**.  
Host: `raspberrypi.local` o IP — Username: `pi` — Password: quella scelta nell'Imager.

---

## 6. Installazione del software

### 6.1 Aggiorna il sistema

Connettiti via SSH (o apri un terminale sul RPi) e aggiorna tutto:

```bash
sudo apt update && sudo apt upgrade -y
```

> Ci vogliono 5-10 minuti alla prima esecuzione.

### 6.2 Installa Git

```bash
sudo apt install -y git
```

### 6.3 Clona il repository

```bash
cd ~
git clone https://github.com/BorisLandoni/claude-token-monitor.git
cd claude-token-monitor
```

### 6.4 Esegui l'installer automatico

```bash
bash setup-rpi.sh
```

> In alternativa, senza clonare prima il repo, puoi lanciare l'installer con un'unica riga:
> ```bash
> curl -fsSL https://raw.githubusercontent.com/BorisLandoni/claude-token-monitor/main/setup-rpi.sh | bash
> ```

Lo script fa automaticamente tutto:

| Cosa fa | Dettaglio |
|---|---|
| Installa dipendenze di sistema | `python3`, librerie grafiche, Chromium |
| Installa Node.js + **Claude Code CLI** | Per l'autenticazione OAuth (`claude login`) |
| Crea ambiente virtuale Python | In `rpi-monitor/backend/.venv` |
| Installa pacchetti Python | FastAPI, uvicorn, httpx, pydantic |
| Configura il **display 5" + touch resistivo** | `hdmi_cvt 800 480` + overlay `ads7846` + calibrazione X11 |
| Crea servizio systemd | `claude-monitor.service` — si avvia automaticamente al boot |
| Configura kiosk Chromium | autostart su display, con auto-restart |

**Durata**: circa 3-5 minuti. Al termine può chiedere un riavvio per attivare il display.

### 6.5 Riavvia il Raspberry Pi

```bash
sudo reboot
```

Dopo il riavvio, il sistema si avvia automaticamente in modalità kiosk: sul display appare la dashboard del Claude Monitor.

---

## 7. Primo accesso a Claude

L'autenticazione avviene via **OAuth di Claude Code** — nessun login con email/password, nessun cookie da copiare. Lo script `setup-rpi.sh` esegue `claude login` durante l'installazione, e da quel momento il monitor è autonomo: rinnova l'access token automaticamente quando scade, usando il `refresh_token` salvato in `~/.claude/.credentials.json`.

### 7.1 Apri la dashboard dal PC

Con il RPi acceso e connesso alla stessa LAN:

```
http://raspberrypi.local:8080
```

Oppure, se mDNS non funziona, usa l'IP diretto:

```
http://192.168.1.XX:8080
```

> La dashboard si apre anche dal telefono sulla stessa rete WiFi.

### 7.2 Verifica i dati

Dopo l'installazione, la pagina **ADESSO** dovrebbe mostrare:
- Gauge sessione con la **% utilizzata** in grande e la **% rimasta** sotto
- Countdown al reset (es. "Si ripristina tra 3h 38m · alle 12:00")
- Box settimanale con % utilizzato + reset day/ora
- Sparkline storica (vuota al primo boot, si popola entro qualche minuto)

Se vedi "Token OAuth scaduto" o dati `—`:

1. Vai in **Impostazioni** (icona ⚙ in basso)
2. Premi **Riprova Connessione** → il backend tenta il refresh automatico
3. Se anche il refresh fallisce (raro, solo dopo settimane di inattività), apri terminale SSH e fai:
   ```bash
   claude login
   ```
   Poi torna in dashboard e premi di nuovo **Riprova Connessione**.

---

## 8. Verifica che tutto funzioni

### 8.1 Stato del servizio backend

```bash
systemctl status claude-monitor
```

Output atteso (verde = ok):
```
● claude-monitor.service - Claude Monitor (RPi)
   Active: active (running) since ...
```

### 8.2 Log in tempo reale

```bash
journalctl -u claude-monitor -f
```

Log tipici:
```
[poll] httpx OK: https://claude.ai/api/...
[account] sessione:77% usato | rimasti:-- | piano:pro
```

Esci con `Ctrl+C`.

### 8.3 Health check del server

```bash
curl http://localhost:8080/health
```

Risposta attesa:
```json
{"ok": true}
```

### 8.4 Dati account

```bash
curl http://localhost:8080/api/account | python3 -m json.tool
```

Risposta attesa (dopo il login):
```json
{
  "has_data": true,
  "session_pct_used": 77,
  "session_pct_remaining": 23,
  "session_resets_at_ts": 1700000000,
  "weekly_pct_used": 45,
  "plan": "pro",
  ...
}
```

### 8.5 Stato della sessione

```bash
curl http://localhost:8080/api/session | python3 -m json.tool
```

```json
{
  "logged_in": true,
  "email": "tuaemail@example.com",
  "session_status": "ok",
  "plan": "pro"
}
```

### 8.6 Verifica kiosk (sul display)

Al riavvio il kiosk si avvia automaticamente dopo ~20-30 secondi (attende che il backend sia pronto). Se vedi il browser Chromium con la dashboard, tutto funziona.

---

## 9. Impostazioni e personalizzazione

### 9.1 Intervallo di aggiornamento

Nella pagina **IMPOSTAZIONI** della dashboard:

| Opzione | Comportamento |
|---|---|
| 1 minuto | **Default** — display sempre fresco |
| 2 minuti | Bilanciamento |
| 5 minuti | Risparmio risorse |
| 10 / 30 minuti | Polling minimo |

> La barra sotto il grafico mostra il countdown al prossimo aggiornamento e si allinea automaticamente al valore scelto.
> Il backend ha comunque un cool-down minimo verso Claude.ai (30s) per evitare rate-limit lato Anthropic.

### 9.2 Tema grafico

Sei temi disponibili in IMPOSTAZIONI:

| Tema | Tipo |
|---|---|
| **Blu** | Scuro, default |
| **Grigio** | Grigio scuro neutro, alto contrasto |
| **Viola** | Scuro caldo |
| **Chiaro** | Bianco/giallo paglierino |
| **Verde** | Scuro verde militare |
| **Ambra** | Scuro ambra/terminal |

Il tema viene salvato automaticamente.

### 9.3 Rinnovo connessione Claude

Quando vedi **⚠ Token OAuth scaduto** in Impostazioni:

1. Premi **Riprova Connessione** → tenta refresh automatico via `refresh_token`
2. Se fallisce, SSH al RPi ed esegui:
   ```bash
   claude login
   ```
3. Torna in dashboard, **Riprova Connessione**

### 9.4 Aggiornamento OTA dal display

In IMPOSTAZIONI:
1. **Verifica Aggiornamenti** → confronta versione locale e GitHub
2. Se disponibile, appare **Aggiorna Adesso**
3. Il backend fa `git fetch + git reset --hard origin/<branch>` e riavvia il servizio + kiosk
4. La dashboard ricarica da sola dopo qualche secondo

### 9.5 Forzare un aggiornamento dati immediato

Premi **AGGIORNA** sulla pagina ADESSO, o via API:
```bash
curl -X POST http://localhost:8080/api/poll
```

---

## 10. Gestione del servizio

### Comandi principali

```bash
# Avviare il servizio
sudo systemctl start claude-monitor

# Fermarlo
sudo systemctl stop claude-monitor

# Riavviarlo
sudo systemctl restart claude-monitor

# Abilitare l'avvio automatico al boot (già attivo dopo install.sh)
sudo systemctl enable claude-monitor

# Disabilitare l'avvio automatico
sudo systemctl disable claude-monitor

# Log completi degli ultimi 100 messaggi
journalctl -u claude-monitor -n 100
```

### Kiosk Chromium

Il kiosk si avvia automaticamente tramite LXDE autostart. Se il display è spento:

```bash
# Riavviare il kiosk manualmente (sul desktop RPi o via SSH con display locale)
DISPLAY=:0 bash ~/claude-token-monitor/rpi-monitor/setup/start-kiosk.sh &
```

---

## 11. Aggiornamenti

Per aggiornare il software all'ultima versione:

```bash
cd ~/claude-token-monitor

### Da v1.3+: aggiornamento OTA dal display

In **Impostazioni → Verifica Aggiornamenti → Aggiorna Adesso**. Il backend fa `git fetch` + `git reset --hard origin/<branch>`, riavvia il servizio + kiosk, e la dashboard ricarica.

### Aggiornamento manuale via SSH

```bash
cd ~/claude-token-monitor

# Sovrascrive eventuali modifiche locali, evita merge conflict
git fetch && git reset --hard origin/$(git rev-parse --abbrev-ref HEAD)

# Aggiorna pacchetti Python se requirements.txt è cambiato
source rpi-monitor/backend/.venv/bin/activate
pip install -r rpi-monitor/backend/requirements.txt --upgrade
deactivate

# Riavvia
sudo systemctl restart claude-monitor
```

---

## 12. Risoluzione problemi

### Il display non mostra nulla

1. **Controlla il cavo micro-HDMI**: deve essere nella porta HDMI 0 (quella vicina all'USB-C)
2. **Controlla l'alimentazione**: usa un alimentatore da almeno 27W/3A
3. **Forza la risoluzione**: aggiungi queste righe a `/boot/firmware/config.txt`:
   ```
   hdmi_group=2
   hdmi_mode=87
   hdmi_cvt=800 480 60 6 0 0 0
   hdmi_force_hotplug=1
   ```
   Poi `sudo reboot`

### Il touch non funziona

1. **Controlla l'innesto sul GPIO**: il display deve essere ben appoggiato su tutti i 40 pin (il touch resistivo passa da lì, non da USB)
2. **Verifica l'overlay**: `dmesg | grep -i ads7846` — deve comparire il controller touch registrato. Controlla che in `/boot/firmware/config.txt` sia presente la riga `dtoverlay=ads7846,...` aggiunta da `setup-rpi.sh`
3. **Calibrazione**: il file `/usr/share/X11/xorg.conf.d/99-calibration.conf` viene creato dall'installer; per ricalibrare usa `xinput-calibrator`

### `http://raspberrypi.local:8080` non si apre

1. **Verifica che il RPi sia connesso**: `ping raspberrypi.local` dal PC
2. **Usa l'IP diretto**: cerca l'IP nel router o usa `hostname -I` sul RPi
3. **Verifica il servizio**: `systemctl status claude-monitor` — deve essere `active (running)`
4. **Controlla la porta**: `ss -tlnp | grep 8080`

### "Token OAuth scaduto" persistente

Il refresh automatico tramite `refresh_token` di solito risolve da solo. Se rimane scaduto:

1. **Riprova Connessione** in Impostazioni: tenta refresh + retry
2. Se fallisce, via SSH:
   ```bash
   claude login
   sudo systemctl restart claude-monitor
   ```
3. Verifica che `claude` CLI sia installato: `which claude`
4. Controlla `~/.claude/.credentials.json` — deve contenere `claudeAiOauth.refreshToken`

### La dashboard mostra "Nessun dato"

1. **Verifica login OAuth**: `cat ~/.claude/.credentials.json | python3 -m json.tool | grep -i token`
2. **Stato auth**: `curl http://localhost:8080/api/session`
3. **Forza poll**: `curl -X POST http://localhost:8080/api/poll`
4. **Log**: `journalctl -u claude-monitor -n 50` — cerca righe `[oauth]`

### Il servizio crasha al boot

Controlla i log:
```bash
journalctl -u claude-monitor -b --no-pager | tail -50
```

Errori comuni:
- `ModuleNotFoundError`: il venv non è attivato correttamente → riinstalla con `bash rpi-monitor/setup/install.sh`
- `Port already in use`: un'altra istanza è già in esecuzione → `sudo systemctl stop claude-monitor`; `sudo pkill -f server.py`; `sudo systemctl start claude-monitor`

### I dati non si aggiornano

L'access token OAuth dura circa 1 ora ma viene **rinnovato automaticamente** dal backend tramite `refresh_token`. Se nonostante questo non vedi aggiornamenti:

1. Controlla che il poll sia attivo: barra refresh sotto il grafico dovrebbe scorrere
2. Verifica log: `journalctl -u claude-monitor -f` — cerca `[oauth]` e `[account]`
3. Forza poll manuale: bottone **AGGIORNA** in dashboard, o `curl -X POST http://localhost:8080/api/poll`
4. Se vedi `429 rate limit`: il backend aspetta 180s prima di riprovare, comportamento normale

### Controllare la RAM disponibile

```bash
free -h
```

Su RPi 4 2GB ci aspettiamo ~800 MB liberi a riposo con il kiosk attivo.

---

## Architettura in sintesi

```
Claude.ai (web/mobile/VS Code/Claude Code)
       │  uso normale — zero token consumati dal monitor
       ▼
  Anthropic OAuth Usage API (gratuita)
       │  GET /api/oauth/usage ~1s
       │  refresh_token automatico
       ▼
┌─────────────────────────────┐
│  RPi 4  ·  FastAPI  :8080   │
│  rpi-monitor/backend/       │
└──────────────┬──────────────┘
               │
               ▼
    Chromium kiosk 800×480
    Dashboard touch (3 pagine)
    ┌─────────┬──────────┬───────────────┐
    │  ADESSO │  CREDITI │  IMPOSTAZIONI │
    └─────────┴──────────┴───────────────┘
```

---

## Sicurezza e privacy

- **Endpoint OAuth gratuito**: nessun token a pagamento consumato dal monitor
- **Token solo locale**: `~/.claude/.credentials.json` resta sul dispositivo — non viene mai inviato a server esterni
- **Nessun cookie/password**: l'auth è esclusivamente OAuth via Claude Code
- **Accesso alla dashboard non protetto**: chiunque sulla stessa LAN può vederla — usare in reti private

---

*MIT License — Boris Landoni*
