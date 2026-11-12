"""Prompt 4 SmokeTest: HTTP checks plus a human/agent browser observation.

Run from the project root: python scripts/smoke_test.py
Use --no-open when an agent opens the printed URL with its browser tool.
No source edits, additional packages, or stored data are required.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
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
    options = args.parse_args()
    if options.browser_result_file and options.browser_result_file.exists():
        raise RuntimeError('Browser result file must not exist before the check starts.')
    node = shutil.which('node')
    if not node:
        raise RuntimeError('Node.js must be available on PATH.')
    processes = []
    with tempfile.TemporaryDirectory(prefix='invest-smoke-') as temp:
        with open(Path(temp) / 'services.log', 'w+', encoding='utf-8') as log:
            try:
                backend_port, frontend_port = free_port(), free_port()
                while frontend_port == backend_port:
                    frontend_port = free_port()
                backend_url = f'http://127.0.0.1:{backend_port}'
                frontend_url = f'http://127.0.0.1:{frontend_port}'
                common = dict(stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                backend = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', str(backend_port)], cwd=ROOT, **common)
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
                print(f'BROWSER URL: {frontend_url}', flush=True)
                if not options.no_open:
                    webbrowser.open(frontend_url)
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
