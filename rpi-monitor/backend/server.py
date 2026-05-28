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
        'weekly_resets_label':  _keep('weekly_resets_label'),
        'weekly_resets_at_ts':  limits.get('weekly_resets_at_ts') or _keep('weekly_resets_at_ts'),
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
    # Osserva eventuali shift di session_resets_at_ts per registrare i reset
    store.observe_session_reset_ts(store.account.get('session_resets_at_ts'))
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


# ── Account endpoints ──────────────────────────────────────────────────────────

@app.get('/api/account')
def get_account():
    if not store.account:
        return {'has_data': False}
    return {'has_data': True, **store.account}


@app.get('/api/account/history')
def get_account_history():
    return store.get_session_history()


@app.get('/api/account/resets')
def get_account_resets():
    """Timestamp Unix dei reset sessione osservati (ultimi 7 giorni)."""
    return store.get_reset_events()


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
    from claude_client import COOKIES_FILE
    if COOKIES_FILE.exists():
        COOKIES_FILE.unlink()
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

# ── Claude Login (kiosk) ─────────────────────────────────────────────────────

_login_url:    Optional[str] = None
_login_status: str           = 'idle'   # idle|starting|waiting|success|error
_login_proc                  = None
_login_token_before: Optional[str] = None


def _read_access_token() -> Optional[str]:
    """Legge l'accessToken corrente da .credentials.json (None se assente)."""
    f = Path.home() / '.claude' / '.credentials.json'
    try:
        import json as _json
        data = _json.loads(f.read_text())
        return (data.get('claudeAiOauth') or {}).get('accessToken')
    except Exception:
        return None


@app.post('/api/login/start')
async def start_claude_login():
    global _login_url, _login_status, _login_proc, _login_token_before
    if _login_status in ('waiting', 'starting'):
        return {'ok': True, 'url': _login_url, 'status': _login_status}

    _login_url = None
    _login_status = 'starting'
    # Confronto sul contenuto del token, non sul mtime (che cambia anche per refresh falliti)
    _login_token_before = _read_access_token()

    def _run():
        global _login_url, _login_status, _login_proc
        try:
            env = os.environ.copy()
            env['TERM'] = 'dumb'
            env['NO_COLOR'] = '1'
            # stdin=PIPE per poter rispondere a eventuali prompt interattivi
            # (es. "Choose login method: 1) ...")
            _login_proc = subprocess.Popen(
                ['claude', 'login'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, env=env,
            )
            # Risposta predefinita per il prompt di scelta metodo (1 = Claude Pro/Max)
            try:
                _login_proc.stdin.write('1\n')
                _login_proc.stdin.flush()
            except Exception:
                pass

            for line in _login_proc.stdout:
                print(f'[login] {line.rstrip()}')
                # Cattura QUALSIASI URL (claude.ai, console.anthropic.com, ecc.)
                m = _re.search(r'https?://[\w.-]+\.[a-z]{2,}\S*', line)
                if m and not _login_url:
                    _login_url = m.group().rstrip('.,)\'"')
                    _login_status = 'waiting'
                # Successo SOLO se il token cambia davvero (non basta mtime)
                cur = _read_access_token()
                if cur and cur != _login_token_before:
                    _login_status = 'success'
                    break
            try:
                _login_proc.wait(timeout=300)
            except Exception:
                pass
            if _login_status != 'success':
                cur = _read_access_token()
                _login_status = 'success' if (cur and cur != _login_token_before) else 'error'
        except Exception as e:
            _login_status = 'error'
            print(f'[login] errore: {e}')
        finally:
            _login_proc = None

    threading.Thread(target=_run, daemon=True).start()
    # Aspetta fino a 20s che appaia l'URL (RPi 3B+ è lento ad avviare claude)
    for _ in range(40):
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
        # ?ts= bypassa la cache CDN di raw.githubusercontent.com
        url = f'{GITHUB_VERSION_URL}?ts={int(time.time())}'
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url, headers={'Cache-Control': 'no-cache'})
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

    _update_log = [f'Aggiornamento da GitHub (dir: {d})...\n']
    _update_running = True

    def _git(args, timeout=120):
        """Esegue git con env safe (no prompt credenziali) + timeout + log streaming."""
        env = {
            **os.environ,
            'GIT_TERMINAL_PROMPT': '0',     # niente prompt interattivi
            'GIT_ASKPASS':         'echo',  # fallback per password
            'GCM_INTERACTIVE':     'never', # disabilita git-credential-manager
        }
        _update_log.append(f'\n$ git {" ".join(args)}\n')
        proc = subprocess.Popen(
            ['git', '-C', str(d), *args],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, env=env,
            stdin=subprocess.DEVNULL,
        )
        try:
            for line in proc.stdout:
                _update_log.append(line)
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            _update_log.append(f'\n[✗] timeout dopo {timeout}s\n')
            return -1
        return proc.returncode

    def _run():
        global _update_running
        try:
            # 1. Branch corrente
            br_proc = subprocess.run(
                ['git', '-C', str(d), 'rev-parse', '--abbrev-ref', 'HEAD'],
                capture_output=True, text=True, timeout=10,
            )
            branch = br_proc.stdout.strip() or 'main'
            _update_log.append(f'Branch: {branch}\n')

            # 2. Fetch (download, no merge)
            if _git(['fetch', '--all', '--prune'], timeout=120) != 0:
                _update_log.append('\n[✗] git fetch fallito\n')
                return

            # 3. Hard reset all'origin (sovrascrive qualsiasi modifica locale)
            if _git(['reset', '--hard', f'origin/{branch}'], timeout=60) != 0:
                _update_log.append('\n[✗] git reset fallito\n')
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
