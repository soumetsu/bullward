<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CandlestickSeries, HistogramSeries, createChart } from 'lightweight-charts'
import { DOWN_COLOR, UP_COLOR, toChartData, utcTime } from '../chartData'

const props = defineProps({ rows: { type: Array, required: true }, selected: { type: String, default: '' } })
const emit = defineEmits(['select'])
const host = ref(null)
let chart, priceSeries, volumeSeries
let byTime = new Map()
function showSelection(timestamp) {
  const row = byTime.get(Date.parse(timestamp) / 1000)
  if (row) chart?.setCrosshairPosition(row.close, row.time, priceSeries)
}
function resetView() { chart?.timeScale().fitContent() }
function load(newRows, oldRows) {
  if (!chart) return
  const { candles, prices, volumes } = toChartData(newRows)
  byTime = new Map(candles.map((row) => [row.time, row]))
  // A change at the latest timestamp can be applied without resetting the viewport.
  const tail = oldRows ? oldRows.length - 1 : -1
  const canUpdate = tail >= 0 && newRows.length >= oldRows.length &&
    newRows[tail].timestamp === oldRows[tail].timestamp &&
    oldRows.slice(0, tail).every((row, index) => row === newRows[index])
  if (canUpdate) {
    for (let index = tail; index < prices.length; index++) {
      priceSeries.update(prices[index])
      volumeSeries.update(volumes[index])
    }
  } else {
    priceSeries.setData(prices)
    volumeSeries.setData(volumes)
    if (!oldRows?.length) resetView()
  }
  showSelection(props.selected)
}
onMounted(() => {
  chart = createChart(host.value, {
    autoSize: true,
    layout: { background: { type: 'solid', color: '#ffffff' }, textColor: '#66736e', fontFamily: 'Segoe UI, sans-serif', fontSize: 12, attributionLogo: true,
      panes: { separatorColor: '#e3e9e5', separatorHoverColor: '#c7d9d0' } },
    grid: { vertLines: { color: '#f0f3f1' }, horzLines: { color: '#eef2ef' } },
    rightPriceScale: { borderColor: '#e3e9e5' },
    timeScale: { timeVisible: true, secondsVisible: false, borderColor: '#e3e9e5', tickMarkFormatter: (time) => utcTime(time * 1000) },
    localization: { locale: 'en-US', timeFormatter: (time) => new Date(time * 1000).toISOString().replace('T', ' ').replace('.000Z', ' UTC') },
  })
  priceSeries = chart.addSeries(CandlestickSeries, { upColor: UP_COLOR, downColor: DOWN_COLOR, borderVisible: false, wickUpColor: UP_COLOR, wickDownColor: DOWN_COLOR,
    priceFormat: { type: 'price', precision: 2, minMove: 0.01 } }, 0)
  volumeSeries = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false }, 1)
  chart.panes()[0].setStretchFactor(3)
  chart.panes()[1].setStretchFactor(1)
  const select = ({ time }) => { const row = byTime.get(time); if (row) emit('select', row.timestamp) }
  chart.subscribeCrosshairMove(select)
  chart.subscribeClick(select)
  load(props.rows)
})
watch(() => props.rows, load)
watch(() => props.selected, showSelection)
onBeforeUnmount(() => chart?.remove())
defineExpose({ resetView })
</script>

<template>
  <div ref="host" class="chart-canvas" role="img" aria-label="Candlesticks with aligned volume below. Use the candle inspector or data table for exact values."></div>
</template>
<style scoped>
.chart-canvas { width: 100%; height: 440px; }
@media (max-width: 650px) { .chart-canvas { height: 360px; } }
</style>
