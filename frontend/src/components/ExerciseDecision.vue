<script setup>
import { nextTick, ref, shallowRef } from 'vue'
import { postJson } from '../api'
import { tagCapture } from '../chartCapture'
import CandleChart from './CandleChart.vue'

const emit = defineEmits(['busy'])
const snapshot = shallowRef(null)
const result = shallowRef(null)
const rendering = ref(false)
const chart = ref(null)
const capture = ref('')
const busy = ref(false)
const error = ref('')
const heading = ref(null)
const emptyIndicators = { series: [], crossovers: [] }
async function ask(selection) {
  if (busy.value) return
  busy.value = true
  emit('busy', true)
  error.value = ''
  result.value = null
  capture.value = ''
  try {
    await nextTick()
    heading.value.focus({ preventScroll: true })
    heading.value.scrollIntoView({
      block: 'start',
      behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',
    })
    snapshot.value = await postJson('/api/chart-exercise/snapshots', selection)
    rendering.value = true
    await nextTick()
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
    chart.value.resetView()
    await new Promise((resolve) => requestAnimationFrame(resolve))
    const pixels = chart.value.screenshot()
    const canvas = document.createElement('canvas')
    canvas.width = pixels.width
    canvas.height = pixels.height + 32
    const context = canvas.getContext('2d')
    context.drawImage(pixels, 0, 32)
    context.fillStyle = '#ffffff'
    context.fillRect(0, 0, canvas.width, 30)
    context.fillStyle = '#182b42'
    context.font = '14px sans-serif'
    context.fillText(`Signal for ${snapshot.value.signal_minute} | 1: ${snapshot.value.candle_times[0]} | 2: ${snapshot.value.candle_times[1]}`, 8, 20)
    capture.value = await tagCapture(canvas, snapshot.value.id)
    rendering.value = false
    result.value = await postJson('/api/chart-exercise', { snapshot_id: snapshot.value.id, image_url: capture.value })
  } catch (cause) {
    error.value = cause.message
  } finally {
    rendering.value = false
    busy.value = false
    emit('busy', false)
  }
}
defineExpose({ ask })
</script>

<template>
  <div v-if="rendering" class="capture-render" aria-hidden="true" inert>
    <CandleChart ref="chart" :rows="snapshot.candles" :indicators="emptyIndicators" flat-color="#687585" :show-price-line="false"
      :labels="snapshot.candle_times.map((timestamp, index) => ({ timestamp, text: String(index + 1) }))" />
  </div>
  <section id="chart-decision" class="panel tool-panel chart-decision" :class="{ 'chart-decision-active': busy || result || error }" aria-labelledby="exercise-title">
    <h2 id="exercise-title" ref="heading" tabindex="-1">Decision API · chart-reading exercise</h2>
    <p v-if="busy" role="status">{{ capture ? 'Reading the chart image…' : 'Capturing the one-minute chart…' }}</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <div v-if="result" class="exercise-result" aria-live="polite">
      <p><strong>{{ result.status === 'stale_chart' ? 'Freshness guard' : 'Model choice' }}: {{ result.choice ?? 'Unavailable' }}</strong> · Signal minute: {{ result.signal_minute }}</p>
      <p>Choice probability: {{ result.probability == null ? 'Not available' : (result.probability * 100).toFixed(1) + '%' }}</p>
      <p>{{ result.status }} · {{ result.explanation }}</p>
      <p v-if="result.status === 'stale_chart'">Wait is a local freshness guard; no model choice was requested.</p>
    </div>
    <p>For the chart clock minute (candle 3), read only the preceding one-minute bodies labeled 1 and 2, oldest first. Two green bodies → long. Two red bodies → short. A mixed pair, a flat gray body, a missing or unreadable candle, or a stale capture → wait. Ignore candle 3, wicks, volume, and trend. This exercise places no trades.</p>
    <ol class="exercise-flow" aria-label="How the chart-reading exercise connects">
      <li>Chart clock → floor to minute 3</li>
      <li>Exact minutes 1 and 2 → labeled PNG</li>
      <li>FastAPI → image + times → OpenAI Decisions</li>
      <li>Model choice + probability → display only</li>
    </ol>
    <figure v-if="capture">
      <img :src="capture" alt="One-minute price chart with the two preceding candles labeled 1 and 2">
      <figcaption>{{ snapshot.symbol }} · {{ snapshot.mode }} clock {{ snapshot.clock }} · signal for {{ snapshot.signal_minute }}</figcaption>
    </figure>
  </section>
</template>
<style scoped>
#exercise-title { scroll-margin-top: 24px; }
.exercise-flow { display: flex; flex-wrap: wrap; gap: 12px; padding: 0; list-style: none; }
.exercise-flow li { flex: 1; min-width: 170px; border: 1px solid #c7d9d0; border-radius: 8px; padding: 16px; background: #f4f8f6; }
</style>
