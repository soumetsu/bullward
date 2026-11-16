<script setup>
import { computed, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { postJson, requestJson } from '../api'
import FrozenChartCapture from './FrozenChartCapture.vue'

const props = defineProps({
  rows: { type: Array, required: true },
  symbol: { type: String, required: true },
  interval: { type: String, required: true },
  windows: { type: Object, required: true },
  replay: Boolean,
  replayContext: Object,
  replayReady: Boolean,
  rangeStart: { type: Function, required: true },
})
const emit = defineEmits(['active', 'busy', 'account-ready'])
const state = shallowRef(null)
const exporter = ref(null)
const busy = ref(false)
const stopping = ref(false)
const error = ref('')
const active = computed(() => Boolean(state.value?.run.enabled))
const key = `invest-paper-automation-${props.replay ? 'replay' : 'live'}`
const money = (value) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(value))
const utc = (value) => value.replace('T', ' ').replace(/(?:\.0+)?(?:Z|\+00:00)$/, ' UTC')
const completed = () => props.rows.filter((row) => row.ended).map((row) => row.timestamp)
let handled = new Set()
let polling, viewVersion = 0
const path = (id) => `/api/paper/automation/${id}`

function selection() {
  const result = { symbol: props.symbol, interval: props.interval, ...props.windows }
  if (props.replay) {
    const { start, cursor, through_id, runBook } = props.replayContext
    result.replay = { start, cursor, through_id, book: runBook }
  }
  return result
}

async function refresh(id = state.value?.run.id) {
  if (!id) return
  const version = ++viewVersion
  const result = await requestJson(path(id))
  if (version === viewVersion) state.value = result
}

async function start() {
  if (busy.value || active.value) return
  busy.value = true
  error.value = ''
  handled = new Set(completed())
  try {
    viewVersion++
    state.value = await postJson('/api/paper/automation/start', selection())
    sessionStorage.setItem(key, state.value.run.id)
    if (props.replay) emit('account-ready')
  } catch (cause) {
    error.value = cause.message
  } finally {
    busy.value = false
  }
  processCompleted()
}

async function stop() {
  if (!active.value || stopping.value) return
  stopping.value = true
  error.value = ''
  try {
    const stopped = await postJson(path(state.value.run.id) + '/stop', {})
    viewVersion++
    state.value = stopped
  } catch (cause) {
    error.value = `Stop could not be confirmed. ${cause.message}`
  } finally {
    stopping.value = false
  }
}

async function analyze(timestamp, runId) {
  const base = path(runId)
  const prepared = await postJson(base + '/candles', {
    ...selection(), timestamp, range_start: props.rangeStart(timestamp),
  })
  if (prepared.status !== 'awaiting_capture') return
  const analysis = {}
  try {
    analysis.image_url = await exporter.value.take(prepared.snapshot)
  } catch (cause) {
    analysis.capture_error = cause.message.slice(0, 300)
  }
  await postJson(`${base}/jobs/${prepared.id}/analyze`, analysis)
}

async function processCompleted() {
  if (!active.value || stopping.value || busy.value || (props.replay && !props.replayContext)) return
  const runId = state.value.run.id
  const next = completed().filter((timestamp) => !handled.has(timestamp))
  if (!next.length) return
  busy.value = true
  try {
    for (const timestamp of next) {
      if (!active.value || stopping.value || state.value.run.id !== runId) break
      handled.add(timestamp)
      await analyze(timestamp, runId)
    }
  } catch (cause) {
    error.value = cause.message
  } finally {
    try {
      await refresh(runId)
    } catch (cause) {
      error.value ||= cause.message
    }
    busy.value = false
  }
  if (!error.value) processCompleted()
}

function stopOnLeave() {
  if (active.value) {
    fetch(path(state.value.run.id) + '/stop', { method: 'POST', keepalive: true }).catch(() => {})
  }
}

watch(active, (value) => emit('active', value), { flush: 'sync' })
watch(busy, (value) => emit('busy', value), { flush: 'sync' })
watch([() => props.rows, () => props.replayContext], processCompleted, { flush: 'post' })
onMounted(async () => {
  window.addEventListener('pagehide', stopOnLeave)
  const previous = sessionStorage.getItem(key)
  if (previous) {
    try {
      await refresh(previous)
      if (active.value) await stop()
    } catch {
      sessionStorage.removeItem(key)
    }
  }
  polling = setInterval(() => {
    if (!stopping.value && !busy.value) refresh().catch(() => {})
  }, 1500)
})
onBeforeUnmount(() => {
  window.removeEventListener('pagehide', stopOnLeave)
  stopOnLeave()
  clearInterval(polling)
})
</script>

<template>
  <FrozenChartCapture ref="exporter" />
  <section class="panel tool-panel" aria-labelledby="automation-heading">
    <h2 id="automation-heading">Paper automation</h2>
    <p class="muted">{{ symbol }} · {{ interval }} · SMA {{ windows.short }}/{{ windows.long }} · Simulated orders</p>
    <div class="automation-controls">
      <button :disabled="active || busy || stopping || (replay && !replayReady)" @click="start">Start paper automation</button>
      <button :disabled="!active || stopping" @click="stop">Stop paper automation</button>
      <span role="status">{{ stopping ? 'Stopping…' : active ? (busy ? 'Analyzing completed candle…' : 'Paper automation on') : 'Paper automation off' }}</span>
    </div>
    <p v-if="error" role="alert">{{ error }}</p>
    <template v-if="state">
      <p v-if="state.run.test_mode" class="muted">Disposable replay account</p>
      <dl class="paper-balances">
        <div><dt>Cash</dt><dd data-testid="automation-cash">{{ money(state.account.cash) }}</dd></div>
        <div><dt>{{ state.run.symbol }} shares</dt><dd data-testid="automation-shares">{{ state.account.shares }}</dd></div>
      </dl>
      <div v-if="state.jobs.length" class="table-scroll">
        <table aria-label="Paper automation history">
          <thead><tr><th>Candle start</th><th>Crossover</th><th>Proposal</th><th>Review</th><th>Outcome</th><th>Decision</th></tr></thead>
          <tbody>
            <tr v-for="job in state.jobs" :key="job.id">
              <td>{{ utc(job.candle) }}</td><td>{{ job.crossover ?? '—' }}</td><td>{{ job.proposal ?? '—' }}</td>
              <td>{{ job.verdict ?? '—' }}</td>
              <td><strong>{{ job.status }}</strong><br v-if="job.fill_price || job.reason">
                {{ job.fill_price ? `${job.quantity} share(s) at ${money(job.fill_price)} · Order #${job.order_id}` : job.reason }}
              </td>
              <td>
                <a v-if="job.decision_id" :href="`/api/decisions/${job.decision_id}`">{{ job.decision_id }}</a>
                <template v-if="job.capture"><br><a :href="job.capture.url">Saved chart</a></template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-else class="muted">Newly completed candles will appear here.</p>
    </template>
  </section>
</template>
