// Regression check for keep_mounted: a Model3D panel scrolled out of view
// keeps its iframe (hidden, not destroyed) — camera and tool state survive —
// while an ordinary panel beside it still culls. Serve a slider + model3d
// on :8936, then `node scripts/_keepcheck.mjs`.
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
  args: ['--no-sandbox', '--disable-gpu', '--enable-unsafe-swiftshader'],
})
const page = await b.newPage()
await page.setViewport({ width: 1000, height: 700 })
await page.goto('http://127.0.0.1:8936', { waitUntil: 'networkidle2', timeout: 25000 })
await page.waitForSelector('[data-pc-panel-id] iframe', { timeout: 20000 })
await sleep(4000)   // let xeokit boot inside the iframe

const state = () => page.evaluate(() => {
  const panels = [...document.querySelectorAll('[data-pc-panel-id]')]
  const m3d = panels.find((p) => p.querySelector('iframe'))
  return {
    iframe: !!m3d?.querySelector('iframe'),
    m3dVisibility: m3d ? getComputedStyle(m3d).visibility : null,
    slider: !!document.querySelector('[data-pc-panel-id] input[type="range"]'),
  }
})
const before = await state()
if (!before.iframe || !before.slider) {
  console.log('FAIL: setup —', JSON.stringify(before)); process.exit(1)
}

async function panBy(dy) {
  await page.mouse.move(500, dy > 0 ? 80 : 620)
  await page.mouse.down({ button: 'right' })
  await page.mouse.move(500, (dy > 0 ? 80 : 620) + dy, { steps: 8 })
  await page.mouse.up({ button: 'right' })
  await sleep(120)
}
// pan far enough that BOTH panels are past the cull margin
let out = null
for (let i = 0; i < 25; i++) {
  await panBy(-540)
  out = await state()
  if (!out.slider && out.m3dVisibility === 'hidden') break
}
if (out.slider) { console.log('FAIL: slider never culled'); process.exit(1) }
if (!out.iframe) { console.log('FAIL: model3d iframe was destroyed'); process.exit(1) }
if (out.m3dVisibility !== 'hidden') {
  console.log(`FAIL: kept panel not hidden (visibility=${out.m3dVisibility})`)
  process.exit(1)
}
console.log('culled state OK: slider unmounted, m3d iframe alive + hidden')

for (let i = 0; i < 35; i++) {
  await panBy(540)
  const s = await state()
  if (s.slider && s.m3dVisibility === 'visible') break
}
const after = await state()
console.log(after.iframe && after.slider && after.m3dVisibility === 'visible'
  ? 'PASS: m3d kept (same iframe), slider remounted'
  : `FAIL: ${JSON.stringify(after)}`)
await b.close()
process.exit(after.iframe && after.slider ? 0 : 1)
