<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import CandleChart from './components/CandleChart.vue'
import IndicatorControls from './components/IndicatorControls.vue'
import PaperAccount from './components/PaperAccount.vue'
import ReplayControls from './components/ReplayControls.vue'
import { toChartData, utcDate, utcTime } from './chartData'
import './style.css'

const rows = ref([])
const selectedTime = ref('')
const loading = ref(true)
const error = ref('')
const chart = ref(null)
const mode = new URLSearchParams(location.search).get('source')
const sample = mode === 'sample'
const replay = mode === 'replay'
const paper = mode === 'paper'
const symbol = 'AAPL'
const intervals = { '1m': '1 minute', '5m': '5 minutes', '15m': '15 minutes', '1h': '1 hour', '1d': 'Daily' }
const interval = ref('1m')
const source = ref('Yahoo Finance')
const sourceTime = ref(null)
const now = ref(Date.now())
const feedStatus = ref('connecting')
const socketStatus = ref('Connecting')
const indicators = ref({ series: [], crossovers: [] })
const feedLabels = {
  connecting: 'Connecting to Yahoo Finance',
  waiting: 'Waiting for a price',
  live: 'Receiving updates',
  disconnected: 'Upstream disconnected · retrying',
  disabled: 'Feed disabled',
}
let controller
let socket, retry, clock
const selected = computed(() => rows.value.find((row) => row.timestamp === selectedTime.value))
const latest = computed(() => rows.value.at(-1))
const selectedIndicators = computed(() => indicators.value.series.map((item) => ({
  ...item, value: item.points.find((point) => point.timestamp === selectedTime.value)?.value ?? null,
})))
const sourceAge = computed(() => {
  if (sourceTime.value === null) return 'No observations'
  const seconds = Math.max(0, Math.floor((now.value - sourceTime.value) / 1000))
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.floor(seconds / 60)
  return minutes < 60 ? `${minutes}m ago` : `${Math.floor(minutes / 60)}h ${minutes % 60}m ago`
})
const price = (value) => value.toFixed(2)
const count = (value) => value === null ? 'Unknown' : value.toLocaleString('en-US')

function setRows(nextRows) {
  const followLatest = !selectedTime.value || selectedTime.value === latest.value?.timestamp
  rows.value = nextRows
  if (followLatest || !selected.value) selectedTime.value = latest.value?.timestamp ?? ''
}

async function loadSample() {
  controller?.abort()
  const request = new AbortController()
  controller = request
  loading.value = true
  error.value = ''
  rows.value = []
  try {
    const response = await fetch('/api/history', { signal: request.signal, cache: 'no-store' })
    if (!response.ok) throw new Error(`History request failed (HTTP ${response.status}).`)
    const data = await response.json()
    if (controller === request) setRows(toChartData(data).candles)
  } catch (cause) {
    if (controller === request && cause.name !== 'AbortError') error.value = `${cause.message} Please try again.`
  } finally {
    if (controller === request) loading.value = false
  }
}

function connect(clearHistory = false) {
  clearTimeout(retry)
  socket?.close()
  if (clearHistory) setRows([])
  loading.value = !rows.value.length
  error.value = ''
  socketStatus.value = 'Connecting'
  const url = new URL('/api/candles/live', location.href)
  url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
  url.search = new URLSearchParams({ symbol, interval: interval.value })
  const connection = new WebSocket(url)
  socket = connection
  connection.onopen = () => {
    if (socket === connection) socketStatus.value = 'Connected'
  }
  connection.onmessage = ({ data }) => {
    if (socket !== connection) return
    const message = JSON.parse(data)
    if (message.symbol !== symbol || message.interval !== interval.value) return
    source.value = message.source
    feedStatus.value = message.feed_status
    sourceTime.value = message.latest_source_ms
    if (message.type === 'snapshot' || message.candles.length || message.removed.length) {
      const merged = new Map(message.type === 'snapshot' ? [] : rows.value.map((row) => [row.timestamp, row]))
      for (const timestamp of message.removed) merged.delete(timestamp)
      for (const { candle, ...status } of message.candles) merged.set(candle.timestamp, { ...candle, ...status })
      setRows([...merged.values()].sort((a, b) => a.timestamp.localeCompare(b.timestamp)))
    }
    loading.value = false
    error.value = ''
  }
  connection.onclose = () => {
    if (socket !== connection) return
    socketStatus.value = 'Disconnected · retrying'
    loading.value = false
    if (!rows.value.length) error.value = 'The dashboard lost its connection to FastAPI.'
    retry = setTimeout(connect, 3000)
  }
}

function loadHistory() {
  if (sample) loadSample()
  else connect()
}

onMounted(() => {
  if (replay || paper) {
    loading.value = false
    return
  }
  loadHistory()
  if (!sample) clock = setInterval(() => { now.value = Date.now() }, 1000)
})
onBeforeUnmount(() => {
  controller?.abort()
  clearTimeout(retry)
  clearInterval(clock)
  const connection = socket
  socket = undefined
  connection?.close()
})
</script>

<template>
  <header class="app-header">
    <a class="brand" href="/" aria-label="Project 4 Invest home"><span class="brand-mark">I↗</span> Project 4 <strong>Invest</strong></a>
    <nav aria-label="Workspace"><a href="/">Live chart</a><a href="/?source=replay" :aria-current="replay ? 'page' : undefined">Replay</a><a href="/?source=paper" :aria-current="paper ? 'page' : undefined">Paper account</a></nav>
  </header>
  <main v-if="paper"><PaperAccount /></main>
  <main v-else>
    <div class="page-heading">
      <div><p class="eyebrow">MARKET RESEARCH</p><h1>{{ replay ? 'Replay' : 'Price & volume' }}</h1></div>
      <span v-if="sample" class="sample-badge"><span></span> Synthetic one-minute sample</span>
      <div v-else class="chart-actions">
        <label for="timeframe">Timeframe</label>
        <select id="timeframe" v-model="interval" @change="!replay && connect(true)">
          <option v-for="(label, value) in intervals" :key="value" :value="value">{{ label }}</option>
        </select>
      </div>
    </div>
    <ReplayControls v-if="replay" :interval="interval" @frame="setRows($event.map(({ candle, ...status }) => ({ ...candle, ...status })))" />
    <IndicatorControls :rows="rows" :sample="sample" @result="indicators = $event" />
    <section v-if="!sample && !replay" class="summary-grid" aria-label="Market feed">
      <div class="summary-card"><span class="label">Instrument</span><strong>{{ symbol }}</strong><span class="muted">{{ intervals[interval] }} · {{ source }}</span></div>
      <div class="summary-card"><span class="label">Latest source observation</span><strong class="numeric">{{ sourceAge }}</strong><span v-if="sourceTime !== null" class="muted">{{ new Date(sourceTime).toISOString() }}</span></div>
      <div class="summary-card"><span class="label">Upstream feed</span><strong role="status">{{ socketStatus === 'Connected' ? feedLabels[feedStatus] : 'Status unavailable' }}</strong><span class="muted">Dashboard socket · {{ socketStatus }}</span></div>
    </section>
    <section v-if="loading" class="notice" role="status">Loading candles…</section>
    <section v-else-if="error" class="notice error" role="alert"><h2>Unable to load history</h2><p>{{ error }}</p><button @click="loadHistory">Retry</button></section>
    <section v-else-if="!rows.length && !replay" class="notice" role="status"><h2>No candles available</h2><p>{{ sample ? 'The sample is empty.' : 'No regular-session candles have been recorded for this instrument.' }}</p><button @click="loadHistory">Reload history</button></section>
    <template v-else-if="rows.length">
      <div class="analysis-grid">
        <section class="panel chart-panel" aria-labelledby="chart-title">
          <div class="panel-heading"><div><h2 id="chart-title">{{ replay ? 'Replay candlestick chart' : 'Candlestick chart' }}</h2><p>{{ !sample && !latest.ended ? 'Latest candle forming' : 'Price above · volume below' }}</p></div><div class="chart-actions"><span class="interval">{{ intervals[interval] }} · UTC</span><button class="quiet" @click="chart?.resetView()">Reset view</button></div></div>
          <CandleChart ref="chart" :rows="rows" :selected="selectedTime" :indicators="indicators" @select="selectedTime = $event" />
          <div class="chart-footer"><span>Hover or select a candle to inspect its values.</span><span class="legend"><i class="up"></i> Up <i class="down"></i> Down</span></div>
          <div v-if="indicators.series.length" class="indicator-legend">
            <span class="sma-short">{{ indicators.series[0].name }}</span>
            <span class="sma-long">{{ indicators.series[1].name }}</span>
            <span v-if="indicators.crossovers.length">Crossovers</span>
            <button v-for="signal in indicators.crossovers" :key="signal.timestamp" class="quiet"
                    @click="selectedTime = signal.timestamp">
              {{ signal.direction === 'up' ? 'SMA crossed up' : 'SMA crossed down' }} · {{ utcDate(signal.timestamp) }} {{ utcTime(signal.timestamp) }} UTC
            </button>
          </div>
        </section>
        <aside v-if="selected" class="panel inspector" aria-labelledby="inspector-title">
          <p class="eyebrow">CANDLE INSPECTOR</p><h2 id="inspector-title">{{ utcDate(selected.timestamp) }}</h2>
          <label for="candle-time">Candle start · UTC</label>
          <select id="candle-time" v-model="selectedTime"><option v-for="row in rows" :key="row.timestamp" :value="row.timestamp">{{ sample ? '' : utcDate(row.timestamp) + ' · ' }}{{ utcTime(row.timestamp) }} UTC</option></select>
          <dl class="price-grid"><div v-for="field in ['open', 'high', 'low', 'close']" :key="field"><dt>{{ field }}</dt><dd :data-testid="`selected-${field}`">{{ price(selected[field]) }}</dd></div></dl>
          <div class="volume-detail"><span class="label">Candle volume</span><strong data-testid="selected-volume">{{ count(selected.volume) }} <small v-if="selected.volume !== null">shares</small></strong></div>
          <p v-if="!sample" class="inspector-note">{{ selected.ended ? 'Interval ended' : 'Forming' }} · {{ selected.observed_minutes }} of {{ selected.expected_minutes }} minutes observed</p>
          <dl v-if="selectedIndicators.length" class="indicator-values">
            <div v-for="item in selectedIndicators" :key="item.id">
              <dt>{{ item.name }}</dt><dd>{{ item.value === null ? 'Unavailable' : price(item.value) }}</dd>
            </div>
          </dl>
        </aside>
      </div>
      <section class="panel data-panel" aria-labelledby="data-title">
        <div class="panel-heading"><h2 id="data-title">Candle data</h2><button v-if="!replay" class="quiet" @click="loadHistory">Refresh data</button></div>
        <div class="table-scroll"><table><thead><tr><th scope="col">Start (UTC)</th><th scope="col">Open</th><th scope="col">High</th><th scope="col">Low</th><th scope="col">Close</th><th scope="col">Volume (shares)</th></tr></thead><tbody><tr v-for="row in rows" :key="row.timestamp" :class="{ selected: selectedTime === row.timestamp }"><th scope="row"><button class="time-button" :aria-pressed="selectedTime === row.timestamp" @click="selectedTime = row.timestamp">{{ sample ? '' : utcDate(row.timestamp) + ' · ' }}{{ utcTime(row.timestamp) }}</button></th><td>{{ price(row.open) }}</td><td>{{ price(row.high) }}</td><td>{{ price(row.low) }}</td><td>{{ price(row.close) }}</td><td>{{ count(row.volume) }}</td></tr></tbody></table></div>
      </section>
    </template>
    <footer class="attribution">TradingView Lightweight Charts™<br>Copyright (с) 2025 TradingView, Inc. <a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">TradingView</a> · <a href="https://tradingview.github.io/lightweight-charts/docs" target="_blank" rel="noopener noreferrer">Chart documentation</a></footer>
  </main>
</template>
