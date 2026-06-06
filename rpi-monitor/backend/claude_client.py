"""
Claude.ai client: OAuth usage polling via Claude Code credentials.

poll_oauth_usage()  → legge ~/.claude/.credentials.json, chiama
                      /api/oauth/usage (~1s) e mappa la risposta nel
                      formato 'account'. Rinnova l'access token in
                      automatico via refresh_token quando scade (401).

Nessun cookie, nessun browser headless: l'endpoint OAuth gratuito di
Claude Code è sufficiente per sessione 5h, settimanale 7d e crediti.
"""

import json
import time
from pathlib import Path
from typing import Optional

import httpx

# Claude Code OAuth credentials (auto-managed by Claude Code)
OAUTH_CREDENTIALS_FILE = Path.home() / '.claude' / '.credentials.json'
OAUTH_USAGE_URL        = 'https://api.anthropic.com/api/oauth/usage'
OAUTH_REFRESH_URL      = 'https://console.anthropic.com/v1/oauth/token'
# client_id pubblico di Claude Code (lo stesso usato dal CLI)
OAUTH_CLIENT_ID        = '9d1c250a-e61b-44d9-88ed-5944d1962f5e'
OAUTH_BETA_HEADER      = 'oauth-2025-04-20'
OAUTH_MIN_INTERVAL     = 30    # secondi minimi tra due chiamate OAuth
OAUTH_BACKOFF_429      = 180   # secondi di attesa dopo un 429


# ── ClaudeClient ──────────────────────────────────────────────────────────────

class ClaudeClient:

    _oauth_last_call: float = 0        # timestamp ultima chiamata riuscita
    _oauth_retry_after: float = 0      # timestamp fino a quando NON chiamare (dopo 429)
    last_raw_oauth: Optional[dict] = None  # ultima risposta OAuth grezza (per debug)

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
        try:
            async with httpx.AsyncClient(timeout=20.0) as cli:
                resp = await cli.post(
                    OAUTH_REFRESH_URL,
                    json={
                        'grant_type':    'refresh_token',
                        'refresh_token': rt,
                        'client_id':     OAUTH_CLIENT_ID,
                    },
                    headers={'Content-Type': 'application/json'},
                )
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
            # Riscrivi mantenendo gli altri campi esistenti
            data = json.loads(OAUTH_CREDENTIALS_FILE.read_text())
            creds = data.get('claudeAiOauth', {}) or {}
            creds['accessToken']  = access
            creds['refreshToken'] = refresh
            creds['expiresAt']    = int((time.time() + expin) * 1000)
            data['claudeAiOauth'] = creds
            OAUTH_CREDENTIALS_FILE.write_text(json.dumps(data, indent=2))
            print(f'[oauth] refresh OK · nuovo token valido per {expin}s')
            return True
        except Exception as e:
            print(f'[oauth] refresh errore: {e}')
            return False

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
            return None
        try:
            async with httpx.AsyncClient(timeout=15.0) as cli:
                resp = await cli.get(
                    OAUTH_USAGE_URL,
                    headers={
                        'Authorization': f'Bearer {token}',
                        'anthropic-beta': OAUTH_BETA_HEADER,
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
                            },
                        )
                else:
                    print('[oauth] refresh fallito — necessario claude login manuale')
                    return {'_error': 'oauth_expired'}
            if resp.status_code == 401:
                # Anche dopo refresh 401 → veramente scaduto
                return {'_error': 'oauth_expired'}
            if resp.status_code == 429:
                self._oauth_retry_after = time.time() + OAUTH_BACKOFF_429
                print(f'[oauth] 429 rate limit — pausa {OAUTH_BACKOFF_429}s')
                return None
            if resp.status_code != 200:
                print(f'[oauth] HTTP {resp.status_code}: {resp.text[:200]}')
                return None

            self._oauth_last_call = time.time()

            data = resp.json()
            ClaudeClient.last_raw_oauth = data  # salva per debug endpoint
            print(f'[oauth] raw response: {json.dumps(data)[:600]}')
            out: dict = {'plan': 'pro'}

            def _to_pct(v) -> int:
                """Converte utilization API → intero 0-100.
                L'endpoint Anthropic restituisce frazioni 0-1 (es. 0.62 = 62%)."""
                f = float(v)
                if f <= 1.0:
                    f *= 100
                return min(100, max(0, round(f)))

            # Sessione 5h
            fh = data.get('five_hour') or {}
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
                    out['weekly_resets_at_ts'] = ts
                    try:
                        import datetime as _dt
                        d = _dt.datetime.fromtimestamp(ts)
                        days_it = ['Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab', 'Dom']
                        out['weekly_resets_label'] = f"{days_it[d.weekday()]} {d.strftime('%H:%M')}"
                    except Exception:
                        pass

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
            return None

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
