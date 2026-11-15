export async function requestJson(url, options) {
  const response = await fetch(url, { cache: 'no-store', ...options })
  const data = await response.json()
  if (!response.ok) {
    throw new Error(typeof data.detail === 'string' ? data.detail : `Request failed (HTTP ${response.status}).`)
  }
  return data
}

export function postJson(url, data, signal) {
  return requestJson(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
    signal,
  })
}
