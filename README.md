# Claude Token Monitor

Monitor l'utilizzo del tuo account Claude.ai — percentuale sessione, countdown reset, token per messaggio e costo stimato — su un **Raspberry Pi con display touch 5"**, senza consumare un singolo token.

---

## Come funziona

```
Claude.ai (web / desktop / mobile / VS Code)
       │
       │  uso normale — zero token consumati
       ▼
  claude.ai/settings › Utilizzo
       │  ◄─── polling automatico ogni 60 s
       │        Strategia a tre livelli:
       │        1. httpx con cookie salvati  (~1 s)
       │        2. Playwright headless       (~20-40 s, se httpx fallisce)
       │        3. DOM scraping              (fallback testo pagina)
       ▼
┌──────────────────────────────────┐
│  Raspberry Pi 4  ·  FastAPI :8080 │  ◄── Tampermonkey può inviare token/msg
│  rpi-monitor/backend/server.py   │
└────────────────┬─────────────────┘
                 │
                 ▼
      Chromium kiosk 800×480
      Dashboard touch — 5 pagine
```

Il monitor accede a `claude.ai/settings` usando i **cookie di sessione del tuo browser** (salvati dopo un login una-tantum con Playwright). Non vengono usate API Anthropic a pagamento: il polling replica esattamente le stesse chiamate che claude.ai fa già internamente.

---

## Hardware

| Componente | Modello | Prezzo |
|---|---|---|
| Single-board computer | **Raspberry Pi 4 — 2 GB** | ~€45 |
| Display touch HDMI | [5" 800×480 capacitivo (Futuranet)](https://futuranet.it/prodotto/display-touch-screen-5-800x480-pixel/) | €59 |
| MicroSD | 16 GB Classe 10 (Samsung/SanDisk Endurance) | ~€8 |
| Alimentatore | USB-C 27W / 5.1V 3A ufficiale RPi | ~€12 |
| Cavo micro-HDMI → HDMI | incluso con il display o separato | — |

> **RPi 4 2 GB** è il target principale: login Playwright in ~25 s, kiosk 800×480 fluido.  
> **RPi 3B+** funziona ma il login richiede ~50 s e la RAM è più stretta.

---

## Installazione rapida

```bash
# 1. Clona il repo sul Raspberry Pi
git clone https://github.com/BorisLandoni/claude-token-monitor.git
cd claude-token-monitor

# 2. Esegui l'installer (installa dipendenze, crea il servizio systemd, configura il kiosk)
bash rpi-monitor/setup/install.sh

# 3. Apri la dashboard dal PC o telefono (stessa rete WiFi)
#    http://raspberrypi.local:8080
#    Vai in IMPOSTAZIONI → inserisci email e password Claude.ai → ACCEDI

# 4. Riavvia il Raspberry Pi — il kiosk si avvia automaticamente
sudo reboot
```

Per la guida completa passo-passo (flash SD, WiFi, SSH, risoluzione problemi) → [GUIDA-RASPBERRY.md](GUIDA-RASPBERRY.md)

---

## Struttura del repository

```
rpi-monitor/
├── backend/
│   ├── server.py          FastAPI (porta 8080) + background poll loop
│   ├── claude_client.py   Login Playwright + polling httpx + DOM scraping
│   ├── store.py           Store in RAM + persistenza data.json
│   └── requirements.txt
├── frontend/
│   └── index.html         SPA 800×480 touch (5 pagine, Chart.js, arc gauge)
└── setup/
    ├── install.sh         Installer automatico per Raspberry Pi OS
    ├── claude-monitor.service   Definizione servizio systemd
    └── claude-kiosk.desktop     Autostart LXDE kiosk
```

---

## Dashboard

| Pagina | Contenuto |
|---|---|
| **ADESSO** | Arc gauge sessione corrente (%), countdown reset, limiti settimanali, token e costo sessione |
| **ORA** | Grafico 24 h a barre — input (ciano) / output (verde) |
| **GIORNO** | Grafico ultimi 7 giorni |
| **SETTIMANA** | Grafico ultime 4 settimane |
| **IMPOSTAZIONI** | Login Claude.ai, tema grafico, intervallo aggiornamento |

### Temi disponibili

| Tema | Sfondo | Uso consigliato |
|---|---|---|
| **Scuro** | `#07111C` | Default — ambienti con poca luce |
| **Blu** | `#030D18` | Variante più profonda |
| **Viola** | `#0C0718` | Alternativa calda |

---

## Dati monitorati

Il backend legge da `claude.ai/settings › Utilizzo`:

- **Sessione corrente**: % usato, % rimanente, countdown reset (es. "Si ripristina tra 3 h 38 min")
- **Settimanale**: % usato, giorno/ora reset (es. "sab 17:59")
- **Token per messaggio**: input, output, cache read, cache creation (via Tampermonkey)
- **Costo stimato** in USD — prezzi claude-sonnet-4-x:

| Tipo token | Prezzo per 1 M token |
|---|---|
| Input | $3.00 |
| Output | $15.00 |
| Cache read | $0.30 |
| Cache creation | $3.75 |

---

## Tampermonkey (opzionale — token per messaggio)

Senza Tampermonkey il monitor mostra solo i dati di utilizzo da `claude.ai/settings` (percentuali sessione/settimanale e reset timer). Per aggiungere il conteggio token per singolo messaggio e il costo stimato in tempo reale, installa lo userscript:

1. Installa l'estensione [Tampermonkey](https://www.tampermonkey.net/) nel browser
2. Crea un nuovo script e incolla il contenuto di `rpi-monitor/browser-extension/claude_monitor.user.js`  
   *(o installalo direttamente dalla URL raw del file)*
3. Nello script, imposta l'URL del server:
   ```js
   const SERVER_URL = 'http://IP-del-RPi:8080';
   ```
4. Salva e ricarica claude.ai

Lo userscript intercetta i completamenti dell'API claude.ai e invia i token a `POST /api/tokens` del backend.

---

## API REST

Il backend espone le stesse API del server Node.js originale (porta 3333), rendendo lo userscript compatibile senza modifiche.

### Account e utilizzo

| Metodo | Endpoint | Descrizione |
|---|---|---|
| `GET` | `/api/account` | Percentuali sessione/settimanale, reset timer, piano |
| `GET` | `/api/session` | Stato cookie, email, età del cookie in ore |
| `POST` | `/api/login` | Login Playwright — body: `{"email":"…","password":"…"}` |
| `POST` | `/api/logout` | Cancella cookie e dati account |
| `POST` | `/api/poll` | Forza un poll immediato |

### Token

| Metodo | Endpoint | Descrizione |
|---|---|---|
| `GET` | `/api/tokens` | Totali sessione (input, output, cache, costo) |
| `POST` | `/api/tokens` | Aggiungi evento token (da Tampermonkey) |
| `DELETE` | `/api/tokens` | Azzera la storia sessione |
| `GET` | `/api/tokens/hourly` | Aggregato per ora — ultime 24 h |
| `GET` | `/api/tokens/daily` | Aggregato per giorno — ultimi 7 giorni |
| `GET` | `/api/tokens/weekly` | Aggregato per settimana — ultime 4 settimane |

### Impostazioni

| Metodo | Endpoint | Descrizione |
|---|---|---|
| `GET` | `/api/settings` | Legge `poll_interval`, `theme`, `email` |
| `PUT` | `/api/settings` | Aggiorna — body: `{"poll_interval":60,"theme":"dark"}` |
| `GET` | `/health` | Health check — risponde `{"ok":true}` |

#### Esempi curl

```bash
# Stato account
curl http://raspberrypi.local:8080/api/account | python3 -m json.tool

# Forza aggiornamento immediato
curl -X POST http://raspberrypi.local:8080/api/poll

# Invia manualmente un evento token
curl -X POST http://raspberrypi.local:8080/api/tokens \
  -H 'Content-Type: application/json' \
  -d '{"input_tokens":1200,"output_tokens":350,"cache_read_tokens":0,"cache_creation_tokens":0}'

# Cambia intervallo di polling a 2 minuti
curl -X PUT http://raspberrypi.local:8080/api/settings \
  -H 'Content-Type: application/json' \
  -d '{"poll_interval":120}'
```

---

## Gestione servizio

```bash
sudo systemctl status claude-monitor      # stato
sudo systemctl restart claude-monitor     # riavvio
sudo journalctl -u claude-monitor -f      # log in tempo reale
sudo journalctl -u claude-monitor -n 100  # ultimi 100 log
```

### Aggiornamento

```bash
cd ~/claude-token-monitor
sudo systemctl stop claude-monitor
git pull
source rpi-monitor/backend/.venv/bin/activate
pip install -r rpi-monitor/backend/requirements.txt --upgrade
deactivate
sudo systemctl start claude-monitor
```

---

## Sicurezza e privacy

- **Nessuna API Anthropic a pagamento**: il polling usa i cookie di sessione, come fa il browser
- **Credenziali non salvate**: email usata solo per il login, password mai memorizzata su disco
- **Cookie salvati localmente** in `rpi-monitor/backend/cookies.json` (solo sul Raspberry Pi)
- **Nessun dato inviato a server esterni**: tutto rimane sulla rete locale
- **Accesso dashboard non protetto**: chiunque sulla stessa rete WiFi può vedere i dati — usa in reti domestiche o private

I cookie di sessione Claude.ai durano 7-30 giorni. Quando scadono, la dashboard mostra "Sessione scaduta": vai in IMPOSTAZIONI → Disconnetti → reinserisci le credenziali.

---

## Licenza

MIT — Boris Landoni
