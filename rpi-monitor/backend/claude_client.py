"""
Claude.ai client: Playwright-based login + lightweight httpx polling.

First run:  login_playwright(email, password)  → saves cookies + discovers API endpoint
Subsequent: poll_limits()                       → fast httpx poll with saved cookies
Fallback:   poll_with_playwright()              → loads settings page if httpx fails
"""

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Optional, Callable

import httpx

try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

COOKIES_FILE   = Path(__file__).parent / 'cookies.json'
ENDPOINTS_FILE = Path(__file__).parent / 'endpoints.json'
CHROMIUM_PATH  = os.getenv('CHROMIUM_PATH', '')

# Candidate endpoints tried when polling (best candidates first)
CANDIDATE_URLS = [
    'https://claude.ai/api/organizations',
    'https://claude.ai/api/account',
    'https://claude.ai/api/me',
    'https://claude.ai/api/bootstrap',
    'https://claude.ai/api/auth/session',
]

_BROWSER_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'it-IT,it;q=0.9,en;q=0.8',
    'Referer': 'https://claude.ai/',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
}


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
    def __init__(self, on_limits_found: Optional[Callable[[dict], None]] = None):
        self.on_limits_found = on_limits_found
        self._discovered_url: Optional[str] = None
        self._load_endpoints()

    def _load_endpoints(self):
        if ENDPOINTS_FILE.exists():
            try:
                self._discovered_url = json.loads(ENDPOINTS_FILE.read_text()).get('limits_url')
            except Exception:
                pass

    def _save_endpoints(self, url: str):
        self._discovered_url = url
        ENDPOINTS_FILE.write_text(json.dumps({'limits_url': url}))

    def has_cookies(self) -> bool:
        return COOKIES_FILE.exists()

    def get_cookie_age_seconds(self) -> Optional[float]:
        if not COOKIES_FILE.exists():
            return None
        return time.time() - COOKIES_FILE.stat().st_mtime

    def _httpx_cookies(self) -> dict:
        if not COOKIES_FILE.exists():
            return {}
        try:
            raw = json.loads(COOKIES_FILE.read_text())
            return {c['name']: c['value'] for c in raw}
        except Exception:
            return {}

    def _make_playwright_launch_opts(self) -> dict:
        opts: dict = {'headless': True}
        if CHROMIUM_PATH:
            opts['executable_path'] = CHROMIUM_PATH
        return opts

    # ── Login (one-time) ──────────────────────────────────────────────────────

    async def login_playwright(self, email: str, password: str) -> tuple[bool, str]:
        """
        Log in to claude.ai.
        - Se password fornita → login classico email+password (headless).
        - Se password vuota  → magic link: apre finestra visibile, compila
          l'email e aspetta fino a 5 minuti che l'utente incolli il link
          dall'email nella barra degli indirizzi della finestra aperta.
        """
        if not HAS_PLAYWRIGHT:
            return False, 'Playwright non installato. Esegui: pip install playwright && playwright install chromium'

        magic_link_mode = not password.strip()

        try:
            async with async_playwright() as pw:
                launch_opts = self._make_playwright_launch_opts()
                if magic_link_mode:
                    launch_opts['headless'] = False   # finestra visibile

                browser = await pw.chromium.launch(**launch_opts)
                ctx = await browser.new_context(
                    user_agent=_BROWSER_HEADERS['User-Agent'],
                    viewport={'width': 1280, 'height': 800},
                )
                page = await ctx.new_page()

                found_limits: dict = {}
                found_url: list = [None]

                async def handle_response(response):
                    try:
                        if 'claude.ai' not in response.url or response.status != 200:
                            return
                        ct = response.headers.get('content-type', '')
                        if 'application/json' not in ct:
                            return
                        body = await response.json()
                        limits = extract_account_limits(body)
                        if limits:
                            found_limits.update(limits)
                            if found_url[0] is None:
                                found_url[0] = response.url
                    except Exception:
                        pass

                page.on('response', handle_response)

                # Step 1: navigate to login
                await page.goto('https://claude.ai/login',
                                wait_until='domcontentloaded', timeout=30_000)
                await page.wait_for_timeout(1500)

                # Step 2: fill email
                try:
                    sel = 'input[type="email"], input[name="email"], input[autocomplete="email"]'
                    await page.locator(sel).first.fill(email)
                    btn = page.locator('button[type="submit"]').first
                    if await btn.is_visible():
                        await btn.click()
                    await page.wait_for_timeout(2000)
                except Exception as e:
                    print(f'[login] email step: {e}')

                if magic_link_mode:
                    # Mostra istruzioni nella finestra Playwright
                    print('[login] modalita magic link — in attesa che utente clicchi il link...')
                    await page.evaluate("""() => {
                        const d = document.createElement('div');
                        d.style.cssText = `position:fixed;top:0;left:0;right:0;z-index:99999;
                            background:#0D1E2E;color:#E2F0FF;padding:18px 24px;
                            border-bottom:2px solid #00C8FF;font-family:sans-serif;
                            font-size:15px;line-height:1.6;`;
                        d.innerHTML = `
                            <b style="color:#00C8FF">CLAUDE MONITOR — Magic Link Login</b><br>
                            Hai ricevuto un'email da Anthropic con un link di accesso.<br>
                            <b>Copia il link dall'email e incollalo nella barra degli indirizzi
                            di questa finestra.</b><br>
                            <small style="color:#5A7FA0">La finestra si chiuderà automaticamente al completamento.</small>`;
                        document.body.prepend(d);
                    }""")
                    # Attendi fino a 5 minuti che l'URL cambi a claude.ai (login OK)
                    try:
                        await page.wait_for_url(
                            lambda url: 'claude.ai' in url and '/login' not in url,
                            timeout=300_000
                        )
                        await page.wait_for_load_state('networkidle', timeout=15_000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(2000)
                else:
                    # Step 3: fill password
                    try:
                        await page.locator('input[type="password"]').first.fill(password)
                        await page.keyboard.press('Enter')
                    except Exception as e:
                        print(f'[login] password step: {e}')

                    # Step 4: wait for redirect
                    try:
                        await page.wait_for_url('https://claude.ai/**', timeout=30_000)
                        await page.wait_for_load_state('networkidle', timeout=15_000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(2000)

                # Step 5: navigate to settings/utilizzo to get usage data
                try:
                    await page.goto('https://claude.ai/settings',
                                    wait_until='domcontentloaded', timeout=20_000)
                    await page.wait_for_timeout(1000)
                    # Try clicking "Utilizzo" tab
                    for sel in ('[role="tab"]:has-text("Utilizzo")',
                                'button:has-text("Utilizzo")',
                                'a:has-text("Utilizzo")'):
                        try:
                            el = page.locator(sel).first
                            if await el.is_visible(timeout=2000):
                                await el.click()
                                await page.wait_for_timeout(1500)
                                break
                        except Exception:
                            pass
                    await page.wait_for_timeout(2000)
                    # DOM scrape as supplement
                    dom_data = await _scrape_settings_dom(page)
                    if dom_data:
                        found_limits.update(dom_data)
                except Exception as e:
                    print(f'[login] settings nav: {e}')

                cookies = await ctx.cookies()
                await browser.close()

                if not cookies:
                    return False, 'Accesso fallito: nessun cookie. Controlla email/password.'

                COOKIES_FILE.write_text(json.dumps(cookies))
                print(f'[login] {len(cookies)} cookie salvati')

                if found_url[0]:
                    self._save_endpoints(found_url[0])
                    print(f'[login] endpoint: {found_url[0]}')

                if found_limits:
                    if self.on_limits_found:
                        self.on_limits_found(found_limits)

                return True, f'Accesso riuscito ({len(cookies)} cookie)'

        except Exception as e:
            return False, f'Errore accesso: {str(e)}'

    # ── Fast httpx poll ───────────────────────────────────────────────────────

    async def poll_limits(self) -> Optional[dict]:
        """Poll claude.ai account limits via httpx with saved cookies. ~1s."""
        cookies = self._httpx_cookies()
        if not cookies:
            return None

        urls = []
        if self._discovered_url:
            urls.append(self._discovered_url)
        urls.extend(u for u in CANDIDATE_URLS if u != self._discovered_url)

        async with httpx.AsyncClient(
            headers=_BROWSER_HEADERS,
            cookies=cookies,
            follow_redirects=True,
            timeout=15.0,
        ) as client:
            for url in urls:
                try:
                    resp = await client.get(url)
                    if resp.status_code in (401, 403):
                        print('[poll] sessione scaduta')
                        return {'_error': 'session_expired'}
                    if resp.status_code != 200:
                        continue
                    try:
                        data = resp.json()
                    except Exception:
                        continue
                    limits = extract_account_limits(data)
                    if limits:
                        if url != self._discovered_url:
                            self._save_endpoints(url)
                        print(f'[poll] httpx OK: {url}')
                        return limits
                except Exception as e:
                    print(f'[poll] {url}: {e}')

        return None

    # ── Playwright fallback poll ───────────────────────────────────────────────

    async def poll_with_playwright(self) -> Optional[dict]:
        """Load settings page with Playwright to extract usage data. ~20-40s."""
        if not HAS_PLAYWRIGHT or not COOKIES_FILE.exists():
            return None

        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(**self._make_playwright_launch_opts())
                ctx = await browser.new_context(
                    user_agent=_BROWSER_HEADERS['User-Agent'])

                saved = json.loads(COOKIES_FILE.read_text())
                await ctx.add_cookies(saved)

                page = await ctx.new_page()
                found_limits: list = [None]
                found_url:    list = [None]

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
                            found_url[0] = response.url
                    except Exception:
                        pass

                page.on('response', handle_response)

                # Navigate directly to settings/utilizzo
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

                # DOM scrape
                dom_data = await _scrape_settings_dom(page)

                # Merge API + DOM data
                combined = found_limits[0] or {}
                if dom_data:
                    combined = {**combined, **dom_data}

                if found_url[0]:
                    self._save_endpoints(found_url[0])

                # Refresh cookies
                new_cookies = await ctx.cookies()
                if new_cookies:
                    COOKIES_FILE.write_text(json.dumps(new_cookies))

                await browser.close()
                return combined if combined else None

        except Exception as e:
            print(f'[pw-poll] {e}')
            return None
