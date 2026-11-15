"""Prompt 4 SmokeTest: HTTP checks plus a human/agent browser observation.

Run from the project root: python scripts/smoke_test.py
Use --no-open when an agent opens the printed URL with its browser tool.
No source edits, additional packages, or stored data are required.
"""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_ready(url, process):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'Service exited with {process.returncode}')
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError(f'Timed out waiting for {url}')


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('--no-open', action='store_true')
    args.add_argument('--browser-result-file', type=Path, help='Agent mode: wait for a fresh PASS/FAIL file written after browser observation.')
    mode = args.add_mutually_exclusive_group()
    mode.add_argument('--live', action='store_true', help='Verify the Yahoo dashboard with a disposable database.')
    mode.add_argument('--fixture', action='store_true', help='Exercise the live dashboard with labeled constructed input.')
    options = args.parse_args()
    if options.browser_result_file and options.browser_result_file.exists():
        raise RuntimeError('Browser result file must not exist before the check starts.')
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Node.js must be available on PATH.')
    processes = []
    with tempfile.TemporaryDirectory(prefix='invest-smoke-', dir=ROOT / 'backend/data') as temp:
        with open(Path(temp) / 'services.log', 'w+', encoding='utf-8') as log:
            try:
                backend_port, frontend_port = free_port(), free_port()
                while frontend_port == backend_port:
                    frontend_port = free_port()
                backend_url = f'http://127.0.0.1:{backend_port}'
                frontend_url = f'http://127.0.0.1:{frontend_port}'
                common = dict(stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                database = Path(temp) / 'observations.sqlite3'
                backend_env = dict(os.environ, INVEST_OBSERVATIONS_DB=str(database))
                if options.live or options.fixture:
                    backend_env['INVEST_LIVE_FEED'] = '1'
                    backend_args = ['scripts.smoke_live_backend', str(backend_port), temp, 'fixture' if options.fixture else 'live']
                else:
                    backend_env['INVEST_LIVE_FEED'] = '0'
                    backend_args = ['uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', str(backend_port)]
                backend = subprocess.Popen([sys.executable, '-m', *backend_args], cwd=ROOT, env=backend_env, **common)
                processes.append(backend)
                wait_ready(backend_url + '/api/health', backend)
                env = dict(os.environ, INVEST_API_TARGET=backend_url)
                frontend = subprocess.Popen([node, str(ROOT / 'frontend/node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', str(frontend_port), '--strictPort'], cwd=ROOT / 'frontend', env=env, **common)
                processes.append(frontend)
                wait_ready(frontend_url + '/api/health', frontend)
                with urlopen(frontend_url + '/api/history', timeout=5) as response:
                    rows = json.load(response)
                expected = {'timestamp': '2026-11-09T14:31:00Z', 'open': 102, 'high': 104, 'low': 100, 'close': 101, 'volume': 850}
                assert len(rows) == 3, f'Expected three fixture candles, got {len(rows)}'
                assert rows[1] == expected, f'Unexpected middle candle: {rows[1]}'
                print('HTTP PASS: three candles; 14:31 UTC matches all six source fields.', flush=True)
                browser_url = frontend_url if options.live or options.fixture else frontend_url + '/?source=sample'
                print(f'BROWSER URL: {browser_url}', flush=True)
                if not options.no_open:
                    webbrowser.open(browser_url)
                if options.live or options.fixture:
                    print(f'Disposable observation database: {database}', flush=True)
                    print(f'Touch the file "disconnect" in {temp} while the dashboard is open. '
                          'Confirm Upstream disconnected and Dashboard socket · Connected.', flush=True)
                    if options.fixture:
                        print('Constructed smoke test data. Start at 1m: two candles, latest 14:31 UTC, '
                              'close 102 and volume 10. Touch "update" in the same directory. Expect the same '
                              'two candles, with close 104 and volume 15 at 14:31. Touch "next"; expect a '
                              'third candle at 14:32 with close 101 and volume 10. Switch to 5m and Daily; '
                              'both have one candle starting at 14:30 with O100 H104 L100 C101 and unknown volume.', flush=True)
                    else:
                        print('Compare a live Yahoo observation in SQLite with the UTC timestamp and candle values '
                              'on screen. Verify a timeframe switch. If no regular-session price arrives, report pending.', flush=True)
                else:
                    print('Browser check: confirm three distinct candles with aligned volume bars. Select 14:31 UTC; expect Nov 9, 2026, O 102, H 104, L 100, C 101, volume 850 shares. Check Reset view and TradingView attribution.', flush=True)
                if options.browser_result_file:
                    print(f'Waiting for browser observation in {options.browser_result_file}', flush=True)
                    deadline = time.monotonic() + 600
                    while not options.browser_result_file.exists():
                        if time.monotonic() > deadline:
                            raise RuntimeError('Browser observation timed out.')
                        time.sleep(0.2)
                    answer = options.browser_result_file.read_text(encoding='utf-8-sig')
                    options.browser_result_file.unlink()
                else:
                    answer = input('Enter PASS only after the browser checks pass (anything else fails): ')
                if answer.strip().lower() != 'pass':
                    raise RuntimeError('Browser observation was not confirmed as PASS.')
                if options.live or options.fixture:
                    with closing(sqlite3.connect(database)) as connection:
                        stored = connection.execute(
                            'SELECT source_time_ms, source_message_json FROM observations ORDER BY id'
                        ).fetchall()
                    assert stored, 'No upstream observation was recorded.'
                    assert (Path(temp) / 'disconnect').exists(), 'Upstream disconnect was not requested.'
                    print(f'Stored {len(stored)} observations. Latest stored source time: {stored[-1][0]}.', flush=True)
                    if options.fixture:
                        assert [json.loads(payload)['price'] for _, payload in stored] == [100, 102, 104, 101], stored
                print('PASS: HTTP checks and browser observation completed.', flush=True)
                return 0
            except (Exception, KeyboardInterrupt) as error:
                print(f'FAIL: {error}', file=sys.stderr)
                log.flush()
                log.seek(0)
                print(log.read(), file=sys.stderr)
                return 1
            finally:
                for process in reversed(processes):
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                print('Stopped the services started by this check.', flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
