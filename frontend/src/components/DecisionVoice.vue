<script setup>
import { onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { postJson, requestJson } from '../api'
import { waitForIce } from '../voiceConnection'

const id = ref(new URLSearchParams(location.search).get('decision') ?? '')
const summary = shallowRef(null)
const audio = ref(null)
const phase = ref('idle')
const status = ref('Voice off')
const error = ref('')
const micActive = ref(false)
const audioReady = ref(false)
const autoplayBlocked = ref(false)
const loading = ref(false)
let current = null
let summaryRequest

async function loadDecision() {
  if (current) return
  const requested = id.value.trim()
  error.value = ''
  summary.value = null
  if (!/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/i.test(requested)) {
    error.value = 'Enter a saved decision ID.'
    return
  }
  summaryRequest?.abort()
  const request = new AbortController()
  summaryRequest = request
  loading.value = true
  try {
    const result = await requestJson(`/api/decisions/${requested}/voice-summary`, { signal: request.signal })
    if (!request.signal.aborted) summary.value = result
  } catch (cause) {
    if (!request.signal.aborted) error.value = cause.message
  } finally {
    if (summaryRequest === request) loading.value = false
  }
}

function cleanup(run = current, message = 'Voice ended') {
  if (!run || current !== run) return
  current = null
  clearTimeout(run.timeout)
  run.abort.abort()
  run.stream?.getTracks().forEach((track) => track.stop())
  run.channel?.close()
  run.peer?.close()
  if (audio.value) {
    audio.value.pause()
    audio.value.srcObject = null
  }
  micActive.value = false
  audioReady.value = false
  autoplayBlocked.value = false
  phase.value = 'idle'
  status.value = message
}

function failed(run, message) {
  if (current !== run) return
  error.value = message
  cleanup(run, 'Voice disconnected')
}

function providerEvent(run, data) {
  if (current !== run) return
  let event
  try {
    event = JSON.parse(data)
  } catch {
    return
  }
  if (event?.type === 'session.started' && phase.value === 'connecting') {
    clearTimeout(run.timeout)
    phase.value = 'active'
    status.value = 'Voice connected'
  } else if (event?.type === 'session.closed') {
    cleanup(run)
  } else if (event?.type === 'error' || event?.type === 'session.failed') {
    failed(run, 'The voice provider ended the connection with an error.')
  }
}

async function start() {
  if (current || !summary.value) return
  error.value = ''
  if (!navigator.mediaDevices?.getUserMedia || !window.RTCPeerConnection) {
    error.value = 'Voice requires microphone and WebRTC support on localhost or HTTPS.'
    return
  }
  const decisionId = summary.value.decision_id
  const run = { abort: new AbortController() }
  current = run
  phase.value = 'requesting'
  status.value = 'Waiting for microphone access…'
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    if (current !== run) {
      stream.getTracks().forEach((track) => track.stop())
      return
    }
    run.stream = stream
    phase.value = 'connecting'
    status.value = 'Connecting voice…'
    micActive.value = true
    run.timeout = setTimeout(() => failed(run, 'The voice session did not start in time.'), 35_000)
    const peer = new RTCPeerConnection()
    run.peer = peer
    peer.addEventListener('track', ({ track }) => {
      if (current !== run || track.kind !== 'audio') return
      audio.value.srcObject = new MediaStream([track])
      audioReady.value = true
      audio.value.play().catch(() => {
        if (current === run) autoplayBlocked.value = true
      })
    })
    peer.addEventListener('connectionstatechange', () => {
      if (['disconnected', 'failed', 'closed'].includes(peer.connectionState)) {
        if (phase.value === 'ending') cleanup(run)
        else failed(run, 'The WebRTC connection was lost.')
      }
    })
    const tracks = stream.getAudioTracks()
    if (!tracks.length) throw new Error('No microphone audio track was available.')
    for (const track of tracks) {
      peer.addTrack(track, stream)
      track.addEventListener('ended', () => failed(run, 'The microphone disconnected.'), { once: true })
    }
    const channel = peer.createDataChannel('oai-events')
    run.channel = channel
    channel.addEventListener('message', ({ data }) => providerEvent(run, data))
    channel.addEventListener('close', () => {
      if (phase.value === 'ending') cleanup(run)
      else failed(run, 'The voice event channel disconnected.')
    })
    await peer.setLocalDescription(await peer.createOffer())
    await waitForIce(peer, run.abort.signal)
    const result = await postJson('/api/voice/sessions',
      { decision_id: decisionId, sdp: peer.localDescription.sdp }, run.abort.signal)
    if (current === run) await peer.setRemoteDescription({ type: 'answer', sdp: result.sdp })
  } catch (cause) {
    const message = cause.name === 'NotAllowedError' ? 'Microphone access was denied.'
      : cause.name === 'NotFoundError' ? 'No microphone was found.'
      : cause.message
    failed(run, message)
  }
}

function end() {
  const run = current
  if (!run) return
  if (phase.value !== 'active' || run.channel.readyState !== 'open') {
    cleanup(run)
    return
  }
  phase.value = 'ending'
  status.value = 'Ending voice…'
  run.stream.getTracks().forEach((track) => track.stop())
  micActive.value = false
  try {
    run.timeout = setTimeout(() => cleanup(run), 5_000)
    run.channel.send(JSON.stringify({ type: 'session.close' }))
  } catch {
    cleanup(run)
  }
}

const leave = () => cleanup()
onMounted(() => {
  if (id.value) loadDecision()
  window.addEventListener('pagehide', leave)
})
onBeforeUnmount(() => {
  window.removeEventListener('pagehide', leave)
  summaryRequest?.abort()
  leave()
})
</script>

<template>
  <section class="panel tool-panel" aria-labelledby="voice-heading">
    <h1 id="voice-heading">Decision voice</h1>
    <form class="tool-form" @submit.prevent="loadDecision">
      <label>Saved decision ID <input v-model="id" class="decision-id-input" :disabled="phase !== 'idle'" required></label>
      <button :disabled="phase !== 'idle' || loading">Load decision</button>
    </form>
    <p v-if="error" role="alert">{{ error }}</p>
    <template v-if="summary">
      <h2>{{ summary.symbol ?? 'Symbol unavailable' }} <small v-if="summary.interval">· {{ summary.interval }}</small></h2>
      <p class="muted">Decision {{ summary.decision_id }}</p>
      <dl class="voice-findings">
        <div><dt>Analyst proposal</dt><dd>{{ summary.proposal ?? 'None recorded' }}</dd></div>
        <div><dt>Reviewer verdict</dt><dd>{{ summary.reviewer?.answers?.verdict ?? 'None recorded' }}</dd></div>
        <div><dt>Validation</dt><dd>{{ summary.validation_status }}</dd></div>
        <div><dt>Paper outcome</dt><dd>{{ summary.paper_outcome.status.replaceAll('_', ' ') }}</dd></div>
      </dl>
      <p v-if="summary.paper_outcome.reason">{{ summary.paper_outcome.reason }}</p>
      <p v-if="summary.paper_outcome.fill_price != null">
        {{ summary.paper_outcome.quantity }} share(s) at ${{ summary.paper_outcome.fill_price }}.
        <template v-if="summary.paper_outcome.cash_after_fill != null">
          Cash after fill ${{ summary.paper_outcome.cash_after_fill }}. Shares after fill {{ summary.paper_outcome.shares_after_fill }}.
        </template>
      </p>
      <p class="muted">Start voice shares the selected read-only summary and microphone audio with OpenAI for this conversation.</p>
      <div class="automation-controls">
        <button :disabled="phase !== 'idle'" @click="start">Start voice</button>
        <button :disabled="phase === 'idle' || phase === 'ending'" @click="end">End voice</button>
        <span role="status">{{ status }}</span>
        <span>{{ micActive ? 'Microphone active' : 'Microphone off' }}</span>
      </div>
      <p v-if="autoplayBlocked">Select Play to hear the reply.</p>
    </template>
    <audio ref="audio" v-show="audioReady" controls autoplay aria-label="Decision voice audio" @play="autoplayBlocked = false"></audio>
  </section>
</template>
