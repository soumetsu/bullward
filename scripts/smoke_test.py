"""Dashboard SmokeTest with an automated workstation mode and focused manual checks.

Run from the project root: python -m scripts.smoke_test --workstation
Use --no-open when an agent opens the printed URL with its browser tool.
No source edits, additional packages, or stored data are required.
"""
import argparse
from contextlib import closing
from decimal import Decimal
from hashlib import sha256
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
from urllib.request import Request, urlopen
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


def paper_snapshot(database):
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        return tuple(connection.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
                     for table in ('paper_orders', 'paper_fills', 'paper_pnl'))


def retry_first_order(database, frontend_url):
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        book, request_id, symbol, cursor = connection.execute(
            'SELECT account_id, request_id, symbol, after_id FROM paper_orders WHERE id = 1'
        ).fetchone()
    payload = json.dumps(dict(book=book, request_id=request_id, symbol=symbol, cursor=cursor,
                              action='buy', quantity=4)).encode()
    request = Request(frontend_url + '/api/paper/orders', data=payload, headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=5) as response:
        assert json.load(response)['id'] == 1
    print('HTTP retry returned the same paper order #1.', flush=True)


def check_chart_decision(database, frontend_url, live):
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        decision_id, decision_json, snapshot_id, png, snapshot_json = connection.execute(
            '''SELECT d.id, d.decision_json, c.snapshot_id, c.png, s.snapshot_json
               FROM decisions d JOIN decision_captures c ON d.id = c.decision_id
               JOIN decision_snapshots s ON s.id = c.snapshot_id ORDER BY d.rowid DESC LIMIT 1'''
        ).fetchone()
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name = 'paper_orders' AND type = 'table'"
        ).fetchone()
        count = connection.execute('SELECT COUNT(*) FROM paper_orders').fetchone()[0] if table else 0
    assert count == 0
    result, snapshot = json.loads(decision_json), json.loads(snapshot_json)
    assert result['facts'] == snapshot['facts'] and result['snapshot_id'] == snapshot_id
    assert result['facts']['market_data'] == 'constructed'
    assert result['facts']['sma']['crossover'] == 'up'
    assert result['facts']['decision_time'] == '2026-11-09T14:35:00.000000+00:00'
    assert [candle['volume'] for candle in snapshot['candles']] == [None, 40, 60, 80, 100]
    assert not result['eligible_for_paper_order']
    with urlopen(frontend_url + f'/api/decisions/{decision_id}', timeout=5) as response:
        assert json.load(response) == result
    with urlopen(frontend_url + result['capture']['url'], timeout=5) as response:
        assert response.headers.get_content_type() == 'image/png' and response.read() == png
    digest = sha256(png).hexdigest()
    assert result['capture']['sha256'] == digest
    if not live:
        assert result['analyst']['answers'] == {'action': 'buy', 'crossover': 'up', 'evidence': 'sufficient'}
        assert result['reviewer']['answers'] == {'verdict': 'accept'}
        for role in ('analyst', 'reviewer'):
            assert result[role]['provider_decision_id'] == f'fixture-{role}-{digest}'
        assert result['visual_comparison']['status'] == 'matches'
    else:
        for role in ('analyst', 'reviewer'):
            print(f'{role}: {result[role]["status"]}, {result[role]["answers"]}', flush=True)
        if not all(result[role]['call'] == 'returned' for role in ('analyst', 'reviewer')):
            print(f'Live chart analysis pending. Status: {result["status"]}.', flush=True)
            return False
    print(f'Saved decision and PNG verified ({len(png)} bytes, SHA-256 {digest}). Order count: {count}.', flush=True)
    return True


def check_automation(database, frontend_url):
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        run_id, book, enabled, test_mode = connection.execute(
            'SELECT id, book, enabled, test_mode FROM paper_automation_runs'
        ).fetchone()
        assert (enabled, test_mode) == (0, 1)
        rows = connection.execute(
            """SELECT j.id, j.status, j.decision_id, j.order_id, f.price, d.decision_json, c.png
               FROM paper_automation_jobs j JOIN decisions d ON d.id = j.decision_id
               JOIN decision_captures c ON c.decision_id = d.id
               LEFT JOIN paper_fills f ON f.order_id = j.order_id WHERE run_id = ? ORDER BY candle""",
            (run_id,),
        ).fetchall()
        assert connection.execute('SELECT COUNT(*) FROM paper_orders').fetchone() == (1,)
        assert connection.execute('SELECT COUNT(*) FROM paper_fills').fetchone() == (1,)
        assert connection.execute('SELECT cash, shares FROM paper_accounts WHERE id = ?', (book,)).fetchone() == ('993', 1)
        assert connection.execute("SELECT cash, shares FROM paper_pnl WHERE event = 'Fill'").fetchall() == [('993', 1)]
    assert len(rows) == 2
    for row, status, action, verdict in zip(rows, ['filled', 'skipped'], ['buy', 'hold'],
                                           ['accept', 'reject_unsupported_signal'], strict=True):
        _, recorded, decision_id, order_id, price, data, png = row
        decision = json.loads(data)
        assert recorded == status and decision['id'] == decision_id and decision['proposal'] == action
        assert decision['reviewer']['answers']['verdict'] == verdict
        digest = sha256(png).hexdigest()
        assert decision['analyst']['provider_decision_id'] == f'fixture-analyst-{digest}'
        assert decision['reviewer']['provider_decision_id'] == f'fixture-reviewer-{digest}'
        assert (order_id, price) == ((1, '7') if status == 'filled' else (None, None))
    request = Request(f'{frontend_url}/api/paper/automation/{run_id}/jobs/{rows[0][0]}/analyze',
                      data=b'{}', headers={'Content-Type': 'application/json'})
    with urlopen(request, timeout=5) as response:
        repeated = json.load(response)
    assert repeated['decision_id'] == rows[0][2] and repeated['order_id'] == 1 and repeated['status'] == 'filled'
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
        assert connection.execute('SELECT COUNT(*) FROM paper_fills').fetchone() == (1,)
        assert connection.execute('SELECT cash, shares FROM paper_accounts WHERE id = ?', (book,)).fetchone() == ('993', 1)
    print('Two chart decisions traced in SQLite. Approved buy filled once at $7, cash $993, 1 share. Veto and retry added no fill.', flush=True)


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument('--no-open', action='store_true')
    args.add_argument('--browser-result-file', type=Path, help='Agent mode: wait for a fresh PASS/FAIL file written after browser observation.')
    args.add_argument('--browser-timeout', type=int, default=600, help='Seconds to wait for an agent browser result.')
    mode = args.add_mutually_exclusive_group()
    mode.add_argument('--live', action='store_true', help='Verify the Yahoo dashboard with a disposable database.')
    mode.add_argument('--fixture', action='store_true', help='Exercise the live dashboard with labeled constructed input.')
    mode.add_argument('--replay', action='store_true', help='Verify two offline replays against a separate constructed store.')
    mode.add_argument('--prompt9', action='store_true', help='Verify indicators and paper execution with constructed replay data.')
    mode.add_argument('--chart', action='store_true', help='Check chart capture and review with a controlled provider.')
    mode.add_argument('--chart-live', action='store_true', help='Submit a constructed chart to the configured live provider.')
    mode.add_argument('--automation', action='store_true', help='Check paper automation with disposable replay and fixed replies.')
    mode.add_argument('--voice', action='store_true', help='Check decision voice using a local WebRTC peer and synthetic audio.')
    mode.add_argument('--workstation', action='store_true', help='Run the offline indicator, chart and automation browser checks.')
    options = args.parse_args()
    chart = options.chart or options.chart_live
    if options.browser_timeout <= 0:
        args.error('--browser-timeout must be positive.')
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
                if options.workstation:
                    backend_env.update(INVEST_SOURCE_LABEL='Constructed workstation smoke test',
                                       INVEST_PAPER_TEST_MODE='1', INVEST_PAPER_ORDER_SIZE='4', INVEST_PAPER_SELL_SIZE='1')
                    backend_args = ['scripts.workstation_fixture', str(backend_port)]
                if options.replay or options.prompt9 or chart or options.automation or options.voice:
                    module = ('scripts.check_voice' if options.voice else
                              'scripts.check_automation' if options.automation else
                              'scripts.check_prompt9' if options.prompt9 or chart else 'scripts.check_replay')
                    subprocess.run(
                        [sys.executable, '-m', module, '--seed', str(database)],
                        cwd=ROOT, check=True, timeout=30, **common,
                    )
                    backend_env['INVEST_SOURCE_LABEL'] = 'Constructed replay test data'
                    snapshot = database.read_bytes()
                    if chart:
                        backend_env['INVEST_PAPER_ORDER_SIZE'] = '1'
                        backend_env['INVEST_PAPER_SELL_SIZE'] = '1'
                        backend_args = ['scripts.check_decisions', '--serve', str(backend_port), '--chart']
                        if options.chart_live:
                            backend_args.append('--live')
                    elif options.automation:
                        backend_env.update(INVEST_SOURCE_LABEL='Constructed automation fixture',
                                           INVEST_PAPER_TEST_MODE='1', INVEST_PAPER_ORDER_SIZE='1', INVEST_PAPER_SELL_SIZE='1')
                        backend_args = ['scripts.check_automation', '--serve', str(backend_port)]
                    elif options.voice:
                        decisions = json.loads(database.with_suffix('.json').read_text(encoding='utf-8'))
                        backend_args = ['scripts.check_voice', '--serve', str(backend_port)]
                backend = subprocess.Popen([sys.executable, '-m', *backend_args], cwd=ROOT, env=backend_env, **common)
                processes.append(backend)
                wait_ready(backend_url + '/api/health', backend)
                env = dict(os.environ, INVEST_API_TARGET=backend_url)
                frontend_command = [node, str(ROOT / 'frontend/node_modules/vite/bin/vite.js'), '--host',
                                    '127.0.0.1', '--port', str(frontend_port), '--strictPort']
                if options.voice:
                    frontend_command.extend(['--config', 'vite.voice-smoke.config.js'])
                frontend = subprocess.Popen(frontend_command, cwd=ROOT / 'frontend', env=env, **common)
                processes.append(frontend)
                wait_ready(frontend_url + '/api/health', frontend)
                with urlopen(frontend_url + '/api/history', timeout=5) as response:
                    rows = json.load(response)
                expected = {'timestamp': '2026-11-09T14:31:00Z', 'open': 102, 'high': 104, 'low': 100, 'close': 101, 'volume': 850}
                assert len(rows) == 3, f'Expected three fixture candles, got {len(rows)}'
                assert rows[1] == expected, f'Unexpected middle candle: {rows[1]}'
                print('HTTP PASS: three candles; 14:31 UTC matches all six source fields.', flush=True)
                if options.workstation:
                    if not __package__:
                        sys.path.insert(0, str(ROOT))
                    from scripts.workstation_fixture import check
                    from scripts.workstation_runner import run_browser

                    run_browser(node, frontend_url, Path(temp), log)
                    with urlopen(frontend_url + '/api/workstation-smoke/state', timeout=5) as response:
                        check(database, json.load(response)['calls'])
                    return 0
                browser_url = frontend_url
                if options.voice:
                    browser_url += '/?source=voice&decision=' + decisions['filled']
                elif options.replay or options.prompt9 or chart or options.automation:
                    browser_url += '/?source=replay'
                elif not (options.live or options.fixture):
                    browser_url += '/?source=sample'
                print(f'BROWSER URL: {browser_url}', flush=True)
                if not options.no_open:
                    webbrowser.open(browser_url)
                if options.voice:
                    print('Expect buy, accept, one filled share at $7 and $993 cash. Start voice and verify '
                          'Voice connected, Microphone active, and a remote audio track. End voice and check '
                          'Microphone off and Synthetic microphone track ended. Start again, then choose '
                          'Disconnect fixture peer and verify the same cleanup.', flush=True)
                    failed_url = f'{frontend_url}/?source=voice&decision={decisions["vetoed"]}&voice_fixture=failed'
                    print(f'Failed-connection URL: {failed_url}', flush=True)
                    print(f'Denied-microphone URL: {browser_url}&voice_fixture=denied', flush=True)
                    print('The failed decision was vetoed and has no fill. Start voice and verify cleanup. '
                          'Then verify the denied-microphone message.', flush=True)
                elif options.automation:
                    print('Start replay. Step through source 14:34:05 UTC. Start paper automation, then step to '
                          '14:35:05 (approved buy pending), 14:35:15 (fills at $7), and 14:36:05 '
                          '(vetoed hold). Confirm cash $993, 1 share and both decision links, then Stop.', flush=True)
                elif chart:
                    print('Select SMA, start Replay and play through. In Candle start, select 14:34 UTC. '
                          'Click Ask about chart. Confirm SMA, 1m, cutoff 14:35 UTC. In controlled mode expect '
                          'buy/up/sufficient, a matching crossover, and reviewer accept. Open the saved PNG. '
                          'The constructed fixture is ineligible for an order.', flush=True)
                elif options.prompt9:
                    print(f'Constructed replay database: {database}', flush=True)
                    print('Replay SMA, then RANGE, then AAPL as described in README.md. Create the empty '
                          f'file "restart" in {temp} after checking P&L. The runner retries the first buy '
                          'request and restarts FastAPI. Then reopen the saved paper account.', flush=True)
                elif options.replay:
                    print(f'Constructed replay test database: {database}', flush=True)
                    print('Confirm AAPL and Live feed disabled. Start the default UTC receipt range. '
                          'Select the 14:31 UTC candle after advancing to receipt 14:32:06. '
                          'Expect close 104 and volume 18 (1018 - 1000). At the next receipt, the late price '
                          'raises the high to 108. The correction at that same receipt time changes close to 103 '
                          'and volume to 20 (1020 - 1000). Restart and compare. Try a range after 15:00 UTC '
                          'and check for the no-recorded-observations message.', flush=True)
                elif options.live or options.fixture:
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
                if options.browser_result_file or options.prompt9:
                    result_file = options.browser_result_file or Path(temp) / 'browser-result.txt'
                    if options.prompt9:
                        print('Write PASS to the result file after checking the restored account, or FAIL on discrepancy.', flush=True)
                    print(f'Waiting for browser observation in {result_file}', flush=True)
                    deadline = time.monotonic() + options.browser_timeout
                    restarted = False
                    restart_marker = Path(temp) / 'restart'
                    while not result_file.exists():
                        if options.prompt9 and restart_marker.exists() and not restarted:
                            before_restart = paper_snapshot(database)
                            retry_first_order(database, frontend_url)
                            backend.terminate()
                            backend.wait(timeout=5)
                            backend = subprocess.Popen(
                                [sys.executable, '-m', *backend_args], cwd=ROOT, env=backend_env, **common,
                            )
                            processes.append(backend)
                            wait_ready(backend_url + '/api/health', backend)
                            assert paper_snapshot(database) == before_restart, 'Stored paper history changed after retry or restart.'
                            restarted = True
                            print(f'FastAPI restarted (PID {backend.pid}). Refresh the paper account page.', flush=True)
                        if time.monotonic() > deadline:
                            raise RuntimeError('Browser observation timed out.')
                        time.sleep(0.2)
                    answer = result_file.read_text(encoding='utf-8-sig')
                    result_file.unlink()
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
                if options.replay:
                    assert database.read_bytes() == snapshot, 'Replay changed the fixture database.'
                    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
                        count, = connection.execute('SELECT COUNT(*) FROM observations').fetchone()
                    assert count == 6, count
                    print('SQLite unchanged byte for byte. Observation count: 6.', flush=True)
                if options.prompt9:
                    assert restarted, 'Restart FastAPI before completing this check.'
                    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as connection:
                        assert connection.execute('SELECT COUNT(*) FROM paper_fills').fetchone() == (3,)
                        assert connection.execute('SELECT COUNT(*) FROM paper_orders').fetchone() == (4,)
                        assert connection.execute('SELECT name, window FROM chart_indicators ORDER BY id').fetchall() == [
                            ('Range 2', 2), ('Range 3', 3),
                        ]
                        rows = connection.execute(
                            'SELECT cash, shares, realized, unrealized, total FROM paper_pnl ORDER BY id'
                        ).fetchall()
                        assert [tuple(map(Decimal, row)) for row in rows] == [
                            (600, 4, 0, 0, 0), (600, 4, 0, 40, 40), (710, 3, 10, 30, 40),
                            (710, 3, 10, -15, -5), (1025, 0, 25, 0, 25),
                        ], rows
                    print(f'SQLite confirmed 3 fills, 4 orders, 2 indicators, and P&L {rows}.', flush=True)
                if chart and not check_chart_decision(database, frontend_url, options.chart_live):
                    return 2
                if options.automation:
                    check_automation(database, frontend_url)
                if options.voice:
                    with urlopen(frontend_url + '/api/voice-fixture/report', timeout=5) as response:
                        report = json.load(response)
                    assert len(report) == 3, report
                    *connected, failed = report
                    for attempt in connected:
                        assert attempt['answered'] and attempt['summary']['decision_id'] == decisions['filled']
                        assert attempt['summary']['paper_outcome']['cash_after_fill'] == '993'
                        assert attempt['summary']['proposal'] == 'buy'
                    assert not failed['answered'] and failed['summary']['decision_id'] == decisions['vetoed']
                    assert failed['summary']['paper_outcome']['status'] == 'skipped'
                    assert failed['summary']['paper_outcome']['fill_price'] is None
                    assert database.read_bytes() == snapshot, 'Voice changed the observation or paper database.'
                    print('Verified both voice summaries, the controlled connection failure, and an unchanged SQLite database.', flush=True)
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
