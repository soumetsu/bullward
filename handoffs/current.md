# Current project state

Updated: 2026-11-11

## Project and root

Project 4 Invest is a planned local investing and trading tool for its owner and other traders, with intended AI assistance and possible automation, including options trading.

Project root: `C:\Users\henry\Desktop\IST 402\Project 4 Invest`

## Files and directories created

- `README.md` — overview, goals, intended stack, detected prerequisites, dependency status, folder map, and current run status.
- `AGENTS.md` — project scope, preservation, credentials, dependency approval, verification, SmokeTest, AutoLoop, and handoff instructions.
- `backend/main.py` — FastAPI health and synthetic history routes.
- `backend/requirements.txt` — pinned FastAPI and Uvicorn dependencies.
- `backend/data/sample-candles.csv` — supplied synthetic candles used by `GET /api/history`.
- `frontend/` — Vue/Vite chart workspace with Lightweight Charts 5.2.1, aligned volume pane, inspector, table, UTC adapter/tests, styling, and `/api` proxy.
- `scripts/smoke_test.py` — repeatable manual-assisted browser smoke check with service startup/cleanup.
- `docs/user_preferences.md` — explicit product/design choices from chat only; `AGENTS.md` now enforces that update rule.
- `docs/screenshots/prompt-04-chart.jpg` — browser evidence for the selected 14:31 UTC candle.
- `prompts/04-chat-follow-up.md` — saved scope and preferences message.
- `docs/design-pipeline.md` — planned data flow, responsibilities, and current implementation status.
- `docs/verification.md` — environment discovery and Prompt 2–4 results, including failures.
- `prompts/prompts/` — user-supplied ordered Prompts 1–14 and the sample-candle CSV.
- `handoffs/create-handoff.md` — end-of-session and resume procedure.
- `handoffs/current.md` — this handoff.

## Checks performed

- Prompt 4 passed: Lightweight Charts 5.2.1 resolves, both adapter tests pass, Vite build passes, and browser selection of 14:31 UTC shows O 102, H 104, L 100, C 101, volume 850 shares with three separate candles and aligned volume bars.
- The manual-assisted smoke runner exited 0 after an actual browser observation. An initial console-input failure was resolved with a file-based observation mode. All processes started for the final check were stopped by the runner; the initial stalled runner and its own children were explicitly cleaned up.

- Confirmed the root folder existed and was empty before setup.
- Confirmed the folder is not a Git repository; Git status is unavailable here.
- Recorded Windows 10.0.26200.0, Python 3.14.8 (`C:\Python314\python.exe`), pip 26.2.1, Node.js 24.21.0, npm 11.19.0, pnpm 11.25.0, and Corepack 0.36.0.
- FastAPI 0.162.4 and Uvicorn 0.54.0 are installed in `C:\Python314\Lib\site-packages`; Vue 3.5.45, Vite 8.3.7, and `@vitejs/plugin-vue` 6.0.10 are in `frontend/package-lock.json`.
- Prompt 2 passed: direct backend and Vite-proxied health requests both returned HTTP 200 with `{"status":"ok"}`; with FastAPI stopped, the frontend proxy returned 502; it recovered after restart.
- Prompt 3 passed: browser opened `GET /api/history`; the 14:31 UTC candle matched all six source fields.
- `npm run build` passed after fixing the scaffold's stale `/src/main.ts` entry to `/src/main.js`. Ports 8000, 5173, and 5174 were unavailable; the verified services used 8001 and 5175. Uvicorn `--reload` failed in this environment, so the documented command omits it.
- The initial project scaffold path check passed. The root is still not a Git repository; the global npm inventory remains unavailable because its configured user-level directory does not exist.

## Outstanding questions

- See `docs/user_preferences.md` for the owner's chart/indicator/option-chain, advising, bot, alert, stop-loss/take-profit, and IV direction. Strategies, execution scope, data providers, notification channels, and detailed display rules remain undecided.
- Should the project be initialized as a Git repository?
- Which example images, designs, and Markdown prompts will be added next?

## Next task

STOP after Prompt 4, as explicitly requested in the latest chat. Prompts 1–4 are complete. Do not start Prompt 5 or implement the broader future goals without a new user instruction. No commit was made; the root is not a Git repository. When resuming, read the preference file, verify the current source and checks, and follow the user's next scope.

## Latest operational update — 2026-11-11

Restarted backend on http://127.0.0.1:8001 and frontend on http://127.0.0.1:5175; left both running. Direct health, frontend HTTP, and proxied health checks passed after resolving sandbox socket restrictions. Created missing root `.env` (comment-only, not loaded by the app) and `.gitignore` protecting environment files. No dependencies changed. See docs/verification.md for commands and results. No outstanding question for this request. Next task: await user direction; Prompt 5 remains unstarted. On resume, recheck service availability rather than assuming processes persisted.

## Prompt 5 — 2026-11-11

Prompt 5 is complete. Added `backend/yahoo_stream.py` and documented the standalone Yahoo Finance subscription check in README, design, and verification. The student approved installation into `C:\Python314\python.exe`. Pinned yfinance 1.8.0 in `backend/requirements.txt`. Both yfinance clients imported with websockets 17.4, and `python -m pip check` passed. The chart adapter tests and synthetic history fixture passed during preflight.

`python -m backend.yahoo_stream AAPL --timeout 30` opened the Yahoo WebSocket and received AAPL at 353.445 with source time 15:23:11.000 UTC and backend receipt at 15:23:11.770 UTC. Yahoo reported cumulative day volume of 7,658,617 shares and a last trade size of 100 shares. The check exited 0. Simulated quote, timeout, and remote closure checks also passed outside the sandbox after an initial asyncio stall. See `docs/verification.md` for commands and results.

Next task when authorized: Prompt 6 covers recording observations in SQLite. No approvals or checks are outstanding for Prompt 5.

## Prompt 6 — 2026-11-11

Added `backend/observations.py` and connected `backend/yahoo_stream.py` to SQLite. The table keeps the source message, ticker, source time, UTC receipt time, and optional revision link. The default `backend/data/market-observations.sqlite3` was initialized with zero rows and is ignored by Git along with its sidecars. README documents initialization and alternate paths. No dependencies were added.

`python -m scripts.check_observation_storage` used constructed test data in a disposable database. It suppressed an exact repeat, retained distinct same-time and late messages, linked an identifiable revision, and reopened the same five rows in a fresh recorder process. A separate live AAPL test read one saved row back from a disposable SQLite file. The existing chart adapter and synthetic history checks also passed. Commands and observed rows are in `docs/verification.md`.

No approvals or checks are outstanding for Prompt 6. Next task when authorized: Prompt 7, forming market candles from the stored observations.

## Prompt 7 — 2026-11-11

Added `backend/candles.py` to derive UTC one-minute candles from a read-only SQLite query, with separate minute status and a receipt cutoff. The calculator reorders late observations by source time and selects the effective version of an identified revision. The default database contained zero rows. README documents the command, and `docs/design-pipeline.md` documents the aggregation rules.

`python -m scripts.check_minute_candles` passed ten comparisons using disposable constructed observations after moving its test directory into the workspace. The required 15:00 UTC candle returned 100 / 103 / 100 / 101 and volume 11 while open, after the minute ended, and after receiving the late observation. The storage restart check and the synthetic history fixture passed. See `docs/verification.md` for the full results. No dependencies or UI changes were needed.

No checks or approvals are outstanding for Prompt 7. Next task when authorized: Prompt 7A, deriving larger timeframes from one-minute candles.
