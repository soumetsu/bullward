<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { CandlestickSeries, HistogramSeries, LineSeries, createChart, createSeriesMarkers } from 'lightweight-charts'
import { DOWN_COLOR, UP_COLOR, toChartData, utcTime } from '../chartData'

const props = defineProps({
  rows: { type: Array, required: true },
  selected: { type: String, default: '' },
  indicators: { type: Object, required: true },
  labels: { type: Array, default: () => [] },
  showSmaMarkers: { type: Boolean, default: true },
  flatColor: { type: String, default: '' },
  showPriceLine: { type: Boolean, default: true },
})
const emit = defineEmits(['select', 'hover'])
const host = ref(null)
let chart, priceSeries, volumeSeries
let markers
const lines = new Map()
let byTime = new Map()
function showSelection(timestamp) {
  const row = byTime.get(Date.parse(timestamp) / 1000)
  if (row) chart?.setCrosshairPosition(row.close, row.time, priceSeries)
}
function resetView() { chart?.timeScale().fitContent() }
function visibleRange() { return chart?.timeScale().getVisibleRange() }
function screenshot() { return chart.takeScreenshot(true, false) }
function showIndicators() {
  if (!chart) return
  const active = new Set()
  for (const item of props.indicators.series) {
    if (item.type !== 'sma') continue
    active.add(item.id)
    if (!lines.has(item.id)) {
      const color = item.id === 'short' ? '#385bbc' : item.id === 'long' ? '#ad5920' : '#7052a8'
      lines.set(item.id, chart.addSeries(LineSeries, {
        color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
      }, 0))
    }
    lines.get(item.id).setData(item.points.map(({ timestamp, value }) => ({
      time: Date.parse(timestamp) / 1000, ...(value === null ? {} : { value }),
    })))
  }
  for (const [id, line] of lines) {
    if (!active.has(id)) {
      chart.removeSeries(line)
      lines.delete(id)
    }
  }
  markers.setMarkers([...(props.showSmaMarkers ? props.indicators.crossovers : []).map(({ timestamp, direction }) => ({
    time: Date.parse(timestamp) / 1000,
    position: direction === 'up' ? 'belowBar' : 'aboveBar',
    shape: direction === 'up' ? 'arrowUp' : 'arrowDown',
    color: direction === 'up' ? UP_COLOR : DOWN_COLOR,
    text: direction === 'up' ? 'SMA up' : 'SMA down',
  })), ...props.labels.filter((label) => byTime.has(Date.parse(label.timestamp) / 1000)).map((label) => ({
    time: Date.parse(label.timestamp) / 1000, position: 'aboveBar', shape: 'circle', color: '#182b42', text: label.text,
  }))].sort((a, b) => a.time - b.time))
}
function load(newRows, oldRows) {
  if (!chart) return
  const { candles, prices, volumes } = toChartData(newRows)
  if (props.flatColor) {
    for (const price of prices) {
      if (price.open === price.close) price.color = props.flatColor
    }
  }
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
    priceLineVisible: props.showPriceLine,
    priceFormat: { type: 'price', precision: 2, minMove: 0.01 } }, 0)
  markers = createSeriesMarkers(priceSeries)
  volumeSeries = chart.addSeries(HistogramSeries, { priceFormat: { type: 'volume' }, lastValueVisible: false, priceLineVisible: false }, 1)
  chart.panes()[0].setStretchFactor(3)
  chart.panes()[1].setStretchFactor(1)
  function inspect(event, { time }) {
    const row = byTime.get(time)
    if (row) emit(event, row.timestamp)
  }
  chart.subscribeCrosshairMove((point) => inspect('hover', point))
  chart.subscribeClick((point) => inspect('select', point))
  load(props.rows)
  showIndicators()
})
watch(() => props.rows, load)
watch(() => props.selected, showSelection)
watch(() => props.indicators, showIndicators)
watch(() => props.showSmaMarkers, showIndicators)
onBeforeUnmount(() => chart?.remove())
defineExpose({ resetView, visibleRange, screenshot })
</script>

<template>
  <div ref="host" class="chart-canvas" role="img" aria-label="Candlesticks with aligned volume below. Use the candle inspector or data table for exact values."></div>
</template>
<style scoped>
.chart-canvas { width: 100%; height: 440px; }
@media (max-width: 650px) { .chart-canvas { height: 360px; } }
</style>
