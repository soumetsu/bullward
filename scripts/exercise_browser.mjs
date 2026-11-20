import assert from 'node:assert/strict'
import { mkdirSync, writeFileSync } from 'node:fs'
import { setTimeout as delay } from 'node:timers/promises'

const [endpoint, base] = process.argv.slice(2)
const live = process.env.INVEST_EXERCISE_LIVE_CHECK === '1'
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
  const deadline = Date.now() + (live ? 25_000 : 15_000)
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
  const cases = [['GREEN', 'long', ['green', 'green']], ['RED', 'short', ['red', 'red']], ['MIX', 'wait', ['green', 'red']]]
  if (!live) cases.push(['FLAT', 'wait', ['gray', 'green']], ['FAIL', null, ['green', 'green']], ['GAP', 'wait'])
  for (const [symbol, expected, expectedColors] of cases) {
    await field('.replay-controls select', symbol)
    await button('Start replay')
    await button('Play')
    await text('.replay-controls', 'Replay complete')
    assert.equal(await evaluate(() => document.querySelector('.analysis-grid').getBoundingClientRect().top < document.querySelector('[aria-labelledby="indicator-heading"]').getBoundingClientRect().top), true, 'Chart should precede indicator controls')
    const workstationSession = sessionId
    let popoutTarget
    if (symbol === 'GAP') {
      await send('Runtime.evaluate', { expression: 'document.querySelector(".inspector button").click()', userGesture: true })
      for (let attempt = 0; attempt < 50 && !popoutTarget; attempt++) {
        const { targetInfos } = await send('Target.getTargets', {}, false)
        popoutTarget = targetInfos.find(({ url }) => url.startsWith(base) && new URL(url).searchParams.get('panel') === 'inspector')
        if (!popoutTarget) await delay(100)
      }
      assert.ok(popoutTarget, 'Inspector pop-out should open a new window')
      const attached = await send('Target.attachToTarget', { targetId: popoutTarget.targetId, flatten: true }, false)
      sessionId = attached.sessionId
    }
    if (symbol === 'GREEN') {
      assert.equal(await evaluate(() => {
        window.scrollTo(0, 0)
        return document.querySelector('#exercise-title').getBoundingClientRect().top >= innerHeight
      }), true, 'Decision panel should start below the viewport')
    }
    await button('Ask about chart')
    await wait('Decision API heading scrolled into view and focused', () => {
      const heading = document.querySelector('#exercise-title')
      const bounds = heading.getBoundingClientRect()
      return document.activeElement === heading && bounds.top >= 0 && bounds.bottom <= innerHeight
    })
    await text('.chart-decision', symbol === 'FAIL' ? 'provider_error' : symbol === 'GAP' ? 'stale_chart' : 'completed')
    await text('.exercise-result', 'Signal minute: 2026-11-09T14:32:00Z')
    if (expected) await text('.exercise-result', (symbol === 'GAP' ? 'Freshness guard: ' : 'Model choice: ') + expected)
    await wait('Decision result visible in the viewport', () => {
      const result = document.querySelector('.exercise-result')?.getBoundingClientRect()
      return result && result.top >= 0 && result.bottom <= innerHeight
    })
    if (live) {
      const displayed = await evaluate(() => document.querySelector('.exercise-result').innerText)
      assert.match(displayed, /Choice probability: (?:\d{1,3}\.\d%|Not available)/)
      console.log(displayed)
    } else if (!['FAIL', 'GAP'].includes(symbol)) {
      await text('.exercise-result', '91.0%')
    }
    const pixels = await evaluate(async () => {
      const image = document.querySelector('.chart-decision img')
      await image.decode()
      const canvas = document.createElement('canvas')
      canvas.width = image.naturalWidth; canvas.height = image.naturalHeight
      const ctx = canvas.getContext('2d'); ctx.drawImage(image, 0, 0)
      // Read the center of each candle's column in the price pane. This excludes the volume pane and price scale.
      const palette = { green: [20, 133, 116], red: [200, 91, 79], gray: [104, 117, 133] }
      const colors = [0.235, 0.705].map((fraction) => {
        const data = ctx.getImageData(Math.floor(canvas.width * fraction), 32, 1, 285).data
        const counts = { green: 0, red: 0, gray: 0 }
        for (let i = 0; i < data.length; i += 4) {
          for (const [color, rgb] of Object.entries(palette)) {
            if (rgb.every((value, channel) => value === data[i + channel])) counts[color]++
          }
        }
        const [color, count] = Object.entries(counts).sort((a, b) => b[1] - a[1])[0]
        return count ? color : null
      })
      return { width: canvas.width, height: canvas.height, colors }
    })
    assert.deepEqual([pixels.width, pixels.height], [960, 472])
    if (expectedColors) assert.deepEqual(pixels.colors, expectedColors, symbol + ' captured candle body colors')
    await text('.exercise-flow', 'OpenAI Decisions')
    if (process.env.INVEST_EXERCISE_SCREENSHOTS && ['GREEN', 'RED', 'MIX'].includes(symbol)) {
      mkdirSync(process.env.INVEST_EXERCISE_SCREENSHOTS, { recursive: true })
      const png = await evaluate(() => document.querySelector('.chart-decision img').src.split(',')[1])
      writeFileSync(process.env.INVEST_EXERCISE_SCREENSHOTS + '/' + symbol + '.png', Buffer.from(png, 'base64'))
    }
    console.log('Browser PASS:', symbol, expected ?? 'provider failure', '14:32 signal, real labeled PNG')
    if (popoutTarget) {
      await send('Target.closeTarget', { targetId: popoutTarget.targetId }, false)
      sessionId = workstationSession
    }
  }
  console.log('Browser PASS: Ask about chart brought the Decision API heading and result into view, with focus on the heading.')

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
