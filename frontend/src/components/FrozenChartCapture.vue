<script setup>
import { nextTick, ref, shallowRef } from 'vue'
import { tagCapture } from '../chartCapture'
import CandleChart from './CandleChart.vue'

const frozen = shallowRef(null)
const chart = ref(null)
const paint = () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))

async function take(snapshot) {
  if (!snapshot.candles.length || snapshot.facts.close === null) {
    throw new Error('There is no completed chart at this cutoff.')
  }
  frozen.value = snapshot
  try {
    await nextTick()
    await paint()
    chart.value.resetView()
    return await tagCapture(chart.value.screenshot(), snapshot.id)
  } finally {
    frozen.value = null
  }
}

defineExpose({ take })
</script>

<template>
  <div v-if="frozen" class="capture-render" aria-hidden="true" inert>
    <CandleChart ref="chart" :rows="frozen.candles" :indicators="frozen.indicators" />
  </div>
</template>
