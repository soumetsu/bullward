# Verification log

This file records environment checks and repeatable project checks. Record failures as well as passes. Update the observed result whenever a check is rerun in a changed environment.

## Initial setup inspection — 2026-11-10

### Environment and dependency discovery

| Check | Expected | Observed | Result |
|---|---|---|---|
| Operating system | Identify the current OS | `Microsoft Windows NT 10.0.26200.0` | Pass |
| Active Python | Report interpreter and version | `C:\Python314\python.exe`, Python 3.14.8 | Pass |
| pip | Report version | pip 26.2.1 | Pass |
| Python dependencies | Identify available project-relevant packages | `pip list` showed only pip; FastAPI, Uvicorn, and pytest imports were unavailable | Pass; packages not installed |
| Node.js | Report version | v24.21.0 | Pass |
| npm | Report version | 11.19.0 | Pass |
| pnpm | Report version | 11.25.0 | Pass |
| Corepack | Report version | 0.36.0 | Pass |
| Yarn | Check command availability | `yarn` was not recognized | Unavailable |
| Git status | Read current branch and worktree state | Git is installed, but the project folder is not a Git repository; `git status` returned “fatal: not a git repository” | Cannot run |
| Dependency files and lockfiles | Find existing Python/Node manifests and locks | None found in the project folder | Pass |
| Global npm package inventory | List installed global packages | `npm ls --global --depth=0` failed because `C:\Users\henry\AppData\Roaming\npm` does not exist | Failed; global inventory unavailable |

The project folder existed and contained no files before setup. No dependency installation or manifest change was needed for this documentation task.

## Prompt 2 preflight — 2026-11-10

Read `README.md` and `AGENTS.md`. Inspected `backend/` and `frontend/`; both are empty. Rechecked the active interpreter and package managers before the Vue/FastAPI connection work.

| Check | Expected | Observed | Result |
|---|---|---|---|
| Python environment | Identify interpreter and check FastAPI/ASGI server | `C:\Python314\python.exe`, Python 3.14.8; neither `fastapi` nor `uvicorn` is installed | Pass; dependencies are missing |
| Frontend environment | Identify Node and package manager | Node v24.21.0, npm 11.19.0, pnpm 11.25.0 | Pass |
| Existing frontend | Inspect code, manifests, and locks | `frontend/` is empty; no `package.json`, lockfile, Vue app, or Vite config exists | Pass |
| Existing backend | Inspect code and dependency declarations | `backend/` is empty; no FastAPI app or Python dependency manifest exists | Pass |

### Proposed Prompt 2 dependencies and changes (approval pending)

- Backend: FastAPI 0.162.4 provides the HTTP API framework; Uvicorn 0.54.0 serves the ASGI app locally. Both support the active Python 3.14 interpreter. Proposed manifest: `backend/requirements.txt` containing `fastapi==0.162.4` and `uvicorn==0.54.0`. Exact install command: `python -m pip install -r backend/requirements.txt`. Target: the existing interpreter at `C:\Python314\python.exe` (global site-packages; no virtual environment). Files changed: new `backend/requirements.txt`, plus installed packages in that interpreter's site-packages.
- Frontend: Vue 3 with the official Vite-based scaffolder. Node v24.21.0 meets Vue's current documented minimum. Proposed commands: `npm create vue@latest frontend` (decline optional features for a minimal app), then `npm install` from `frontend/`. Files changed: the generated frontend app and `package.json`; `npm install` creates/updates `package-lock.json` and `node_modules/`. The Vite proxy config will also be added for `/api` requests.
- Prompt 2 implementation files after approval: `backend/main.py`, `frontend/vite.config.js`, `README.md`, and this verification log.
- At the end of preflight, no install, scaffold command, manifest change, or application edit had been performed; those actions proceeded only after the student approved them.

## Prompt 2 implementation and verification — 2026-11-10

The student approved the proposed dependency setup. The first pip attempt could not resolve `pypi.org` in the sandbox; rerunning the same approved command with network access succeeded. FastAPI 0.162.4 and Uvicorn 0.54.0 are installed at `C:\Python314\Lib\site-packages`. The official Vue scaffolder created the app; the generated TypeScript/devtools extras were removed to keep this initial frontend in JavaScript with only Vue, Vite, and the official Vue plugin. `npm install` in `frontend/` added 35 packages, audited 36 packages, and reported 0 vulnerabilities. `npm ls --depth=0` observed Vue 3.5.45, Vite 8.3.7, and `@vitejs/plugin-vue` 6.0.10.

| Check | Command or URL | Expected | Observed | Result |
|---|---|---|---|---|
| Backend imports | `python -c "import fastapi,uvicorn; print(fastapi.__version__, uvicorn.__version__)"` | FastAPI and Uvicorn import at pinned versions | `0.162.4 0.54.0` | Pass |
| Frontend dependencies | `npm ls --depth=0` from `frontend/` | Vue, Vite, and Vue plugin resolve | Vue 3.5.45, Vite 8.3.7, plugin 6.0.10 | Pass |
| Frontend build | `npm run build` from `frontend/` | Production bundle is created | Initial attempt failed because `index.html` still referenced removed `src/main.ts`; changed it to `src/main.js`, then build succeeded with Vite 8.3.7 | Pass after one AutoLoop correction |
| Direct backend request | `GET http://127.0.0.1:8001/api/health` | HTTP 200 and JSON status | HTTP 200, `{"status":"ok"}` | Pass |
| Request through Vue server | `GET http://127.0.0.1:5175/api/health` | Same response as direct backend | HTTP 200, `{"status":"ok"}` | Pass |
| Backend stopped | Repeat the frontend URL while FastAPI is stopped | Proxy request fails, proving it depends on FastAPI | HTTP 502; Vite logged `ECONNREFUSED 127.0.0.1:8001` | Pass |
| Backend recovery | Restart FastAPI and repeat frontend URL | Successful response resumes | HTTP 200, `{"status":"ok"}` | Pass |

The default ports 8000 and 5173 could not be bound in this environment; 5174 was also unavailable. The checked start commands use available ports 8001 and 5175. A Uvicorn `--reload` attempt failed when its Windows worker process could not reconnect to the reloader pipe (`WinError 2`), so the verified backend command omits `--reload`.

Verified start and stop commands (run each service in its own terminal; stop with Ctrl+C):

```powershell
# From the project root
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001

# From frontend/
npm run dev -- --host 127.0.0.1 --port 5175
```

The Vue page is available at `http://127.0.0.1:5175/`. No browser-driven user workflow exists yet; a full UI SmokeTest remains for the later workstation smoke-test prompt.

### Scaffold check

Command to run from the project root:

```powershell
$required = @('README.md','AGENTS.md','backend','frontend','docs/design-pipeline.md','docs/verification.md','prompts/01-generic-project-startup.md','handoffs/create-handoff.md','handoffs/current.md')
$missing = $required | Where-Object { -not (Test-Path -LiteralPath $_) }
if ($missing) { throw "Missing: $($missing -join ', ')" }
'All required scaffold paths exist.'
```

Expected result: `All required scaffold paths exist.` Observed result: `All required scaffold paths exist.` (exit code 0, 2026-11-10).

### Follow-up checks

- A full user-facing SmokeTest remains for a later prompt that adds an interactive UI feature. It must start the local services, open a browser, perform a user operation, compare the visible result with its expectation, and check stored data when relevant.

## Prompt 3 historical CSV — 2026-11-10

The supplied `prompts/prompts/data/sample-candles.csv` had the expected six-column header and three synthetic rows. It was copied unchanged to `backend/data/sample-candles.csv`; the original prompt asset was preserved. `GET /api/history` uses Python's built-in `csv` module, converts price fields to numbers and volume to an integer, and orders rows by the UTC timestamp.

Opened `http://127.0.0.1:8001/api/history` in the browser. Expected second row from the source CSV:

```text
timestamp = 2026-11-09T14:31:00Z
open      = 102
high      = 104
low       = 100
close     = 101
volume    = 850
```

Observed HTTP 200 and a JSON array ordered 14:30, 14:31, 14:32 UTC. The 14:31 row showed `open: 102.0`, `high: 104.0`, `low: 100.0`, `close: 101.0`, `volume: 850`; all six fields matched the source row. Result: pass. FastAPI logged a 404 for the browser's automatic `/favicon.ico` request; this did not affect the history endpoint.

## Prompt 4 preflight — 2026-11-10

Inspected `AGENTS.md`, `frontend/package.json`, `frontend/package-lock.json`, Node.js, npm, and the installed frontend package tree. Node is v24.21.0 and npm is 11.19.0. Vue 3.5.45 and Vite 8.3.7 are installed. `npm ls lightweight-charts` returned an empty match (exit code 1), so the requested chart library is missing.

The current published Lightweight Charts release observed in TradingView's release notes is 5.2.1. The official 5.2 docs describe its client-side chart package and `npm install --save lightweight-charts` setup. Proposed compatible exact version: `lightweight-charts@5.2.1` for the existing Vue/Vite frontend.

Proposed install command, from `frontend/`: `npm install --save-exact lightweight-charts@5.2.1`. Target: the existing `frontend/` npm project. Install/manifest files affected: `frontend/package.json`, `frontend/package-lock.json`, and `frontend/node_modules/`. The later chart implementation will add or update Vue source files and must label the data synthetic, expose candle details, include aligned volume, and show TradingView attribution.

At this preflight point, the chart dependency and implementation were pending. The user first held off, then explicitly instructed the agent to follow through on Prompt 4 and stop after it. The completion record below supersedes this pending state.

## Prompt 4 completion and chat preferences — 2026-11-10

Read README, AGENTS, the actual handoff files in `handoffs/` (no root HANDOFF.md exists), design/verification notes, and Prompt 4. The latest chat resumed the previously proposed 5.2.1 install and authorized the preference documentation. Ran `npm install --save-exact lightweight-charts@5.2.1` in `frontend/`: added 2 packages, audited 38, zero reported vulnerabilities. Package manifest and lockfile now record the exact chart dependency. No Python environment was created or changed.

Implemented a Vue chart workspace using the existing three CSV/API candles, with an aligned lower volume pane, UTC minute conversion shared by both series, a candle inspector, keyboard-accessible selection and table, reset/refresh controls, loading/error/empty states, and TradingView attribution. The saved chat preferences are future product direction; no bot, live feed, indicator, option-chain, IV, advising, or alert feature was added in this prompt.

| Check | Expected | Observed |
|---|---|---|
| `npm ls lightweight-charts` in frontend | Installed exact package | 5.2.1 resolves; pass |
| `node --test frontend/src/chartData.test.js` | Seconds conversion, distinct UTC minutes, aligned series; malformed input rejected | 2 tests passed, 0 failed |
| `npm run build` in frontend | Successful Vue/production chart bundle | Vite 8.3.7 built 21 modules successfully |
| Browser at temporary localhost URL | Three separate minute candles and volume bars | Visible at 14:30, 14:31, 14:32 UTC; volume aligned underneath |
| Select table row / dropdown for 14:31 UTC | Nov 9, 2026; O102 H104 L100 C101; 850 shares | Inspector and table matched all values; selected close 101.00, volume 850 shares |
| Reset view, attribution | Chart data remains visible; required notice/link displayed | Pass; TradingView logo, notice, and link visible |
| Smoke runner with actual browser observation | HTTP fixture and visible chart check pass; services cleaned up | Final run exited 0; printed PASS and stopped its own services |

Screenshot evidence: `docs/screenshots/prompt-04-chart.jpg`. Final observed browser URL was `http://127.0.0.1:49797/` (temporary test ports; stopped afterward). The saved screenshot from the first browser run shows the same confirmed values.

Repeatable command from root: `python scripts/smoke_test.py`. This is a manual-assisted UI check: the runner starts services and checks the API; a human or agent performs the visible browser operation and confirms the result. It is not yet the fully automated Prompt 14 test. Agent command used: `python scripts/smoke_test.py --no-open --browser-result-file .smoke-browser-result.txt`; PASS was written only after selecting the candle and observing the browser. The result file is consumed and removed.

Failure/correction record: the first interactive runner passed HTTP and browser checks but this tool's terminal did not deliver its confirmation input. The stalled runner and its own child services were identified and stopped. Added file-based confirmation, reran the check without source edits during execution, observed the browser again, and confirmed exit 0 and service cleanup. One harness correction cycle. No further prompts were executed.

## Service restart — 2026-11-11

Created missing root `.env` as a comment-only placeholder and `.gitignore` to exclude local environment files. No credentials or dependencies added. The application does not yet load the root `.env`.

Initial sandbox service/HTTP attempts stalled or failed with socket restrictions. Restarted the identified project processes outside the sandbox. Backend: `python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001`; frontend (from frontend/): `node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5175`.

Checks: `Invoke-RestMethod http://127.0.0.1:8001/api/health` returned status ok; `Invoke-WebRequest http://127.0.0.1:5175/` returned HTTP 200; `Invoke-RestMethod http://127.0.0.1:5175/api/health` returned status ok. All expected results passed. Services left running. No UI changes; browser SmokeTest not rerun.

## Prompt 5 preflight and implementation — 2026-11-11

Inspected the instructions, handoff, backend requirements, frontend manifest and lockfile, and Git status. Windows is 10.0.26200.0. The active backend interpreter is `C:\Python314\python.exe` (Python 3.14.8, pip 26.2.1) with FastAPI 0.162.4 and Uvicorn 0.54.0. Package metadata and import discovery found neither yfinance nor websockets. Node is 24.21.0, npm 11.19.0, pnpm 11.25.0, and Corepack 0.36.0. Yarn is absent and the project has no Git repository or Python lockfile.

Requested approval for `C:\Python314\python.exe -m pip install --only-binary=:all: yfinance==1.8.0`, targeting the existing global Python environment, and for adding `yfinance==1.8.0` to `backend/requirements.txt`. The release provides `AsyncWebSocket` and declares `websockets>=13.0` as a dependency. [PyPI release](https://pypi.org/project/yfinance/1.8.0/) · [Release dependency metadata](https://github.com/ranaroussi/yfinance/blob/1.8.0/pyproject.toml) · [WebSocket documentation](https://ranaroussi.github.io/yfinance/reference/yfinance.websocket.html)

Prepared `backend/yahoo_stream.py`. `python -m backend.yahoo_stream --help` passed. A sample quote parsing check confirmed the ticker filter, Unix millisecond conversion to UTC, receipt timestamp, and integer volume. The chart adapter's two tests passed and the backend history fixture still returned the three synthetic candles. An asyncio loop check stalled in the sandbox. A repeat outside the sandbox with a sample transport passed all four cases: one matching quote, timeout after opening, remote closure, and timeout before opening. These sample checks did not contact Yahoo.

`python -m backend.yahoo_stream AAPL --timeout 30` is the repeatable subscription check. Expected: opens a WebSocket, sends the AAPL subscription, prints one quote with ticker, price, source and backend receipt times, and available volume fields, or reports connection status and unverified receipt by the deadline. Before installation, the command exited with `No module named 'yfinance'`. Connection and receipt are unverified. The package install and import/version verification await student approval.

## Prompt 5 live subscription — 2026-11-11

After student approval, `C:\Python314\python.exe -m pip install --only-binary=:all: yfinance==1.8.0` exited 0 and installed the package and its dependencies into the existing Python environment. Added `yfinance==1.8.0` to `backend/requirements.txt`.

| Check | Expected | Observed |
|---|---|---|
| `python -c 'import sys, yfinance, websockets; from yfinance import AsyncWebSocket, WebSocket; print(sys.executable, yfinance.__version__, websockets.__version__, AsyncWebSocket.__name__, WebSocket.__name__)'` | Both clients import in the backend interpreter | `C:\Python314\python.exe 1.8.0 17.4 AsyncWebSocket WebSocket` |
| `python -m pip check` | No dependency conflicts | `No broken requirements found.` |
| `python -m backend.yahoo_stream AAPL --timeout 30` | WebSocket opens, subscription sends, and a matching quote arrives before the deadline | Opened and sent AAPL subscription, then returned one quote and exited 0 |

| Field | Observed value | Units or meaning |
|---|---|---|
| Ticker | AAPL | Yahoo ticker |
| Price | 353.445 | Price in the received frame |
| Source time | `1794410591000` (`2026-11-11T15:23:11.000+00:00`) | Unix milliseconds, also rendered as UTC |
| Backend receipt | `2026-11-11T15:23:11.770+00:00` | Local backend clock, rendered as UTC to milliseconds |
| `day_volume` | 7,658,617 | Yahoo's cumulative daily share count |
| `last_size` | 100 | Shares in the reported last trade |

The live check ran outside the sandbox so the Windows asyncio loop and outbound WebSocket could initialize. No Yahoo account was used. Prompt 5 is verified. The existing browser smoke test still covers the synthetic chart. This prompt added a terminal command without changing the UI.

## Prompt 6 SQLite observation storage — 2026-11-11

The existing `C:\Python314\python.exe` interpreter (3.14.8) provides `sqlite3` with SQLite 3.50.4 and JSON support. Rechecked the Windows environment, Python package versions, frontend manifest, and lockfile. No dependency install or manifest change was needed.

| Check | Expected | Observed |
|---|---|---|
| `python -m backend.observations --db PATH` with a fresh temporary path, then repeat | Create the schema and safely reopen it | Both runs exited 0. The empty observations table was readable. |
| `python -m scripts.check_observation_storage` | Save the first quote once, retain a changed same-time quote, preserve late arrival and revision history, and reopen after restart | A disposable database contained the five rows below. The second recorder process reopened the same rows and suppressed a second replay of the exact repeat. Exited 0. |
| `python -m backend.yahoo_stream AAPL --timeout 30 --db PATH` with a disposable path | Receive a live AAPL update and persist the decoded fields | Received AAPL at 353.43 with source time `1794410778000` and receipt `2026-11-11T15:26:18.638022+00:00`. SQLite returned one row with the full decoded message, including exchange `NMS` and day volume `7836657`. |
| `python -m backend.observations` | Initialize the default local database | Created `backend/data/market-observations.sqlite3`. A read-only count returned 0. Both test databases were disposable. |
| `node --test frontend/src/chartData.test.js` and a direct call to the backend `history()` handler | Existing chart parsing and three-candle fixture remain valid | Both adapter tests passed. The history handler returned three rows with volume 850 on the middle candle. |

Stored rows from the disposable restart check. All five are **constructed test data**, with ticker `AAPL`, volume `850`, and `test_data: true` in the decoded payload. Receipt times are UTC on Nov 11, 2026.

| ID | Price | Source time (Unix ms) | Backend receipt (UTC) | Event ID | Revision of |
|---:|---:|---:|---|---|---:|
| 1 | 101 | 1794234660000 | 15:26:37.827883 | — | — |
| 2 | 102 | 1794234660000 | 15:26:37.917418 | — | — |
| 3 | 100 | 1794234600000 | 15:26:37.925690 | — | — |
| 4 | 103 | 1794234720000 | 15:26:37.937825 | test-event-1 | — |
| 5 | 104 | 1794234720000 | 15:26:37.937825 | test-event-1 | 4 |

The repeated input changed only JSON key order and arrival time, leaving row 1 intact. Read one row per ID to count it once. Rows 1 and 2 share a timestamp but have no known correction link. Row 3 arrived later with an earlier market time. Rows 4 and 5 demonstrate revision history when the caller can establish event identity. The two recorder process checks ran outside the sandbox for the Windows event loop. Prompt 6 added backend storage and a terminal check, so the existing chart browser SmokeTest was not rerun.

## Prompt 7 one-minute candles — 2026-11-11

Inspected the SQLite observation schema in the existing Python 3.14.8 environment (SQLite 3.50.4). The default database had zero rows. The candle calculator uses the Python standard library and reads SQLite without writing.

Run `python -m scripts.check_minute_candles` from the project root. It builds a disposable database with **constructed test observations**. For the candle starting `2026-11-09T15:00:00Z`, these are the expected and observed values:

| Check time (UTC) and scenario | Expected open/high/low/close | Expected volume | Expected ended | Observed |
|---|---|---:|---|---|
| 15:00:59, minute still open | 100 / 103 / 100 / 101 | 11 | false | Match |
| 15:01:00, minute ended | 100 / 103 / 100 / 101 | 11 | true | Match |
| 15:01:00, before middle tick arrives | 100 / 101 / 100 / 101 | 11 | true | Match |
| 15:01:10, after middle tick arrives | 100 / 103 / 100 / 101 | 11 | true | Match |
| 15:01:00, missing baseline, reset, missing counter, or empty intervening minute | 100 / 103 / 100 / 101 | null | true | All four matched |
| 15:01:00, before identifiable correction | 100 / 103 / 100 / 101 | 11 | true | Match |
| 15:01:20, after identifiable correction | 100 / 102 / 100 / 101 | 11 | true | Match |

The original check could not open its database in the sandbox's system temp directory. Moved the disposable test directory under `backend/data/`. The rerun passed all ten comparisons and removed its test database. One correction cycle.

`python -m backend.candles aapl --as-of 2026-11-09T15:01:00Z` returned `[]` as expected for the empty default store. `python -m scripts.check_observation_storage` passed the recorder restart check. The synthetic `history()` fixture still returned three candles and 850 shares for the middle row. No UI changed in this prompt.

## Prompt 8 and timeframe prerequisite — 2026-11-12

Resumed from clean `main` at `e7a6efd`. The handoff's earlier Git and next-task entries predate the current repository and chat direction. Checked Windows, Python 3.14.8, pip 26.2.1, Node 24.21.0, npm 11.19.0, pnpm 11.25.0, Corepack 0.36.0, and the frontend lock and package tree. The installed Python packages include yfinance 1.8.0, websockets 17.4, and tzdata 2026.6. The existing frontend and Python packages were sufficient. `python -m pip check` reported no broken requirements. The default observation store was empty.

| Check | Expected | Observed |
|---|---|---|
| `python -m scripts.check_timeframes` | Sample 5m at 14:30 UTC has O100 H105 L99 C104 V3550. Report 3/5 minutes and daily 3/390 as forming at 14:33 UTC. | Both matched. A constructed 14:35 minute started a new 5m candle and stayed in the same daily candle. Nov 10 started a new daily candle. Known zero and unknown volume remained distinct. The holiday, daylight saving transition, and early-close hourly bucket matched. |
| `python -m scripts.check_minute_candles` | Preserve all 10 comparisons for order, volume, revisions, and cutoff. | All passed. |
| `python -m scripts.check_observation_storage` | Restart reopens the same five rows and suppresses the repeated frame. | Passed outside the sandbox. In the sandbox the system temp database was inaccessible, and the workspace-temp retry stalled in the recorder subprocess. |
| `node --test frontend/src/chartData.test.js` and `npm run build` in `frontend/` | Existing UTC conversion and validation pass and production bundle builds. | 2 tests passed. Vite built 21 modules. |
| `python scripts/smoke_test.py --fixture --no-open --browser-result-file .smoke-fixture-outside.txt` | Start both services, receive constructed observations, update and roll over a candle, switch timeframe, distinguish upstream loss, and check SQLite. | Exited 0 outside the sandbox, with browser and database results below. The first run in the sandbox timed out waiting for backend health and stopped its child process. |
| `python scripts/smoke_test.py --live --no-open --browser-result-file .smoke-live-result.txt` | Follow a real Yahoo price from SQLite into the browser. Disconnect the upstream feed with the page open. | Subscribed outside the sandbox overnight. Zero observations arrived. Browser showed AAPL, Yahoo Finance, no observations, and “Waiting for a price.” After upstream disconnect it showed “Upstream disconnected · retrying” and “Dashboard socket · Connected.” Switching to 15 minutes loaded the matching empty history. The runner received PENDING and exited 1. **Live quote-to-candle verification is pending.** |

Browser comparisons for **constructed smoke test data** on Nov 9, 2026, in UTC:

| Browser action | Observed |
|---|---|
| Open the 1m dashboard | Two rows. The 14:31 candle showed O102 H102 L102 C102, volume 10, and latest source time 14:31:05. |
| Deliver the 14:31:35 observation | Same two rows. The 14:31 candle changed to O102 H104 L102 C104, volume 15. The dashboard showed “Receiving updates” and source time 14:31:35. SQLite row 3 held price 104 and day volume 1015, up 5 from the previous 1010. |
| Deliver the 14:32:05 observation | Third candle at 14:32, O101 H101 L101 C101, volume 10. SQLite row 4 held price 101 and day volume 1025. |
| Switch to 5m and then Daily | Each showed one 14:30 candle, O100 H104 L100 C101, volume Unknown. Daily showed 3 of 390 minutes observed. |
| Disconnect the constructed upstream | The candle stayed visible. Feed status changed to “Upstream disconnected · retrying” with “Dashboard socket · Connected.” |
| Open `/?source=sample` and choose 14:31 UTC | The synthetic label and three rows appeared. The inspector showed O102 H104 L100 C101 and 850 shares. |

Both smoke runners cleaned up their temporary services and databases. The repeatable checks are `python scripts/smoke_test.py --fixture` and `python scripts/smoke_test.py --live`. No source edits are required to trigger the fixture updates or disconnect.

## Prompt 8 follow-up and Prompt 9 — 2026-11-15

Pushed the reviewed Prompt 8 change as `dc1ef87` to `origin/main`. The first stage attempt hit the sandbox's protected Git index. The escalated stage passed, but Git lacked a configured author. The commit used the previous commit's GitHub noreply identity as a one-command override. The push succeeded.

Rechecked Windows 10.0.26200.0, Python 3.14.8, pip 26.2.1, SQLite 3.50.4, Node 24.21.0, npm 11.19.0, pnpm 11.25.0, Corepack 0.36.0, and the current Python and frontend dependencies. `python -m pip check` passed. No packages, manifests, or lockfiles changed.

| Check | Expected | Observed |
|---|---|---|
| `python -m scripts.check_minute_candles`, `python -m scripts.check_timeframes`, `python -m scripts.check_observation_storage` | Existing candle, calendar, and SQLite restart checks pass | All passed. The storage check used the previously verified outside-sandbox path. |
| `node --test frontend/src/chartData.test.js` and `npm run build` in `frontend/` | UTC adapter tests and production bundle pass | Both tests passed. Vite built 22 modules with Replay. |
| `python -m backend.yahoo_stream AAPL --timeout 30 --db backend/data/invest-sunday-check.sqlite3` | Retry receipt of a real observation | The Yahoo socket opened and sent the AAPL subscription. It timed out after 30 seconds early Sunday Eastern time and created no database. Prompt 8's live quote-to-candle check remains pending during a regular session. |
| `python -m scripts.check_replay` | Verify receipt order, tied receipts, late arrivals, revision, empty range, and repeatability | Six frames replayed in order. Both result sequences and every stored row matched. |
| `python scripts/smoke_test.py --replay --no-open --browser-timeout 2700 --browser-result-file .smoke-replay-retry.txt` | Start FastAPI with Yahoo off and compare two browser replays with the separate SQLite fixture | Passed. Both runs ended with identical chart rows. The test store had six observations and was byte-for-byte identical before and after. |
| `python scripts/smoke_test.py --fixture --no-open --browser-timeout 2700 --browser-result-file .smoke-fixture-retry.txt` | Preserve Prompt 8's candle update, rollover, interval switching, and disconnect behavior | Passed. SQLite held the same four constructed observations as in the earlier smoke check. The dashboard socket remained connected when the fixture upstream disconnected. |

The initial browser launch outlasted the 600-second confirmation deadline. Both initial runners exited 1 and cleaned up their services. Added a configurable confirmation timeout and reran both checks with a longer window. The browser automation also timed out when filling zero seconds in a date field because the browser normalized the value to minutes. Filling the native field with the normalized value succeeded.

Replay browser results for the labeled constructed AAPL recording on Nov 9, 2026:

| Observation and receipt in UTC | 14:31 candle in the browser |
|---|---|
| Source 14:32:05, receipt 14:32:06, before the late trade | O102 H104 L102 C104, volume 18 from 1018 − 1000 |
| Late source 14:31:25, receipt 14:32:10 | O102 H108 L102 C104, volume 18 |
| Correction to source 14:31:45, also received at 14:32:10 | O102 H108 L102 C103, volume 20 from 1020 − 1000 |
| Restarted the same range and played through | Same candles, values, and final observation timestamps |
| Requested 15:00–15:01 by UTC receipt | “No recorded observations in this range.” |

The default observation store contained zero rows. Replay against an actual Yahoo session remains pending until a capture exists. The browser SmokeTest command is `python scripts/smoke_test.py --replay`.

## Prompts 9A–9D

Used the existing Python 3.14.8, SQLite, FastAPI, Node 24.21.0, npm 11.19.0, Vue 3.5.45, Vite 8.3.7, and Lightweight Charts 5.2.1. No dependency or manifest changes.

### Indicators

`python -m scripts.check_prompt9` compared every completed prefix of the constructed replay against the full Python calculation and verified that the forming candle had gaps and no signal. For the seven closes 13, 10, 7, 13, 19, 7, 1:

| UTC candle start | Short (2) | Long (3) | Signal |
|---|---:|---:|---|
| 14:30 | — | — | — |
| 14:31 | 11.5 | — | — |
| 14:32 | 8.5 | 10 | — |
| 14:33 | 10 | 10 | — |
| 14:34 | 16 | 13 | Up |
| 14:35 | 13 | 13 | — |
| 14:36 | 4 | 9 | Down |

Both markers appeared on the browser chart. The candle inspector showed 16/13 and 4/9 when selected. On the RANGE fixture, the third candle showed Range 2 = 5 and Range 3 = 6. A zero window showed “The indicator window must be a positive whole number.” Both saved configurations returned after refresh and SQLite held exactly those two rows.

### Paper orders and P&L

The constructed AAPL replay started with $1,000. The browser showed pending orders followed by fills linked to observations 17, 20, and 23. Hold and wait produced no orders. The sale from the empty position was recorded as rejected. An HTTP retry of the first buy returned order #1 again. After FastAPI restarted, a direct SQLite read verified the same three fills, four orders, and five valuation rows. The archived browser account displayed the same balance and history before and after restart.

| Event | Cash | Shares | Realized | Unrealized | Total |
|---|---:|---:|---:|---:|---:|
| Buy 4 at $100 | $600 | 4 | $0 | $0 | $0 |
| Value at $110 | $600 | 4 | $0 | $40 | $40 |
| Sell 1 at $110 | $710 | 3 | $10 | $30 | $40 |
| Value at $95 | $710 | 3 | $10 | −$15 | −$5 |
| Close 3 at $105 | $1,025 | 0 | $25 | $0 | $25 |

The first numerical assertion compared stored decimal spellings and rejected equivalent values such as `40.0` and `40`. Comparing decimals fixed the check. The first browser harness timed out while awaiting the remaining operations, and a separate local HTTP retry attempt was blocked in the sandbox. The rerun moved the retry into the smoke harness and used a longer confirmation window. `python scripts/smoke_test.py --prompt9 --no-open --browser-timeout 14400 --browser-result-file .smoke-prompt9-result.txt` exited 0 and removed its temporary database and services. The documented interactive command is `python scripts/smoke_test.py --prompt9`.

The final `python -m scripts.check_prompt9` also passed live matching with constructed prices. A pending order ignored pre-submission observations, two buy prices produced average cost 110, a partial sale realized 15 and left 45 unrealized, and the matcher rejected fractional quantity, insufficient cash, and a second sale exceeding the remaining position. Existing replay, minute candle, timeframe, SQLite restart, and chart adapter checks passed. Vite built 25 modules successfully, and `git diff --check` passed.

The sandbox blocked the process inventory check used to identify the running development backend. The outside-sandbox restart verified ownership by the listener's command line and left the development services running. FastAPI health on port 8001 and the frontend, indicators, and paper endpoints through Vite on port 5175 each returned HTTP 200.
