import { test } from 'node:test'
import assert from 'node:assert/strict'
import { toChartData } from './chartData.js'

const rows = [
  { timestamp: '2026-11-09T14:30:00Z', open: 100, high: 103, low: 99, close: 102, volume: 1200 },
  { timestamp: '2026-11-09T14:31:00Z', open: 102, high: 104, low: 100, close: 101, volume: 850 },
]
test('UTC minutes become distinct, aligned second timestamps in both chart series', () => {
  const { prices, volumes } = toChartData([...rows].reverse())
  assert.equal(prices[1].time - prices[0].time, 60)
  assert.equal(new Date(prices[1].time * 1000).toISOString(), '2026-11-09T14:31:00.000Z')
  assert.deepEqual(prices.map((r) => r.time), volumes.map((r) => r.time))
  assert.equal(prices[1].close, 101)
  assert.equal(volumes[1].value, 850)
})
test('reject duplicate minutes, ambiguous timezone, and malformed values', () => {
  assert.throws(() => toChartData([rows[0], rows[0]]), /duplicate/)
  assert.throws(() => toChartData([{ ...rows[0], timestamp: '2026-11-09T14:30:00' }]), /UTC/)
  assert.throws(() => toChartData([{ ...rows[0], volume: '1200' }]), /numeric/)
})
