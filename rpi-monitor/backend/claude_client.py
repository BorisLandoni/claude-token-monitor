"""
Claude.ai client: OAuth usage polling via Claude Code credentials.

poll_oauth_usage()  → legge ~/.claude/.credentials.json, chiama
                      /api/oauth/usage (~1s) e mappa la risposta nel
                      formato 'account'. Rinnova l'access token in
                      automatico via refresh_token quando scade (401).

Nessun cookie, nessun browser headless: l'endpoint OAuth gratuito di
Claude Code è sufficiente per sessione 5h, settimanale 7d e crediti.
"""

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import httpx

# Claude Code OAuth credentials (lo stesso file/formato del CLI ufficiale)
OAUTH_CREDENTIALS_FILE = Path.home() / '.claude' / '.credentials.json'
OAUTH_USAGE_URL        = 'https://api.anthropic.com/api/oauth/usage'
# Endpoint unico per scambio authorization_code e refresh (form-urlencoded)
OAUTH_TOKEN_URL        = 'https://console.anthropic.com/v1/oauth/token'
OAUTH_REFRESH_URL      = OAUTH_TOKEN_URL
# Pagina di autorizzazione (code=true → mostra il codice da incollare).
# Host/path identici a quelli generati dal CLI ufficiale di Claude Code.
OAUTH_AUTHORIZE_URL    = 'https://claude.com/cai/oauth/authorize'
OAUTH_REDIRECT_URI     = 'https://platform.claude.com/oauth/code/callback'
OAUTH_SCOPES           = ('org:create_api_key user:profile user:inference '
                          'user:sessions:claude_code user:mcp_servers user:file_upload')
# client_id pubblico di Claude Code (lo stesso usato dal CLI)
OAUTH_CLIENT_ID        = '9d1c250a-e61b-44d9-88ed-5944d1962f5e'
OAUTH_BETA_HEADER      = 'oauth-2025-04-20'
# L'endpoint usage tende a rispondere 429 ai client senza UA del CLI
OAUTH_USER_AGENT       = 'claude-code/2.1.0'
OAUTH_PROFILE_URL      = 'https://api.anthropic.com/api/oauth/profile'
OAUTH_MIN_INTERVAL     = 30    # secondi minimi tra due chiamate OAuth
OAUTH_BACKOFF_429      = 180   # secondi di attesa dopo un 429


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b'=').decode()


# ── ClaudeClient ──────────────────────────────────────────────────────────────

class ClaudeClient:

    _oauth_last_call: float = 0        # timestamp ultima chiamata riuscita
    _oauth_retry_after: float = 0      # timestamp fino a quando NON chiamare (dopo 429)
    _oauth_refresh_retry_after: float = 0  # backoff sull'endpoint di refresh (dopo 429)
    last_raw_oauth: Optional[dict] = None  # ultima risposta OAuth grezza (per debug)
    _plan_label: Optional[str] = None      # es. 'Ultra' (da /api/oauth/profile)
    _plan_checked: float = 0
    last_error: Optional[str] = None       # ultimo errore di poll (visibile in /api/debug/oauth)

    # ── OAuth (Claude Code credentials) ───────────────────────────────────────

    def has_oauth(self) -> bool:
        return OAUTH_CREDENTIALS_FILE.exists()

    def has_auth(self) -> bool:
        """True se ci sono credenziali OAuth di Claude Code disponibili."""
        return self.has_oauth()

    def get_oauth_token(self) -> Optional[str]:
        """Legge il token OAuth dal file gestito da Claude Code."""
        if not OAUTH_CREDENTIALS_FILE.exists():
            return None
        try:
            data = json.loads(OAUTH_CREDENTIALS_FILE.read_text())
            return data.get('claudeAiOauth', {}).get('accessToken')
        except Exception as e:
            print(f'[oauth] errore lettura credentials: {e}')
            return None

    def _get_refresh_token(self) -> Optional[str]:
        if not OAUTH_CREDENTIALS_FILE.exists():
            return None
        try:
            data = json.loads(OAUTH_CREDENTIALS_FILE.read_text())
            return data.get('claudeAiOauth', {}).get('refreshToken')
        except Exception:
            return None

    async def refresh_oauth_token(self) -> bool:
        """
        Rinnova l'access token usando il refresh_token in .credentials.json.
        Scrive il nuovo token nel file (stesso formato di Claude Code).
        Restituisce True se il refresh è riuscito.
        """
        rt = self._get_refresh_token()
        if not rt:
            print('[oauth] refresh: nessun refresh_token disponibile')
            return False
        # Backoff: dopo un 429 sul refresh, non ribattere subito (evita di
        # auto-infliggersi il rate limit chiamando ogni 60s con token scaduto).
        now = time.time()
        if now < self._oauth_refresh_retry_after:
            wait = int(self._oauth_refresh_retry_after - now)
            print(f'[oauth] refresh in backoff — riprovo tra {wait}s')
            return False
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as cli:
                resp = await cli.post(
                    OAUTH_REFRESH_URL,
                    data={
                        'grant_type':    'refresh_token',
                        'refresh_token': rt,
                        'client_id':     OAUTH_CLIENT_ID,
                    },
                    headers={'Content-Type': 'application/x-www-form-urlencoded'},
                )
            if resp.status_code == 429:
                self._oauth_refresh_retry_after = time.time() + OAUTH_BACKOFF_429 * 4
                print(f'[oauth] refresh 429 — backoff {OAUTH_BACKOFF_429 * 4}s')
                return False
            if resp.status_code != 200:
                print(f'[oauth] refresh fallito HTTP {resp.status_code}: {resp.text[:300]}')
                return False
            new = resp.json()
            access  = new.get('access_token')
            refresh = new.get('refresh_token', rt)
            expin   = int(new.get('expires_in', 3600))
            if not access:
                print('[oauth] refresh: risposta senza access_token')
                return False
            self._write_credentials(access, refresh, expin, new.get('scope'))
            print(f'[oauth] refresh OK · nuovo token valido per {expin}s')
            return True
        except Exception as e:
            print(f'[oauth] refresh errore: {e}')
            return False

    # ── OAuth login nativo (PKCE) ─────────────────────────────────────────────

    def build_authorize_url(self) -> tuple[str, str, str]:
        """Genera (authorize_url, code_verifier, state) per il flusso PKCE.
        L'utente apre l'URL, autorizza su claude.ai e ottiene un codice da
        incollare (`code=true` → flusso manuale, nessun callback localhost)."""
        verifier  = _b64url(secrets.token_bytes(32))
        challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
        state     = _b64url(secrets.token_bytes(32))
        params = {
            'code':                  'true',
            'client_id':             OAUTH_CLIENT_ID,
            'response_type':         'code',
            'redirect_uri':          OAUTH_REDIRECT_URI,
            'scope':                 OAUTH_SCOPES,
            'code_challenge':        challenge,
            'code_challenge_method': 'S256',
            'state':                 state,
        }
        return f'{OAUTH_AUTHORIZE_URL}?{urlencode(params)}', verifier, state

    async def exchange_code(self, code: str, verifier: str, state: str) -> dict:
        """Scambia il codice di autorizzazione per access+refresh token e li
        scrive in .credentials.json. Il codice incollato può essere nel formato
        `<code>#<state>`: si usa la parte prima di `#`."""
        raw = (code or '').strip()
        if '#' in raw:
            code_part, _, state_part = raw.partition('#')
        else:
            code_part, state_part = raw, state
        data = {
            'grant_type':    'authorization_code',
            'code':          code_part.strip(),
            'redirect_uri':  OAUTH_REDIRECT_URI,
            'client_id':     OAUTH_CLIENT_ID,
            'code_verifier': verifier,
            'state':         (state_part or state).strip(),
        }
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as cli:
                resp = await cli.post(
                    OAUTH_TOKEN_URL, data=data,
                    headers={'Content-Type': 'application/x-www-form-urlencoded'},
                )
        except Exception as e:
            return {'_error': f'rete: {e}'}
        if resp.status_code != 200:
            msg = resp.text[:300]
            try:
                j = resp.json()
                msg = j.get('error_description') or j.get('error') or msg
            except Exception:
                pass
            print(f'[oauth] exchange fallito HTTP {resp.status_code}: {msg}')
            return {'_error': f'HTTP {resp.status_code}: {msg}'}
        j = resp.json()
        access  = j.get('access_token')
        refresh = j.get('refresh_token')
        if not access:
            return {'_error': 'risposta senza access_token'}
        self._write_credentials(access, refresh, int(j.get('expires_in', 28800)), j.get('scope'))
        print('[oauth] login completato · credenziali scritte')
        return {'ok': True}

    def _write_credentials(self, access: str, refresh: Optional[str],
                           expires_in: int, scope: Optional[str]) -> None:
        """Scrive accessToken/refreshToken/expiresAt in ~/.claude/.credentials.json
        nel formato di Claude Code, preservando gli altri campi già presenti."""
        OAUTH_CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if OAUTH_CREDENTIALS_FILE.exists():
            try:
                data = json.loads(OAUTH_CREDENTIALS_FILE.read_text())
            except Exception:
                data = {}
        creds = data.get('claudeAiOauth') or {}
        creds['accessToken'] = access
        if refresh:
            creds['refreshToken'] = refresh
        creds['expiresAt'] = int((time.time() + expires_in) * 1000)
        if scope:
            creds['scopes'] = scope.split()
        elif not creds.get('scopes'):
            creds['scopes'] = OAUTH_SCOPES.split()
        creds.setdefault('subscriptionType', 'pro')
        data['claudeAiOauth'] = creds
        tmp = str(OAUTH_CREDENTIALS_FILE) + '.tmp'
        Path(tmp).write_text(json.dumps(data, indent=2))
        os.replace(tmp, OAUTH_CREDENTIALS_FILE)
        try:
            os.chmod(OAUTH_CREDENTIALS_FILE, 0o600)
        except Exception:
            pass

    async def poll_oauth_usage(self) -> Optional[dict]:
        """
        Chiama /api/oauth/usage e restituisce un dict nel formato 'account'.
        Mappa: five_hour → session, seven_day → weekly, extra_usage → credits.
        Gestisce rate limit (429) con backoff automatico.
        """
        now = time.time()

        # Backoff dopo 429: aspetta OAUTH_BACKOFF_429 secondi
        if now < self._oauth_retry_after:
            wait = int(self._oauth_retry_after - now)
            print(f'[oauth] rate limited — riprovo tra {wait}s')
            return None

        # Intervallo minimo tra chiamate (evita burst)
        if now - self._oauth_last_call < OAUTH_MIN_INTERVAL:
            return None

        token = self.get_oauth_token()
        if not token:
            ClaudeClient.last_error = 'accessToken assente in .credentials.json'
            return None
        try:
            async with httpx.AsyncClient(timeout=15.0) as cli:
                resp = await cli.get(
                    OAUTH_USAGE_URL,
                    headers={
                        'Authorization': f'Bearer {token}',
                        'anthropic-beta': OAUTH_BETA_HEADER,
                        'User-Agent': OAUTH_USER_AGENT,
                    },
                )
            # Token scaduto → prova il refresh automatico una volta sola
            if resp.status_code == 401:
                print('[oauth] 401 — provo refresh automatico')
                if await self.refresh_oauth_token():
                    token = self.get_oauth_token()
                    async with httpx.AsyncClient(timeout=15.0) as cli:
                        resp = await cli.get(
                            OAUTH_USAGE_URL,
                            headers={
                                'Authorization': f'Bearer {token}',
                                'anthropic-beta': OAUTH_BETA_HEADER,
                                'User-Agent': OAUTH_USER_AGENT,
                            },
                        )
                else:
                    print('[oauth] refresh fallito — necessario "claude auth login" manuale')
                    ClaudeClient.last_error = '401 + refresh fallito: serve nuovo login'
                    return {'_error': 'oauth_expired'}
            if resp.status_code == 401:
                # Anche dopo refresh 401 → veramente scaduto
                ClaudeClient.last_error = '401 anche dopo refresh'
                return {'_error': 'oauth_expired'}
            if resp.status_code == 429:
                self._oauth_retry_after = time.time() + OAUTH_BACKOFF_429
                ClaudeClient.last_error = f'429 rate limit: {resp.text[:200]}'
                print(f'[oauth] 429 rate limit — pausa {OAUTH_BACKOFF_429}s')
                return None
            if resp.status_code != 200:
                print(f'[oauth] HTTP {resp.status_code}: {resp.text[:200]}')
                ClaudeClient.last_error = f'HTTP {resp.status_code}: {resp.text[:200]}'
                return None

            self._oauth_last_call = time.time()
            ClaudeClient.last_error = None

            data = resp.json()
            ClaudeClient.last_raw_oauth = data  # salva per debug endpoint
            print(f'[oauth] raw response: {json.dumps(data)[:600]}')
            out: dict = {}
            await self._ensure_plan()
            if self._plan_label:
                out['plan'] = self._plan_label

            def _to_pct(v) -> int:
                """Converte utilization API → intero 0-100 (l'API restituisce già percentuali)."""
                return min(100, max(0, round(float(v))))

            # Sessione 5h — assente (null) sui piani senza finestra (es. Ultra verificato)
            fh = data.get('five_hour') or {}
            out['has_session_window'] = bool(fh)
            if fh.get('utilization') is not None:
                spct = _to_pct(fh['utilization'])
                out['session_pct_used']      = spct
                out['session_pct_remaining'] = 100 - spct
            if fh.get('resets_at'):
                out['reset_at']             = fh['resets_at']
                out['session_resets_at_ts'] = self._iso_to_ts(fh['resets_at'])
                out['reset_at_ts']          = out['session_resets_at_ts']

            # Weekly 7d
            sd = data.get('seven_day') or {}
            if sd.get('utilization') is not None:
                wpct = _to_pct(sd['utilization'])
                out['weekly_pct_used']      = wpct
                out['weekly_pct_remaining'] = 100 - wpct
            if sd.get('resets_at'):
                ts = self._iso_to_ts(sd['resets_at'])
                if ts is not None:
                    ts = int(round(ts / 60.0) * 60)   # l'API dà 15:59:59.8 → 16:00
                    out['weekly_resets_at_ts'] = ts
                    try:
                        import datetime as _dt
                        d = _dt.datetime.fromtimestamp(ts)
                        days_it = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom']
                        out['weekly_resets_label'] = f"{days_it[d.weekday()]} {d.strftime('%H:%M')}"
                    except Exception:
                        pass

            # Dettaglio settimana: inizio finestra, uso per fonte
            bd = data.get('seven_day_breakdown') or {}
            wstart = self._iso_to_ts(bd.get('window_started_at')) if bd.get('window_started_at') else None
            if wstart is not None:
                out['weekly_started_ts'] = int(round(wstart / 60.0) * 60)
            rows = [{'name': r.get('display_name') or r.get('key'), 'percent': r.get('percent')}
                    for r in (bd.get('rows') or []) if isinstance(r, dict)]
            if rows:
                out['weekly_sources'] = rows

            # Limiti settimanali per modello (es. Fable)
            scoped = []
            for lim in data.get('limits') or []:
                if not isinstance(lim, dict) or lim.get('kind') != 'weekly_scoped':
                    continue
                model = ((lim.get('scope') or {}).get('model') or {}).get('display_name')
                if model:
                    scoped.append({'name': model, 'percent': lim.get('percent'),
                                   'active': bool(lim.get('is_active'))})
            if scoped:
                out['weekly_scoped'] = scoped

            # Credito incluso per le sessioni cloud: nell'API ha un nome in codice
            # (oggi "iguana_necktie"), quindi lo si riconosce dalla forma: oggetto con
            # limit_dollars e used_dollars valorizzati (il settimanale li ha null).
            for key, val in data.items():
                if (isinstance(val, dict) and key != 'seven_day'
                        and val.get('limit_dollars') is not None
                        and val.get('used_dollars') is not None):
                    lim_d  = float(val['limit_dollars'])
                    used_d = float(val['used_dollars'])
                    out['cloud_credit_limit_usd']     = round(lim_d, 2)
                    out['cloud_credit_used_usd']      = round(used_d, 2)
                    out['cloud_credit_remaining_usd'] = round(
                        float(val['remaining_dollars']) if val.get('remaining_dollars') is not None
                        else lim_d - used_d, 2)
                    exp = self._iso_to_ts(val['resets_at']) if val.get('resets_at') else None
                    if exp is not None:
                        out['cloud_credit_expires_ts'] = exp
                    break

            # Crediti (extra_usage, in centesimi EUR)
            eu = data.get('extra_usage') or {}
            if eu.get('is_enabled') and eu.get('monthly_limit') is not None:
                limit_cents = float(eu['monthly_limit'])
                used_cents  = float(eu.get('used_credits', 0))
                out['credits_limit_eur']   = round(limit_cents / 100, 2)
                out['credits_spent_eur']   = round(used_cents  / 100, 2)
                out['credits_balance_eur'] = round((limit_cents - used_cents) / 100, 2)

            print(f'[oauth] session:{out.get("session_pct_used")}% weekly:{out.get("weekly_pct_used")}% spesi:{out.get("credits_spent_eur")}€')
            return out

        except Exception as e:
            print(f'[oauth] errore: {e}')
            ClaudeClient.last_error = f'errore: {e}'
            return None

    async def _ensure_plan(self) -> None:
        """Legge il piano dal profilo account (al massimo ogni 6 ore)."""
        if time.time() < self._plan_checked + 6 * 3600 and self._plan_label:
            return
        if time.time() < self._plan_checked + 300:   # non martellare se fallisce
            return
        self._plan_checked = time.time()
        prof = await self.fetch_profile()
        org = (prof or {}).get('organization') or {}
        label = org.get('plan_display_name') or None
        if label:
            ClaudeClient._plan_label = label

    async def fetch_profile(self) -> Optional[dict]:
        """Profilo account (piano, tier) da /api/oauth/profile. None se non disponibile."""
        token = self.get_oauth_token()
        if not token:
            return None
        try:
            async with httpx.AsyncClient(timeout=15.0) as cli:
                resp = await cli.get(
                    OAUTH_PROFILE_URL,
                    headers={
                        'Authorization': f'Bearer {token}',
                        'anthropic-beta': OAUTH_BETA_HEADER,
                        'User-Agent': OAUTH_USER_AGENT,
                    },
                )
            if resp.status_code != 200:
                return {'_error': f'HTTP {resp.status_code}: {resp.text[:200]}'}
            return resp.json()
        except Exception as e:
            return {'_error': str(e)}

    def seconds_until_next_poll(self) -> int:
        """Secondi rimanenti prima che sia consentita un'altra chiamata OAuth."""
        now = time.time()
        if now < self._oauth_retry_after:
            return int(self._oauth_retry_after - now)
        remaining = OAUTH_MIN_INTERVAL - (now - self._oauth_last_call)
        return max(0, int(remaining))

    @staticmethod
    def _iso_to_ts(iso: str) -> Optional[int]:
        try:
            import datetime as _dt
            s = iso.replace('Z', '+00:00')
            return int(_dt.datetime.fromisoformat(s).timestamp())
        except Exception:
            return None
