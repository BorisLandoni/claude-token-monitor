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
| Single-board computer | **Raspberry Pi 4 — 2 GB** | Target principale. Il 4B 4GB va benissimo. RPi 3B+ funziona ma è più lento. |
| Display touch HDMI | [5" 800×480 capacitivo — Futuranet](https://futuranet.it/prodotto/display-touch-screen-5-800x480-pixel/) | Include driver HDMI + USB touch |
| Cavo micro-HDMI → HDMI | **incluso con il display** o acquisto separato | RPi 4 usa micro-HDMI (non HDMI standard) |
| Cavo USB-A → USB-micro | Qualsiasi | Per alimentare il touch del display |
| MicroSD | **16 GB Classe 10** minimo, 32 GB consigliato | Samsung Endurance o SanDisk Endurance |
| Alimentatore | **USB-C 27W / 5.1V 3A** ufficiale RPi | Evita problemi di sotto-tensione |
| Case (opzionale) | Qualsiasi case RPi 4 con slot laterale | Per montaggio fisso |

> **Nota micro-HDMI**: Il Raspberry Pi 4 ha **due porte micro-HDMI** sul lato. Usa la porta **HDMI 0** (quella più vicina all'USB-C di alimentazione) come uscita primaria.

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

1. **Cavo micro-HDMI** (display) → porta **HDMI 0** del RPi (quella vicina all'USB-C)
2. **Cavo USB-A → USB-micro** (display, per il touch) → una delle porte USB del RPi
3. **Cavo USB-C** (alimentatore) → porta USB-C del RPi

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
bash rpi-monitor/setup/install.sh
```

Lo script fa automaticamente tutto:

| Cosa fa | Dettaglio |
|---|---|
| Installa dipendenze di sistema | `python3`, `chromium-browser`, `fonts-noto`, `xdotool`, `unclutter` |
| Crea ambiente virtuale Python | In `rpi-monitor/backend/.venv` |
| Installa pacchetti Python | FastAPI, uvicorn, Playwright, httpx, aiofiles |
| Configura Playwright | Usa il Chromium di sistema (nessun download da 300 MB) |
| Crea servizio systemd | `claude-monitor.service` — si avvia automaticamente al boot |
| Configura kiosk Chromium | `claude-kiosk.desktop` in LXDE autostart |

**Durata**: circa 3-5 minuti su RPi 4.

### 6.5 Riavvia il Raspberry Pi

```bash
sudo reboot
```

Dopo il riavvio, il sistema si avvia automaticamente in modalità kiosk: sul display appare la dashboard del Claude Monitor.

---

## 7. Primo accesso a Claude.ai

Il monitor deve accedere al tuo account Claude.ai per leggere i dati di utilizzo. L'accesso avviene **una sola volta**: le credenziali non vengono salvate, solo i cookie di sessione.

### 7.1 Apri la dashboard dal PC

**Mentre il RPi è acceso e connesso alla stessa rete WiFi**, apri il browser sul tuo PC e vai su:

```
http://raspberrypi.local:8080
```

oppure, se il DNS mDNS non funziona, usa l'IP diretto:

```
http://192.168.1.XX:8080
```

> La dashboard si apre anche dal telefono, connesso alla stessa rete WiFi.

### 7.2 Naviga in IMPOSTAZIONI

Sulla dashboard (lato PC o direttamente sul display touch):

1. Tocca/clicca l'icona **≡ IMPOSTAZIONI** nella barra di navigazione in basso
2. Trovi il form di login nella sezione superiore

### 7.3 Inserisci le credenziali Claude.ai

1. **Email**: la tua email di accesso a Claude.ai
2. **Password**: la tua password Claude.ai
3. Clicca **"ACCEDI"**

Il processo di login richiede **30-60 secondi** (Playwright apre un browser headless, compila il form, naviga alla pagina Utilizzo e salva i cookie). Aspetta finché il pulsante non mostra "Connesso".

> **Sicurezza**: l'email è salvata nelle impostazioni locali, la password viene usata solo durante il login e non viene mai memorizzata su disco. Solo i cookie di sessione vengono salvati in `rpi-monitor/backend/cookies.json`.

### 7.4 Verifica i dati

Dopo il login, la pagina **ADESSO** della dashboard dovrebbe mostrare:
- L'arc gauge con la percentuale di utilizzo sessione (es. "77%")
- Il countdown al reset (es. "Si ripristina tra 3h 38m")
- I limiti settimanali (es. "Sab 17:59")

Se i dati non appaiono subito, attendi il primo ciclo di polling (max 60 secondi) oppure clicca il pulsante **"Aggiorna ora"** nelle impostazioni.

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
  "cookie_age_hours": 2.3
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
| 30 secondi | Polling frequente — più aggiornato, usa leggermente più risorse |
| 1 minuto | **Default** — bilanciamento ideale |
| 2 minuti | Risparmio risorse — indicato se il RPi fa anche altro |
| 5 minuti | Polling minimo — dati aggiornati ogni 5 minuti |

> Il minimo assoluto è 30 secondi (hardcoded nel backend per evitare ban).

### 9.2 Tema grafico

Tre temi disponibili nella pagina IMPOSTAZIONI:

| Tema | Sfondo | Uso consigliato |
|---|---|---|
| **Scuro** | `#07111C` | Default — ottimo in ambienti con poca luce |
| **Blu** | `#030D18` | Variante più profonda |
| **Viola** | `#0C0718` | Alternativa calda |

Il tema viene salvato automaticamente.

### 9.3 Cambio credenziali Claude.ai

Se cambi la password Claude.ai o vuoi disconnetterti:

**Via dashboard** → IMPOSTAZIONI → clicca **"Disconnetti"**, poi inserisci le nuove credenziali.

**Via API**:
```bash
curl -X POST http://localhost:8080/api/logout
```

Poi riesegui il login dalla dashboard.

### 9.4 Forzare un aggiornamento immediato

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

# Ferma il servizio
sudo systemctl stop claude-monitor

# Aggiorna il codice
git pull

# Aggiorna i pacchetti Python
source rpi-monitor/backend/.venv/bin/activate
pip install -r rpi-monitor/backend/requirements.txt --upgrade
deactivate

# Riavvia
sudo systemctl start claude-monitor
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

1. **Controlla il cavo USB**: il cavo USB-A → USB-micro del display deve essere collegato a una porta USB del RPi (non all'alimentatore)
2. **Verifica**: `dmesg | grep -i touch` — cerca qualcosa come `usb-hid` o `eGalax`
3. **Calibrazione**: installa `xinput-calibrator` e segui la procedura

### `http://raspberrypi.local:8080` non si apre

1. **Verifica che il RPi sia connesso**: `ping raspberrypi.local` dal PC
2. **Usa l'IP diretto**: cerca l'IP nel router o usa `hostname -I` sul RPi
3. **Verifica il servizio**: `systemctl status claude-monitor` — deve essere `active (running)`
4. **Controlla la porta**: `ss -tlnp | grep 8080`

### Errore "Accesso fallito" durante il login

1. **Controlla email e password**: prova ad accedere manualmente su claude.ai
2. **Autenticazione a due fattori**: se hai 2FA attivo su Claude.ai, il login automatico non funziona — devi disabilitarlo temporaneamente o usare Tampermonkey
3. **Chromium non trovato**: `which chromium-browser` — deve rispondere con un percorso
4. **Playwright deps mancanti**: riinstalla le dipendenze
   ```bash
   source ~/claude-token-monitor/rpi-monitor/backend/.venv/bin/activate
   playwright install-deps chromium
   ```

### La dashboard mostra "Nessun dato"

1. **Esegui il login** dalla pagina IMPOSTAZIONI (vedi sezione 7)
2. **Verifica lo stato**: `curl http://localhost:8080/api/session`
3. **Forza un aggiornamento**: `curl -X POST http://localhost:8080/api/poll`
4. **Controlla i log**: `journalctl -u claude-monitor -n 50`

### Il servizio crasha al boot

Controlla i log:
```bash
journalctl -u claude-monitor -b --no-pager | tail -50
```

Errori comuni:
- `ModuleNotFoundError`: il venv non è attivato correttamente → riinstalla con `bash rpi-monitor/setup/install.sh`
- `Port already in use`: un'altra istanza è già in esecuzione → `sudo systemctl stop claude-monitor`; `sudo pkill -f server.py`; `sudo systemctl start claude-monitor`

### I dati non si aggiornano (sessione scaduta)

I cookie di sessione di Claude.ai durano circa 7-30 giorni. Quando scadono, la dashboard mostra "Sessione scaduta". Soluzione:

1. Vai su IMPOSTAZIONI nella dashboard
2. Clicca **"Disconnetti"**
3. Reinserisci email e password → clicca **"ACCEDI"**

### Controllare la RAM disponibile

```bash
free -h
```

Su RPi 4 2GB ci aspettiamo ~800 MB liberi a riposo con il kiosk attivo.

---

## Architettura in sintesi

```
Claude.ai (web/mobile/VS Code/Desktop)
       │  uso normale — zero token consumati
       ▼
  claude.ai/settings > Utilizzo
       │  ◄─── polling automatico ogni 60 s
       │        (Playwright + httpx + cookie di sessione)
       ▼
┌─────────────────────────────┐
│  RPi 4  ·  FastAPI  :8080   │  ← Tampermonkey può inviargli dati (opz.)
│  rpi-monitor/backend/       │
└──────────────┬──────────────┘
               │
               ▼
    Chromium kiosk 800×480
    Dashboard touch (5 pagine)
    ┌─────────┬────────┬──────────┬────────────┬───────────────┐
    │  ADESSO │  ORA   │  GIORNO  │  SETTIMANA │  IMPOSTAZIONI │
    └─────────┴────────┴──────────┴────────────┴───────────────┘
```

---

## Sicurezza e privacy

- **Nessuna chiamata alle API Anthropic a pagamento**: il polling usa i cookie di sessione del browser, proprio come fa claude.ai
- **Le credenziali non vengono salvate**: solo i cookie sono memorizzati in `cookies.json` (locale sul RPi)
- **Nessun dato inviato a server esterni**: tutto rimane sulla rete locale
- **Accesso alla dashboard**: chiunque sulla stessa rete WiFi può vedere la dashboard — se vuoi restringere l'accesso, configura un firewall o usa solo la rete locale domestica

---

*MIT License — Boris Landoni*
