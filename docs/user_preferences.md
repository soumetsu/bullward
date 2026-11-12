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

- Keep the interface clean and easy to inspect while retaining useful trading context.
- Make trading volume visible alongside charts.
- Make implied volatility (IV), changes in IV, and historical IV context visible and easy to compare. Avoid requiring a search or showing only an isolated current number.
- Provide a clear place to view charts, indicators, and option chains.
- Support automatically suggested notifications or alerts for price changes and movements.
- Suggest stop-loss and take-profit levels where applicable, in the order screen or another appropriate place.

## Details still to be decided

The owner has not yet specified the bot's strategy, brokerage/execution scope, alert thresholds or channels, stop-loss/take-profit calculation rules, options data provider, IV display format, or visual theme. Do not present implementation assumptions as user preferences.

## Chat decision record

- Initial interview: investing/trading tool for personal and broader use; AI assistance, possible automation, options trading; Python/FastAPI/Vue; future reference images and prompts.
- Prompt 4 follow-up: explicit bot, chart/indicator/option-chain workspace, advising and Decisions API, suggested price alerts and stop-loss/take-profit levels, and visible volume/IV/change/history context. Create this preference record and maintain it only from explicit choices typed in this chat. Complete Prompt 4 and stop after it.
