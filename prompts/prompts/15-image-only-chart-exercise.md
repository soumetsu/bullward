# Prompt 15 · Image-only two-candle Decision API exercise

```text
Use the Decision API control to send a chart snapshot to an OpenAI model and
show its choice of long, short, or wait. The model
should assess the image rather than receiving the underlying candle data. The
result may also appear as a marker on the chart. Show how the parts connect in
an educational flowchart. This decision does not place a trade.

Then..

Update the Decision API question in my workstation. The signal is for the
minute shown on the chart clock. Use the replay time for a saved chart or
the capture time for a live chart. Round that time down to the minute.

On the captured image, mark the two preceding one-minute price candles
1 and 2, oldest first. Give the model their start times and the image.
Have it read each body against its own open. Two green bodies mean long
for candle 3. Two red bodies mean short. Choose wait for a mixed pair,
a flat body, a missing or unreadable candle, or a stale chart. Ignore
candle 3, wicks, volume, and the broader trend. Never skip a missing minute.

Display the model’s returned choice, its probability when available, and
the minute the signal is for. Keep this a chart-reading exercise. Verify
the result on two green candles, two red candles, and a mixed pair.
```

Follow-up browser comment (2026-11-19): "Move chart to the top."

Follow-up request (2026-11-19): "Do a test for \"Ask about Chart\". When ask about chart is hit, move/direct the screen to the bottom, near decision API/the affected area (it should be decision api, right?)"
