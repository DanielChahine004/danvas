// Right-click context menu state. A plain right-click (no pan-drag) resolves what
// it landed on — a panel, a drawing/arrow, or the current selection — and opens a
// menu there. The pan handler (input.ts) calls openContextMenuAt only when the
// right-button gesture didn't move, so right-drag still pans.
import { signal } from 'alien-signals'
import { store } from './store'
import { screenToPage } from './editor'
import { hitTestDrawing, hitTestArrow } from './hittest'
import type { WriteSignal } from './types'

export interface ContextMenuState {
  x: number
  y: number
  ids: string[]
}
export const contextMenu = signal<ContextMenuState | null>(null) as WriteSignal<ContextMenuState | null>

export function openContextMenuAt(clientX: number, clientY: number, target: EventTarget | null): void {
  const sel = store.instance().selectedIds
  let ids: string[] = []

  const panelEl = (target as HTMLElement | null)?.closest?.('[data-pc-panel-id]') as HTMLElement | null
  if (panelEl) {
    const id = panelEl.getAttribute('data-pc-panel-id')!
    ids = sel.includes(id) && sel.length > 1 ? sel.slice() : [id]
  } else {
    const z = store.camera().z
    const pt = screenToPage({ x: clientX, y: clientY })
    const hit = hitTestDrawing(pt, 6 / z) || hitTestArrow(pt, 8 / z)
    if (hit) ids = sel.includes(hit) && sel.length > 1 ? sel.slice() : [hit]
    else if (sel.length) ids = sel.slice() // empty spot but something's selected → act on it
  }

  if (!ids.length) {
    contextMenu(null)
    return
  }
  // Select what we're acting on, so the menu and the canvas agree.
  store.transact('local', () => store.instance({ ...store.instance(), selectedIds: ids }))
  contextMenu({ x: clientX, y: clientY, ids })
}

export function closeContextMenu(): void {
  if (contextMenu()) contextMenu(null)
}
