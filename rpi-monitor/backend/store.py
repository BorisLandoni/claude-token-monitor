"""In-memory data store with JSON persistence."""

import json
import time
import datetime
from pathlib import Path
from typing import Optional

DATA_FILE = Path(__file__).parent / 'data.json'

PRICE_PER_M = {
    'input': 3.00,
    'output': 15.00,
    'cache_read': 0.30,
    'cache_creation': 3.75,
}

IT_DAYS = ['Dom', 'Lun', 'Mar', 'Mer', 'Gio', 'Ven', 'Sab']

history: list[dict] = []
account: Optional[dict] = None
settings: dict = {
    'poll_interval': 60,
    'theme': 'dark',
    'email': '',
}


def load():
    global history, account, settings
    try:
        if DATA_FILE.exists():
            d = json.loads(DATA_FILE.read_text())
            history = d.get('history', [])
            account = d.get('account', None)
            settings.update(d.get('settings', {}))
            print(f'Caricati {len(history)} eventi + dati account')
    except Exception as e:
        print(f'Caricamento fallito: {e}')


def save():
    global history
    try:
        if len(history) > 100_000:
            history = history[-100_000:]
        DATA_FILE.write_text(json.dumps({'history': history, 'account': account, 'settings': settings}))
    except Exception as e:
        print(f'Salvataggio fallito: {e}')


def calc_cost(event: dict) -> float:
    return (
        event['input'] * PRICE_PER_M['input'] +
        event['output'] * PRICE_PER_M['output'] +
        event['cache_read'] * PRICE_PER_M['cache_read'] +
        event['cache_creation'] * PRICE_PER_M['cache_creation']
    ) / 1_000_000


def session_totals() -> dict:
    total_input = sum(e['input'] for e in history)
    total_output = sum(e['output'] for e in history)
    total_cache_read = sum(e['cache_read'] for e in history)
    total_cache_creation = sum(e['cache_creation'] for e in history)
    total_cost = sum(e['cost'] for e in history)
    last_ts = history[-1]['ts'] if history else None
    return {
        'total_input': total_input,
        'total_output': total_output,
        'total_cache_read': total_cache_read,
        'total_cache_creation': total_cache_creation,
        'requests_count': len(history),
        'cost_usd': round(total_cost, 6),
        'last_updated': datetime.datetime.fromtimestamp(last_ts / 1000, datetime.timezone.utc).isoformat().replace('+00:00', 'Z') if last_ts else None,
    }


def add_token_event(input_tokens: int, output_tokens: int,
                    cache_read: int = 0, cache_creation: int = 0) -> dict:
    event = {
        'ts': int(time.time() * 1000),
        'input': input_tokens,
        'output': output_tokens,
        'cache_read': cache_read,
        'cache_creation': cache_creation,
        'cost': 0.0,
    }
    event['cost'] = calc_cost(event)
    history.append(event)
    save()
    return event


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


def _aggregate(events: list[dict]) -> dict:
    return {
        'input': sum(e['input'] for e in events),
        'output': sum(e['output'] for e in events),
        'cost': sum(e['cost'] for e in events),
    }


def get_hourly() -> list[dict]:
    now_ms = int(time.time() * 1000)
    HOUR = 3_600_000
    result = []
    for i in range(23, -1, -1):
        start = now_ms - (i + 1) * HOUR
        end = now_ms - i * HOUR
        events = [e for e in history if start <= e['ts'] < end]
        agg = _aggregate(events)
        label = str(datetime.datetime.fromtimestamp(start / 1000, datetime.timezone.utc).hour).zfill(2)
        result.append({'label': label, 'input': agg['input'], 'output': agg['output'],
                       'cost': round(agg['cost'], 6)})
    return result


def get_daily() -> list[dict]:
    now_ms = int(time.time() * 1000)
    DAY = 86_400_000
    result = []
    for i in range(6, -1, -1):
        start = now_ms - (i + 1) * DAY
        end = now_ms - i * DAY
        events = [e for e in history if start <= e['ts'] < end]
        agg = _aggregate(events)
        d = datetime.datetime.fromtimestamp(start / 1000, datetime.timezone.utc)
        label = IT_DAYS[(d.weekday() + 1) % 7]
        result.append({'label': label, 'input': agg['input'], 'output': agg['output'],
                       'cost': round(agg['cost'], 6)})
    return result


def get_weekly() -> list[dict]:
    now_ms = int(time.time() * 1000)
    WEEK = 7 * 86_400_000
    result = []
    for i in range(3, -1, -1):
        start = now_ms - (i + 1) * WEEK
        end = now_ms - i * WEEK
        events = [e for e in history if start <= e['ts'] < end]
        agg = _aggregate(events)
        d = datetime.datetime.fromtimestamp(start / 1000, datetime.timezone.utc)
        jan1 = datetime.datetime(d.year, 1, 1, tzinfo=datetime.timezone.utc)
        week = int(((d - jan1).days + jan1.weekday() + 1) / 7) + 1
        result.append({'label': f'W{week}', 'input': agg['input'], 'output': agg['output'],
                       'cost': round(agg['cost'], 6)})
    return result
