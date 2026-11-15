<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { postJson, requestJson } from '../api'

const props = defineProps({ rows: { type: Array, required: true }, sample: Boolean })
const emit = defineEmits(['result'])
const catalog = ref(null)
const shortInput = ref(2)
const longInput = ref(3)
const pair = ref({ short: 2, long: 3 })
const name = ref('')
const kind = ref('')
const windowInput = ref(2)
const pairError = ref('')
const saveError = ref('')
const calculationError = ref('')
const saving = ref(false)
let pending
const positiveInteger = (value) => Number.isSafeInteger(value) && value > 0

function applyWindows() {
  if (!positiveInteger(shortInput.value) || !positiveInteger(longInput.value) || shortInput.value >= longInput.value) {
    pairError.value = 'Use positive whole numbers with the short window smaller than the long window.'
    return
  }
  pairError.value = ''
  pair.value = { short: shortInput.value, long: longInput.value }
}

async function save() {
  saveError.value = ''
  if (!positiveInteger(windowInput.value)) {
    saveError.value = 'The indicator window must be a positive whole number.'
    return
  }
  if (!name.value.trim()) {
    saveError.value = 'Enter a name for the indicator.'
    return
  }
  saving.value = true
  try {
    const configured = await postJson('/api/indicators', {
      name: name.value, type: kind.value, settings: { window: windowInput.value },
    })
    catalog.value = { ...catalog.value, configured }
    name.value = ''
  } catch (error) {
    saveError.value = error.message
  } finally {
    saving.value = false
  }
}

async function calculate() {
  pending?.abort()
  emit('result', { series: [], crossovers: [] })
  if (!props.rows.length) return
  const request = new AbortController()
  pending = request
  calculationError.value = ''
  try {
    const result = await postJson('/api/indicators/calculate', {
      ...pair.value,
      candles: props.rows.map(({ timestamp, close, high, low, ended }) =>
        ({ timestamp, close, high, low, ended: props.sample || ended })),
    }, request.signal)
    if (!request.signal.aborted) emit('result', result)
  } catch (error) {
    if (!request.signal.aborted) calculationError.value = error.message
  }
}

onMounted(async () => {
  try {
    catalog.value = await requestJson('/api/indicators')
    kind.value = catalog.value.types[0]?.type ?? ''
  } catch (error) {
    saveError.value = error.message
  }
})
watch([() => props.rows, pair, catalog], calculate, { immediate: true })
onBeforeUnmount(() => pending?.abort())
</script>

<template>
  <section class="panel tool-panel" aria-labelledby="indicator-heading">
    <h2 id="indicator-heading">Indicators</h2>
    <form class="tool-form" @submit.prevent="applyWindows" novalidate>
      <label>Short SMA window <input v-model.number="shortInput" type="number" min="1" step="1"></label>
      <label>Long SMA window <input v-model.number="longInput" type="number" min="2" step="1"></label>
      <button>Apply SMA windows</button>
    </form>
    <p v-if="pairError" role="alert">{{ pairError }}</p>
    <p v-if="calculationError" role="alert">{{ calculationError }}</p>
    <form v-if="catalog" class="tool-form" @submit.prevent="save" novalidate>
      <label>Indicator type <select v-model="kind">
        <option v-for="item in catalog.types" :key="item.type" :value="item.type">{{ item.label }}</option>
      </select></label>
      <label>Indicator name <input v-model="name" maxlength="64"></label>
      <label>Indicator window <input v-model.number="windowInput" type="number" min="1" step="1"></label>
      <button :disabled="saving">Add indicator</button>
    </form>
    <p v-if="saveError" role="alert">{{ saveError }}</p>
    <ul v-if="catalog?.configured.length" class="saved-indicators" aria-label="Saved indicators">
      <li v-for="item in catalog.configured" :key="item.id">{{ item.name }} · {{ item.settings.window }} candles</li>
    </ul>
  </section>
</template>
