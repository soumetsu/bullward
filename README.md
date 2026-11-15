# Project 4 Invest

## Overview

Project 4 Invest is a local investing and trading project for its owner and other people interested in trading. The current app displays AAPL price and volume candles from recorded Yahoo Finance observations using TradingView Lightweight Charts. AI advising, an automated trading bot, indicators, option chains, suggested alerts, and stop-loss/take-profit assistance are future goals. See `docs/user_preferences.md` for choices stated by the owner in chat.

## Goals

- Provide a local web interface for investing and trading research.
- Connect a Vue frontend to Python application logic through a FastAPI HTTP API.
- Leave room for AI-assisted research, workflow automation, and options-related workflows.
- Evaluate TradingView, yfinance, and possibly Webull as external sources after the first use cases are defined.
- Keep credentials outside source code, prompts, logs, and Git.

FastAPI runs one Yahoo Finance subscription for AAPL and stores observations in SQLite. A separate command checks receipt and storage of one price update for a ticker.

## Technology

- **Backend:** Python 3.14 with FastAPI as the API and transport layer; Uvicorn serves it locally.
- **Frontend:** Vue 3.5 with Vite 8, written in JavaScript.
- **Communication:** JSON over local HTTP and WebSocket between the browser frontend and FastAPI.
- **Market data:** Yahoo Finance through yfinance, with a separate synthetic CSV view.
- **Storage:** Python's built-in `sqlite3` module stores received market observations locally.
- **Potential AI and automation:** Not selected or implemented.

## Prerequisites found on this computer

Environment inspection on 2026-11-10 found:

- Windows, reported as `Microsoft Windows NT 10.0.26200.0`.
- Python 3.14.8 at `C:\Python314\python.exe`; pip 26.2.1.
- Node.js 24.21.0 and npm 11.19.0.
- pnpm 11.25.0 and Corepack 0.36.0.
- Yarn is not installed or available on `PATH`.
- The project is a Git repository.

FastAPI and Uvicorn are now installed in the active interpreter. pytest is not installed. The global npm package listing could not be read because npm's configured user-level global directory, `C:\Users\henry\AppData\Roaming\npm`, does not exist.

## Dependencies

Backend dependencies are declared in `backend/requirements.txt`: FastAPI 0.162.4, Uvicorn 0.54.0, and yfinance 1.8.0. They are installed in the active Python interpreter at `C:\Python314\Lib\site-packages`.

Frontend dependencies are declared in `frontend/package.json` and pinned by `frontend/package-lock.json`: Vue 3.5.45, Vite 8.3.7, `@vitejs/plugin-vue` 6.0.10, and Lightweight Charts 5.2.1. Additional market-data, AI, and browser-test dependencies require review before installation. The chart and smoke checks currently use existing tools only.

Reuse the active Python interpreter by default. Do not create or recreate a virtual environment unless the project owner approves a need for one. Before adding any dependency, document its purpose, compatible version, exact installation command, installation target, and files that the installation or manifest change will affect; obtain approval first.

## Folder map

```text
Project 4 Invest/
├── AGENTS.md
├── README.md
├── backend/
│   ├── main.py
│   ├── candles.py
│   ├── live_feed.py
│   ├── observations.py
│   ├── timeframes.py
│   ├── requirements.txt
│   ├── yahoo_stream.py
│   └── data/
│       ├── market-observations.sqlite3 (local database)
│       └── sample-candles.csv
├── frontend/
│   ├── index.html
│   ├── package.json
│   ├── package-lock.json
│   ├── vite.config.js
│   └── src/
│       ├── App.vue
│       ├── chartData.js
│       ├── chartData.test.js
│       ├── style.css
│       ├── components/CandleChart.vue
│       └── main.js
├── docs/
│   ├── design-pipeline.md
│   ├── user_preferences.md
│   ├── screenshots/prompt-04-chart.jpg
│   └── verification.md
├── scripts/
│   ├── check_minute_candles.py
│   ├── check_observation_storage.py
│   ├── check_timeframes.py
│   ├── smoke_live_backend.py
│   └── smoke_test.py
├── prompts/
│   ├── 04-chat-follow-up.md
│   └── prompts/
│       ├── 01-generic-project-startup.md
│       ├── 02-connect-vue-and-fastapi.md
│       ├── 03-load-historical-csv.md
│       ├── 04-integrate-candlestick-chart.md
│       ├── 05-connect-yahoo-finance.md
│       ├── 06-record-observations-in-sqlite.md
│       ├── 07-form-market-candles.md
│       ├── 07a-derive-higher-timeframes.md
│       ├── 08-display-live-updates.md
│       ├── 09-replay-recorded-observations.md
│       ├── 09a-calculate-moving-averages.md
│       ├── 09b-create-chart-indicators.md
│       ├── 09c-build-paper-trading.md
│       ├── 09d-record-profit-and-loss.md
│       ├── 10-add-decision-api.md
│       ├── 10a-ask-about-chart.md
│       ├── 11-add-agent-review.md
│       ├── 12-apply-reviewed-paper-decision.md
│       ├── 13-add-live-speech.md
│       ├── 14-automate-workstation-smoke-test.md
│       └── data/sample-candles.csv
└── handoffs/
    ├── create-handoff.md
    └── current.md
```

The source map omits generated `frontend/node_modules/`, `frontend/dist/`, and Python bytecode caches.

## Setup and run

Install dependencies from the project root with `python -m pip install -r backend/requirements.txt`, then run `npm install` from `frontend/` to restore the locked Vue packages.

Run each service in its own terminal:

```powershell
# Terminal 1: from the project root
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001

# Terminal 2: from frontend/
npm run dev -- --host 127.0.0.1 --port 5175
```

Open the Vue app at `http://127.0.0.1:5175/`. The backend health route is at `http://127.0.0.1:8001/api/health`; the same route through Vite is `http://127.0.0.1:5175/api/health`. Stop each server with Ctrl+C in its terminal. Ports 8000, 5173, and 5174 were unavailable during setup, so the verified commands use 8001 and 5175.

`GET http://127.0.0.1:8001/api/history` returns the synthetic one-minute sample candles copied from `prompts/prompts/data/sample-candles.csv` into `backend/data/sample-candles.csv`.

Build the frontend from `frontend/` with `npm run build`. The exact checks and observed results are in `docs/verification.md`.

## Chart workflow and SmokeTest

Open the dashboard to view AAPL at one minute by default. The timeframe selector also offers 5 minutes, 15 minutes, 1 hour, and Daily. Each switch receives a history snapshot and then updates for that interval on a new WebSocket. Hover the chart, use the candle-time selector, or select a table row to inspect OHLC and volume. The inspector shows whether the interval is forming and how many minutes have observations. The dashboard reports Yahoo Finance feed status separately from its FastAPI socket and shows the UTC time and age of the newest source observation.

The chart uses the 2026 Nasdaq regular-session calendar. It starts recording when FastAPI runs and builds history from the local store. An empty database starts with an empty chart. Unknown volume appears as **Unknown** with no bar. `GET /api/history?symbol=AAPL&interval=5m` returns the matching stored history. Open `/?source=sample` for the synthetic chart from `GET /api/history`. Set `INVEST_LIVE_FEED=0` before starting FastAPI to view previously stored data with the Yahoo connection disabled. `INVEST_OBSERVATIONS_DB` overrides the SQLite file.

From the project root, run `python scripts/smoke_test.py`. This starts both services on temporary free localhost ports, checks the known fixture through the frontend proxy, opens a browser, and asks you to perform the visible checks before entering PASS. It stops its own services on completion/failure. This is a **manual-assisted browser SmokeTest**, not the fully automated workstation test in Prompt 14. Agent mode is `python scripts/smoke_test.py --no-open --browser-result-file .smoke-browser-result.txt`: open the printed URL and write PASS to that fresh file only after actually observing the browser checks. The runner consumes the file and cleans up its services.

Run `python scripts/smoke_test.py --fixture` to check the live chart path with labeled constructed prices. The runner prints the temporary directory and the expected chart values. To trigger each step, create the named empty file in that directory, for example `New-Item -ItemType File -Path "<temporary-directory>/update"` in PowerShell. Run `python scripts/smoke_test.py --live` during the regular session to follow a real Yahoo quote. Both modes use a disposable database and let the tester disconnect the upstream feed while leaving the FastAPI socket connected.

For the UTC chart conversion checks, run `node --test frontend/src/chartData.test.js` from the root.

## Yahoo Finance subscription check

Run this command from the project root using the backend interpreter:

```powershell
python -m backend.yahoo_stream AAPL --timeout 30
```

The command reports whether the WebSocket opened, then records and prints the first matching price update or reports unverified receipt. It requires no Yahoo account or credentials. Yahoo's `time` field is interpreted as Unix milliseconds and also shown as a UTC date and time. The backend records its UTC receipt time when it reads the frame. For stocks, `day_volume` is the reported cumulative daily share count and `last_size` is the reported size of the latest trade in shares. The command includes those fields when present.

## SQLite observation store

The default database is `backend/data/market-observations.sqlite3`. Initialize it from the project root with `python -m backend.observations`. The receiver also initializes it when the first quote arrives. Both commands accept `--db PATH` for a different file.

Each row retains the full decoded Yahoo message, source, ticker, source time in Unix milliseconds, and backend receipt time in UTC. The recorder compares the full decoded payload after sorting its JSON keys. An exact repeat keeps the original row and its receipt time. Changed payloads at the same source time remain separate. When a provider identifies a revisable event, the changed row can link back to the previous version. The Yahoo receiver currently leaves that link empty because its price messages do not establish a correction.

Read one row per `id` to count each stored observation once. Use `ORDER BY received_at_utc, id` for receipt order or `ORDER BY source_time_ms, id` for market time. Run `python -m scripts.check_observation_storage` for the disposable storage check. It prints labeled test rows and verifies they persist after restarting the recorder.

## One-minute candles from observations

Run `python -m backend.candles AAPL` to read the default SQLite database and calculate candles for AAPL. Use `--db PATH` for a different database or `--as-of 2026-11-09T15:01:00Z` to use only observations received by that time. The command prints an empty list when the selected ticker has no observations.

Each result contains a six-field `candle` with the UTC minute start, open, high, low, close, and volume. The `ended` flag sits outside the candle. Late observations and linked corrections can update a candle after its minute ends. A `null` volume means the session counter did not provide enough information to calculate the minute's total.

Run `python -m scripts.check_minute_candles` for the disposable storage and calculation test and `python -m scripts.check_timeframes` for the higher-timeframe and calendar checks.

## Next setup decisions

Prompt 9 covers replaying recorded observations. The options and automation workflows can be defined when their source and execution requirements are known.
