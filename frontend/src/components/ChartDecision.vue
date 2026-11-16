<script setup>
import { shallowRef, ref } from 'vue'
import { postJson } from '../api'
import { voiceLink } from '../voiceConnection'
import FrozenChartCapture from './FrozenChartCapture.vue'

const emit = defineEmits(['busy'])
const snapshot = shallowRef(null)
const decision = shallowRef(null)
const capture = ref('')
const exporter = ref(null)
const error = ref('')
const busy = ref(false)
const utc = (value) => value.replace('T', ' ').replace(/(?:\.0+)?(?:Z|\+00:00)$/, ' UTC')

async function ask(selection) {
  if (busy.value) return
  busy.value = true
  emit('busy', true)
  error.value = ''
  snapshot.value = null
  capture.value = ''
  decision.value = null
  try {
    snapshot.value = await postJson('/api/decision-snapshots', selection)
    capture.value = await exporter.value.take(snapshot.value)
    decision.value = await postJson('/api/decisions', { snapshot_id: snapshot.value.id, image_url: capture.value })
  } catch (cause) {
    error.value = cause.message
  } finally {
    busy.value = false
    emit('busy', false)
  }
}

defineExpose({ ask })
</script>

<template>
  <FrozenChartCapture ref="exporter" />
  <section v-if="busy || error || snapshot" class="panel tool-panel chart-decision" aria-labelledby="decision-title">
    <h2 id="decision-title">Chart analysis</h2>
    <p v-if="busy" role="status">{{ capture ? 'Waiting for analysis and review…' : 'Freezing chart at the selected candle…' }}</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <figure v-if="capture">
      <img :src="capture" alt="Frozen chart supplied to the Decisions API">
      <figcaption>{{ snapshot.facts.symbol }} · {{ snapshot.facts.interval }} · cutoff {{ utc(snapshot.facts.decision_time) }}</figcaption>
    </figure>
    <template v-if="decision">
      <p><strong>Final check</strong> · {{ decision.status }} · {{ decision.eligible_for_paper_order ? 'Eligible for paper review workflow' : 'Ineligible for a paper order' }}</p>
      <p>{{ decision.explanation }}</p>
      <p v-if="decision.visual_comparison">
        <strong>Crossover comparison</strong> · Analyst: {{ decision.visual_comparison.model }} ·
        Python: {{ decision.visual_comparison.calculated }} · {{ decision.visual_comparison.status }}
      </p>
      <div class="decision-roles">
        <section v-for="(role, label) in { Analyst: decision.analyst, Reviewer: decision.reviewer }" :key="label">
          <h3>{{ label }}</h3>
          <p class="muted">{{ role.provider }} · {{ role.model }} · {{ role.status }}</p>
          <dl v-for="question in role.questions" :key="question.name">
            <dt>{{ question.name }} · {{ question.instructions }}</dt>
            <dd>{{ role.answers[question.name] ?? 'No answer' }}</dd>
          </dl>
          <details>
            <summary>Allowed answers</summary>
            <p v-for="question in role.questions" :key="question.name">
              {{ question.name }} · {{ question.choices.map((choice) => choice.value).join(', ') }}
            </p>
          </details>
        </section>
      </div>
      <p class="muted">Decision {{ decision.id }} · {{ decision.facts?.market_data }} market data</p>
      <div class="automation-controls">
        <a v-if="decision.capture" :href="decision.capture.url">Open saved PNG</a>
        <a :href="voiceLink(decision.id)">Discuss by voice</a>
      </div>
    </template>
  </section>
</template>
