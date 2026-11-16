export function waitForIce(peer, signal) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => finish(new Error('Preparing the WebRTC connection timed out.')), 10_000)
    function finish(error) {
      clearTimeout(timeout)
      peer.removeEventListener('icegatheringstatechange', check)
      signal.removeEventListener('abort', abort)
      if (error) reject(error)
      else resolve()
    }
    function abort() { finish(signal.reason) }
    function check() {
      if (peer.iceGatheringState === 'complete') finish()
    }
    peer.addEventListener('icegatheringstatechange', check)
    signal.addEventListener('abort', abort, { once: true })
    if (signal.aborted) abort()
    else check()
  })
}

export function voiceLink(decisionId) {
  return '/?source=voice&decision=' + encodeURIComponent(decisionId)
}
