import assert from 'node:assert/strict'
import { setTimeout as delay } from 'node:timers/promises'

const [endpoint, base] = process.argv.slice(2)
const socket = new WebSocket(endpoint)
const pending = new Map()
let id = 0
let sessionId

socket.addEventListener('message', ({ data }) => {
  const reply = JSON.parse(data)
  const command = pending.get(reply.id)
  if (!command) return
  if (reply.error) command.reject(new Error(reply.error.message))
  else command.resolve(reply.result)
})
socket.addEventListener('close', () => {
  for (const command of pending.values()) command.reject(new Error('Browser connection closed'))
})

function send(method, params = {}, attached = true) {
  const requestId = ++id
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Browser command timed out: ' + method)), 8000)
    pending.set(requestId, {
      resolve: (value) => { clearTimeout(timer); resolve(value) },
      reject: (error) => { clearTimeout(timer); reject(error) },
    })
    socket.send(JSON.stringify({ id: requestId, method, params, sessionId: attached ? sessionId : undefined }))
  }).finally(() => pending.delete(requestId))
}

async function evaluate(fn, ...args) {
  const { result, exceptionDetails } = await send('Runtime.evaluate', {
    expression: '(' + fn.toString() + ')(...' + JSON.stringify(args) + ')',
    returnByValue: true,
    awaitPromise: true,
  })
  if (exceptionDetails) throw new Error(result.description || exceptionDetails.text)
  return result.value
}

async function wait(description, fn, ...args) {
  const deadline = Date.now() + 15_000
  while (Date.now() < deadline) {
    const value = await evaluate(fn, ...args)
    if (value) return value
    await delay(100)
  }
  throw new Error('Timed out waiting for ' + description)
}

async function button(name) {
  await wait('enabled ' + name, (label) => {
    const target = [...document.querySelectorAll('button')].find((item) =>
      !item.disabled && item.getClientRects().length &&
      (item.getAttribute('aria-label') ?? item.innerText.trim()) === label)
    if (!target) return false
    target.click()
    return true
  }, name)
}

async function field(selector, value) {
  await wait(selector, (query, input) => {
    const target = document.querySelector(query)
    if (!target || target.disabled) return false
    target.value = input
    target.dispatchEvent(new Event('input', { bubbles: true }))
    target.dispatchEvent(new Event('change', { bubbles: true }))
    return true
  }, selector, value)
}

async function text(selector, expected, exact = false) {
  await wait(selector + ' to show ' + expected, (query, value, exact) => {
    const element = document.querySelector(query)
    if (!element?.getClientRects().length) return false
    return exact ? element.innerText.trim() === value : element.innerText.includes(value)
  }, selector, expected, exact)
}

async function step(time) {
  await button('Next observation')
  await text('.replay-progress dd', 'T' + time)
}

async function selectCandle(time) {
  await wait('selectable candle ' + time, (value) => {
    const button = [...document.querySelectorAll('.time-button')]
      .find((item) => item.innerText.endsWith(value))
    if (!button) return false
    button.click()
    return button.getClientRects().length > 0
  }, time)
}

async function indicator(value) {
  await wait('Smoke SMA value ' + value, (expected) => {
    const label = [...document.querySelectorAll('.indicator-values dt')]
      .find((item) => item.innerText === 'Smoke SMA')
    return label?.nextElementSibling.innerText === expected
  }, value)
}

async function fixture(path, method = 'GET') {
  const response = await fetch(base + '/api/workstation-smoke/' + path, {
    method, signal: AbortSignal.timeout(5000),
  })
  assert.equal(response.status, 200, path)
  return response.json()
}

async function counts(orders, fills) {
  const state = await fixture('state')
  assert.deepEqual([state.orders, state.fills], [orders, fills])
  return state
}

async function job(time, action, status, detail = '') {
  return wait('automation ' + time + ' ' + action + ' ' + status + ' ' + detail, (time, action, status, detail) => {
    const row = [...document.querySelectorAll('[aria-label="Paper automation history"] tbody tr')]
      .find((item) => item.cells[0].innerText.includes(time))
    return row?.cells[2].innerText === action && row.cells[4].querySelector('strong').innerText === status
      && row.innerText.includes(detail)
  }, time, action, status, detail)
}

async function checkChart() {
  await selectCandle('14:34')
  await field('input[maxlength="64"]', 'Smoke SMA')
  await button('Add indicator')
  await indicator('87.50')
  await button('Edit window for Smoke SMA')
  await field('[aria-labelledby="indicator-heading"] form:nth-of-type(2) input[type="number"]', '3')
  await button('Save window')
  await text('.saved-indicators', '3 candles')
  await indicator('78.33')
  console.log('Browser PASS: saved SMA changed from 87.50 (2 closes) to 78.33 (3 closes).')

  await counts(0, 0)
  await button('Ask about chart')
  await text('.chart-decision figcaption', 'SMOKE · 1m · cutoff 2026-11-09 14:35:00 UTC')
  await text('.chart-decision', 'Final check · valid')
  const [answers, pixels] = await evaluate(async () => {
    const image = document.querySelector('.chart-decision img')
    await image.decode()
    const canvas = document.createElement('canvas')
    canvas.width = image.naturalWidth
    canvas.height = image.naturalHeight
    const context = canvas.getContext('2d')
    context.drawImage(image, 0, 0)
    const { data } = context.getImageData(0, 0, canvas.width, canvas.height)
    return [
      [...document.querySelectorAll('.decision-roles dd')].map((item) => item.innerText),
      [canvas.width, canvas.height, data.some((value, index) => value !== data[index % 4])],
    ]
  })
  assert.deepEqual(answers, ['buy', 'up', 'sufficient', 'accept'])
  assert.deepEqual(pixels, [960, 440, true])
  const success = await counts(0, 0)
  assert.deepEqual(success.calls.map(({ candle, role }) => [candle, role]), [
    ['14:34', 'analyst'], ['14:34', 'reviewer'],
  ])

  await selectCandle('14:33')
  await button('Ask about chart')
  await text('.chart-decision', 'Final check · provider_error')
  await text('.chart-decision', 'OpenAI returned HTTP 503.')
  const failed = await counts(0, 0)
  assert.deepEqual(failed.calls.map(({ candle, role }) => [candle, role]), [
    ['14:34', 'analyst'], ['14:34', 'reviewer'], ['14:33', 'analyst'],
  ])
  console.log('Browser PASS: decoded chart, buy/up/sufficient and accept, visible provider HTTP 503, zero fills.')
}

async function checkAutomation() {
  await button('Restart replay')
  await text('.replay-progress dd', 'T14:30:05')
  for (const time of ['14:31:05', '14:32:05', '14:33:05', '14:34:05']) await step(time)
  await button('Start paper automation')
  await text('[aria-labelledby="automation-heading"] [role="status"]', 'Paper automation on')
  await text('[data-testid="automation-cash"]', '$1,000.00', true)
  await text('[data-testid="automation-shares"]', '0', true)
  await text('[aria-labelledby="paper-heading"]', 'zero fees')
  await counts(0, 0)

  await step('14:35:05')
  await job('14:34:00', 'buy', 'pending')
  await counts(1, 0)
  await step('14:35:15')
  await job('14:34:00', 'buy', 'filled', '4 share(s) at $100.00')
  const before = await counts(1, 1)
  assert.deepEqual(await fixture('duplicate', 'POST'), { row_id: null })
  assert.deepEqual(await counts(1, 1), before, 'Duplicate price changed the ledger or provider calls')

  await step('14:36:05')
  await job('14:35:00', 'hold', 'no_order')
  await step('14:37:05')
  await job('14:36:00', 'sell', 'pending')
  await counts(2, 1)
  await step('14:37:15')
  await job('14:36:00', 'sell', 'filled', '1 share(s) at $110.00')
  await step('14:37:25')
  await step('14:38:05')
  await job('14:37:00', 'hold', 'no_order')
  await step('14:39:05')
  await job('14:38:00', 'buy', 'skipped', 'reject_unsupported_signal')
  await counts(2, 2)

  await step('14:40:05')
  await job('14:39:00', 'hold', 'no_order')
  await step('14:41:05')
  await job('14:40:00', 'close', 'pending')
  await counts(3, 2)
  await step('14:41:15')
  await job('14:40:00', 'close', 'filled', '3 share(s) at $105.00')
  await text('[data-testid="paper-cash"]', '$1,025.00', true)
  await text('[data-testid="paper-shares"]', '0', true)
  const last = await wait('last fill P&L', () => {
    const rows = [...document.querySelectorAll('[aria-label="Paper profit and loss"] tbody tr')]
    const fill = rows.find((row) => row.cells[2].innerText === 'Fill' && row.cells[4].innerText === '$105.00')
    return fill && [...fill.cells].slice(5).map((cell) => cell.innerText)
  })
  assert.deepEqual(last, ['$1,025.00', '0', '$25.00', '$0.00', '$25.00'])

  await button('Stop paper automation')
  await text('[aria-labelledby="automation-heading"] [role="status"]', 'Paper automation off')
  const stopped = await counts(3, 3)
  await step('14:42:05')
  await step('14:43:05')
  await text('.replay-controls', 'Replay complete')
  await selectCandle('14:42')
  await text('.inspector-note', 'Interval ended')
  await text('.indicator-legend', 'SMA crossed up · Nov 9, 2026 14:42 UTC')
  assert.deepEqual(await counts(3, 3), stopped, 'A completed candle after Stop changed paper or model state')
  console.log('Browser PASS: 4 at $100, 1 at $110, 3 at $105; $1,025 cash, 0 shares, $25 realized, $0 unrealized; duplicate, veto and Stop added no fill.')
}

try {
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('Browser could not open DevTools connection')), 8000)
    socket.addEventListener('open', () => { clearTimeout(timer); resolve() }, { once: true })
    socket.addEventListener('error', () => { clearTimeout(timer); reject(new Error('Browser DevTools connection failed')) }, { once: true })
  })
  const { targetId } = await send('Target.createTarget', { url: 'about:blank' }, false)
  const attached = await send('Target.attachToTarget', { targetId, flatten: true }, false)
  sessionId = attached.sessionId
  await send('Page.enable')
  await send('Page.navigate', { url: base + '/?source=replay' })
  await button('Start replay')
  await text('.replay-progress dd', 'T14:30:05')
  for (const time of ['14:31:05', '14:32:05', '14:33:05', '14:34:05', '14:35:05']) await step(time)
  await checkChart()
  await checkAutomation()
} catch (error) {
  console.error(error)
  if (sessionId) {
    try {
      console.error('Visible dashboard at failure:\n' + await evaluate(() => document.body.innerText.slice(0, 6500)))
    } catch { /* the browser may already have exited */ }
  }
  process.exitCode = 1
} finally {
  if (socket.readyState === WebSocket.OPEN) {
    await send('Browser.close', {}, false).catch(() => {})
    socket.close()
  }
}
