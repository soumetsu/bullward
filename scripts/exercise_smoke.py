"""Run the chart exercise browser SmokeTest with controlled or live model replies."""
from argparse import ArgumentParser
from contextlib import closing, nullcontext
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from scripts.smoke_test import ROOT, free_port, wait_ready


def serve(port, live):
    import uvicorn
    from backend.main import app
    from backend.observations import initialize_database
    from backend.chart_capture import decode_capture
    from scripts.check_prompt9 import save
    database = Path(os.environ['INVEST_OBSERVATIONS_DB'])
    initialize_database(database)
    for symbol, prices in {'GREEN': [100, 105, 90, 95], 'RED': [100, 95, 110, 105],
                           'MIX': [100, 105, 110, 105], 'FLAT': [100, 100, 90, 95],
                           'FAIL': [100, 105, 90, 95], 'GAP': [100, 105, 90, 95]}.items():
        for time, price in zip(['14:30:05', '14:30:50', '14:31:05', '14:31:50'], prices):
            if symbol == 'GAP' and time.startswith('14:31'):
                continue
            save(database, symbol, time, price)
        save(database, symbol, '14:32:17', 200)  # candle 3 must be ignored
    calls = []
    def transport(request, timeout):
        payload = json.loads(request.data)
        text, image = payload['input'][0]['content']
        context = json.loads(text['text'])
        assert set(context) == {'clock', 'signal_minute', 'candle_times'}
        assert context['signal_minute'] == '2026-11-09T14:32:00Z'
        assert context['candle_times'] == ['2026-11-09T14:30:00Z', '2026-11-09T14:31:00Z']
        _, tag = decode_capture(image['image_url'])
        with closing(sqlite3.connect(database)) as connection:
            snapshot = json.loads(connection.execute('SELECT snapshot FROM chart_exercises WHERE id=?', (tag,)).fetchone()[0])
        symbol = snapshot['symbol']
        calls.append(symbol)
        if symbol == 'FAIL':
            raise HTTPError(request.full_url, 503, 'Controlled outage', {}, None)
        choice = {'GREEN': 'long', 'RED': 'short', 'MIX': 'wait', 'FLAT': 'wait'}[symbol]
        return BytesIO(json.dumps({'answers': [{'name': 'direction', 'type': 'choice', 'choice': choice,
            'probabilities': [{'value': choice, 'probability': 0.91}]}]}).encode())
    @app.get('/api/exercise-smoke-state')
    def state():
        return {'calls': calls}
    credentials = nullcontext() if live else patch.dict(os.environ, {'OPENAI_API_KEY': 'fixture-only'})
    provider = nullcontext() if live else patch('backend.chart_exercise.urlopen', transport)
    with credentials, provider:
        uvicorn.run(app, host='127.0.0.1', port=port)


def main(live):
    from scripts.workstation_runner import run_browser
    processes = []
    with TemporaryDirectory(prefix='invest-exercise-') as directory:
        database = Path(directory) / 'exercise.sqlite3'
        with open(Path(directory) / 'services.log', 'w+', encoding='utf-8') as log:
            try:
                backend_port, frontend_port = free_port(), free_port()
                backend_url, frontend_url = f'http://127.0.0.1:{backend_port}', f'http://127.0.0.1:{frontend_port}'
                env = dict(os.environ, INVEST_LIVE_FEED='0', INVEST_OBSERVATIONS_DB=str(database))
                common = dict(stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                backend_command = [sys.executable, '-m', 'scripts.exercise_smoke', '--serve', str(backend_port)]
                if live:
                    backend_command.append('--live')
                backend = subprocess.Popen(backend_command, env=env, cwd=ROOT, **common)
                processes.append(backend)
                wait_ready(backend_url + '/api/health', backend)
                node = shutil.which('node')
                frontend = subprocess.Popen([node, str(ROOT / 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', str(frontend_port), '--strictPort'],
                    cwd=ROOT / 'frontend', env=dict(env, INVEST_API_TARGET=backend_url), **common)
                processes.append(frontend)
                wait_ready(frontend_url + '/api/health', frontend)
                with patch.dict(os.environ, {'INVEST_EXERCISE_LIVE_CHECK': '1' if live else '0'}):
                    run_browser(node, frontend_url, Path(directory), log, ROOT / 'scripts/exercise_browser.mjs')
                if not live:
                    with urlopen(backend_url + '/api/exercise-smoke-state', timeout=5) as response:
                        assert json.load(response)['calls'] == ['GREEN', 'RED', 'MIX', 'FLAT', 'FAIL']
                with closing(sqlite3.connect(database)) as connection:
                    rows = connection.execute('SELECT result,image FROM chart_exercises ORDER BY rowid').fetchall()
                    expected = ['long', 'short', 'wait'] if live else ['long', 'short', 'wait', 'wait', None, 'wait']
                    assert [json.loads(row[0])['choice'] for row in rows] == expected
                    assert all(row[1].startswith('data:image/png;base64,') for row in rows)
                    assert not connection.execute("SELECT name FROM sqlite_master WHERE name IN ('paper_orders','paper_fills')").fetchall()
                if live:
                    print('PASS: live OpenAI model returned long, short, and wait. SQLite recorded zero orders or fills.')
                    return
                from backend.chart_exercise import analyze, prepare, ExerciseImage, ExerciseSelection
                from scripts.check_prompt9 import at
                live_snapshot = prepare(database, ExerciseSelection(symbol='GREEN'), now=at('14:32:17'))
                assert live_snapshot['mode'] == 'live' and not live_snapshot['stale']
                assert live_snapshot['signal_minute'] == '2026-11-09T14:32:00Z'
                assert [(row['timestamp'][11:16], row['open'], row['close']) for row in live_snapshot['candles']] == [
                    ('14:30', 100, 105), ('14:31', 90, 95),
                ]
                print('PASS: live snapshot returns the exact pair for the capture minute without a replay cursor.')
                with closing(sqlite3.connect(database)) as connection:
                    snapshot_id, image = connection.execute('SELECT id,image FROM chart_exercises ORDER BY rowid LIMIT 1').fetchone()
                request = ExerciseImage(snapshot_id=snapshot_id, image_url=image)
                assert analyze(database, request, provider=lambda *_: {})['status'] == 'malformed_answer'
                no_probability = analyze(database, request, provider=lambda *_: {'answers': [{'name': 'direction', 'type': 'choice', 'choice': 'long'}]})
                assert no_probability['choice'] == 'long' and no_probability['probability'] is None
                with closing(sqlite3.connect(database)) as connection:
                    snapshot = json.loads(connection.execute('SELECT snapshot FROM chart_exercises WHERE id=?', (snapshot_id,)).fetchone()[0])
                    snapshot['mode'] = 'live'  # old historical minute must expire before any provider call
                    connection.execute('UPDATE chart_exercises SET snapshot=? WHERE id=?', (json.dumps(snapshot), snapshot_id))
                    connection.commit()
                def must_not_call(*_):
                    raise AssertionError('Stale image reached model')
                assert analyze(database, request, provider=must_not_call)['status'] == 'stale_chart'
                print('PASS: malformed reply, missing probability, and expired live-minute guard.')
                print('PASS: image/timestamp-only transport; green/long, red/short, mixed/wait, flat/wait; failure and missing-minute guard; saved PNG/results; zero paper orders or fills.')
            except Exception:
                log.flush()
                log.seek(0)
                print(log.read(), file=sys.stderr)
                raise
            finally:
                for process in reversed(processes):
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                print('Stopped test services.')


if __name__ == '__main__':
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Send the three constructed chart PNGs to OpenAI.')
    parser.add_argument('--serve', type=int, help='Internal fixture server port.')
    args = parser.parse_args()
    if args.live and not os.environ.get('OPENAI_API_KEY', '').strip():
        parser.exit(2, 'Live model verification requires OPENAI_API_KEY in the process environment.\n')
    if args.serve is None:
        main(args.live)
    else:
        serve(args.serve, args.live)
