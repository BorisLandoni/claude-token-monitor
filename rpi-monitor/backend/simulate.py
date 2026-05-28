"""
Claude Monitor — Simulazione locale
------------------------------------
Avvia:   python simulate.py
Browser: http://localhost:8080

Non richiede connessione a Claude.ai.
La sessione avanza automaticamente nel tempo (1% ogni ~3 minuti).
Dopo 5 ore si resetta da sola, proprio come il vero Claude.
"""
import asyncio
import math
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

PORT = 8080
_repo_frontend = Path(__file__).parent.parent / 'frontend'
FRONTEND_DIR   = _repo_frontend if _repo_frontend.exists() else Path(__file__).parent

# ── Stato simulazione ─────────────────────────────────────────────────────────
_rng = random.Random(int(time.time()))
START_TS         = time.time()
SESSION_START_TS = START_TS - _rng.uniform(1.2, 3.8) * 3600   # già partita da un po'
SESSION_SECS     = 5 * 3600

WEEKLY_PCT    = _rng.randint(28, 58)
DESIGN_PCT    = _rng.randint(12, 48)
ROUTINES_USED = _rng.randint(0, 3)
CREDITS_SPENT = round(_rng.uniform(1.2, 7.8), 2)
CREDITS_LIMIT = 10.0

SETTINGS = {'poll_interval': 60, 'theme': 'blue', 'email': 'demo@simulazione.it'}


def _session_pct() -> float:
    global SESSION_START_TS
    now     = time.time()
    elapsed = now - SESSION_START_TS
    if elapsed >= SESSION_SECS:
        SESSION_START_TS = now
        elapsed = 0
    raw    = (elapsed / SESSION_SECS) * 100
    jitter = math.sin(elapsed / 240) * 1.5
    return min(99.0, max(0.0, raw + jitter))

def _reset_ts() -> int:
    return int(SESSION_START_TS + SESSION_SECS)

def _account() -> dict:
    spct = _session_pct()
    return {
        'has_data':              True,
        'session_pct_used':      round(spct),
        'session_pct_remaining': round(100 - spct),
        'weekly_pct_used':       WEEKLY_PCT,
        'weekly_pct_remaining':  100 - WEEKLY_PCT,
        'weekly_resets_label':   'Lun 00:00',
        'weekly_resets_at_ts':   _reset_ts() + 2 * 86400,
        'design_pct_used':       DESIGN_PCT,
        'design_pct_remaining':  100 - DESIGN_PCT,
        'routines_used':         ROUTINES_USED,
        'routines_limit':        5,
        'credits_spent_eur':     CREDITS_SPENT,
        'credits_limit_eur':     CREDITS_LIMIT,
        'credits_balance_eur':   round(CREDITS_LIMIT - CREDITS_SPENT, 2),
        'credits_reset_label':   'giugno 1',
        'reset_at':              datetime.fromtimestamp(_reset_ts(), tz=timezone.utc).isoformat(),
        'reset_at_ts':           _reset_ts(),
        'session_resets_at_ts':  _reset_ts(),
        'session_status':        'ok',
        'plan':                  'pro',
        'ts':                    int(time.time() * 1000),
    }

def _history() -> list:
    """Genera 24h di campioni finti con pattern giornaliero realistico."""
    now_ts = int(time.time())
    pts = []
    for i in range(1440, 0, -1):   # 1 punto/min × 24h
        ts   = now_ts - i * 60
        hour = (datetime.fromtimestamp(ts, tz=timezone.utc).hour + 1) % 24
        # Uso basso di notte, picchi mattina/pomeriggio
        base = (
            0  if hour < 7  else
            45 if hour < 9  else
            75 if hour < 13 else
            55 if hour < 14 else
            80 if hour < 19 else
            30 if hour < 22 else 5
        )
        pct = round(min(99, max(0, base + math.sin(i / 20) * 8 + _rng.uniform(-3, 3))), 1)
        pts.append({'ts': ts, 'pct': pct})
    return pts


# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title='Claude Monitor — Simulazione')


@app.get('/api/account')
def get_account():
    return _account()

@app.get('/api/account/history')
def get_account_history():
    return _history()

@app.get('/api/account/resets')
def get_account_resets():
    # Sim: tre reset finti scaglionati nelle ultime ore
    now_ts = int(time.time())
    return [now_ts - 5 * 3600, now_ts - 11 * 3600, now_ts - 18 * 3600]

@app.get('/api/session')
def get_session():
    return {
        'logged_in':         True,
        'oauth_available':   True,
        'cookies_available': False,
        'email':             SETTINGS['email'],
        'session_status':    'ok',
        'cookie_age_hours':  None,
    }

@app.post('/api/logout')
def logout():
    return {'ok': True}

@app.post('/api/poll')
async def force_poll():
    await asyncio.sleep(0.4)
    return {'ok': True, 'refreshed': True, 'next_poll_in': 30, 'account': _account()}

@app.get('/api/settings')
def get_settings():
    return SETTINGS

@app.put('/api/settings')
async def update_settings(req: Request):
    body = await req.json()
    for k, v in body.items():
        if v is not None:
            SETTINGS[k] = v
    return {'ok': True, 'settings': SETTINGS}

@app.get('/api/version')
def get_version():
    ver_file = Path(__file__).parent.parent.parent / 'VERSION'
    v = ver_file.read_text().strip() if ver_file.exists() else '1.3.0'
    return {'version': v, 'commit': 'sim0001', 'install_dir': None}

@app.get('/api/version/check')
async def check_version():
    ver_file = Path(__file__).parent.parent.parent / 'VERSION'
    current  = ver_file.read_text().strip() if ver_file.exists() else '1.3.0'
    return {'current': current, 'latest': current, 'up_to_date': True, 'update_available': False}

@app.post('/api/login/start')
async def login_start():
    await asyncio.sleep(1.0)
    return {'ok': True, 'url': None, 'status': 'error'}

@app.get('/api/login/status')
def login_status():
    return {'url': None, 'status': 'idle'}

@app.post('/api/login/cancel')
def login_cancel():
    return {'ok': True}

@app.post('/api/restart')
def restart():
    return {'ok': True}

@app.post('/api/kiosk/exit')
def kiosk_exit():
    return {'ok': True}

@app.post('/api/kiosk/start')
def kiosk_start():
    return {'ok': True}

@app.get('/api/kiosk/diag')
def kiosk_diag():
    return {'autostart_exists': False, 'script_exists': False, 'running_procs': ''}

@app.post('/api/update')
async def run_update():
    return {'ok': False, 'reason': 'Non disponibile in modalità simulazione'}

@app.get('/api/update/log')
def update_log():
    return {'running': False, 'log': ''}

@app.get('/health')
def health():
    return {'ok': True, 'mode': 'simulation'}

@app.get('/')
def index():
    return FileResponse(FRONTEND_DIR / 'index.html')

if FRONTEND_DIR.exists():
    app.mount('/assets', StaticFiles(directory=str(FRONTEND_DIR)), name='assets')


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    elapsed_h = (time.time() - SESSION_START_TS) / 3600
    pct       = round(_session_pct())
    print()
    print('=' * 52)
    print('   CLAUDE MONITOR  —  Modalita Simulazione')
    print('=' * 52)
    print(f'   Browser  ->  http://localhost:{PORT}')
    print(f'   Sessione :  {pct}% usato  (iniziata {elapsed_h:.1f}h fa)')
    print(f'   Crediti  :  {CREDITS_SPENT:.2f} € / {CREDITS_LIMIT:.0f} €')
    print(f'   Routine  :  {ROUTINES_USED} / 5')
    print('   Nessuna connessione a Claude.ai richiesta')
    print('=' * 52)
    print()
    uvicorn.run('simulate:app', host='0.0.0.0', port=PORT, reload=False)
