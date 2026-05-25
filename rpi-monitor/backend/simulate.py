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
FRONTEND_DIR = Path(__file__).parent.parent / 'frontend'

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

SETTINGS = {'poll_interval': 60, 'theme': 'dark', 'email': 'demo@simulazione.it'}

# Dati orari: realistici, picchi 9-12 e 14-18
_now_h = datetime.now(timezone.utc).hour
HOURLY = []
for _i in range(24):
    _h = (_now_h - 23 + _i) % 24
    if 8 <= _h <= 22:
        _base = 0.030 if (9 <= _h <= 12 or 14 <= _h <= 18) else 0.010
        _cost = round(_base * _rng.uniform(0.3, 2.2), 5)
    else:
        _cost = 0.0
    HOURLY.append({'label': str(_h).zfill(2), 'cost': _cost, 'input': 0, 'output': 0})


# ── Funzioni helper ───────────────────────────────────────────────────────────
def _session_pct() -> float:
    global SESSION_START_TS
    now     = time.time()
    elapsed = now - SESSION_START_TS
    if elapsed >= SESSION_SECS:          # sessione scaduta → reset automatico
        SESSION_START_TS = now
        elapsed = 0
    raw    = (elapsed / SESSION_SECS) * 100
    jitter = math.sin(elapsed / 240) * 1.5   # piccola oscillazione realistica
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
        'weekly_resets_label':   'Lunedì',
        'design_pct_used':       DESIGN_PCT,
        'design_pct_remaining':  100 - DESIGN_PCT,
        'routines_used':         ROUTINES_USED,
        'routines_limit':        5,
        'credits_spent_eur':     CREDITS_SPENT,
        'credits_limit_eur':     CREDITS_LIMIT,
        'credits_balance_eur':   round(CREDITS_LIMIT - CREDITS_SPENT, 2),
        'credits_reset_label':   'giugno 1',
        'reset_at':              datetime.fromtimestamp(_reset_ts(), tz=timezone.utc).isoformat(),
        'session_resets_at_ts':  _reset_ts(),
        'session_status':        'ok',
        'plan':                  'pro',
        'ts':                    int(time.time() * 1000),
    }


# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title='Claude Monitor — Simulazione')


@app.get('/api/account')
def get_account():
    return _account()

@app.post('/api/account')
async def post_account(req: Request):
    return {'ok': True, 'account': _account()}

@app.get('/api/tokens/hourly')
def get_hourly():
    return HOURLY

@app.get('/api/tokens/daily')
def get_daily():
    return []

@app.get('/api/tokens/weekly')
def get_weekly():
    return []

@app.get('/api/tokens')
def get_tokens():
    return {
        'requests_count': 47, 'cost_usd': 0.312,
        'total_input': 120000, 'total_output': 35000, 'last_updated': None,
    }

@app.post('/api/tokens')
async def post_tokens(req: Request):
    return {'ok': True, 'stats': {'requests_count': 48, 'cost_usd': 0.315}}

@app.delete('/api/tokens')
def delete_tokens():
    return {'ok': True}

@app.get('/api/session')
def get_session():
    return {
        'logged_in':        True,
        'email':            SETTINGS['email'],
        'session_status':   'ok',
        'cookie_age_hours': round((time.time() - START_TS) / 3600 + 0.3, 1),
    }

@app.post('/api/login')
async def login(req: Request):
    await asyncio.sleep(1.5)   # simula latenza Playwright
    return {'ok': True, 'message': 'Login simulato riuscito'}

@app.post('/api/logout')
def logout():
    return {'ok': True}

@app.post('/api/poll')
async def force_poll():
    await asyncio.sleep(0.4)
    return {'ok': True, 'account': _account()}

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
    print(f'   Browser  →  http://localhost:{PORT}')
    print(f'   Sessione :  {pct}% usato  (iniziata {elapsed_h:.1f}h fa)')
    print(f'   Crediti  :  {CREDITS_SPENT:.2f} € / {CREDITS_LIMIT:.0f} €')
    print(f'   Routine  :  {ROUTINES_USED} / 5')
    print('   Nessuna connessione a Claude.ai richiesta')
    print('=' * 52)
    print()
    uvicorn.run('simulate:app', host='0.0.0.0', port=PORT, reload=False)
