import { waitForIce } from '../src/voiceConnection'

// Loaded only by the isolated smoke-test Vite configuration.
const scenario = new URLSearchParams(location.search).get('voice_fixture')
const originalFetch = window.fetch.bind(window)
const label = document.createElement('output')
label.id = 'voice-fixture-status'
label.textContent = 'Synthetic voice fixture · no physical microphone'
document.body.prepend(label)

function audioStream() {
  const context = new AudioContext()
  return { context, stream: context.createMediaStreamDestination().stream }
}

Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
  value: async () => {
    if (scenario === 'denied') throw new DOMException('Fixture denied permission', 'NotAllowedError')
    const { context, stream } = audioStream()
    const [track] = stream.getAudioTracks()
    const stop = track.stop.bind(track)
    label.textContent = 'Synthetic microphone track live'
    track.stop = () => {
      stop()
      label.textContent = 'Synthetic microphone track ' + track.readyState
      context.close().catch(() => {})
    }
    return stream
  },
})

let remote
let remoteChannel
function closeRemote() { remote?.close() }
window.addEventListener('pagehide', closeRemote)

const disconnect = document.createElement('button')
disconnect.textContent = 'Disconnect fixture peer'
disconnect.addEventListener('click', () => remoteChannel?.close())
document.body.prepend(disconnect)

window.fetch = async (url, options) => {
  if (url !== '/api/voice/sessions' || options?.method !== 'POST' || scenario === 'failed') {
    return originalFetch(url, options)
  }
  const { sdp: offer } = JSON.parse(options.body)
  closeRemote()
  const peer = new RTCPeerConnection()
  remote = peer
  const { context, stream } = audioStream()
  peer.addEventListener('connectionstatechange', () => {
    if (['closed', 'failed', 'disconnected'].includes(peer.connectionState)) context.close().catch(() => {})
  })
  peer.addEventListener('datachannel', ({ channel }) => {
    remoteChannel = channel
    channel.addEventListener('close', () => {
      peer.close()
      context.close().catch(() => {})
    })
    channel.addEventListener('open', () => channel.send(JSON.stringify({ type: 'session.started', session: { id: 'live_fixture' } })))
    channel.addEventListener('message', ({ data }) => {
      if (JSON.parse(data).type === 'session.close') channel.send(JSON.stringify({ type: 'session.closed' }))
    })
  })
  try {
    await peer.setRemoteDescription({ type: 'offer', sdp: offer })
    peer.addTrack(stream.getAudioTracks()[0], stream)
    await peer.setLocalDescription(await peer.createAnswer())
    await waitForIce(peer, options.signal)
    const registration = await originalFetch('/api/voice-fixture/answer', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: options.signal,
      body: JSON.stringify({ offer, answer: peer.localDescription.sdp }),
    })
    if (!registration.ok) throw new Error('Could not register the fixture SDP answer.')
    return await originalFetch(url, options)
  } catch (error) {
    peer.close()
    await context.close().catch(() => {})
    throw error
  }
}
