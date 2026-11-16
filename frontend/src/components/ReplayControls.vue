<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import PaperAccount from './PaperAccount.vue'

const props = defineProps({
  interval: { type: String, required: true },
  automationActive: Boolean,
  automationBusy: Boolean,
})
const emit = defineEmits(['frame', 'context', 'ready'])
const catalog = ref(null)
const symbol = ref('')
const start = ref('')
const end = ref('')
const observation = ref(null)
const loading = ref(false)
const playing = ref(false)
const finished = ref(false)
const hasCandles = ref(false)
const status = ref('')
const error = ref('')
const book = ref('')
const hasOrders = ref(false)
const ordering = ref(false)
let cursor, selection, request, timer

const playbackTime = computed(() => observation.value
  ? new Date(observation.value.source_time_ms).toISOString()
  : '')
const ready = computed(() => Boolean(observation.value && !finished.value && !loading.value && !playing.value && !ordering.value))

async function readJson(url, signal, method = 'GET') {
  const response = await fetch(url, { signal, method, cache: 'no-store' })
  const data = await response.json()
  if (!response.ok) {
    throw new Error(typeof data.detail === 'string' ? data.detail : `Replay request failed (HTTP ${response.status}).`)
  }
  return data
}

function pause() {
  clearTimeout(timer)
  playing.value = false
}

function reset() {
  pause()
  request?.abort()
  request = undefined
  cursor = undefined
  selection = undefined
  observation.value = null
  finished.value = false
  hasCandles.value = false
  hasOrders.value = false
  ordering.value = false
  loading.value = false
  error.value = ''
  status.value = ''
  emit('frame', [])
  emit('context', null)
}

function selectSymbol() {
  reset()
  const saved = catalog.value.symbols.find((item) => item.symbol === symbol.value)
  if (!saved) return
  start.value = saved.first_receipt.slice(0, 19)
  end.value = new Date(Math.floor(Date.parse(saved.last_receipt) / 1000) * 1000 + 1000).toISOString().slice(0, 19)
}

function publishContext() {
  emit('context', {
    symbol: selection.symbol, start: selection.start, cursor: cursor.id,
    through_id: cursor.throughId, book: hasOrders.value ? book.value : null, runBook: book.value,
  })
}

function orderSubmitted() {
  hasOrders.value = true
  publishContext()
}

async function loadCatalog() {
  const pending = new AbortController()
  request = pending
  error.value = ''
  try {
    catalog.value = await readJson('/api/replay/symbols', pending.signal)
    symbol.value = catalog.value.symbols[0]?.symbol ?? ''
    selectSymbol()
  } catch (cause) {
    if (!pending.signal.aborted) error.value = cause.message
  }
}

async function advance() {
  if (!selection || loading.value || finished.value || ordering.value) return
  if (props.automationBusy) {
    if (playing.value) timer = setTimeout(advance, 350)
    return
  }
  const query = new URLSearchParams(selection)
  if (cursor) {
    query.set('after_id', cursor.id)
    query.set('through_id', cursor.throughId)
  }
  if (hasOrders.value) query.set('book', book.value)
  const pending = new AbortController()
  request = pending
  loading.value = true
  error.value = ''
  try {
    const frame = await readJson(`/api/replay?${query}`, pending.signal, hasOrders.value ? 'POST' : 'GET')
    if (request !== pending) return
    observation.value = frame.observation
    hasCandles.value = frame.candles.length > 0
    emit('frame', frame.candles)
    finished.value = frame.done
    if (frame.observation) {
      cursor = { id: frame.observation.id, throughId: frame.through_id }
      publishContext()
      status.value = frame.done ? 'Replay complete' : 'Replay ready'
    } else {
      status.value = 'No recorded observations in this range.'
    }
    if (frame.done) pause()
  } catch (cause) {
    if (request !== pending) return
    error.value = cause.message
    pause()
  } finally {
    if (request === pending) loading.value = false
  }
  if (request === pending && playing.value) timer = setTimeout(advance, 350)
}

function startReplay() {
  reset()
  book.value = crypto.randomUUID()
  selection = { symbol: symbol.value, start: start.value + 'Z', end: end.value + 'Z', interval: props.interval }
  advance()
}

function togglePlay() {
  if (playing.value) {
    pause()
  } else {
    playing.value = true
    advance()
  }
}

watch(() => props.interval, reset)
watch(ready, (available) => emit('ready', available), { immediate: true, flush: 'sync' })
onMounted(loadCatalog)
onBeforeUnmount(() => {
  pause()
  request?.abort()
})
defineExpose({ enablePaperAccount: orderSubmitted })
</script>

<template>
  <section class="panel replay-controls" aria-label="Replay controls">
    <p v-if="error" role="alert">{{ error }} <button v-if="!catalog" @click="loadCatalog">Retry</button></p>
    <p v-if="!catalog && !error" role="status">Loading recorded symbols…</p>
    <template v-if="catalog">
      <p class="muted">{{ catalog.source }} · {{ catalog.feed_status === 'disabled' ? 'Live feed disabled' : 'Stored observations' }}</p>
      <p v-if="!catalog.symbols.length" role="status">No recorded observations are available.</p>
      <form v-else @submit.prevent="startReplay">
        <label>Saved symbol
          <select v-model="symbol" :disabled="automationActive || automationBusy" @change="selectSymbol">
            <option v-for="item in catalog.symbols" :key="item.symbol">{{ item.symbol }}</option>
          </select>
        </label>
        <label>Start receipt (UTC)
          <input v-model="start" type="datetime-local" step="1" required :disabled="automationActive || automationBusy" @input="reset">
        </label>
        <label>End before (UTC)
          <input v-model="end" type="datetime-local" step="1" required :disabled="automationActive || automationBusy" @input="reset">
        </label>
        <button type="submit" :disabled="loading || playing || automationActive || automationBusy">{{ observation ? 'Restart replay' : 'Start replay' }}</button>
      </form>
    </template>
    <div v-if="observation" class="replay-progress">
      <button @click="togglePlay" :disabled="ordering || finished || (!playing && (loading || automationBusy))">{{ playing ? 'Pause' : 'Play' }}</button>
      <button @click="advance" :disabled="ordering || loading || playing || finished || automationBusy">Next observation</button>
      <dl>
        <div><dt>Source time (UTC)</dt><dd>{{ playbackTime }}</dd></div>
        <div><dt>Recorded receipt (UTC)</dt><dd>{{ observation.received_at }}</dd></div>
      </dl>
    </div>
    <p v-if="status" role="status">{{ status }}</p>
    <p v-if="observation && !hasCandles" class="muted">No regular-session candle at this point in the replay.</p>
  </section>
  <PaperAccount v-if="observation" :key="book" :book="book" :symbol="symbol" :cursor="observation.id"
                :locked="loading || playing || finished || automationBusy"
                @ordered="orderSubmitted"
                @busy="ordering = $event" />
</template>
