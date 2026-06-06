# Claude Token Monitor

Dashboard fisica per Raspberry Pi che monitora in tempo reale l'utilizzo del tuo account Claude.ai — % sessione 5 ore, % settimanale, crediti, storico 24 ore — **senza consumare un singolo token**.

---

## Come funziona

```
Claude.ai (web / desktop / mobile / VS Code / Claude Code)
       │
       │  uso normale — zero token consumati dal monitor
       ▼
  Anthropic OAuth Usage API (gratuita)
       │  GET /api/oauth/usage  ·  ~1 secondo
       │  refresh_token automatico quando il token scade
       ▼
+-----------------------------------+
|  Raspberry Pi  ·  FastAPI :8080   |
|  rpi-monitor/backend/server.py    |
+-----------------------------------+
                 │
                 ▼
      Chromium kiosk 800x480
      Dashboard touch — 3 pagine
```

Il monitor legge l'**OAuth token di Claude Code** da `~/.claude/.credentials.json` e chiama `https://api.anthropic.com/api/oauth/usage`. Quando l'access token scade, il backend usa il `refresh_token` per rinnovarlo **automaticamente**, senza interazione utente — finché il refresh_token rimane valido (settimane). Solo quando anche quello scade serve un `claude login` manuale via SSH.

---

## Hardware

| Componente | Modello | Prezzo |
|---|---|---|
| Single-board computer | **Raspberry Pi 3 Model B+** (prototipo) — o Raspberry Pi 4 / 5 | ~€35–45 |
| Display touch HDMI | [5" 800×480 resistivo (Futuranet)](https://futuranet.it/prodotto/display-touch-screen-5-800x480-pixel/) | €59 |
| MicroSD | 16 GB Classe 10 (Samsung/SanDisk Endurance) | ~€8 |
| Alimentatore | USB-C 27W / 5.1V 3A ufficiale RPi | ~€12 |
| Cavo HDMI / micro-HDMI | ponticello HDMI incluso (vedi nota) | — |

> **Questo progetto è stato realizzato e collaudato su un Raspberry Pi 3 Model B+.**
> Il display Futuranet da 5" è un pannello **resistivo** che si **appoggia sul connettore GPIO a 40 pin**
> del Raspberry (ne ricava alimentazione e segnale touch tramite il controller ADS7846/XPT2046), mentre
> il video passa da un **ponticello HDMI corto**. Sul Pi 3B+ quel ponticello si innesta **direttamente sulla
> porta HDMI full-size**: il pannello resta solidale al Pi senza cavi video volanti. È il motivo per cui
> abbiamo scelto questo abbinamento.
>
> **Raspberry Pi 4 / 5** funzionano e sono più veloci all'avvio di Chromium, ma espongono due porte
> **micro-HDMI**: serve quindi un adattatore **micro-HDMI → HDMI** (sul Pi 4 usare la porta **HDMI 0**,
> quella vicina all'USB-C). Il touch resistivo via GPIO è configurato automaticamente da `setup-rpi.sh`
> (overlay `ads7846`), quindi **non è un touch USB plug-and-play**: usare l'installer fornito.

---

## Installazione rapida (Raspberry Pi)

```bash
curl -fsSL https://raw.githubusercontent.com/BorisLandoni/claude-token-monitor/main/setup-rpi.sh | bash
```

Lo script:
1. Installa Node.js, Python, dipendenze di sistema
2. Installa **Claude Code** (`claude` CLI)
3. Esegue `claude login` — autenticazione one-shot da browser
4. Clona il repository
5. Crea l'ambiente Python
6. Crea e avvia il servizio **systemd** (autostart al boot)
7. Configura **Chromium in modalità kiosk** sul display

Dopo il setup:
- Dashboard locale: `http://localhost:8080`
- Da PC/telefono sulla stessa LAN: `http://<IP-del-rpi>:8080`

Guida passo-passo completa (flash SD, WiFi, SSH, troubleshooting) → [GUIDA-RASPBERRY.md](GUIDA-RASPBERRY.md)

---

## Sviluppo / simulazione su Windows o macOS

```bash
python rpi-monitor/backend/simulate.py
# poi apri http://localhost:8080
```

`simulate.py` genera dati finti realistici (% sessione che avanza nel tempo, 24h di storico con pattern giornaliero) **senza connettersi a Claude.ai**. Utile per sviluppo UI e test.

Per testare con dati reali su Windows, basta avere [Claude Code](https://claude.ai/download) installato e loggato, poi lanciare `python rpi-monitor/backend/server.py`.

---

## Struttura del repository

```
claude-token-monitor/
├── VERSION                       Versione corrente (es. 1.4.3)
├── setup-rpi.sh                  Script di setup automatico RPi
├── rpi-monitor/
│   ├── backend/
│   │   ├── server.py             FastAPI + background poll loop + OTA update
│   │   ├── claude_client.py      OAuth poll + refresh automatico
│   │   ├── store.py              Storage in RAM + persistenza data.json
│   │   ├── simulate.py           Server di simulazione (dati finti)
│   │   └── requirements.txt
│   └── frontend/
│       └── index.html            SPA 800x480 touch (gauge, sparkline, settings)
├── README.md
└── GUIDA-RASPBERRY.md            Guida passo-passo per RPi
```

---

## Dashboard

Tre pagine, navigazione touch in basso.

### ADESSO
- **Gauge sessione**: arco circolare con % utilizzato in grande, % rimasto sotto.
- **Countdown reset**: tempo al prossimo reset della finestra 5h, con orario esatto.
- **Timeline sessione**: barra orizzontale che mostra dove sei nelle 5h correnti.
- **Box settimanale**: % utilizzata grande + % rimasto + giorno/ora del prossimo reset settimanale.
- **Sparkline storica**: grafico utilizzo nelle ultime ore con:
  - Selettore finestra: **4h / 12h / 24h**
  - Slider per scorrere nel tempo (passato + futuro fino al prossimo reset)
  - Linee orarie + etichetta giorno (es. `gio 00h`) ai cambi di data
  - Marker `↺` verde solo sui **reset reali osservati** dal backend (no proiezioni)
  - Indicatore "ora corrente" (pallino ciano)
- **Barra refresh**: countdown al prossimo aggiornamento automatico (durata = setting in Impostazioni).
- **Bottone Aggiorna**: forza un poll immediato; rispetta il cool-down anti-rate-limit di Anthropic.

### CREDITI
- Spesa mensile in € (importo + barra + % utilizzato)
- Saldo disponibile in €
- Prossimo reset crediti (visibile solo se l'API lo fornisce)

### IMPOSTAZIONI
- **Stato OAuth Claude Code** (attivo / scaduto)
- **Riprova Connessione**: tenta refresh automatico via `refresh_token`. Solo se fallisce, mostra istruzioni per `claude login` manuale.
- **Cancella sessione locale**: pulisce eventuali cookie locali (conferma a 2 step).
- **Versione software + Verifica aggiornamenti**: aggiornamento OTA da GitHub direttamente dall'UI (vedi sotto).
- **Esci dal Kiosk**: chiude Chromium per accedere al desktop (conferma a 2 step).
- **Quanto spesso richiedere nuovi dati a Claude**: 1 / 2 / 5 / 10 / 30 minuti.
- **Tema colore**: 6 temi (Blu, Grigio, Viola, Chiaro, Verde, Ambra).
- **URL accesso da PC/telefono**: visualizzato a schermo.

---

## Aggiornamento OTA dall'UI

Da v1.3.x in poi:
1. **Impostazioni → Verifica Aggiornamenti**: confronta `VERSION` locale con quella su GitHub.
2. Se c'è una nuova versione, appare **Aggiorna Adesso**.
3. Il backend esegue in sequenza:
   - `git fetch --all --prune` (timeout 120s)
   - `git reset --hard origin/<branch>` (sovrascrive modifiche locali, no merge conflict)
   - `sudo systemctl restart claude-monitor` + `pkill chromium` (reload pulito del kiosk)
4. Toast verde a schermo + reload automatico della UI dopo 5s.

Variabili d'ambiente git impostate per evitare blocchi: `GIT_TERMINAL_PROMPT=0`, `GCM_INTERACTIVE=never`, `GIT_ASKPASS=echo`.

In alternativa (manuale via SSH):
```bash
cd ~/claude-token-monitor
git fetch && git reset --hard origin/<branch>
sudo systemctl restart claude-monitor
```

---

## Autenticazione

### OAuth Claude Code (unico metodo supportato)

Il monitor legge `~/.claude/.credentials.json` creato da Claude Code:

```json
{
  "claudeAiOauth": {
    "accessToken":  "sk-ant-oat...",
    "refreshToken": "sk-ant-ort...",
    "expiresAt":    1234567890000
  }
}
```

Chiama:
```
GET https://api.anthropic.com/api/oauth/usage
Authorization: Bearer <accessToken>
anthropic-beta: oauth-2025-04-20
```

Restituisce: % sessione (5h), % settimanale (7d), reset timestamps, crediti spesi, plan.

**Auto-refresh**: alla prima 401, il backend chiama `https://console.anthropic.com/v1/oauth/token` con `grant_type=refresh_token` per ottenere un nuovo `accessToken` (e lo riscrive nel file), poi ritenta. Trasparente per l'utente.

---

## Persistenza reset sessione

Il backend traccia gli shift di `session_resets_at_ts`: quando il valore cambia significativamente (>30 min), registra `(new_value − 5h)` come momento reale del reset in `data.json`.

Conseguenze:
- I marker `↺` sul grafico riflettono i **veri** reset (non proiezioni).
- I reset accumulati sopravvivono ai riavvii del servizio.
- Cleanup automatico oltre i 7 giorni.

Al primo install non c'è storico, quindi vedi solo il marker del **prossimo** reset (che è dato esplicito dell'API). Man mano che i reset avvengono, vengono memorizzati e iniziano ad apparire anche quelli passati.

---

## API REST

Backend sulla porta 8080.

| Metodo | Endpoint | Descrizione |
|---|---|---|
| `GET`  | `/api/account` | Snapshot sessione + settimanale + crediti |
| `GET`  | `/api/account/history` | Campioni % sessione (ultime 24h) |
| `GET`  | `/api/account/resets` | Timestamp Unix dei reset osservati (ultimi 7 giorni) |
| `GET`  | `/api/session` | Stato OAuth, email, logged_in |
| `POST` | `/api/poll` | Forza poll immediato (con refresh automatico se serve) |
| `POST` | `/api/logout` | Cancella eventuali cookie locali |
| `GET`  | `/api/settings` | Legge `poll_interval`, `theme` |
| `PUT`  | `/api/settings` | Aggiorna impostazioni |
| `GET`  | `/api/version` | Versione corrente + commit |
| `GET`  | `/api/version/check` | Confronta con GitHub |
| `POST` | `/api/update` | Avvia aggiornamento OTA |
| `GET`  | `/api/update/log` | Log streaming dell'update |
| `POST` | `/api/login/start` | Avvia `claude login` (fallback se refresh fallisce) |
| `GET`  | `/api/login/status` | Stato login |
| `POST` | `/api/login/cancel` | Annulla login in corso |
| `POST` | `/api/kiosk/exit` | Chiude Chromium |
| `POST` | `/api/restart` | Riavvia il servizio |
| `GET`  | `/health` | Health check |

### Esempi

```bash
# Stato account
curl http://raspberrypi.local:8080/api/account | python3 -m json.tool

# Forza poll (rispetta cool-down)
curl -X POST http://raspberrypi.local:8080/api/poll

# Cambia intervallo a 5 minuti
curl -X PUT http://raspberrypi.local:8080/api/settings \
  -H 'Content-Type: application/json' \
  -d '{"poll_interval":300}'

# Reset registrati
curl http://raspberrypi.local:8080/api/account/resets
```

---

## Gestione servizio

```bash
sudo systemctl status claude-monitor      # stato
sudo systemctl restart claude-monitor     # riavvio
sudo journalctl -u claude-monitor -f      # log live
sudo journalctl -u claude-monitor -n 100  # ultimi 100 log
```

---

## Sicurezza e privacy

- **Endpoint OAuth gratuito**: nessun token a pagamento consumato dal monitor
- **Token solo locale**: `~/.claude/.credentials.json` non lascia mai il dispositivo
- **Nessun server esterno coinvolto**: tutto sulla LAN
- **`data.json`**: contiene solo storico % uso + reset timestamps, nessuna credenziale
- **Dashboard non protetta**: usare in reti private/domestiche

---

## Licenza

MIT — Boris Landoni
