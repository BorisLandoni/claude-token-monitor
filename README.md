# Claude Token Monitor

Monitor l'utilizzo del tuo account Claude.ai — percentuale sessione, countdown reset e limiti settimanali — su un **Raspberry Pi con display touch 5"**, senza consumare un singolo token.

---

## Come funziona

```
Claude.ai (web / desktop / mobile / VS Code)
       │
       │  uso normale — zero token consumati
       ▼
  Anthropic OAuth API  +  claude.ai DOM scrape (opzionale)
       │  polling automatico ogni 60 s
       │
       │  Strategia a due livelli:
       │  1. OAuth token Claude Code  (~1 s)   ← PRIMARIO
       │  2. Cookie sessionKey + DOM scrape    ← opzionale, per crediti/design
       ▼
+---------------------------------+
|  Raspberry Pi 4  · FastAPI :8080 |
|  rpi-monitor/backend/server.py  |
+---------------------------------+
                 |
                 ▼
      Chromium kiosk 800x480
      Dashboard touch — 5 pagine
```

Il monitor usa l'**OAuth token di Claude Code** (salvato in `~/.claude/.credentials.json`) per chiamare l'endpoint `https://api.anthropic.com/api/oauth/usage`. Nessun login, nessun Playwright per l'auth, nessun Cloudflare. Il token si rinnova automaticamente finché Claude Code è installato.

---

## Hardware

| Componente | Modello | Prezzo |
|---|---|---|
| Single-board computer | **Raspberry Pi 4 — 2 GB** | ~€45 |
| Display touch HDMI | [5" 800×480 capacitivo (Futuranet)](https://futuranet.it/prodotto/display-touch-screen-5-800x480-pixel/) | €59 |
| MicroSD | 16 GB Classe 10 (Samsung/SanDisk Endurance) | ~€8 |
| Alimentatore | USB-C 27W / 5.1V 3A ufficiale RPi | ~€12 |
| Cavo micro-HDMI -> HDMI | incluso con il display o separato | — |

> **RPi 4 2 GB** è il target principale: dashboard 800×480 fluida.
> **RPi 3B+** funziona ma la RAM è più stretta.

---

## Installazione rapida (Raspberry Pi)

```bash
# Scarica ed esegui lo script di setup automatico
curl -fsSL https://raw.githubusercontent.com/BorisLandoni/claude-token-monitor/main/setup-rpi.sh | bash
```

Lo script fa tutto da solo:
1. Installa Node.js, Python, dipendenze di sistema
2. Installa **Claude Code** (`claude` CLI)
3. Avvia **`claude login`** — accedi una volta col browser
4. Clona il repository
5. Crea l'ambiente Python + installa Playwright
6. Crea e avvia il servizio **systemd** (autostart al boot)
7. Configura **Chromium in modalità kiosk** (si apre sul display all'avvio)

Dopo il setup:
- Dashboard locale: `http://localhost:8080`
- Da PC/telefono sulla stessa rete: `http://<IP-del-rpi>:8080`

Per la guida completa passo-passo (flash SD, WiFi, SSH, risoluzione problemi) → [GUIDA-RASPBERRY.md](GUIDA-RASPBERRY.md)

---

## Installazione su Windows (simulazione/sviluppo)

```
start-windows.bat
```

Il bat installa le dipendenze Python, avvia il backend e apre il browser su `http://localhost:8080`.

**Prerequisito:** [Claude Code](https://claude.ai/download) installato e loggato (`claude login` fatto almeno una volta).

---

## Struttura del repository

```
claude-token-monitor/
├── setup-rpi.sh              Script di setup automatico per Raspberry Pi
├── start-windows.bat         Avvio rapido su Windows
├── rpi-monitor/
│   ├── backend/
│   │   ├── server.py         FastAPI (porta 8080) + background poll loop
│   │   ├── claude_client.py  OAuth token + cookie import + DOM scraping
│   │   ├── store.py          Store in RAM + persistenza data.json
│   │   └── requirements.txt
│   └── frontend/
│       └── index.html        SPA 800x480 touch (5 pagine, gauge, sparkline)
└── GUIDA-RASPBERRY.md        Guida completa per RPi
```

---

## Dashboard

| Pagina | Contenuto |
|---|---|
| **ADESSO** | Arc gauge sessione corrente (%), countdown reset, limiti settimanali, crediti spesi |
| **ORA** | Sparkline costo ultime 24 h, token in/out per fascia oraria |
| **GIORNO** | Grafico ultimi 7 giorni |
| **SETTIMANA** | Grafico ultime 4 settimane |
| **IMPOST.** | Stato auth OAuth/cookie, import cookie opzionale, tema, intervallo polling |

### Temi disponibili

| Tema | Sfondo | Uso consigliato |
|---|---|---|
| **Scuro** | `#07111C` | Default — ambienti con poca luce |
| **Blu** | `#030D18` | Variante più profonda |
| **Viola** | `#0C0718` | Alternativa calda |

---

## Autenticazione

### Primaria — OAuth Claude Code (automatica)

Il monitor legge `~/.claude/.credentials.json` (creato da Claude Code dopo il login) e chiama:

```
GET https://api.anthropic.com/api/oauth/usage
Authorization: Bearer <token>
anthropic-beta: oauth-2025-04-20
```

Ritorna: % sessione (5h), % settimanale (7d), timestamp reset, crediti spesi.

Il token viene rinnovato automaticamente da Claude Code — nessun intervento richiesto.

### Opzionale — Cookie sessionKey (per dati extra)

Se vuoi vedere anche **Claude Design %** e **routine giornaliere**, puoi importare i cookie di sessione da Chrome:

1. Apri `IMPOST.` nella dashboard
2. Espandi "Importa cookie da Chrome"
3. Incolla il blocco cURL copiato da DevTools → Network → `copy as cURL`

I cookie vengono usati solo per il DOM scrape di `claude.ai/settings/utilizzo`.

---

## API REST

Il backend espone un'API REST sulla porta 8080.

| Metodo | Endpoint | Descrizione |
|---|---|---|
| `GET` | `/api/account` | Percentuali sessione/settimanale, reset timer, crediti |
| `GET` | `/api/session` | Stato OAuth, stato cookie, email |
| `POST` | `/api/poll` | Forza un poll immediato |
| `POST` | `/api/import-cookies` | Importa cookie da testo cURL |
| `POST` | `/api/logout` | Cancella cookie e dati account |
| `GET` | `/api/settings` | Legge `poll_interval`, `theme` |
| `PUT` | `/api/settings` | Aggiorna impostazioni |
| `GET` | `/health` | Health check |

#### Esempi curl

```bash
# Stato account
curl http://raspberrypi.local:8080/api/account | python3 -m json.tool

# Forza aggiornamento immediato
curl -X POST http://raspberrypi.local:8080/api/poll

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
git pull
sudo systemctl restart claude-monitor
```

---

## Sicurezza e privacy

- **Nessuna API Anthropic a pagamento**: l'endpoint OAuth è gratuito e non consuma token
- **Nessuna password mai memorizzata**: l'auth usa il token OAuth di Claude Code
- **Token locale**: `~/.claude/.credentials.json` rimane solo sul dispositivo
- **Cookie opzionali**: salvati in `rpi-monitor/backend/cookies.json` solo se importati
- **Nessun dato inviato a server esterni**: tutto rimane sulla rete locale
- **Accesso dashboard non protetto**: usa in reti domestiche o private

---

## Licenza

MIT — Boris Landoni
