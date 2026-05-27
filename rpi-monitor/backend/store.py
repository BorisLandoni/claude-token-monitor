"""In-memory data store with JSON persistence."""

import json
import time
import datetime
from pathlib import Path
from typing import Optional

DATA_FILE = Path(__file__).parent / 'data.json'

samples:  list[dict] = []   # {'ts': int, 'pct': float}  — session % history
account:  Optional[dict] = None
settings: dict = {
    'poll_interval': 60,
    'theme': 'dark',
    'email': '',
}


def load():
    global samples, account, settings
    try:
        if DATA_FILE.exists():
            d = json.loads(DATA_FILE.read_text())
            samples  = d.get('samples', [])
            account  = d.get('account', None)
            settings.update(d.get('settings', {}))
            print(f'Caricati {len(samples)} sample + dati account')
    except Exception as e:
        print(f'Caricamento fallito: {e}')


def save():
    try:
        DATA_FILE.write_text(json.dumps({
            'samples':  samples,
            'account':  account,
            'settings': settings,
        }))
    except Exception as e:
        print(f'Salvataggio fallito: {e}')


def add_sample(session_pct_used: float) -> None:
    """Registra un campione di utilizzo sessione per la sparkline storica."""
    global samples
    now_ts = int(time.time())
    # Deduplicazione a 55s: ogni poll OAuth (min 58s) produce un punto distinto
    if samples and (now_ts - samples[-1]['ts']) < 55:
        samples[-1] = {'ts': now_ts, 'pct': round(session_pct_used, 1)}
    else:
        samples.append({'ts': now_ts, 'pct': round(session_pct_used, 1)})
    # Mantieni solo le ultime 24 ore
    cutoff = now_ts - 86_400
    samples = [s for s in samples if s['ts'] >= cutoff]


def get_session_history() -> list[dict]:
    """Restituisce i campioni di sessione % delle ultime 24 ore."""
    return list(samples)


def to_unix_ts(v) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v / 1000) if v > 1e10 else int(v)
    try:
        d = datetime.datetime.fromisoformat(str(v).replace('Z', '+00:00'))
        return int(d.timestamp())
    except Exception:
        return None
