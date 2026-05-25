"""
Claude Monitor — RPi backend
FastAPI server: account limits, login, settings.
Default port: 8080
"""

import asyncio
import os
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import store
from claude_client import ClaudeClient

PORT = int(os.getenv('PORT', 8080))
_repo_frontend = Path(__file__).parent.parent / 'frontend'
FRONTEND_DIR   = _repo_frontend if _repo_frontend.exists() else Path(__file__).parent

client = ClaudeClient()


# ── Account limits processing ─────────────────────────────────────────────────

def process_account_limits(limits: dict):
    reset_at_ts = store.to_unix_ts(limits.get('reset_at') or limits.get('session_resets_at'))
    # Preserve credits/design data from previous Playwright scrape when fast httpx
    # poll runs (it won't return billing data).
    prev = store.account or {}
    def _keep(key):
        return limits.get(key) if limits.get(key) is not None else prev.get(key)

    store.account = {
        # Count-based (may be None for Pro accounts that only show %)
        'messages_remaining': limits.get('messages_remaining'),
        'messages_limit':     limits.get('messages_limit'),
        'messages_used':      limits.get('messages_used'),
        # Percentage-based (Pro accounts — from claude.ai/settings > Utilizzo)
        'session_pct_used':      limits.get('session_pct_used'),
        'session_pct_remaining': limits.get('session_pct_remaining'),
        # Session reset
        'reset_at':             limits.get('reset_at') or limits.get('session_resets_at'),
        'reset_at_ts':          reset_at_ts,
        'session_resets_at_ts': limits.get('session_resets_at_ts') or reset_at_ts,
        # Weekly limits
        'weekly_pct_used':      limits.get('weekly_pct_used'),
        'weekly_pct_remaining': limits.get('weekly_pct_remaining'),
        'weekly_resets_label':  limits.get('weekly_resets_label'),
        'weekly_resets_at_ts':  store.to_unix_ts(limits.get('weekly_resets_at')),
        # Claude Design limits (DOM scrape only — preserved across fast polls)
        'design_pct_used':      _keep('design_pct_used'),
        'design_pct_remaining': _keep('design_pct_remaining'),
        # Credits / billing (DOM scrape only — preserved across fast polls)
        'credits_spent_eur':    _keep('credits_spent_eur'),
        'credits_limit_eur':    _keep('credits_limit_eur'),
        'credits_balance_eur':  _keep('credits_balance_eur'),
        'credits_reset_label':  _keep('credits_reset_label'),
        # Routine giornaliere (DOM scrape only — preserved across fast polls)
        'routines_used':        _keep('routines_used'),
        'routines_limit':       _keep('routines_limit'),
        # Meta
        'plan':           limits.get('plan', 'pro'),
        'session_status': 'ok',
        'ts':             int(time.time() * 1000),
    }
    store.save()
    spct = store.account.get('session_pct_used')
    remain = store.account.get('messages_remaining')
    if spct is not None:
        store.add_sample(spct)
    print(f"[account] sessione:{spct}% usato | rimasti:{remain} | piano:{store.account['plan']}")


# ── Background poll loop ──────────────────────────────────────────────────────

async def poll_loop():
    while True:
        interval = max(30, store.settings.get('poll_interval', 60))
        await asyncio.sleep(interval)
        if not client.has_auth():
            continue
        try:
            result = await do_poll()
            if result.get('refreshed'):
                print(f'[poll] auto-poll completato')
        except Exception as e:
            print(f'[poll] errore: {e}')


async def do_poll() -> dict:
    """
    Strategia poll:
    1. OAuth (Claude Code) → sessione, weekly, crediti (primario, ~1s).
    2. Se cookie disponibili → DOM scrape Playwright per Claude Design + routine.
    3. Merge: i dati DOM arricchiscono quelli OAuth.
    Ritorna {'refreshed': bool, 'next_poll_in': int}
    """
    merged: dict = {}

    # OAuth primario
    oauth_data = await client.poll_oauth_usage()
    if oauth_data and oauth_data.get('_error') == 'oauth_expired':
        if store.account:
            store.account['session_status'] = 'oauth_expired'
            store.save()
        return {'refreshed': False, 'next_poll_in': 0}
    if oauth_data:
        merged.update(oauth_data)

    # Cookie/DOM secondario (per design + routine)
    if client.has_cookies():
        dom_data = await client.poll_with_playwright()
        if dom_data and not dom_data.get('_error'):
            # OAuth ha priorità su session/weekly; DOM aggiunge il resto
            for k, v in dom_data.items():
                if k not in merged or merged.get(k) is None:
                    merged[k] = v

    if merged:
        process_account_limits(merged)
        return {'refreshed': True, 'next_poll_in': client.seconds_until_next_poll()}
    return {'refreshed': False, 'next_poll_in': client.seconds_until_next_poll()}


# ── App lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    store.load()
    task = asyncio.create_task(poll_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


app = FastAPI(title='Claude Monitor', lifespan=lifespan)


# ── Token endpoints ───────────────────────────────────────────────────────────

class TokenEvent(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


@app.get('/api/tokens')
def get_tokens():
    return store.session_totals()


@app.post('/api/tokens')
def post_tokens(body: TokenEvent):
    store.add_token_event(
        body.input_tokens, body.output_tokens,
        body.cache_read_tokens, body.cache_creation_tokens,
    )
    stats = store.session_totals()
    print(f"[token] #{stats['requests_count']} in:{body.input_tokens} out:{body.output_tokens} | ${stats['cost_usd']}")
    return {'ok': True, 'stats': stats}


@app.delete('/api/tokens')
def delete_tokens():
    store.history.clear()
    store.save()
    return {'ok': True}


@app.get('/api/tokens/hourly')
def get_hourly():
    return store.get_hourly()


@app.get('/api/tokens/daily')
def get_daily():
    return store.get_daily()


@app.get('/api/tokens/weekly')
def get_weekly():
    return store.get_weekly()


# ── Account endpoints ──────────────────────────────────────────────────────────

class AccountData(BaseModel):
    messages_remaining: Optional[int] = None
    messages_limit: Optional[int] = None
    messages_used: Optional[int] = None
    reset_at: Optional[str] = None
    plan: Optional[str] = None


@app.post('/api/account')
def post_account(body: AccountData):
    data = body.model_dump()
    if data['messages_remaining'] is None and data['messages_limit'] is None:
        return {'ok': False, 'reason': 'no useful data'}
    process_account_limits(data)
    return {'ok': True, 'account': store.account}


@app.get('/api/account')
def get_account():
    if not store.account:
        return {'has_data': False}
    return {'has_data': True, **store.account}


@app.get('/api/account/history')
def get_account_history():
    return store.get_session_history()


# ── Login / session ───────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str


@app.post('/api/login')
async def login(body: LoginRequest):
    store.settings['email'] = body.email
    store.save()

    success, message = await client.login_playwright(body.email, body.password)
    if success:
        limits = await client.poll_limits()
        if limits and not limits.get('_error'):
            process_account_limits(limits)

    return {'ok': success, 'message': message}


@app.get('/api/session')
def get_session():
    has_oauth   = client.has_oauth()
    has_cookies = client.has_cookies()
    return {
        'logged_in':         has_oauth or has_cookies,
        'oauth_available':   has_oauth,
        'cookies_available': has_cookies,
        'email':             store.settings.get('email', ''),
        'session_status':    (store.account or {}).get('session_status', 'not_logged_in'),
        'cookie_age_hours':  round(client.get_cookie_age_seconds() / 3600, 1)
            if client.get_cookie_age_seconds() is not None else None,
    }


@app.post('/api/poll')
async def force_poll():
    """Trigger immediato di un poll completo (OAuth + DOM se cookie)."""
    try:
        result = await do_poll()
        return {'ok': True, 'account': store.account, **result}
    except Exception as e:
        return {'ok': False, 'reason': str(e)}


class ImportCookies(BaseModel):
    text: str


@app.post('/api/import-cookies')
async def import_cookies(body: ImportCookies):
    """Importa cookie da Chrome (cURL, raw string, JSON Playwright)."""
    n, msg = client.import_cookies_from_text(body.text)
    if n == 0:
        return {'ok': False, 'message': msg}
    try:
        await do_poll()
    except Exception as e:
        print(f'[import] poll dopo import: {e}')
    return {'ok': True, 'message': msg, 'count': n}


@app.post('/api/logout')
def logout():
    from claude_client import COOKIES_FILE, ENDPOINTS_FILE
    for f in [COOKIES_FILE, ENDPOINTS_FILE]:
        if f.exists():
            f.unlink()
    store.account = None
    store.save()
    return {'ok': True}


# ── Settings ──────────────────────────────────────────────────────────────────

class SettingsUpdate(BaseModel):
    poll_interval: Optional[int] = None
    theme: Optional[str] = None


@app.get('/api/settings')
def get_settings():
    return {
        'poll_interval': store.settings.get('poll_interval', 60),
        'theme': store.settings.get('theme', 'dark'),
        'email': store.settings.get('email', ''),
    }


@app.put('/api/settings')
def update_settings(body: SettingsUpdate):
    if body.poll_interval is not None:
        store.settings['poll_interval'] = max(30, body.poll_interval)
    if body.theme is not None:
        store.settings['theme'] = body.theme
    store.save()
    return {'ok': True, 'settings': store.settings}


# ── Health ────────────────────────────────────────────────────────────────────

@app.get('/health')
def health():
    return {'ok': True}


# ── Setup RPi ─────────────────────────────────────────────────────────────────

_setup_log: list = []
_setup_running: bool = False


def _find_setup_script() -> Optional[Path]:
    candidates = [
        Path.home() / 'claude-token-monitor' / 'setup-rpi.sh',
        Path(__file__).parent.parent.parent / 'setup-rpi.sh',
        Path(__file__).parent.parent / 'setup-rpi.sh',
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


@app.post('/api/setup-rpi')
async def run_setup_rpi():
    global _setup_running, _setup_log
    if _setup_running:
        return {'ok': False, 'reason': 'Setup già in corso'}

    script = _find_setup_script()
    if not script:
        return {'ok': False, 'reason': 'Script setup-rpi.sh non trovato sul dispositivo'}

    _setup_log = [f'Avvio da: {script}\n\n']
    _setup_running = True

    def _run():
        global _setup_running
        try:
            env = os.environ.copy()
            env['NON_INTERACTIVE'] = '1'
            proc = subprocess.Popen(
                ['bash', str(script)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                env=env,
            )
            for line in proc.stdout:
                _setup_log.append(line)
            proc.wait()
            rc = proc.returncode
            _setup_log.append(
                f'\n{"[✓] Setup completato con successo!" if rc == 0 else f"[✗] Errore (codice {rc})"}\n'
            )
        except Exception as e:
            _setup_log.append(f'\n[✗] Errore: {e}\n')
        finally:
            _setup_running = False

    threading.Thread(target=_run, daemon=True).start()
    return {'ok': True}


@app.get('/api/setup-rpi/log')
def get_setup_log():
    return {
        'running': _setup_running,
        'log': ''.join(_setup_log),
        'available': _find_setup_script() is not None,
    }


# ── Frontend ──────────────────────────────────────────────────────────────────

@app.get('/')
def index():
    return FileResponse(FRONTEND_DIR / 'index.html')


# Serve any other static assets from frontend dir
if FRONTEND_DIR.exists():
    app.mount('/assets', StaticFiles(directory=str(FRONTEND_DIR)), name='assets')


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import uvicorn
    print(f'\nClaude Monitor RPi -> http://0.0.0.0:{PORT}')
    uvicorn.run('server:app', host='0.0.0.0', port=PORT, reload=False)
