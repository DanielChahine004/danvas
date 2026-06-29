// End-to-end smoke check for the M1 acceptance gate: load the frontend (vite dev,
// proxying /ws to a running danvas backend), confirm both React panels render,
// then drive the slider and confirm the label updates via the full Python
// round-trip. Needs `npm install puppeteer-core --no-save` and a running backend
// (examples/hello_world.py on :8000) plus `npm run dev` on :5173.
//
// Usage: node scripts/verify-e2e.mjs [url]
import puppeteer from 'puppeteer-core'

const URL = process.argv[2] || 'http://localhost:5173'
const CHROME =
  process.env.CHROME_PATH ||
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: 'new',
  args: ['--no-sandbox', '--disable-gpu'],
})
const page = await browser.newPage()
const logs = []
page.on('console', (m) => logs.push(`[console.${m.type()}] ${m.text()}`))
page.on('pageerror', (e) => logs.push(`[pageerror] ${e.message}`))

let ok = true
const fail = (m) => {
  ok = false
  console.log('FAIL:', m)
}

try {
  await page.goto(URL, { waitUntil: 'networkidle2', timeout: 30000 })
  await page.waitForSelector('input[type=range]', { timeout: 15000 })
  await page.waitForSelector('.pc-label', { timeout: 15000 })

  const sliderVal = await page.$eval('input[type=range]', (el) => el.value)
  const labelText = (await page.$eval('.pc-label', (el) => el.textContent)) || ''
  console.log('initial slider value:', sliderVal)
  console.log('initial label text  :', JSON.stringify(labelText))
  if (sliderVal !== '90') fail(`expected slider 90, got ${sliderVal}`)
  if (!/idle/.test(labelText)) fail(`expected label "idle", got ${JSON.stringify(labelText)}`)

  const panelCount = await page.$$eval('.pc-card', (els) => els.length)
  console.log('panel cards rendered:', panelCount)
  if (panelCount < 2) fail(`expected >=2 panels, got ${panelCount}`)

  await page.$eval('input[type=range]', (el) => {
    const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set
    set.call(el, '120')
    el.dispatchEvent(new Event('input', { bubbles: true }))
    el.dispatchEvent(new Event('change', { bubbles: true }))
  })

  await page
    .waitForFunction(() => /servo at 120/.test(document.querySelector('.pc-label')?.textContent || ''), { timeout: 8000 })
    .catch(() => {})
  const labelAfter = (await page.$eval('.pc-label', (el) => el.textContent)) || ''
  console.log('label after slider=120:', JSON.stringify(labelAfter))
  if (!/servo at 120/.test(labelAfter)) fail(`round-trip: label did not reach "servo at 120" (got ${JSON.stringify(labelAfter)})`)
} catch (e) {
  fail('exception: ' + (e && e.message))
}

console.log('\n--- page logs ---')
console.log(logs.join('\n') || '(none)')
await browser.close()
console.log('\n=== RESULT:', ok ? 'PASS' : 'FAIL', '===')
process.exit(ok ? 0 : 1)
