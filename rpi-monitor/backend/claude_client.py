"""
Claude.ai client: OAuth polling (primary) + Playwright DOM scrape (secondary).

Primary:   poll_oauth_usage()      → Claude Code credentials, ~1s
Secondary: poll_with_playwright()  → cookie-based Playwright for Design + routine data
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

import httpx

try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

COOKIES_FILE   = Path(__file__).parent / 'cookies.json'
CHROMIUM_PATH  = os.getenv('CHROMIUM_PATH', '')

# Claude Code OAuth credentials (auto-managed by Claude Code)
OAUTH_CREDENTIALS_FILE = Path.home() / '.claude' / '.credentials.json'
OAUTH_USAGE_URL        = 'https://api.anthropic.com/api/oauth/usage'
OAUTH_REFRESH_URL      = 'https://console.anthropic.com/v1/oauth/token'
# client_id pubblico di Claude Code (lo stesso usato dal CLI)
OAUTH_CLIENT_ID        = '9d1c250a-e61b-44d9-88ed-5944d1962f5e'
OAUTH_BETA_HEADER      = 'oauth-2025-04-20'
OAUTH_MIN_INTERVAL     = 30    # secondi minimi tra due chiamate OAuth
OAUTH_BACKOFF_429      = 180   # secondi di attesa dopo un 429

_BROWSER_UA = (
    'Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
)


# ── Recursive extractor for any claude.ai JSON response ──────────────────────

def extract_account_limits(obj, depth: int = 0) -> Optional[dict]:
    """
    Recursively search JSON for claude.ai usage/limit data.
    Handles both count-based (messages_remaining/limit) and
    percentage-based (session_pct_used, weekly_pct_used) formats.
    Returns None if nothing useful found.
    """
    if not obj or not isinstance(obj, (dict, list)) or depth > 8:
        return None

    if isinstance(obj, list):
        for item in obj:
            r = extract_account_limits(item, depth + 1)
            if r:
                return r
        return None

    keys_str = ' '.join(str(k) for k in obj.keys()).lower()

    # Pattern A: count-based limits (messages remaining/limit)
    looks_count = (
        'remaining' in keys_str or
        'messages_left' in keys_str or
        ('limit' in keys_str and ('used' in keys_str or 'reset' in keys_str)) or
        'rate_limit' in keys_str or
        'quota' in keys_str or
        ('usage' in keys_str and 'reset' in keys_str)
    )

    # Pattern B: percentage-based usage (Pro accounts show %)
    looks_pct = any(p in keys_str for p in (
        'pct_used', 'percent_used', 'usage_fraction', 'used_fraction',
        'used_pct', 'usage_pct', 'fraction_used',
    ))

    if looks_count or looks_pct:
        r: dict = {}
        for k, v in obj.items():
            kl = str(k).lower()

            # Count-based fields
            if ('remaining' in kl or 'left' in kl) and v is not None:
                try: r['messages_remaining'] = int(v)
                except (TypeError, ValueError): pass

            if (('limit' in kl or 'max' in kl or 'total' in kl)
                    and isinstance(v, (int, float)) and v > 0
                    and 'pct' not in kl and 'percent' not in kl):
                r['messages_limit'] = int(v)

            if (('used' in kl or 'consumed' in kl or 'count' in kl)
                    and isinstance(v, (int, float))
                    and 'pct' not in kl and 'percent' not in kl):
                r['messages_used'] = int(v)

            # Percentage-based fields (Pro accounts)
            if any(p in kl for p in ('pct_used', 'percent_used', 'usage_fraction',
                                      'used_fraction', 'used_pct', 'usage_pct')):
                if v is not None:
                    try:
                        fv = float(v)
                        pct = int(round(fv * 100 if fv <= 1.0 else fv))
                        r['session_pct_used'] = pct
                        r['session_pct_remaining'] = 100 - pct
                    except (TypeError, ValueError): pass

            # Reset/expiry timestamps
            if any(p in kl for p in ('reset', 'resets', 'expires', 'refresh')) and v:
                r['reset_at'] = v

            # Plan/tier
            if ('plan' in kl or 'tier' in kl) and v:
                r['plan'] = str(v)

        has_data = any(k in r for k in (
            'messages_remaining', 'messages_limit', 'session_pct_used'))
        if has_data:
            if ('messages_used' not in r and
                    'messages_limit' in r and 'messages_remaining' in r):
                r['messages_used'] = r['messages_limit'] - r['messages_remaining']
            return r

    # Recurse into nested objects — check for session/weekly containers
    combined: dict = {}
    for k, v in obj.items():
        if not isinstance(v, (dict, list)):
            continue
        kl = str(k).lower()
        inner = extract_account_limits(v, depth + 1)
        if not inner:
            continue
        if 'session' in kl or 'current' in kl:
            for ik, iv in inner.items():
                if ik == 'reset_at':
                    combined['session_resets_at'] = iv
                elif not ik.startswith('session_'):
                    combined[f'session_{ik}'] = iv
                else:
                    combined[ik] = iv
        elif 'weekly' in kl or 'week' in kl:
            for ik, iv in inner.items():
                if ik == 'reset_at':
                    combined['weekly_resets_at'] = iv
                elif not ik.startswith('weekly_'):
                    combined[f'weekly_{ik}'] = iv
                else:
                    combined[ik] = iv
        else:
            combined.update(inner)
    return combined if combined else None


# ── DOM text scraper (fallback when API interception misses) ──────────────────

async def _scrape_settings_dom(page) -> Optional[dict]:
    """
    Read usage and billing data from the settings/utilizzo page text.
    Page order of '% utilizzato': [0] session, [1] weekly all-models,
                                   [2] Claude Design, [3] credits spent.
    """
    try:
        content = await page.text_content('body') or ''
        result: dict = {}

        # ── Usage percentages ────────────────────────────────────────────────
        pcts = re.findall(r'(\d+)%\s+utilizzato', content)
        if pcts:
            pct_used = int(pcts[0])
            result['session_pct_used']      = pct_used
            result['session_pct_remaining'] = 100 - pct_used
        if len(pcts) >= 2:
            wpct = int(pcts[1])
            result['weekly_pct_used']       = wpct
            result['weekly_pct_remaining']  = 100 - wpct
        if len(pcts) >= 3:
            dpct = int(pcts[2])
            result['design_pct_used']       = dpct
            result['design_pct_remaining']  = 100 - dpct

        # ── Session reset: "Si ripristina tra X h Y min" ────────────────────
        m = re.search(r'ripristina tra\s+(?:(\d+)\s*h\s+)?(\d+)\s*min', content)
        if m:
            hours = int(m.group(1)) if m.group(1) else 0
            mins  = int(m.group(2))
            result['session_resets_at_ts'] = int(time.time()) + hours * 3600 + mins * 60

        # ── Weekly reset: "Si ripristina [day] HH:MM" ───────────────────────
        m2 = re.search(r'ripristina\s+(\w{3})\s+(\d{1,2}:\d{2})', content)
        if m2:
            result['weekly_resets_day']   = m2.group(1)
            result['weekly_resets_time']  = m2.group(2)
            result['weekly_resets_label'] = f"{m2.group(1)} {m2.group(2)}"

        # ── Credits spent: "X,XX € spesi" ───────────────────────────────────
        m = re.search(r'([\d]+[,\.]?\d*)\s*€\s+spesi', content)
        if m:
            result['credits_spent_eur'] = float(m.group(1).replace(',', '.'))

        # ── Credits reset: "Si ripristina il [Month Day]" ───────────────────
        m = re.search(r'Si ripristina il\s+([\w]+ \d+|\d+ [\w]+)', content)
        if m:
            result['credits_reset_label'] = m.group(1).strip()

        # ── Monthly spending limit: "17 € Limite di spesa mensile" ──────────
        m = re.search(r'([\d]+[,\.]?\d*)\s*€\s+Limite di spesa', content, re.IGNORECASE)
        if not m:
            m = re.search(r'Limite di spesa[^\d]{0,40}([\d]+[,\.]?\d*)\s*€', content, re.IGNORECASE)
        if m:
            result['credits_limit_eur'] = float(m.group(1).replace(',', '.'))

        # ── Account balance: "4,42 € Saldo attuale" ─────────────────────────
        m = re.search(r'([\d]+[,\.]?\d*)\s*€\s+Saldo attuale', content, re.IGNORECASE)
        if not m:
            m = re.search(r'Saldo attuale\s+([\d]+[,\.]?\d*)\s*€', content, re.IGNORECASE)
        if m:
            result['credits_balance_eur'] = float(m.group(1).replace(',', '.'))

        # ── Daily routines: "N / 5" near "routine giornaliere" ───────────────
        m = re.search(r'(\d+)\s*/\s*(\d+)[^\n]{0,60}routine', content, re.IGNORECASE)
        if not m:
            m = re.search(r'routine[^\n]{0,60}(\d+)\s*/\s*(\d+)', content, re.IGNORECASE)
        if m:
            result['routines_used']  = int(m.group(1))
            result['routines_limit'] = int(m.group(2))

        return result if result else None
    except Exception as e:
        print(f'[dom-scrape] {e}')
        return None


# ── ClaudeClient ──────────────────────────────────────────────────────────────

class ClaudeClient:

    _oauth_last_call: float = 0        # timestamp ultima chiamata riuscita
    _oauth_retry_after: float = 0      # timestamp fino a quando NON chiamare (dopo 429)
    last_raw_oauth: Optional[dict] = None  # ultima risposta OAuth grezza (per debug)

    def has_cookies(self) -> bool:
        return COOKIES_FILE.exists()

    def get_cookie_age_seconds(self) -> Optional[float]:
        if not COOKIES_FILE.exists():
            return None
        return time.time() - COOKIES_FILE.stat().st_mtime

    def _make_playwright_launch_opts(self) -> dict:
        opts: dict = {'headless': True}
        if CHROMIUM_PATH:
            opts['executable_path'] = CHROMIUM_PATH
        return opts

    # ── OAuth (Claude Code credentials, primary auth) ─────────────────────────

    def has_oauth(self) -> bool:
        return OAUTH_CREDENTIALS_FILE.exists()

    def has_auth(self) -> bool:
        """True se c'è almeno un metodo di auth disponibile (OAuth O cookie)."""
        return self.has_oauth() or self.has_cookies()

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

    # ── Playwright fallback poll ───────────────────────────────────────────────

    async def poll_with_playwright(self) -> Optional[dict]:
        """Load settings page with Playwright to extract usage data. ~20-40s."""
        if not HAS_PLAYWRIGHT or not COOKIES_FILE.exists():
            return None

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(**self._make_playwright_launch_opts())
                ctx = await browser.new_context(user_agent=_BROWSER_UA)

                saved = json.loads(COOKIES_FILE.read_text())
                await ctx.add_cookies(saved)

                page = await ctx.new_page()
                found_limits: list = [None]

                async def handle_response(response):
                    if found_limits[0]:
                        return
                    try:
                        if 'claude.ai' not in response.url or response.status != 200:
                            return
                        ct = response.headers.get('content-type', '')
                        if 'application/json' not in ct:
                            return
                        body = await response.json()
                        limits = extract_account_limits(body)
                        if limits:
                            found_limits[0] = limits
                    except Exception:
                        pass

                page.on('response', handle_response)

                await page.goto('https://claude.ai/settings',
                                wait_until='domcontentloaded', timeout=30_000)
                await page.wait_for_timeout(1500)
                for sel in ('[role="tab"]:has-text("Utilizzo")',
                            'button:has-text("Utilizzo")'):
                    try:
                        el = page.locator(sel).first
                        if await el.is_visible(timeout=2000):
                            await el.click()
                            await page.wait_for_timeout(1500)
                            break
                    except Exception:
                        pass
                await page.wait_for_timeout(2500)

                dom_data = await _scrape_settings_dom(page)

                combined = found_limits[0] or {}
                if dom_data:
                    combined = {**combined, **dom_data}

                # Refresh cookies
                new_cookies = await ctx.cookies()
                if new_cookies:
                    COOKIES_FILE.write_text(json.dumps(new_cookies))

                await browser.close()
                return combined if combined else None

        except Exception as e:
            print(f'[pw-poll] {e}')
            return None
