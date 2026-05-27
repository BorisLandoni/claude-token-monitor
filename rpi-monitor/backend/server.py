"""
Claude Monitor — RPi backend
FastAPI server: account limits, login, settings.
Default port: 8080
"""

import asyncio
import os
import re as _re
import subprocess
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import httpx
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

# ── Version / Update ──────────────────────────────────────────────────────────

VERSION_FILE = Path(__file__).parent.parent.parent / 'VERSION'
GITHUB_VERSION_URL = (
    'https://raw.githubusercontent.com/BorisLandoni/'
    'claude-token-monitor/claude/rpi-token-monitor/VERSION'
)


def _get_current_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return 'unknown'


def _get_install_dir() -> Optional[Path]:
    candidates = [
        Path.home() / 'claude-token-monitor',
        Path(__file__).parent.parent.parent,
    ]
    for p in candidates:
        if (p / '.git').exists():
            return p
    return None


def _get_git_commit() -> str:
    d = _get_install_dir()
    if not d:
        return 'unknown'
    try:
        r = subprocess.run(
            ['git', '-C', str(d), 'rev-parse', '--short', 'HEAD'],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() if r.returncode == 0 else 'unknown'
    except Exception:
        return 'unknown'


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
    # Poll immediato al boot — evita di mostrare dati vecchi al primo avvio
    await asyncio.sleep(2)
    if client.has_auth():
        try:
            await do_poll()
            print('[poll] poll iniziale completato')
        except Exception as e:
            print(f'[poll] errore avvio: {e}')
    while True:
        interval = max(60, store.settings.get('poll_interval', 60))
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


# ── Session ───────────────────────────────────────────────────────────────────

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
        'theme': store.settings.get('theme', 'blue'),
        'email': store.settings.get('email', ''),
    }


@app.put('/api/settings')
def update_settings(body: SettingsUpdate):
    if body.poll_interval is not None:
        store.settings['poll_interval'] = max(60, body.poll_interval)
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


# ── Claude Login (kiosk) ─────────────────────────────────────────────────────

_login_url:    Optional[str] = None
_login_status: str           = 'idle'   # idle|starting|waiting|success|error
_login_proc                  = None
_login_cred_mtime_before: Optional[float] = None


def _cred_mtime() -> Optional[float]:
    f = Path.home() / '.claude' / '.credentials.json'
    try:
        return f.stat().st_mtime
    except Exception:
        return None


@app.post('/api/login/start')
async def start_claude_login():
    global _login_url, _login_status, _login_proc, _login_cred_mtime_before
    if _login_status in ('waiting', 'starting'):
        return {'ok': True, 'url': _login_url, 'status': _login_status}

    _login_url = None
    _login_status = 'starting'
    _login_cred_mtime_before = _cred_mtime()

    def _run():
        global _login_url, _login_status, _login_proc
        try:
            env = os.environ.copy()
            env['TERM'] = 'dumb'
            env['NO_COLOR'] = '1'
            _login_proc = subprocess.Popen(
                ['claude', 'login'],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=env,
            )
            for line in _login_proc.stdout:
                print(f'[login] {line.rstrip()}')
                m = _re.search(r'https?://\S{20,}', line)
                if m:
                    _login_url = m.group().rstrip('.,)')
                    _login_status = 'waiting'
                if _cred_mtime() != _login_cred_mtime_before:
                    _login_status = 'success'
                    break
            _login_proc.wait(timeout=300)
            if _login_status not in ('success',):
                new_mtime = _cred_mtime()
                _login_status = 'success' if new_mtime != _login_cred_mtime_before else 'error'
        except Exception as e:
            _login_status = 'error'
            print(f'[login] errore: {e}')
        finally:
            _login_proc = None

    threading.Thread(target=_run, daemon=True).start()
    # Aspetta fino a 5s che appaia l'URL
    for _ in range(10):
        await asyncio.sleep(0.5)
        if _login_url or _login_status == 'error':
            break
    return {'ok': True, 'url': _login_url, 'status': _login_status}


@app.get('/api/login/status')
def get_login_status():
    return {'url': _login_url, 'status': _login_status}


@app.post('/api/login/cancel')
def cancel_login():
    global _login_status, _login_proc
    if _login_proc:
        try:
            _login_proc.terminate()
        except Exception:
            pass
    _login_status = 'idle'
    return {'ok': True}


# ── Restart servizio ──────────────────────────────────────────────────────────

@app.post('/api/restart')
def restart_service():
    def _do():
        time.sleep(1)
        subprocess.run(['sudo', 'systemctl', 'restart', 'claude-monitor'], timeout=30)
    threading.Thread(target=_do, daemon=True).start()
    return {'ok': True}


# ── Kiosk control ─────────────────────────────────────────────────────────────

@app.post('/api/kiosk/exit')
def kiosk_exit():
    """Termina wrapper start-kiosk.sh e chromium — torna al desktop."""
    try:
        subprocess.run(['pkill', '-f', 'start-kiosk.sh'], timeout=5)
    except Exception as e:
        print(f'[kiosk] pkill wrapper: {e}')
    try:
        subprocess.run(['pkill', '-f', 'chromium'], timeout=5)
    except Exception as e:
        print(f'[kiosk] pkill chromium: {e}')
    return {'ok': True}


@app.get('/api/kiosk/diag')
def kiosk_diag():
    """Diagnostica file e processi kiosk."""
    autostart  = Path.home() / '.config' / 'autostart' / 'claude-monitor-kiosk.desktop'
    script     = Path.home() / 'claude-token-monitor' / 'start-kiosk.sh'
    kiosk_log  = Path.home() / 'kiosk.log'
    chromium_paths = []
    for name in ('chromium', 'chromium-browser'):
        try:
            r = subprocess.run(['which', name], capture_output=True, text=True, timeout=3)
            if r.returncode == 0 and r.stdout.strip():
                chromium_paths.append(r.stdout.strip())
        except Exception:
            pass
    try:
        ps = subprocess.run(
            ['pgrep', '-af', 'chromium|start-kiosk'],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
    except Exception:
        ps = ''
    log_tail = ''
    if kiosk_log.exists():
        try:
            log_tail = kiosk_log.read_text(errors='replace').splitlines()[-30:]
            log_tail = '\n'.join(log_tail)
        except Exception as e:
            log_tail = f'(errore lettura: {e})'
    return {
        'autostart_exists':  autostart.exists(),
        'autostart_content': autostart.read_text() if autostart.exists() else None,
        'script_exists':     script.exists(),
        'script_executable': script.exists() and os.access(script, os.X_OK),
        'script_content':    script.read_text() if script.exists() else None,
        'chromium_paths':    chromium_paths,
        'running_procs':     ps,
        'kiosk_log_tail':    log_tail,
        'home':              str(Path.home()),
        'user':              os.environ.get('USER', '?'),
    }


@app.post('/api/kiosk/start')
def kiosk_start():
    """Rilancia il kiosk se è stato chiuso."""
    script = Path.home() / 'claude-token-monitor' / 'start-kiosk.sh'
    if not script.exists():
        return {'ok': False, 'reason': 'Script start-kiosk.sh non trovato'}
    try:
        subprocess.Popen(
            ['bash', str(script)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as e:
        return {'ok': False, 'reason': str(e)}
    return {'ok': True}


# ── Version / Update endpoints ───────────────────────────────────────────────

@app.get('/api/version')
def get_version():
    return {
        'version': _get_current_version(),
        'commit':  _get_git_commit(),
        'install_dir': str(_get_install_dir()) if _get_install_dir() else None,
    }


@app.get('/api/version/check')
async def check_version():
    current = _get_current_version()
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(GITHUB_VERSION_URL)
        latest = r.text.strip() if r.status_code == 200 else None
    except Exception:
        latest = None
    up_to_date = (current == latest) if latest else None
    return {
        'current':          current,
        'latest':           latest,
        'up_to_date':       up_to_date,
        'update_available': (not up_to_date) if (latest and up_to_date is not None) else None,
    }


_update_log: list = []
_update_running: bool = False


@app.post('/api/update')
async def run_update():
    global _update_running, _update_log
    if _update_running:
        return {'ok': False, 'reason': 'Aggiornamento già in corso'}
    d = _get_install_dir()
    if not d:
        return {'ok': False, 'reason': 'Directory di installazione non trovata'}

    _update_log = ['Aggiornamento da GitHub...\n']
    _update_running = True

    def _run():
        global _update_running
        try:
            proc = subprocess.Popen(
                ['git', '-C', str(d), 'pull'],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
            )
            for line in proc.stdout:
                _update_log.append(line)
            proc.wait()
            if proc.returncode != 0:
                _update_log.append(f'\n[✗] git pull fallito (codice {proc.returncode})\n')
                return
            _update_log.append('\n[✓] Codice aggiornato\n')
            _update_log.append('[→] Riavvio servizio + kiosk in background...\n')
            _update_log.append('[✓] Servizio riavviato con successo!\n')

            # Detached: sopravvive al restart del processo corrente.
            # Dopo aver riavviato il servizio, killa chromium per forzare
            # un reload completo del kiosk con cache fresca.
            subprocess.Popen(
                ['bash', '-c',
                 'sleep 2 && sudo systemctl restart claude-monitor && '
                 'sleep 5 && pkill -f chromium'],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except Exception as e:
            _update_log.append(f'\n[✗] Errore: {e}\n')
        finally:
            _update_running = False

    threading.Thread(target=_run, daemon=True).start()
    return {'ok': True}


@app.get('/api/update/log')
def get_update_log_ep():
    return {
        'running': _update_running,
        'log':     ''.join(_update_log),
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
