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
    'claude-token-monitor/main/VERSION'
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
    # Conserva i dati che un poll può non restituire (es. weekly/crediti assenti
    # in una risposta parziale), così la UI non lampeggia a vuoto.
    prev = store.account or {}
    def _keep(key):
        return limits.get(key) if limits.get(key) is not None else prev.get(key)

    store.account = {
        # Count-based (may be None for Pro accounts that only show %)
        'messages_remaining': limits.get('messages_remaining'),
        'messages_limit':     limits.get('messages_limit'),
        'messages_used':      limits.get('messages_used'),
        # Percentage-based (Pro accounts — da /api/oauth/usage five_hour)
        'session_pct_used':      limits.get('session_pct_used'),
        'session_pct_remaining': limits.get('session_pct_remaining'),
        # Session reset
        'reset_at':             limits.get('reset_at') or limits.get('session_resets_at'),
        'reset_at_ts':          reset_at_ts,
        'session_resets_at_ts': limits.get('session_resets_at_ts') or reset_at_ts,
        # Weekly limits (seven_day)
        'weekly_pct_used':      limits.get('weekly_pct_used'),
        'weekly_pct_remaining': limits.get('weekly_pct_remaining'),
        'weekly_resets_label':  _keep('weekly_resets_label'),
        'weekly_resets_at_ts':  limits.get('weekly_resets_at_ts') or _keep('weekly_resets_at_ts'),
        # Credits / billing (extra_usage)
        'credits_spent_eur':    _keep('credits_spent_eur'),
        'credits_limit_eur':    _keep('credits_limit_eur'),
        'credits_balance_eur':  _keep('credits_balance_eur'),
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
    Poll OAuth (Claude Code) → sessione 5h, weekly 7d, crediti (~1s).
    Ritorna {'refreshed': bool, 'next_poll_in': int}
    """
    oauth_data = await client.poll_oauth_usage()
    if oauth_data and oauth_data.get('_error') == 'oauth_expired':
        if store.account:
            store.account['session_status'] = 'oauth_expired'
            store.save()
        else:
            store.account = {'session_status': 'oauth_expired', 'ts': int(time.time() * 1000)}
        return {'refreshed': False, 'next_poll_in': 0}

    if oauth_data:
        process_account_limits(oauth_data)
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


@app.post('/api/account/resets/rescan')
def rescan_resets():
    """Forza un rescan dei samples per ricostruire reset_events."""
    before = len(store.reset_events)
    store._backfill_resets_from_samples()
    return {
        'ok': True,
        'before': before,
        'after': len(store.reset_events),
        'events': store.reset_events,
    }


@app.get('/api/debug/oauth')
def debug_oauth_raw():
    """Ultima risposta grezza dall'endpoint /api/oauth/usage di Anthropic."""
    return {
        'has_data': client.last_raw_oauth is not None,
        'last_error': client.last_error,
        'next_poll_in': client.seconds_until_next_poll(),
        'raw':      client.last_raw_oauth,
        'mapped':   store.account,
    }


@app.get('/api/debug/samples')
def debug_samples():
    """Debug: ultimi sample con timestamp leggibile per verificare drop %."""
    import datetime as _dt
    out = []
    for s in store.samples[-200:]:
        out.append({
            'ts':   s['ts'],
            'time': _dt.datetime.fromtimestamp(s['ts']).strftime('%H:%M:%S'),
            'pct':  s['pct'],
        })
    return {
        'count':        len(store.samples),
        'shown':        len(out),
        'samples':      out,
        'reset_events': [
            {'ts': t, 'time': _dt.datetime.fromtimestamp(t).strftime('%H:%M:%S')}
            for t in store.reset_events
        ],
    }


# ── Session ───────────────────────────────────────────────────────────────────

@app.get('/api/session')
def get_session():
    has_oauth = client.has_oauth()
    return {
        'logged_in':       has_oauth,
        'oauth_available': has_oauth,
        'email':           store.settings.get('email', ''),
        'session_status':  (store.account or {}).get('session_status', 'not_logged_in'),
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
    """Pulisce lo snapshot account locale (le credenziali OAuth restano in
    ~/.claude/.credentials.json, gestite da Claude Code)."""
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


@app.get('/api/network')
def get_network_info():
    """IP LAN del Raspberry, usato per mostrare l'URL di accesso remoto."""
    import socket
    ip = None
    # Trucco: connessione UDP fittizia per scoprire l'IP di routing default
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            pass
    hostname = None
    try:
        hostname = socket.gethostname()
    except Exception:
        pass
    return {
        'ip':       ip,
        'hostname': hostname,
        'port':     PORT,
    }


# ── WiFi (NetworkManager / nmcli) ─────────────────────────────────────────────

class WifiConnect(BaseModel):
    ssid: str
    password: Optional[str] = ''
    hidden: Optional[bool] = False


def _nmcli(args: list, timeout: int = 20) -> tuple:
    """Esegue nmcli senza shell. Ritorna (returncode, output)."""
    try:
        r = subprocess.run(['nmcli', *args], capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or '') + (r.stderr or '')
    except FileNotFoundError:
        return 127, 'nmcli non trovato (NetworkManager non installato)'
    except subprocess.TimeoutExpired:
        return 124, 'timeout'


def _split_t(line: str) -> list:
    """Divide una riga `nmcli -t` rispettando i ':' escapati con backslash."""
    parts, cur, i = [], '', 0
    while i < len(line):
        ch = line[i]
        if ch == '\\' and i + 1 < len(line):
            cur += line[i + 1]
            i += 2
            continue
        if ch == ':':
            parts.append(cur)
            cur = ''
        else:
            cur += ch
        i += 1
    parts.append(cur)
    return parts


@app.get('/api/wifi/status')
def wifi_status():
    rc, out = _nmcli(['-t', '-f', 'DEVICE,TYPE,STATE,CONNECTION', 'device'])
    if rc != 0:
        return {'available': False, 'error': out.strip()}
    res = {'available': True, 'wifi_device': None, 'ssid': None,
           'wifi_state': None, 'ethernet': False, 'ip': None}
    for line in out.splitlines():
        dev, typ, state, conn = (_split_t(line) + ['', '', '', ''])[:4]
        if typ == 'wifi' and not res['wifi_device']:
            res['wifi_device'], res['wifi_state'] = dev, state
            if state == 'connected':
                res['ssid'] = conn
        elif typ == 'ethernet' and state == 'connected':
            res['ethernet'] = True
    if res['wifi_device'] and res['ssid']:
        rc, ip = _nmcli(['-t', '-g', 'IP4.ADDRESS', 'device', 'show', res['wifi_device']])
        if rc == 0 and ip.strip():
            res['ip'] = ip.strip().splitlines()[0].split('/')[0]
    return res


@app.get('/api/wifi/scan')
def wifi_scan():
    _nmcli(['radio', 'wifi', 'on'])
    rc, out = _nmcli(['-t', '-f', 'IN-USE,SSID,SIGNAL,SECURITY',
                      'device', 'wifi', 'list', '--rescan', 'yes'], timeout=30)
    if rc != 0:
        return {'ok': False, 'error': out.strip(), 'networks': []}
    best: dict = {}
    for line in out.splitlines():
        inuse, ssid, sig, sec = (_split_t(line) + ['', '', '', ''])[:4]
        if not ssid:
            continue
        try:
            sig = int(sig)
        except ValueError:
            sig = 0
        n = {'ssid': ssid, 'signal': sig, 'secure': bool(sec and sec != '--'),
             'in_use': inuse.strip() == '*'}
        cur = best.get(ssid)
        if not cur or n['in_use'] or (not cur['in_use'] and sig > cur['signal']):
            best[ssid] = n
    nets = sorted(best.values(), key=lambda n: (not n['in_use'], -n['signal']))
    return {'ok': True, 'networks': nets}


@app.post('/api/wifi/connect')
def wifi_connect(body: WifiConnect):
    ssid = (body.ssid or '').strip()
    if not ssid:
        return {'ok': False, 'reason': 'SSID mancante'}
    _nmcli(['radio', 'wifi', 'on'])
    args = ['device', 'wifi', 'connect', ssid]
    if body.password:
        args += ['password', body.password]
    if body.hidden:
        args += ['hidden', 'yes']
    rc, out = _nmcli(args, timeout=60)
    if rc != 0:
        msg = out.strip()
        if body.password:
            msg = msg.replace(body.password, '***')   # la password non finisce nei log
        print(f'[wifi] connessione a "{ssid}" fallita: {msg[:200]}')
        return {'ok': False, 'reason': msg[:200] or 'connessione fallita'}
    print(f'[wifi] connesso a "{ssid}"')
    return {'ok': True, **wifi_status()}


@app.post('/api/wifi/forget')
def wifi_forget(body: WifiConnect):
    rc, out = _nmcli(['connection', 'delete', 'id', body.ssid])
    return {'ok': rc == 0, 'reason': None if rc == 0 else out.strip()[:200]}


# ── Setup RPi ─────────────────────────────────────────────────────────────────

# ── Claude Login (OAuth nativo PKCE) ─────────────────────────────────────────
# Nessuna dipendenza da `claude` CLI: il backend genera il link di
# autorizzazione, l'utente autorizza su claude.ai e incolla il codice, il
# backend lo scambia per i token e scrive ~/.claude/.credentials.json.
# Robusto e replicabile su qualsiasi Pi (non dipende dalla versione del CLI).

_login_status:   str           = 'idle'   # idle|waiting|success|error
_login_url:      Optional[str] = None
_login_verifier: Optional[str] = None
_login_state:    Optional[str] = None
_login_message:  Optional[str] = None


@app.post('/api/login/start')
def start_claude_login():
    global _login_status, _login_url, _login_verifier, _login_state, _login_message
    url, verifier, state = client.build_authorize_url()
    _login_url      = url
    _login_verifier = verifier
    _login_state    = state
    _login_message  = None
    _login_status   = 'waiting'
    return {'ok': True, 'url': url, 'status': 'waiting',
            'needs_code': True, 'message': None}


@app.get('/api/login/status')
def get_login_status():
    return {'url': _login_url, 'status': _login_status,
            'needs_code': _login_status == 'waiting', 'message': _login_message}


class LoginCode(BaseModel):
    code: str


@app.post('/api/login/code')
async def submit_login_code(body: LoginCode):
    """Scambia il codice incollato dall'utente per i token OAuth e li salva."""
    global _login_status, _login_message
    code = (body.code or '').strip()
    if not code:
        return {'ok': False, 'reason': 'codice vuoto'}
    if not _login_verifier:
        return {'ok': False, 'reason': 'login non avviato — riparti da "Accedi"'}
    res = await client.exchange_code(code, _login_verifier, _login_state or '')
    if res.get('ok'):
        _login_status  = 'success'
        _login_message = None
        # Poll immediato così la dashboard mostra subito i dati
        try:
            await do_poll()
        except Exception:
            pass
        return {'ok': True}
    _login_message = res.get('_error', 'scambio del codice fallito')
    return {'ok': False, 'reason': _login_message}


@app.post('/api/login/cancel')
def cancel_login():
    global _login_status, _login_url, _login_verifier, _login_state, _login_message
    _login_status   = 'idle'
    _login_url      = None
    _login_verifier = None
    _login_state    = None
    _login_message  = None
    return {'ok': True}


# ── Restart servizio ──────────────────────────────────────────────────────────

@app.post('/api/restart')
def restart_service():
    """Riavvia il servizio SENZA sudo: il processo esce con codice non-zero e
    systemd (Restart=on-failure|always) lo rilancia con il codice aggiornato.
    Evita la dipendenza da sudo NOPASSWD, che non era configurata → il vecchio
    `sudo systemctl restart` falliva in silenzio e il codice non si aggiornava."""
    def _do():
        time.sleep(1)
        try:
            subprocess.run(['sync'], timeout=10)  # flush su SD: evita corruzioni
        except Exception:
            pass
        os._exit(3)
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
            _update_log.append('[→] Riavvio del servizio in corso...\n')
            _update_log.append('[i] La pagina si ricarica da sola appena il servizio torna su.\n')
            _update_running = False

            # NB: niente `pkill chromium`. La pagina del monitor aspetta /health
            # (_reloadWhenUp) e si ricarica da sola: così il kiosk NON naviga su
            # localhost mentre il backend è giù, evitando la schermata
            # "ERR_CONNECTION_REFUSED".
            # Auto-restart SENZA sudo: usciamo con codice non-zero e systemd
            # (Restart=on-failure|always) rilancia il servizio col codice nuovo.
            def _bye():
                time.sleep(1.5)
                try:
                    # flush su SD: i file appena scritti da git reset vengono
                    # forzati su disco PRIMA del riavvio → niente repo corrotto.
                    subprocess.run(['sync'], timeout=15)
                except Exception:
                    pass
                os._exit(3)
            threading.Thread(target=_bye, daemon=True).start()
            return
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
