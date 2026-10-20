// Image level-of-detail for panel content.
//
// Boards often show big images small: a 1920px screenshot as a 95px thumbnail,
// a 9000px map zoomed out to 20%. The browser then resamples the full image
// on every repaint of the tiles it sits in, and decodes it again each time a
// culled panel scrolls back in (a remount makes a fresh <img>). Both showed up
// as 40-60 ms pan hitches on real boards.
//
// So every large <img> inside the camera layer is swapped for a copy sized to
// what the current zoom actually needs (x1.5 steps, x1.25 headroom),
// generated once off the main thread and cached by source. A remounted <img>
// is swapped before the browser decodes the original. When the camera settles
// at a zoom that needs more pixels, visible images are upgraded (back to the
// original at full need). Small images, GIFs, SVGs and iframes are untouched;
// a component that changes its own `src` simply gets re-evaluated.
import { effect } from 'alien-signals'
import { store } from '../engine/store'

const MIN_PIXELS = 1_000_000 // below ~1 MP an image isn't worth swapping
const HEADROOM = 1.25
const ORIG = 'data-pc-lod-src' // the component's own src, kept on the element

interface Entry {
  orig: string
  w: number
  h: number
  bitmap?: Promise<ImageBitmap | null> // full decode, made once
  levels: Map<number, Promise<string | null>> // target width -> blob URL
  used: number
}
const cache = new Map<string, Entry>()
const ours = new Set<string>() // blob URLs we minted (never re-processed)
const MAX_ENTRIES = 300

// Data URIs can be megabytes: key on length + samples, not the whole string.
function keyOf(src: string): string {
  if (src.length < 512) return src
  const mid = src.length >> 1
  return `${src.length}:${src.slice(0, 96)}:${src.slice(mid, mid + 64)}:${src.slice(-64)}`
}

function eligible(src: string): boolean {
  if (!src || ours.has(src)) return false
  return !/^data:image\/(gif|svg)|\.(gif|svg)(\?|#|$)/i.test(src)
}

function zoom(): number {
  return store.camera().z || 1
}

// The width in device pixels this <img> needs at the current zoom.
function neededWidth(img: HTMLImageElement): number {
  const r = img.getBoundingClientRect()
  return r.width * (window.devicePixelRatio || 1)
}

function levelFor(e: Entry, need: number): number {
  if (need <= 0) return 0
  let w = 64
  while (w < need * HEADROOM) w = Math.ceil(w * 1.5)
  return w >= e.w * 0.7 ? e.w : w // close to native: just use the original
}

function evict(): void {
  if (cache.size <= MAX_ENTRIES) return
  const oldest = [...cache.entries()].sort((a, b) => a[1].used - b[1].used).slice(0, cache.size - MAX_ENTRIES)
  for (const [k, e] of oldest) {
    for (const p of e.levels.values()) p.then((u) => u && URL.revokeObjectURL(u))
    cache.delete(k)
  }
}

function levelUrl(e: Entry, w: number): Promise<string | null> {
  let p = e.levels.get(w)
  if (p) return p
  if (!e.bitmap) {
    e.bitmap = (async () => {
      try {
        const im = new Image()
        im.src = e.orig
        await im.decode()
        return await createImageBitmap(im)
      } catch {
        return null
      }
    })()
  }
  const h = Math.max(1, Math.round((e.h * w) / e.w))
  p = (async () => {
    const full = await e.bitmap!
    if (!full) return null
    try {
      const bm = await createImageBitmap(full, { resizeWidth: w, resizeHeight: h, resizeQuality: 'high' })
      const cv = new OffscreenCanvas(w, h)
      cv.getContext('bitmaprenderer')!.transferFromImageBitmap(bm)
      const blob = await cv.convertToBlob({ type: 'image/png' })
      const url = URL.createObjectURL(blob)
      ours.add(url)
      return url
    } catch {
      return null
    }
  })()
  e.levels.set(w, p)
  return p
}

// An <img> with no CSS size takes its box from the file (9000px wide map ->
// a 9000px box): a smaller copy would shrink it. Before the first swap, pin
// such an image's box to the original's intrinsic size with inline styles
// (restored when it goes back to the original). CSS-sized images need
// nothing — the copy keeps the aspect ratio.
const PIN = 'data-pc-lod-pin' // the inline width/height we replaced, as JSON
function pinIfIntrinsic(img: HTMLImageElement, e: Entry): void {
  if (img.hasAttribute(PIN)) return
  const ow = img.offsetWidth
  const oh = img.offsetHeight
  const widthIntrinsic = ow === 0 || Math.abs(ow - e.w) <= 1
  if (!widthIntrinsic) return
  img.setAttribute(PIN, JSON.stringify({ w: img.style.width, h: img.style.height }))
  img.style.width = `${e.w}px`
  if (oh === 0 || Math.abs(oh - e.h) <= 1) img.style.height = `${e.h}px`
}
function unpin(img: HTMLImageElement): void {
  const saved = img.getAttribute(PIN)
  if (saved === null) return
  img.removeAttribute(PIN)
  try {
    const { w, h } = JSON.parse(saved)
    img.style.width = w
    img.style.height = h
  } catch {
    /* leave as is */
  }
}
function swap(img: HTMLImageElement, e: Entry, url: string): void {
  if (url === e.orig) unpin(img)
  else pinIfIntrinsic(img, e)
  if (img.src !== url) img.src = url
}

// Point `img` at the right level for the current zoom (async; no-op if the
// element's source changed meanwhile).
async function apply(img: HTMLImageElement): Promise<void> {
  const orig = img.getAttribute(ORIG)
  if (!orig) return
  const e = cache.get(keyOf(orig))
  if (!e) return
  e.used = performance.now()
  const w = levelFor(e, neededWidth(img))
  if (!w) return
  const want = w >= e.w ? orig : await levelUrl(e, w)
  if (!want || img.getAttribute(ORIG) !== orig) return
  swap(img, e, want)
}

// First sight of an <img> (inserted, or its src set by the component).
function adopt(img: HTMLImageElement): void {
  const src = img.getAttribute('src') || ''
  if (!eligible(src)) return
  unpin(img)
  img.setAttribute(ORIG, src)
  const e = cache.get(keyOf(src))
  if (e) {
    // Known image: swap before the browser spends a decode on the original.
    const w = levelFor(e, neededWidth(img))
    if (w && w < e.w) {
      const p = e.levels.get(w)
      if (p) {
        p.then((u) => {
          if (u && img.getAttribute(ORIG) === src) swap(img, e, u)
        })
        return
      }
    }
    void apply(img)
    return
  }
  const onload = () => {
    img.removeEventListener('load', onload)
    if (img.getAttribute(ORIG) !== src) return
    const nw = img.naturalWidth
    const nh = img.naturalHeight
    if (nw * nh < MIN_PIXELS) return
    cache.set(keyOf(src), { orig: src, w: nw, h: nh, levels: new Map(), used: performance.now() })
    evict()
    void apply(img)
  }
  if (img.complete && img.naturalWidth) onload()
  else img.addEventListener('load', onload)
}

export function attachImageLod(layer: HTMLElement): () => void {
  const scan = (root: Element) => {
    if (root instanceof HTMLImageElement) adopt(root)
    else root.querySelectorAll?.('img').forEach((i) => adopt(i as HTMLImageElement))
  }
  const mo = new MutationObserver((muts) => {
    for (const m of muts) {
      if (m.type === 'attributes') {
        const img = m.target as HTMLImageElement
        const src = img.getAttribute('src') || ''
        if (!ours.has(src) && src !== img.getAttribute(ORIG)) adopt(img) // the component set a new src
      } else {
        m.addedNodes.forEach((n) => n.nodeType === 1 && scan(n as Element))
      }
    }
  })
  mo.observe(layer, { childList: true, subtree: true, attributes: true, attributeFilter: ['src'] })
  scan(layer)
  // When the camera settles, re-level the images on screen (sharper after a
  // zoom-in, lighter after a zoom-out).
  let t: ReturnType<typeof setTimeout> | null = null
  let lastZ = zoom()
  const stop = effect(() => {
    const z = store.camera().z
    if (z === lastZ) return
    lastZ = z
    if (t) clearTimeout(t)
    t = setTimeout(() => {
      t = null
      const vw = window.innerWidth
      const vh = window.innerHeight
      layer.querySelectorAll(`img[${ORIG}]`).forEach((n) => {
        const r = (n as HTMLElement).getBoundingClientRect()
        if (r.right < -vw || r.left > 2 * vw || r.bottom < -vh || r.top > 2 * vh) return
        void apply(n as HTMLImageElement)
      })
    }, 250)
  })
  return () => {
    mo.disconnect()
    stop()
    if (t) clearTimeout(t)
  }
}
