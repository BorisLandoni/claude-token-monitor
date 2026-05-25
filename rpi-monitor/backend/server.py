"""
Claude Monitor — RPi backend
FastAPI server: token history, account limits, login, settings.
Compatible with the same API format as server.js (port 3333).
Default port: 8080
"""

import asyncio
import os
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
FRONTEND_DIR = Path(__file__).parent.parent / 'frontend'

client = ClaudeClient()


# ── Account limits processing ─────────────────────────────────────────────────

def process_account_limits(limits: dict):
    reset_at_ts = store.to_unix_ts(limits.get('reset_at') or limits.get('session_resets_at'))
    store.account = {
        # Count-based (may be None for Pro accounts that only show %)
        'messages_remaining': limits.get('messages_remaining'),
        'messages_limit':     limits.get('messages_limit'),
        'messages_used':      limits.get('messages_used'),
        # Percentage-based (Pro accounts — from claude.ai/settings > Utilizzo)
        'session_pct_used':      limits.get('session_pct_used'),
        'session_pct_remaining': limits.get('session_pct_remaining'),
        # Session reset
        'reset_at':           limits.get('reset_at') or limits.get('session_resets_at'),
        'reset_at_ts':        reset_at_ts,
        'session_resets_at_ts': limits.get('session_resets_at_ts') or reset_at_ts,
        # Weekly limits
        'weekly_pct_used':      limits.get('weekly_pct_used'),
        'weekly_pct_remaining': limits.get('weekly_pct_remaining'),
        'weekly_resets_label':  limits.get('weekly_resets_label'),  # e.g. "sab 17:59"
        'weekly_resets_at_ts':  store.to_unix_ts(limits.get('weekly_resets_at')),
        # Meta
        'plan':           limits.get('plan', 'pro'),
        'session_status': 'ok',
        'ts':             int(time.time() * 1000),
    }
    store.save()
    spct = store.account.get('session_pct_used')
    remain = store.account.get('messages_remaining')
    print(f"[account] sessione:{spct}% usato | rimasti:{remain} | piano:{store.account['plan']}")


# ── Background poll loop ──────────────────────────────────────────────────────

async def poll_loop():
    while True:
        interval = max(30, store.settings.get('poll_interval', 60))
        await asyncio.sleep(interval)
        if not client.has_cookies():
            continue
        try:
            limits = await client.poll_limits()
            if limits is None:
                print('[poll] httpx fallito, provo Playwright...')
                limits = await client.poll_with_playwright()

            if limits and limits.get('_error') == 'session_expired':
                if store.account:
                    store.account['session_status'] = 'expired'
                    store.save()
            elif limits:
                process_account_limits(limits)
        except Exception as e:
            print(f'[poll] errore: {e}')


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


# ── Token endpoints (compatible with server.js) ───────────────────────────────

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
    return {
        'logged_in': client.has_cookies(),
        'email': store.settings.get('email', ''),
        'session_status': (store.account or {}).get('session_status', 'not_logged_in'),
        'cookie_age_hours': round(client.get_cookie_age_seconds() / 3600, 1)
            if client.get_cookie_age_seconds() is not None else None,
    }


@app.post('/api/poll')
async def force_poll():
    """Manually trigger an immediate poll of claude.ai."""
    limits = await client.poll_limits()
    if not limits:
        limits = await client.poll_with_playwright()
    if limits and not limits.get('_error'):
        process_account_limits(limits)
        return {'ok': True, 'account': store.account}
    return {'ok': False, 'reason': str(limits)}


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
    print(f'\nClaude Monitor RPi → http://0.0.0.0:{PORT}')
    uvicorn.run('server:app', host='0.0.0.0', port=PORT, reload=False)
