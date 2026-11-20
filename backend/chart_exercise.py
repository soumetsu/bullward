"""Image-only, read-only two-body chart exercise. No simulator calls."""
from contextlib import closing
from datetime import UTC, datetime, timedelta
import json
import math
import os
import sqlite3
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .chart_capture import MAX_DATA_URL, decode_capture
from .decisions import ReplayContext, ProviderFailure, MODEL
from .candles import load_observed_minutes
from .observations import SOURCE
from .timeframes import aggregate_candles

RULE = (
    'Read only the two labeled one-minute PRICE candle bodies, each close against its OWN open. '
    'Candle 1 is oldest, candle 2 is next. Two green bodies (close above open) mean long for candle 3; '
    'two red bodies (close below open) mean short. Choose wait for a mixed pair, either flat gray body, '
    'a missing or unreadable candle, or a stale chart. Never substitute an earlier candle for a missing minute. '
    'Ignore candle 3, wicks, volume, indicators, and broader trend. This is a chart-reading exercise only.'
)


class ExerciseSelection(BaseModel):
    symbol: str = Field(default='AAPL', min_length=1, max_length=64)
    replay: ReplayContext | None = None


class ExerciseImage(BaseModel):
    snapshot_id: UUID
    image_url: str = Field(max_length=MAX_DATA_URL)


def connect(database):
    connection = sqlite3.connect(database)
    connection.execute('CREATE TABLE IF NOT EXISTS chart_exercises (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, image TEXT, result TEXT)')
    return connection


def prepare(database, selection, now=None):
    clock = now or datetime.now(UTC)
    replay = selection.replay
    with closing(connect(database)) as connection:
        if replay:
            row = connection.execute(
                'SELECT received_at_utc FROM observations WHERE id=? AND id<=? AND ticker=? AND source=? AND received_at_utc>=?',
                (replay.cursor, replay.through_id, selection.symbol, SOURCE,
                 replay.start.astimezone(UTC).isoformat(timespec='microseconds')),
            ).fetchone()
            if not row:
                raise ValueError('Replay cursor does not belong to this selection.')
            clock = datetime.fromisoformat(row[0])
        minute = clock.astimezone(UTC).replace(second=0, microsecond=0)
        times = [(minute - timedelta(minutes=n)).isoformat().replace('+00:00', 'Z') for n in (2, 1)]
        replay_limits = {}
        if replay:
            replay_limits = dict(received_from=replay.start.astimezone(UTC),
                                 through_id=replay.through_id, at_id=replay.cursor)
        minutes, _ = load_observed_minutes(database, selection.symbol, as_of=clock, **replay_limits)
        pair = [row for row in minutes if row['candle']['timestamp'] in times]
        candles = [row['candle'] for row in aggregate_candles(pair, '1m', clock)]
        # Missing exact minutes cannot be replaced by neighboring candles.
        present = {item['timestamp'] for item in candles}
        stale = any(time not in present for time in times)
        snapshot = dict(id=str(uuid4()), symbol=selection.symbol, clock=clock.isoformat(),
            signal_minute=minute.isoformat().replace('+00:00', 'Z'), candle_times=times,
            mode='replay' if replay else 'live', stale=stale, candles=candles)
        connection.execute('INSERT INTO chart_exercises(id,snapshot) VALUES (?,?)', (snapshot['id'], json.dumps(snapshot)))
        connection.commit()
    return snapshot


def ask_image(snapshot, image):
    key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not key:
        raise ProviderFailure('provider_unconfigured', 'Set OPENAI_API_KEY in the backend environment.')
    # Only timestamps and image are disclosed. No OHLC, indicators, balances, or calculated direction.
    context = {name: snapshot[name] for name in ('clock', 'signal_minute', 'candle_times')}
    payload = dict(model=MODEL, input=[dict(role='user', content=[
        dict(type='input_text', text=json.dumps(context)), dict(type='input_image', image_url=image),
    ])], questions=[dict(type='choice', name='direction', instructions=RULE,
        choices=[dict(value=value, description=description) for value, description in (
            ('long', 'Both labeled bodies close above their own opens.'),
            ('short', 'Both labeled bodies close below their own opens.'),
            ('wait', 'Mixed, flat, missing, unreadable, or stale.'))])])
    request = Request('https://api.openai.com/v1/decisions', data=json.dumps(payload).encode(),
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=20) as response:
            return json.load(response)
    except HTTPError as error:
        code = error.code
        error.close()
        raise ProviderFailure('provider_error', f'OpenAI returned HTTP {code}.') from None
    except (TimeoutError, URLError, OSError):
        raise ProviderFailure('provider_error', 'OpenAI could not be reached or timed out.') from None
    except ValueError:
        raise ProviderFailure('malformed_answer', 'OpenAI returned unreadable JSON.') from None


def analyze(database, request, provider=None):
    with closing(connect(database)) as connection:
        row = connection.execute('SELECT snapshot FROM chart_exercises WHERE id=?', (str(request.snapshot_id),)).fetchone()
        if not row:
            raise ValueError('Chart snapshot not found. Recapture the chart.')
        snapshot = json.loads(row[0])
        _, tag = decode_capture(request.image_url)
        if tag != snapshot['id']:
            raise ValueError('Image and snapshot do not match. Recapture the chart.')
        result = dict(id=snapshot['id'], signal_minute=snapshot['signal_minute'], choice=None,
            probability=None, status='pending', model=MODEL, explanation='', eligible_for_paper_order=False)
        # A delayed live capture must not silently become a signal for another minute.
        expired = snapshot['mode'] == 'live' and datetime.now(UTC).replace(second=0, microsecond=0) != datetime.fromisoformat(snapshot['signal_minute'])
        if snapshot['stale'] or expired:
            result.update(status='stale_chart', choice='wait', explanation='A required minute is missing or the live capture is stale. Recapture.')
        else:
            try:
                reply = (provider or ask_image)(snapshot, request.image_url)
                if not isinstance(reply, dict) or not isinstance(reply.get('answers'), list) or not all(isinstance(a, dict) for a in reply['answers']):
                    raise ProviderFailure('malformed_answer', 'The model did not return named answers.')
                answers = [a for a in reply.get('answers', []) if a.get('name') == 'direction']
                if len(answers) != 1 or answers[0].get('type') != 'choice' or answers[0].get('choice') not in ('long', 'short', 'wait'):
                    raise ProviderFailure('malformed_answer', 'The model did not return a valid direction choice.')
                answer = answers[0]
                probabilities = answer.get('probabilities') or []
                if not isinstance(probabilities, list) or not all(isinstance(p, dict) for p in probabilities):
                    raise ProviderFailure('malformed_answer', 'The model returned invalid probabilities.')
                probability = next((p.get('probability') for p in probabilities if p.get('value') == answer['choice']), None)
                if probability is not None and (isinstance(probability, bool) or not isinstance(probability, (int, float)) or not math.isfinite(probability) or not 0 <= probability <= 1):
                    raise ProviderFailure('malformed_answer', 'The model returned an invalid probability.')
                result.update(status='completed', choice=answer['choice'], probability=probability, explanation='Model image-reading result. No trade placed.')
            except ProviderFailure as error:
                result.update(status=error.status, explanation=str(error))
        connection.execute('UPDATE chart_exercises SET image=?,result=? WHERE id=?', (request.image_url, json.dumps(result), snapshot['id']))
        connection.commit()
        return result
