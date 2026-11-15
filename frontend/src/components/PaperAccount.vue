<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { postJson, requestJson } from '../api'

const props = defineProps({
  book: { type: String, default: 'live' },
  symbol: { type: String, default: 'AAPL' },
  cursor: Number,
  locked: Boolean,
})
const emit = defineEmits(['ordered', 'busy'])
const state = ref(null)
const historyBook = ref('live')
const quantity = ref(1)
const submitting = ref(false)
const retry = ref(null)
const notice = ref('')
const error = ref('')
const selectedBook = computed(() => props.book === 'live' ? historyBook.value : props.book)
const archived = computed(() => props.book === 'live' && selectedBook.value !== 'live')
const money = (value) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(Number(value))
const utc = (value) => value ? value.replace('T', ' ').replace(/(?:\.0+)?(?:Z|\+00:00)$/, ' UTC') : '—'
let polling, read

async function refresh() {
  read?.abort()
  const pending = new AbortController()
  read = pending
  try {
    const query = new URLSearchParams({ book: selectedBook.value, symbol: props.symbol })
    const next = await requestJson(`/api/paper?${query}`, { signal: pending.signal })
    if (!pending.signal.aborted) state.value = next
  } catch (cause) {
    if (!pending.signal.aborted && !state.value) error.value = cause.message
  }
}

async function order(action) {
  if (submitting.value || props.locked || archived.value) return
  const payload = retry.value ?? {
    request_id: crypto.randomUUID(),
    book: props.book,
    symbol: props.symbol,
    cursor: props.cursor,
    action,
    quantity: ['buy', 'sell'].includes(action) ? quantity.value : null,
  }
  retry.value = payload
  submitting.value = true
  emit('busy', true)
  error.value = ''
  notice.value = ''
  try {
    const result = await postJson('/api/paper/orders', payload)
    retry.value = null
    if (result) {
      emit('ordered')
      const label = result.status === 'pending' ? 'pending the next eligible price' : result.status
      notice.value = `Paper order #${result.id} ${label}.`
      if (result.reason) error.value = result.reason
    } else {
      notice.value = payload.action === 'hold' ? 'Hold recorded as no action.' : 'Wait recorded as no action.'
    }
    await refresh()
  } catch (cause) {
    error.value = cause.message
  } finally {
    submitting.value = false
    emit('busy', Boolean(retry.value))
  }
}

watch([selectedBook, () => props.symbol, () => props.cursor], refresh, { immediate: true })
onMounted(() => {
  if (props.book === 'live') polling = setInterval(refresh, 1500)
})
onBeforeUnmount(() => {
  clearInterval(polling)
  read?.abort()
})
</script>

<template>
  <section class="panel tool-panel" aria-labelledby="paper-heading">
    <h2 id="paper-heading">{{ book === 'live' ? 'Paper account' : 'Replay paper account' }}</h2>
    <label v-if="book === 'live' && state?.replays.length" class="book-picker">Account
      <select v-model="historyBook">
        <option value="live">Live paper account</option>
        <option v-for="run in state.replays" :key="run.id" :value="run.id">
          Replay · {{ run.symbol }} · {{ utc(run.first_order) }} · {{ run.id.slice(0, 8) }}
        </option>
      </select>
    </label>
    <p class="muted">Simulated orders fill at the next eligible observation with zero fees.</p>
    <template v-if="state">
      <dl class="paper-balances">
        <div><dt>Cash</dt><dd data-testid="paper-cash">{{ money(state.account.cash) }}</dd></div>
        <div><dt>{{ state.account.symbol }} shares</dt><dd data-testid="paper-shares">{{ state.account.shares }}</dd></div>
        <div><dt>Total P&amp;L</dt><dd>{{ state.pnl.length ? money(state.pnl[0].total) : state.account.shares ? 'Valuation unavailable' : money(0) }}</dd></div>
      </dl>
      <form v-if="!archived" class="tool-form" @submit.prevent novalidate>
        <label>Shares <input v-model.number="quantity" type="number" min="1" step="1"></label>
        <button v-for="action in ['buy', 'sell', 'close', 'hold', 'wait']" :key="action"
                type="button" :disabled="submitting || locked || !!retry" @click="order(action)">
          {{ action === 'close' ? 'Close position' : action[0].toUpperCase() + action.slice(1) }}
        </button>
        <button v-if="retry" type="button" :disabled="submitting" @click="order(retry.action)">Retry same request</button>
      </form>
      <p v-if="notice" role="status">{{ notice }}</p>
      <p v-if="error" role="alert">{{ error }}</p>
      <h3>Order history</h3>
      <p v-if="!state.orders.length" class="muted">No paper orders yet.</p>
      <div v-else class="table-scroll">
        <table aria-label="Paper orders">
          <thead><tr><th>ID</th><th>Submitted</th><th>Symbol</th><th>Action</th><th>Shares</th><th>Status</th><th>Fill price</th><th>Observation</th><th>Executed</th></tr></thead>
          <tbody>
            <tr v-for="item in state.orders" :key="item.id">
              <td>{{ item.id }}</td><td>{{ utc(item.submitted_at) }}</td><td>{{ item.symbol }}</td><td>{{ item.action }}</td>
              <td>{{ item.quantity ?? '—' }}</td><td>{{ item.status }}<br v-if="item.reason">{{ item.reason }}</td>
              <td>{{ item.price ? money(item.price) : '—' }}</td>
              <td>{{ item.observation_id ?? '—' }}</td><td>{{ utc(item.executed_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <h3>P&amp;L history</h3>
      <p v-if="!state.pnl.length" class="muted">No valuations yet.</p>
      <div v-else class="table-scroll">
        <table aria-label="Paper profit and loss">
          <thead><tr><th>Event time</th><th>Symbol</th><th>Price source</th><th>Price time</th><th>Price</th><th>Cash</th><th>Shares</th><th>Realized</th><th>Unrealized</th><th>Total P&amp;L</th></tr></thead>
          <tbody>
            <tr v-for="item in state.pnl" :key="item.id">
              <td>{{ utc(item.event_time) }}</td><td>{{ item.symbol }}</td><td>{{ item.event }}</td><td>{{ utc(item.price_time) }}</td>
              <td>{{ money(item.price) }}</td><td>{{ money(item.cash) }}</td><td>{{ item.shares }}</td>
              <td>{{ money(item.realized) }}</td><td>{{ money(item.unrealized) }}</td><td>{{ money(item.total) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
    <p v-else-if="error" role="alert">{{ error }}</p>
  </section>
</template>
