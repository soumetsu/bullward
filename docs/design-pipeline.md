# Design pipeline and data flow

## Current chart flow (Prompt 4)

```text
backend/data/sample-candles.csv (synthetic UTC minutes)
  -> Python csv reader -> GET /api/history (six fields)
  -> Vite /api proxy -> Vue App.vue (loading/error/empty states)
  -> chartData.js (validate, sort, UTC ISO -> Unix seconds)
       +-> candlestick series (price pane)
       +-> histogram series (volume pane, same time scale)
  -> CandleChart.vue (hover/click) <-> inspector and candle table
```

The chart uses a shared time scale for both panes, preserves the API candle timestamps, and displays all dates/times in UTC. Candle selection is also available through keyboard-accessible controls. Chart instances are removed on unmount, pending requests are aborted on cleanup, and chart sizing follows its container. A light neutral surface and restrained green/red chart colors are implementation choices, not user-selected theme preferences.

## Yahoo Finance receiver and SQLite recorder (Prompts 5–6)

```text
Yahoo Finance stream -> yfinance AsyncWebSocket (one ticker)
  -> backend/yahoo_stream.py (first price update, source and receipt times)
  -> backend/observations.py -> local SQLite observations table
  -> terminal output with receipt and storage status
```

The command runs separately from FastAPI. It uses the pinned yfinance client's socket and decoder after subscribing so it can end on a clean remote close and enforce a read deadline. The AAPL subscription returned a price update in the live check. Yahoo's reported cumulative daily volume stays separate from its last trade size and from the synthetic candle volumes.

The recorder stores the decoded payload as JSON with its original field names and values. The unique key combines source, optional provider event ID, and the canonical JSON. Receipt time is excluded from the unique key so resending the same payload preserves the first receipt. When an event ID establishes a revision, the new row links to the previous row via `revision_of_id`. Yahoo's receiver does not infer a correction from matching timestamps, so distinct same-time messages keep separate IDs. Source time and receipt time are stored independently for late arrivals, with the row ID available to break ties.

## One-minute candle calculation (Prompt 7)

```text
SQLite observations for one ticker
  -> rows received by the requested time, with known corrections applied
  -> source time order -> UTC minute groups -> OHLC and volume
  -> six-field candle plus separate ended flag
```

`backend/candles.py` reads the store in read-only mode and recalculates on demand. It buckets observations by UTC source minute and sorts by source time, with receipt time and row ID breaking ties. Open and close use the first and last prices. High and low use the extremes. Empty minutes produce no row. The separate `ended` flag turns true at the next minute boundary.

The read includes observations received by the requested time. A late observation is inserted into source-time order on the next calculation and can change an ended candle. A known revision supersedes its linked row as of the revision's receipt. Both versions remain in SQLite. Distinct observations with the same source time stay in the calculation unless a known revision links them.

Volume uses changes in `day_volume` between successive effective observations, assigning each delta to the later observation's minute. An observation in the prior minute can provide the opening baseline. A missing baseline or counter, an intervening empty minute, or a counter decrease makes the affected minute's volume unknown (`null`). A subsequent minute can recover when a valid baseline is available. A reset is observable when the counter drops. Sparse observations may miss a reset that happens between samples.

## Future integration flow

```text
                         (planned)
Trader  <---- input and visible results ---->  Vue browser UI
                                                   |
                                      JSON over local HTTP
                                                   v
                                           FastAPI routes
                                                   |
                                                   v
                                  Python application services
                                  /          |             \
                                 v           v              v
                         yfinance       TradingView     Webull API
                         candidate      candidate       possible
                                 \           |              /
                                  +---- normalized results -+
                                                   |
                                                   v
                                 FastAPI JSON response -> Vue UI
```

AI assistance, options workflows, and automation may be added to Python application services after their user workflows and boundaries are defined. The diagram shows candidates, not current integrations.

## File and component responsibilities

- `frontend/`: Vue app, user inputs, and presentation of research or workflow results. The Vite development server proxies `/api` to FastAPI.
- `backend/`: Python application logic, sample history, and FastAPI routes.
- FastAPI: HTTP/JSON boundary between the browser and backend logic.
- `docs/`: setup, architecture, verification records, and decisions.
- `prompts/`: major prompts used to guide project work.
- `handoffs/`: current state and instructions for resuming work.

## What exists today

The Vue chart workspace, Vite `/api` proxy, FastAPI health/history routes, CSV data, and dependencies are implemented. Lightweight Charts 5.2.1 draws three synthetic candles and their volume. The inspector and table show exact values, and the footer includes TradingView's attribution notice and links. The Yahoo Finance command receives one update and records it in SQLite. The backend can derive one-minute candles from those observations in the terminal. Live chart updates, indicators, AI advising, automation, option chains, IV, and alert/order suggestions remain future work. User-stated direction for those features is in `docs/user_preferences.md`.

## Open design decisions

- Which live market-data and options/IV sources will be used?
- What strategy, execution scope, and order rules should the bot follow?
- What alert criteria, notification channels, and stop-loss/take-profit suggestion rules should be used?
- What IV history and option-chain layout should be implemented once actual data is available?
