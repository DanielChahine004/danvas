// Export: render panels to a PNG (canvas.screenshot() / get_image) and the
// snapshot get/put hooks. This is the engine's image export. The image is a
// *scene* export — shapes at page coords, framed to their bounds, at z=1 —
// independent of any viewer's camera, exactly like the old build.
//
// How it stays camera-independent without disturbing the live view:
// modern-screenshot CLONES the target node, so we hand it the camera layer with
// a one-off transform override (translate(-bounds) scale(1)) applied to the clone
// only — the on-screen canvas never moves. Culled (unmounted) target panels are
// force-mounted first via the exportTargets signal so they exist in the DOM to be
// cloned; non-target panels are dropped from the clone by the `filter`.
import { signal } from 'alien-signals'
import { store } from './store'
import { editor } from './editor'
import { recordBBox } from './hittest'
import type { PanelRecord, WriteSignal } from './types'

// Panels to force-mount during an export (so culled ones are captured). null when
// not exporting. PanelLayer reads this.
export const exportTargets = signal<Set<string> | null>(null) as WriteSignal<Set<string> | null>

function boundsOf(ids: string[]): { x: number; y: number; w: number; h: number } | null {
  let minX = Infinity,
    minY = Infinity,
    maxX = -Infinity,
    maxY = -Infinity
  let any = false
  for (const id of ids) {
    const r = store.peek(id) as PanelRecord | undefined
    if (!r) continue
    any = true
    minX = Math.min(minX, r.x)
    minY = Math.min(minY, r.y)
    maxX = Math.max(maxX, r.x + r.props.w)
    maxY = Math.max(maxY, r.y + r.props.h)
  }
  return any ? { x: minX, y: minY, w: maxX - minX, h: maxY - minY } : null
}

// Block until no panel is still showing its "compiling…"/"loading…" placeholder,
// so a screenshot captures rendered UI, not a spinner. Bounded.
function waitForPanelsReady(budgetMs = 8000): Promise<void> {
  return new Promise((resolve) => {
    const t0 = performance.now()
    const poll = () => {
      const busy = document.querySelector('[data-pc-compiling],[data-pc-loading]')
      if (!busy || performance.now() - t0 > budgetMs) {
        setTimeout(resolve, 120) // small settle so the just-mounted content paints
        return
      }
      setTimeout(poll, 50)
    }
    poll()
  })
}

const allPanelIds = (): string[] =>
  store.ids().filter((id) => {
    const r = store.peek(id)
    return !!r && r.typeName === 'panel'
  })

// Render `shapeIds` (empty = whole page) to a PNG; resolve with raw base64 (no
// data: prefix) to match the wire (`image` frame), or an error string.
export async function toImage(shapeIds: string[]): Promise<{ base64?: string; error?: string }> {
  const container = editor.getContainer()
  const layer = container?.querySelector('[data-pc-camera-layer]') as HTMLElement | null
  if (!layer) return { error: 'no canvas mounted' }

  const wanted = shapeIds.length
    ? shapeIds.map((s) => (s.startsWith('shape:') ? s : `shape:${s}`)).filter((id) => store.has(id))
    : allPanelIds()
  if (!wanted.length) return { error: 'nothing to capture' }

  const bounds = boundsOf(wanted)
  if (!bounds) return { error: 'no bounds' }

  const targetSet = new Set(wanted)
  exportTargets(targetSet)
  // Let force-mount flush, then wait for content (Plotly, etc.) to be ready.
  await new Promise((r) => requestAnimationFrame(() => r(null)))
  await waitForPanelsReady()

  try {
    const { domToPng } = await import('modern-screenshot')
    const isDark = store.instance().darkMode
    const dataUrl = await domToPng(layer, {
      width: Math.ceil(bounds.w),
      height: Math.ceil(bounds.h),
      backgroundColor: isDark ? '#1d1d20' : '#fbfbfb',
      scale: 2, // crisp text for LLM legibility
      // Frame the bounds at z=1 on the CLONE only (live view untouched).
      style: { transform: `translate(${-bounds.x}px, ${-bounds.y}px) scale(1)`, transformOrigin: '0 0' },
      // Drop non-target panels from the clone (so a subset shot shows only them).
      filter: (node: any) => {
        const pid = node && node.getAttribute && node.getAttribute('data-pc-panel-id')
        return !(pid && !targetSet.has(pid))
      },
    })
    return { base64: dataUrl.split(',')[1] }
  } catch (e: any) {
    return { error: String((e && e.message) || e) }
  } finally {
    exportTargets(null)
  }
}

// --- user-facing export (right-click → download PNG / SVG) -------------------
// Exports an arbitrary set of records (panels and/or drawings) framed to their
// bounds at z=1 and downloads it. Panels not in the set are dropped from the
// clone (same filter as toImage); drawings render from the live SVG. SVG export
// uses modern-screenshot's domToSvg (DOM wrapped in a foreignObject).
function boundsOfRecords(ids: string[]): { x: number; y: number; w: number; h: number } | null {
  let minX = Infinity,
    minY = Infinity,
    maxX = -Infinity,
    maxY = -Infinity,
    any = false
  for (const id of ids) {
    const b = recordBBox(store.peek(id))
    if (!b) continue
    any = true
    minX = Math.min(minX, b.x)
    minY = Math.min(minY, b.y)
    maxX = Math.max(maxX, b.x + b.w)
    maxY = Math.max(maxY, b.y + b.h)
  }
  return any ? { x: minX, y: minY, w: maxX - minX, h: maxY - minY } : null
}

function triggerDownload(dataUrl: string, filename: string): void {
  const a = document.createElement('a')
  a.href = dataUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
}

// Shared render pipeline: frame `ids` to their bounds at z=1, force-mount target
// panels, hand the modern-screenshot module + camera layer + options to `fn`.
async function withExport(
  ids: string[],
  transparentBg: boolean,
  fn: (mod: any, layer: HTMLElement, common: any) => Promise<void>,
): Promise<{ ok: boolean; error?: string }> {
  const container = editor.getContainer()
  const layer = container?.querySelector('[data-pc-camera-layer]') as HTMLElement | null
  if (!layer) return { ok: false, error: 'no canvas mounted' }
  const wanted = ids.filter((id) => store.has(id))
  if (!wanted.length) return { ok: false, error: 'nothing to export' }
  const bounds = boundsOfRecords(wanted)
  if (!bounds || bounds.w <= 0 || bounds.h <= 0) return { ok: false, error: 'no bounds' }

  const PAD = 8
  const panelTargets = new Set(wanted.filter((id) => store.peek(id)?.typeName === 'panel'))
  exportTargets(panelTargets)
  await new Promise((r) => requestAnimationFrame(() => r(null)))
  await waitForPanelsReady()
  try {
    const mod: any = await import('modern-screenshot')
    const isDark = store.instance().darkMode
    const common = {
      width: Math.ceil(bounds.w + PAD * 2),
      height: Math.ceil(bounds.h + PAD * 2),
      backgroundColor: transparentBg ? 'transparent' : isDark ? '#1d1d20' : '#fbfbfb',
      style: { transform: `translate(${PAD - bounds.x}px, ${PAD - bounds.y}px) scale(1)`, transformOrigin: '0 0' },
      filter: (node: any) => {
        const pid = node && node.getAttribute && node.getAttribute('data-pc-panel-id')
        return !(pid && !panelTargets.has(pid))
      },
    }
    await fn(mod, layer, common)
    return { ok: true }
  } catch (e: any) {
    return { ok: false, error: String((e && e.message) || e) }
  } finally {
    exportTargets(null)
  }
}

export function exportRecordsToFile(ids: string[], format: 'png' | 'svg'): Promise<{ ok: boolean; error?: string }> {
  return withExport(ids, format === 'svg', async (mod, layer, common) => {
    const dataUrl = format === 'svg' ? await mod.domToSvg(layer, common) : await mod.domToPng(layer, { ...common, scale: 2 })
    triggerDownload(dataUrl, `danvas-export.${format}`)
  })
}

// Copy the records to the clipboard as a PNG (so they can be pasted elsewhere).
export function copyRecordsToClipboard(ids: string[]): Promise<{ ok: boolean; error?: string }> {
  return withExport(ids, false, async (mod, layer, common) => {
    const blob: Blob = await mod.domToBlob(layer, { ...common, scale: 2 })
    if (!navigator.clipboard || typeof (window as any).ClipboardItem === 'undefined') throw new Error('clipboard image write unsupported')
    await navigator.clipboard.write([new (window as any).ClipboardItem({ 'image/png': blob })])
  })
}

// Copy the records to the clipboard as a (transparent-bg) SVG. domToSvg returns a
// data URL; we decode the markup and put it on the clipboard. Vector-aware targets
// (Figma, Illustrator, Inkscape, editors) accept `image/svg+xml`; we also write the
// raw markup as `text/plain` so a plain paste yields the SVG source. Browsers that
// reject the `image/svg+xml` clipboard type fall back to a text-only write.
export function copyRecordsToClipboardSvg(ids: string[]): Promise<{ ok: boolean; error?: string }> {
  return withExport(ids, true, async (mod, layer, common) => {
    const dataUrl: string = await mod.domToSvg(layer, common)
    const svg = await (await fetch(dataUrl)).text()
    if (!navigator.clipboard) throw new Error('clipboard unsupported')
    const CI = (window as any).ClipboardItem
    if (CI) {
      try {
        await navigator.clipboard.write([
          new CI({
            'image/svg+xml': new Blob([svg], { type: 'image/svg+xml' }),
            'text/plain': new Blob([svg], { type: 'text/plain' }),
          }),
        ])
        return
      } catch {
        // Some browsers reject the image/svg+xml clipboard type — fall back to text.
      }
    }
    await navigator.clipboard.writeText(svg)
  })
}

// --- snapshot (user free-form drawings) -------------------------------------
// The drawing layer lands in M8; until then there are no free-form drawings, so
// get returns nothing and put is a no-op. Panels/arrows are recreated from Python
// code, so they're never part of a snapshot.
export function getContent(_panelIds: string[]): any {
  return null
}

export function putContent(_data: any): void {
  // no-op until the drawing layer exists
}
