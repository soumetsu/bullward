export const UP_COLOR = '#148574'
export const DOWN_COLOR = '#c85b4f'
export const utcTime = (timestamp) => new Date(timestamp).toISOString().slice(11, 16)
export const utcDate = (timestamp) => new Intl.DateTimeFormat('en-US', {
  month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC',
}).format(new Date(timestamp))

export function toChartData(rows) {
  if (!Array.isArray(rows)) throw new Error('History must be a list of candles.')
  const candles = rows.map((row) => {
    const time = Date.parse(row.timestamp) / 1000
    if (!row.timestamp?.endsWith('Z') || !Number.isInteger(time) || time % 60 !== 0) throw new Error('Each candle must start on a UTC minute.')
    if (!['open', 'high', 'low', 'close', 'volume'].every((key) => Number.isFinite(row[key]))) throw new Error('Prices and volume must be numeric.')
    if (row.volume < 0 || row.low > Math.min(row.open, row.close) || row.high < Math.max(row.open, row.close)) throw new Error('History contains an invalid price range or volume.')
    return { ...row, time }
  }).sort((a, b) => a.time - b.time)
  if (candles.some((row, i) => i > 0 && row.time === candles[i - 1].time)) throw new Error('History contains duplicate timestamps.')
  return {
    candles,
    prices: candles.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })),
    volumes: candles.map(({ time, open, close, volume }) => ({ time, value: volume, color: close >= open ? UP_COLOR : DOWN_COLOR })),
  }
}
