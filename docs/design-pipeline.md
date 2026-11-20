# Design pipeline and data flow

## Manual image-reading exercise (Prompt 15)

The snapshot filters the workstation’s regular-session one-minute candles to the two required timestamps. The live path uses the observation query’s default row bounds. Replay applies its saved receipt cutoff and row bounds. The exercise uses gray for flat bodies and omits the horizontal price guide to make the candle body colors easier to read. The outbound evidence consists of the three timestamps and the tagged PNG.

The manual chart control uses the independent image-only flow in [chart-exercise.md](chart-exercise.md). Vue renders one-minute data through the two exact preceding minutes, labels 1 and 2, and tags the PNG. FastAPI sends the PNG and timestamp/freshness context only to OpenAI Decisions, then stores the result in `chart_exercises`. The chart clock is the replay receipt cutoff or the live server capture preparation time. Missing minutes or a live-minute rollover produce a local wait guard. This exercise never calls the simulator; the existing automation service retains its analyst/reviewer strategy. The visible chart/inspector now precedes the other workstation panels.

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

## Session timeframes and the dashboard (Prompts 7A–8)

```text
Yahoo Finance -> one AAPL subscription per FastAPI process -> SQLite
  -> effective UTC minutes -> Nasdaq regular-session buckets
  -> HTTP history or WebSocket snapshot and changes -> Vue chart, inspector, and table
```

FastAPI owns the subscription for the local dashboard. It stores valid observations before reporting receipt and retries after a timeout or transport failure. The browser socket carries the upstream status independently of its own connection state. Its first message is a history snapshot for the requested symbol and interval. Subsequent messages include changed candles, removed timestamps, the latest source timestamp, and feed status. A new snapshot on reconnect or interval change covers observations recorded while the browser was disconnected.

The history and WebSocket routes both call the same aggregation. One-, five-, fifteen-, and sixty-minute buckets align to the 9:30 a.m. Eastern session open. Daily spans the session and uses the open as its timestamp. The last hourly bucket may be short. The current calendar is limited to Nasdaq regular sessions in 2026 and includes published holidays and early closes. The IANA Eastern timezone handles daylight saving time. [Nasdaq equity rules](https://listingcenter.nasdaq.com/assets/RuleBook/Nasdaq/rules/Nasdaq%20Equity%201.html) · [Nasdaq 2026 calendar](https://nasdaqtrader.com/Trader.aspx?id=Calendar)

Each bucket uses the first open, maximum high, minimum low, and last close. The volume sums available minutes when each contributing minute has known volume. Coverage counts and the interval end flag travel beside the six candle fields. An ended interval can still change as late observations and corrections arrive. The source timestamp reports the latest effective observation, which can be outside the regular-session chart. The synthetic CSV remains available through the unparameterized history route and its labeled browser view.

## Recorded session replay (Prompt 9)

The Replay view lists symbols from SQLite and selects a UTC receipt range. The API reads one observation at a time in `received_at_utc, id` order and fixes the maximum selected row ID on the first step. Rows added later appear in a new run. Each step sends its original source timestamp, recorded receipt timestamp, and the chart as of that receipt and row ID. The one-minute and timeframe calculators are shared with live history. Late observations and revisions change their original candle only once their receipt is reached. Replay uses read-only SQLite connections and the browser never opens a live socket in Replay mode. With the live feed disabled, FastAPI opens no Yahoo connection and skips initialization of an existing store.

## Indicators and the paper account (Prompts 9A–9D)

Indicator calculations receive the current chart's ordered UTC candles with their completion flags and validated settings. A Python registry entry supplies the label, units, and a function over a trailing window of completed candles. SMA returns average close in price units, and rolling range returns maximum high minus minimum low in price units. The shared adapter returns a value or a gap at each chart timestamp, including on unfinished candles. The SMA pair compares exact rational averages and carries the prior side across ties. Vue draws the SMA lines and crossover markers and exposes values of saved indicators in the candle inspector. Additional named instances use the same registry and keep independent settings in SQLite.

The paper account runs in the observation database. Its live AAPL account and each replay run start at $1,000 with separate orders, fills, and valuations. One Python submission function handles buy, sell, close, hold, and wait. The request ID is unique within an account, and conflicting reuse is rejected. Live matching considers recorded observations after the order's receipt and row ID fence. Replay advances the account with the next server-selected observation, with the cursor, fills, balance, position, and fill P&L committed together. A retry at the same observation is idempotent. Plain replay remains read-only.

Cash and cost basis use decimal arithmetic. A sale deducts its proportional basis, and a full sale removes the remaining basis. Each fill writes a P&L snapshot. The first usable completed one-minute close after the latest account event supplies a valuation, with one saved row per completed candle and no rewrites of historical rows. The candle-end timestamp represents the close price time. The browser displays historical replay accounts separately from the live paper account.

## Completed-candle decisions (Prompt 10)

The server reads market evidence and the paper ledger in one SQLite read transaction. The selected bucket's end time is the receipt cutoff. Replay contributes its selected start, row ceiling, and cursor so a later receipt or revision cannot leak into the snapshot. The position and cash come from the last fill snapshot available by that cutoff, or the starting balance if there was no fill. The snapshot stores the exact candle and SMA series separately from the compact facts sent to the model. It has its own ID and a read endpoint for a reproducible chart.

The local Decisions API sends the frozen facts as JSON to the OpenAI Decisions API with the named choice `paper_action`. Python checks the returned choice against the saved cash, position, and order size. The explanation uses the recorded facts and validation result. SQLite keeps the provider/model, proposal, explanation, validation status, and snapshot reference under the returned local decision ID. Test evidence, missing inputs, provider errors, and account-rule failures never yield an eligible paper order. A valid proposal is evaluated using the snapshot price. Any future execution must recheck the current ledger and next fill price. [OpenAI Decisions API](https://developers.openai.com/api/docs/guides/decisions)

## Chart analysis and review (Prompts 10A and 11)

The dashboard submits the selected candle, interval, active SMA windows, and visible range start. Replay also submits its receipt cursor and account context. The server computes indicators using all eligible history and trims the exported chart to the frozen range through the selected candle. Vue draws that frozen data in a separate chart and uses Lightweight Charts' screenshot API to produce a chart-only PNG. A PNG text chunk carries the snapshot ID. Python checks PNG structure and image data and compares the tag to the saved snapshot before invoking a provider. The database stores the PNG bytes and the API exposes a readback URL. The snapshot tag binds the application's capture flow. The analyst's visual crossover is checked independently against Python's calculated signal. [Lightweight Charts screenshot API](https://tradingview.github.io/lightweight-charts/examples/data/screenshot)

One call per role uses the same saved facts. The chart analyst receives the image in the same message as those facts and returns three named choices. The reviewer receives the same image plus the parsed analyst answers and observed evidence. Each role has a 20-second transport timeout. Results retain each role's questions, named answers, provider, model, status, and response ID. The prototype strategy requires an upward SMA crossover for buy and a downward crossover for sell and close. Python checks strategy, account rules, visual agreement, evidence legibility, and reviewer acceptance before indicating eligibility. It records the result and PNG without placing orders. [OpenAI Decisions request format](https://developers.openai.com/api/reference/resources/decisions/methods/create)

## Paper automation (Prompt 12)

Vue identifies newly completed candles while a run is enabled. The manual and automatic chart paths share the frozen-chart renderer and PNG export. The automatic path claims the account, strategy, symbol, interval, and candle in SQLite before creating a snapshot or making a provider call. The claim survives stop and restart. The run freezes the timeframe and SMA windows and scopes replay to the chosen account and recorded observation range.

An automation job links the saved decision, simulator order, and fill. Python applies only a valid decision with readable matching chart evidence and an accepting reviewer. The account, symbol, candle, and timeframe must match. The simulator transaction links the outcome to the existing order, fill, cash, position, and P&L records. Stop and fill checks serialize in SQLite. Stop rejects queued automatic orders and retains a late analysis for inspection. The next-observation fill rules include a five-minute decision age limit and reject a received price whose market timestamp predates the cutoff. A backend restart disables existing runs before feed processing starts.

Constructed snapshots are ineligible in the default database. The fixture runner explicitly enables constructed execution only for a replay account in a separate database. Manual analysis has no execution path. The browser panel reads the saved run and can still show its most recent jobs after reload.

## Decision voice (Prompt 13)

The voice view loads a saved decision by ID. FastAPI reads the decision and its linked automation outcome from the same read-only SQLite transaction. The summary distinguishes the frozen account at the decision cutoff from the cash and shares in the fill's stored P&L event. Manual analyses and other decisions without a linked order or fill report that absence.

After the student starts voice, Vue requests the microphone, creates the WebRTC audio track and event channel, and sends the offer and decision ID to FastAPI. FastAPI builds the session instructions from the saved summary, exchanges SDP with GPT-Live using its environment credential, and returns the answer. The browser applies the answer, plays the remote track, and keeps the same session for follow-up questions. The provider receives the summary as context and has no tools for order execution or fresh market data. End voice stops capture immediately and closes the connection after the provider acknowledges or the timeout elapses. Disconnects and startup failures also release the track and peer.

## Workstation SmokeTest (Prompt 14)

The smoke runner starts the same Vue and FastAPI entry points used for development, with a new SQLite file and browser profile. SQLite stores the deterministic market observations through the normal recorder. The real replay route delivers one observation per browser step. The offline Decisions transport decodes the chart PNG at the outbound request boundary, checks the snapshot tag, symbol and time, and returns scripted answers or a provider error. The test compares the stored PNG digest with what each role received. The fixture routes exist only in the disposable test server.

Window edits update the existing saved indicator by ID. A new decision snapshot freezes the configured partial-sale size alongside the buy size. Earlier snapshots use their saved order size for either side. A close uses the remaining position at submission and verifies that quantity at the fill.

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

The Vue workspace has a Yahoo Finance dashboard, a labeled synthetic chart, recorded-session replay, configurable indicators, and simulated orders with P&L history. The Vite proxy carries HTTP and WebSocket traffic. Lightweight Charts 5.2.1 displays price with aligned volume, while the inspector and table show exact values. The Yahoo Finance receipt command and one-minute calculator remain available in the terminal. The workspace also supports saved chart analysis, paper automation, and GPT-Live discussion of one saved decision. Option chains, IV, and alerts remain future work. User-stated direction is in `docs/user_preferences.md`.

## Open design decisions

- Which live market-data and options/IV sources will be used?
- What strategy, execution scope, and order rules should the bot follow?
- What alert criteria, notification channels, and stop-loss/take-profit suggestion rules should be used?
- What IV history and option-chain layout should be implemented once actual data is available?

## Chart detail rendering (Prompt 17)

App.vue uses separate expansion state and v-if for crossover history and candle tables, avoiding their DOM/VNode loops while collapsed. Expanded lists scroll within bounded heights. A separate marker visibility prop is applied only to the main chart, default off there; CandleChart defaults it on for existing frozen capture callers. Hiding markers does not alter SMA lines, indicator calculations, numbered capture labels or execution logic.
