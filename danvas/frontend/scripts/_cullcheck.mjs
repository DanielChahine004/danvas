// Regression check for the cull/remount value bug: set the slider,
// right-drag-pan it out of view (unmount), pan back (remount), and assert
// the thumb shows the SET value, not the registered default.
// Run: serve a slider on :8933, then `node scripts/_cullcheck.mjs`.
import puppeteer from 'puppeteer-core'

const CHROME = process.env.CHROME
  || (process.platform === 'win32'
      ? 'C:/Program Files/Google/Chrome/Application/chrome.exe'
      : process.platform === 'darwin'
        ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
        : '/usr/bin/google-chrome')
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const b = await puppeteer.launch({
  executablePath: CHROME, headless: 'new',
  args: ['--no-sandbox', '--disable-gpu'],
})
const page = await b.newPage()
await page.setViewport({ width: 1000, height: 700 })
await page.goto('http://127.0.0.1:8933', { waitUntil: 'networkidle2', timeout: 25000 })
await page.waitForSelector('[data-pc-panel-id] input[type="range"]', { timeout: 15000 })
await sleep(1000)

// drive the slider to 4242 like a user drag
await page.evaluate(() => {
  const r = document.querySelector('[data-pc-panel-id] input[type="range"]')
  const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
  set.call(r, '4242')
  r.dispatchEvent(new Event('input', { bubbles: true }))
  r.dispatchEvent(new Event('change', { bubbles: true }))
})
await sleep(800)   // let the input frame round-trip (broker echoes a post)

// right-drag pans the canvas (wheel zooms) — drag until the panel content
// unmounts (its wrapper stays, the input goes)
async function panBy(dy) {
  await page.mouse.move(500, dy > 0 ? 80 : 620)
  await page.mouse.down({ button: 'right' })
  await page.mouse.move(500, (dy > 0 ? 80 : 620) + dy, { steps: 8 })
  await page.mouse.up({ button: 'right' })
  await sleep(120)
}
let culled = false
for (let i = 0; i < 20 && !culled; i++) {
  await panBy(-540)
  culled = await page.evaluate(() =>
    !!document.querySelector('[data-pc-panel-id]')
    && !document.querySelector('[data-pc-panel-id] input[type="range"]'))
}
if (!culled) { console.log('FAIL: never culled'); process.exit(1) }
console.log('culled OK (wrapper alive, content unmounted)')

// pan back until it remounts
let back = false
for (let i = 0; i < 30 && !back; i++) {
  await panBy(540)
  back = await page.evaluate(() => !!document.querySelector('[data-pc-panel-id] input[type="range"]'))
}
if (!back) { console.log('FAIL: never remounted'); process.exit(1) }
await sleep(500)

const value = await page.evaluate(() => document.querySelector('[data-pc-panel-id] input[type="range"]').value)
console.log(value === '4242'
  ? 'PASS: remounted slider shows 4242'
  : `FAIL: remounted slider shows ${value} (expected 4242)`)
await b.close()
process.exit(value === '4242' ? 0 : 1)
