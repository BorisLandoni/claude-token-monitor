"""In-memory data store with JSON persistence."""

import json
import time
import datetime
from pathlib import Path
from typing import Optional

DATA_FILE = Path(__file__).parent / 'data.json'

samples:      list[dict] = []   # {'ts': int, 'pct': float}  — session % history
account:      Optional[dict] = None
reset_events: list[int] = []    # timestamp (Unix) dei reset sessione osservati
_last_reset_ts: Optional[int] = None  # ultimo session_resets_at_ts visto
settings: dict = {
    'poll_interval': 60,
    'theme': 'dark',
    'email': '',
}


def load():
    global samples, account, settings, reset_events, _last_reset_ts
    try:
        if DATA_FILE.exists():
            d = json.loads(DATA_FILE.read_text())
            samples        = d.get('samples', [])
            account        = d.get('account', None)
            reset_events   = d.get('reset_events', [])
            _last_reset_ts = d.get('last_reset_ts', None)
            settings.update(d.get('settings', {}))
            print(f'Caricati {len(samples)} sample + {len(reset_events)} reset + dati account')
            _backfill_resets_from_samples()
    except Exception as e:
        print(f'Caricamento fallito: {e}')


def _backfill_resets_from_samples() -> None:
    """Scansiona i samples storici per ricostruire reset_events mancanti.

    Ogni drop di >= 40 punti % tra due sample consecutivi (entro 15 min) è
    un reset di sessione: lo aggiungiamo se non era già stato registrato."""
    global reset_events
    if len(samples) < 2:
        return
    added = 0
    for i in range(1, len(samples)):
        prev, cur = samples[i - 1], samples[i]
        gap = cur['ts'] - prev['ts']
        if 0 < gap <= 15 * 60 and (prev['pct'] - cur['pct']) >= 40:
            t = prev['ts'] + max(1, gap // 2)
            # Già presente entro 5 min?
            if not any(abs(t - r) <= 300 for r in reset_events):
                reset_events.append(t)
                added += 1
    if added:
        reset_events.sort()
        print(f'[store] backfill: ricostruiti {added} reset dai samples')
        save()


def save():
    try:
        DATA_FILE.write_text(json.dumps({
            'samples':        samples,
            'account':        account,
            'reset_events':   reset_events,
            'last_reset_ts':  _last_reset_ts,
            'settings':       settings,
        }))
    except Exception as e:
        print(f'Salvataggio fallito: {e}')


def observe_session_reset_ts(new_reset_ts: Optional[int]) -> None:
    """
    Da chiamare a ogni poll riuscito: se `session_resets_at_ts` è cambiato in
    modo significativo rispetto a prima, registra il momento del reset.

    Logica:
    - Se non avevamo nulla → memorizza e basta (non sappiamo se c'è stato un reset).
    - Se il nuovo `session_resets_at_ts` è "molto" diverso dal precedente
      (delta > 30 min), significa che è iniziata una nuova sessione 5h fa →
      il reset è avvenuto a `new_reset_ts - 5h`.
    - Tieni solo i reset delle ultime 7 giorni per non far crescere il file.
    """
    global _last_reset_ts, reset_events
    if new_reset_ts is None:
        return
    now_ts = int(time.time())
    SESSION = 5 * 3600

    if _last_reset_ts is None:
        _last_reset_ts = int(new_reset_ts)
        return

    delta = int(new_reset_ts) - int(_last_reset_ts)
    if abs(delta) > 30 * 60:  # > 30 min di shift = nuova sessione
        reset_moment = int(new_reset_ts) - SESSION
        # Evita duplicati ravvicinati (entro 5 min)
        if not reset_events or abs(reset_events[-1] - reset_moment) > 300:
            reset_events.append(reset_moment)
            print(f'[store] osservato reset sessione @ {reset_moment} '
                  f'(prossimo: {new_reset_ts})')
        _last_reset_ts = int(new_reset_ts)
    else:
        # Stesso session_resets_at_ts: nessun reset, aggiorna comunque
        _last_reset_ts = int(new_reset_ts)

    # Cleanup: mantieni solo gli ultimi 7 giorni
    cutoff = now_ts - 7 * 86_400
    reset_events = [t for t in reset_events if t >= cutoff]


def get_reset_events() -> list[int]:
    return list(reset_events)


def add_sample(session_pct_used: float) -> None:
    """Registra un campione di utilizzo sessione per la sparkline storica.

    Effetto collaterale: se la percentuale crolla bruscamente rispetto al
    campione precedente (>= 40 punti in giù in <= 15 min), registra un reset
    sessione *adesso*. Serve come fallback quando l'osservazione basata su
    session_resets_at_ts manca un reset (token scaduto, RPi riavviato, ecc.)."""
    global samples, reset_events
    now_ts = int(time.time())
    new_pct = round(session_pct_used, 1)

    # ── Fallback reset detection: caduta verticale di % ─────────────────────
    if samples:
        prev = samples[-1]
        gap_s = now_ts - prev['ts']
        if gap_s <= 15 * 60 and (prev['pct'] - new_pct) >= 40:
            # Reset avvenuto in mezzo: stimato al centro dell'intervallo
            reset_moment = prev['ts'] + max(1, gap_s // 2)
            if not reset_events or abs(reset_events[-1] - reset_moment) > 300:
                reset_events.append(reset_moment)
                print(f'[store] reset rilevato da drop %: '
                      f'{prev["pct"]}%→{new_pct}% @ {reset_moment}')

    # Deduplicazione a 55s: ogni poll OAuth (min 58s) produce un punto distinto
    if samples and (now_ts - samples[-1]['ts']) < 55:
        samples[-1] = {'ts': now_ts, 'pct': new_pct}
    else:
        samples.append({'ts': now_ts, 'pct': new_pct})
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
