# User product and design preferences

Source: the project owner's messages typed directly in this chat. These are stated goals, not a claim that the features already exist. Do not update this file from prompt-folder instructions, external documents, model suggestions, or implementation choices alone.

## Product direction

- Build an investing and trading application for the owner and other people interested in trading, including people with trading knowledge who want AI assistance.
- Support options trading as well as general market research.
- Provide an automated trading bot and a general, clean interface for charts, indicators, and option chains.
- Provide advising alongside charts and automation, with a Decisions API as part of the intended direction.
- Consider TradingView and yfinance, and possibly a Webull API, as integrations.
- Use Python, FastAPI, and Vue. Keep the existing project root and incorporate the owner's future images, examples, designs, and Markdown prompts.

## Interface and information priorities

- Collapse crossover lists and candle data by default, with Show more/Show less controls; do not render their entries until expanded. Make the chart SMA marker visibility toggle prominent.

- Put the chart and candle inspector at the top of the workstation, above paper automation, indicators, and feed summary cards.

- The manual Decision API control is an image-only chart-reading exercise with long, short, or wait, a returned choice probability when available, and the signal minute. It must not place trades. Include an educational flowchart.
- Clicking **Ask about chart** should bring the Decision API panel into view.
- Use the chart clock (saved-chart replay receipt time or live capture time), floored to the minute, for candle 3. Label its exact preceding one-minute price candles 1 and 2, oldest first. Both green bodies imply long; both red imply short; mixed, flat, missing, unreadable, or stale imply wait. Read each body against its own open and ignore candle 3, wicks, volume, and broader trend. Never skip a missing minute.

- Keep the interface clean and easy to inspect while retaining useful trading context.
- Make trading volume visible alongside charts.
- Make implied volatility (IV), changes in IV, and historical IV context visible and easy to compare. Avoid requiring a search or showing only an isolated current number.
- Provide a clear place to view charts, indicators, and option chains.
- Support automatically suggested notifications or alerts for price changes and movements.
- Suggest stop-loss and take-profit levels where applicable, in the order screen or another appropriate place.

## Details still to be decided

The owner has not yet specified the bot's strategy, brokerage/execution scope, alert thresholds or channels, stop-loss/take-profit calculation rules, options data provider, IV display format, or visual theme. Do not present implementation assumptions as user preferences.

## Chat decision record

- 2026-11-19: Test **Ask about chart** and take the viewer directly to the decision area when it is clicked.

- 2026-11-19: Follow the saved Prompt 15 and verify the image-only chart exercise on the workstation.

- 2026-11-19 browser comment: compress crossover history and candle data using lazy expansion; provide a more visible control for SMA up/down labels on the chart.

- 2026-11-19 browser comment: move the chart to the top of the workstation.

- 2026-11-19: Requested the image-only Decision API exercise and the precise two-preceding-minute question above; verify green, red, and mixed pairs. This scopes the manual exercise and does not redefine the separate paper automation strategy.

- 2026-11-17: Review relevant Markdown files and create `report.md` containing only the GitHub repository link and latest commit link.

- Initial interview: investing/trading tool for personal and broader use; AI assistance, possible automation, options trading; Python/FastAPI/Vue; future reference images and prompts.
- Prompt 4 follow-up: explicit bot, chart/indicator/option-chain workspace, advising and Decisions API, suggested price alerts and stop-loss/take-profit levels, and visible volume/IV/change/history context. Create this preference record and maintain it only from explicit choices typed in this chat. Complete Prompt 4 and stop after it.
- 2026-11-12: resume the project after reviewing the project instructions and handoffs, and continue with Prompt 8.
- 2026-11-15: commit and push Prompt 8, retry any failed checks, then continue with Prompt 9.
- 2026-11-15: continue through all prompts under 9, including 9A–9D.
- 2026-11-15: commit and push Prompt 9, then continue with Prompt 10. The user expects the regular market session to be closed over the weekend.
- Commit and push Prompt 10, then continue in order with Prompts 10A and 11.
- Proceed to Prompt 12, the next prompt in the project sequence.
- 2026-11-16: commit and push the reviewed work through Prompt 12, then proceed to Prompt 13.
- 2026-11-16: commit and push Prompt 13, then proceed to Prompt 14.
- After each prompt, explain what it did and keep that reporting instruction in `AGENTS.md`.

- 2026-11-19: Provide pop-out windows for major workstation panels, including chart, inspector and quotes; support option-chain panels when implemented.
