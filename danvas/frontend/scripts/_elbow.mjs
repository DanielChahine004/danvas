import puppeteer from 'puppeteer-core'
const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const b = await puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox', '--disable-gpu'] })
const page = await b.newPage()
await page.setViewport({ width: 1200, height: 880, deviceScaleFactor: 1 })
const errs = []
page.on('pageerror', (e) => errs.push(String(e)))
await page.goto('http://localhost:5173', { waitUntil: 'networkidle2', timeout: 25000 })
await page.waitForFunction('window.__danvas && window.__danvas.store', { timeout: 15000 })
await sleep(500)

const ARROW = 'arrow:__elbowtest'
// Seed two panels + a connector elbow arrow (source 'remote' => never echoed to backend).
await page.evaluate((arrowId) => {
  const { store } = window.__danvas
  store.camera({ x: 0, y: 0, z: 1 })
  store.transact('remote', () => {
    store.put({ typeName: 'panel', id: 'shape:__eA', shapeType: 'pcReact', x: 220, y: 200, rotation: 0, opacity: 1, isLocked: false, index: 'a1', props: { w: 200, h: 120 }, meta: {} })
    store.put({ typeName: 'panel', id: 'shape:__eB', shapeType: 'pcReact', x: 720, y: 520, rotation: 0, opacity: 1, isLocked: false, index: 'a2', props: { w: 200, h: 120 }, meta: {} })
    store.put({ typeName: 'arrow', id: arrowId, start: 'shape:__eA', end: 'shape:__eB', opacity: 1, index: 'a3', props: { arrowKind: 'elbow' } })
  })
  // select the arrow so the overlay renders its handles
  store.transact('local', () => store.instance({ ...store.instance(), selectedIds: [arrowId] }))
}, ARROW)
await sleep(400)

const R = { errs }
const handleRects = () =>
  page.evaluate(() =>
    [...document.querySelectorAll('rect')]
      .filter((r) => /resize/.test(r.style.cursor))
      .map((r) => {
        const b = r.getBoundingClientRect()
        return { cx: b.x + b.width / 2, cy: b.y + b.height / 2, cursor: r.style.cursor }
      }),
  )
const arrowProps = () => page.evaluate((id) => ({ ...window.__danvas.store.peek(id).props }), ARROW)
const visibleCount = () =>
  page.evaluate((id) => {
    const { store } = window.__danvas
    // count points on the rendered connector path in the drawing layer
    const paths = [...document.querySelectorAll('[data-pc-drawings] path')]
    return { props: store.peek(id).props, npaths: paths.length }
  }, ARROW)

// 1) Default single-bend elbow => exactly ONE draggable segment handle.
let h = await handleRects()
R.defaultHandles = h.length
R.defaultProps = await arrowProps()

// 2) Double-click the handle to ADD a bend (a jog => +2 coords => more handles).
if (h.length) {
  const t = h[0]
  await page.evaluate(
    ({ x, y }) => {
      const el = [...document.querySelectorAll('rect')].find((r) => /resize/.test(r.style.cursor))
      el.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, clientX: x, clientY: y }))
    },
    { x: t.cx, y: t.cy },
  )
  await sleep(300)
}
let h2 = await handleRects()
R.afterAddBendHandles = h2.length
R.afterAddBendCoords = (await arrowProps()).elbowCoords?.length ?? 0

// 3) Drag one handle a healthy distance => coords change (the bend moves).
const beforeMove = await arrowProps()
if (h2.length) {
  const t = h2[0]
  const dxv = t.cursor.includes('ns') ? 0 : 90
  const dyv = t.cursor.includes('ns') ? 90 : 0
  await page.mouse.move(t.cx, t.cy)
  await page.mouse.down()
  for (let i = 1; i <= 10; i++) {
    await page.mouse.move(t.cx + (dxv * i) / 10, t.cy + (dyv * i) / 10)
    await sleep(8)
  }
  await page.mouse.up()
  await sleep(250)
}
const afterMove = await arrowProps()
R.moveChangedCoords = JSON.stringify(beforeMove.elbowCoords) !== JSON.stringify(afterMove.elbowCoords)

// 4) Make a deliberately tiny jog, then a no-move drag on a handle (pointerdown+up)
//    fires the release-collapse: the sub-min jog pair should be removed.
const tinyBefore = await page.evaluate((id) => {
  const { store } = window.__danvas
  const cur = store.peek(id)
  const cs = (cur.props.elbowCoords || []).slice()
  // shrink the last jog's hop to ~4px so its leg is far below the collapse min
  if (cs.length >= 3) cs[cs.length - 1] = cs[cs.length - 3] + 4
  store.transact('local', () => store.patch(id, { props: { elbowCoords: cs } }))
  return cs.length
}, ARROW)
R.tinyJogCoords = tinyBefore
await sleep(200)
let h3 = await handleRects()
if (h3.length) {
  const t = h3[h3.length - 1]
  await page.mouse.move(t.cx, t.cy)
  await page.mouse.down()
  await page.mouse.move(t.cx + 1, t.cy + 1)
  await page.mouse.up()
  await sleep(300)
}
const afterCollapse = await handleRects()
R.afterCollapseHandles = afterCollapse.length
R.afterCollapseCoords = (await arrowProps()).elbowCoords?.length ?? 0
R.collapsed = R.afterCollapseCoords < tinyBefore

R.render = await visibleCount()

// cleanup so nothing lingers in the session
await page.evaluate((id) => {
  const { store } = window.__danvas
  store.transact('remote', () => {
    store.remove(id)
    store.remove('shape:__eA')
    store.remove('shape:__eB')
  })
  store.transact('local', () => store.instance({ ...store.instance(), selectedIds: [] }))
}, ARROW)

console.log(JSON.stringify(R, null, 1))
await b.close()
process.exit(0)
