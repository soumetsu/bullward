<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import CandleChart from './components/CandleChart.vue'
import { toChartData, utcDate, utcTime } from './chartData'
import './style.css'

const rows = ref([])
const selectedTime = ref('')
const loading = ref(true)
const error = ref('')
const chart = ref(null)
let controller
const selected = computed(() => rows.value.find((row) => row.timestamp === selectedTime.value))
const latest = computed(() => rows.value.at(-1))
const totalVolume = computed(() => rows.value.reduce((sum, row) => sum + row.volume, 0))
const price = (value) => value.toFixed(2)
const count = (value) => value.toLocaleString('en-US')
async function loadHistory() {
  controller?.abort()
  const request = new AbortController()
  controller = request
  loading.value = true
  error.value = ''
  rows.value = []
  try {
    const response = await fetch('/api/history', { signal: request.signal, cache: 'no-store' })
    if (!response.ok) throw new Error(`History request failed (HTTP ${response.status}).`)
    rows.value = toChartData(await response.json()).candles
    selectedTime.value = rows.value.at(-1)?.timestamp ?? ''
  } catch (cause) {
    if (cause.name !== 'AbortError') error.value = `${cause.message} Please try again.`
  } finally {
    if (controller === request) loading.value = false
  }
}
onMounted(loadHistory)
onBeforeUnmount(() => controller?.abort())
</script>

<template>
  <header class="app-header">
    <a class="brand" href="/" aria-label="Project 4 Invest home"><span class="brand-mark">I↗</span> Project 4 <strong>Invest</strong></a>
    <span class="workspace-label">RESEARCH WORKSPACE</span>
  </header>
  <main>
    <div class="page-heading">
      <div><p class="eyebrow">MARKET RESEARCH</p><h1>Price &amp; volume</h1><p class="intro">A clear view of every candle, with the volume behind it.</p></div>
      <span class="sample-badge"><span></span> Synthetic one-minute sample</span>
    </div>
    <section v-if="loading" class="notice" role="status">Loading sample candles…</section>
    <section v-else-if="error" class="notice error" role="alert"><h2>Unable to load history</h2><p>{{ error }}</p><button @click="loadHistory">Retry</button></section>
    <section v-else-if="!rows.length" class="notice" role="status"><h2>No candles available</h2><p>The source has no history to display.</p><button @click="loadHistory">Reload history</button></section>
    <template v-else>
      <section class="summary-grid" aria-label="Sample overview">
        <div class="summary-card"><span class="label">Instrument</span><strong>Sample instrument</strong><span class="muted">Synthetic prices · no live feed</span></div>
        <div class="summary-card"><span class="label">Latest sample close</span><strong class="numeric">{{ price(latest.close) }}</strong><span class="muted">{{ utcTime(latest.timestamp) }} UTC · {{ utcDate(latest.timestamp) }}</span></div>
        <div class="summary-card"><span class="label">Sample volume</span><strong class="numeric">{{ count(totalVolume) }} <small>shares</small></strong><span class="muted">Across {{ rows.length }} one-minute candles</span></div>
      </section>
      <div class="analysis-grid">
        <section class="panel chart-panel" aria-labelledby="chart-title">
          <div class="panel-heading"><div><h2 id="chart-title">Candlestick chart</h2><p>Price above · volume below</p></div><div class="chart-actions"><span class="interval">1 minute · UTC</span><button class="quiet" @click="chart?.resetView()">Reset view</button></div></div>
          <CandleChart ref="chart" :rows="rows" :selected="selectedTime" @select="selectedTime = $event" />
          <div class="chart-footer"><span>Hover or select a candle to inspect its values.</span><span class="legend"><i class="up"></i> Up <i class="down"></i> Down</span></div>
        </section>
        <aside class="panel inspector" aria-labelledby="inspector-title">
          <p class="eyebrow">CANDLE INSPECTOR</p><h2 id="inspector-title">{{ utcDate(selected.timestamp) }}</h2>
          <label for="candle-time">Candle start · UTC</label>
          <select id="candle-time" v-model="selectedTime"><option v-for="row in rows" :key="row.timestamp" :value="row.timestamp">{{ utcTime(row.timestamp) }} UTC</option></select>
          <dl class="price-grid"><div v-for="field in ['open', 'high', 'low', 'close']" :key="field"><dt>{{ field }}</dt><dd :data-testid="`selected-${field}`">{{ price(selected[field]) }}</dd></div></dl>
          <div class="volume-detail"><span class="label">Volume in this minute</span><strong data-testid="selected-volume">{{ count(selected.volume) }} <small>shares</small></strong><p>{{ ((selected.volume / totalVolume) * 100 || 0).toFixed(1) }}% of the loaded sample volume</p></div>
          <p class="inspector-note">Each timestamp marks the start of a one-minute interval. All times are UTC.</p>
        </aside>
      </div>
      <section class="panel data-panel" aria-labelledby="data-title">
        <div class="panel-heading"><div><h2 id="data-title">Candle data</h2><p>{{ utcDate(latest.timestamp) }} · synthetic sample</p></div><button class="quiet" @click="loadHistory">Refresh data</button></div>
        <div class="table-scroll"><table><thead><tr><th scope="col">Start (UTC)</th><th scope="col">Open</th><th scope="col">High</th><th scope="col">Low</th><th scope="col">Close</th><th scope="col">Volume (shares)</th></tr></thead><tbody><tr v-for="row in rows" :key="row.timestamp" :class="{ selected: selectedTime === row.timestamp }"><th scope="row"><button class="time-button" :aria-pressed="selectedTime === row.timestamp" :aria-label="`Inspect ${utcTime(row.timestamp)} UTC`" @click="selectedTime = row.timestamp">{{ utcTime(row.timestamp) }}</button></th><td>{{ price(row.open) }}</td><td>{{ price(row.high) }}</td><td>{{ price(row.low) }}</td><td>{{ price(row.close) }}</td><td>{{ count(row.volume) }}</td></tr></tbody></table></div>
      </section>
    </template>
    <footer class="attribution">TradingView Lightweight Charts™<br>Copyright (с) 2025 TradingView, Inc. <a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">TradingView</a> · <a href="https://tradingview.github.io/lightweight-charts/docs" target="_blank" rel="noopener noreferrer">Chart documentation</a></footer>
  </main>
</template>
