// The "editor" handle — the engine's public face for the Preact panel layer.
// It is the engine's `editor` handle: the ported Card / ReactHost / App
// call the same method names, but each one reads/writes the engine's signals
// instead of a board library. Because the reads go through alien-signals, calling them
// inside a `useValue` selector subscribes the component to exactly those signals.
import { effect } from 'alien-signals'
import { store } from './store'
import type { Camera, Id, Tool } from './types'

let containerEl: HTMLElement | null = null

export function setContainer(el: HTMLElement | null): void {
  containerEl = el
}

function viewportScreenBounds() {
  const r = containerEl?.getBoundingClientRect()
  return {
    x: r?.left ?? 0,
    y: r?.top ?? 0,
    w: r?.width ?? window.innerWidth,
    h: r?.height ?? window.innerHeight,
  }
}

// Camera convention (matches the old build): a page point P appears on screen at
// ((P + camera) * z) + viewportOrigin. Invert for screenToPage.
export function screenToPage(client: { x: number; y: number }): { x: number; y: number } {
  const c = store.camera()
  const vsb = viewportScreenBounds()
  return { x: (client.x - vsb.x) / c.z - c.x, y: (client.y - vsb.y) / c.z - c.y }
}

export function pageToScreen(page: { x: number; y: number }): { x: number; y: number } {
  const c = store.camera()
  const vsb = viewportScreenBounds()
  return { x: (page.x + c.x) * c.z + vsb.x, y: (page.y + c.y) * c.z + vsb.y }
}

// As pageToScreen, but against a SPECIFIC camera rather than the live one. The
// selection overlay's pan-follow layer (SelectionOverlay) positions its children
// at the camera captured on its last render and then imperatively translates the
// whole layer by the live pan delta. A child that re-renders mid-pan (e.g. a live
// panel whose record changes) must therefore lay out against that SAME captured
// camera — using the live one would double-count the pan (the layer translate
// plus a fresh live position).
export function pageToScreenAt(page: { x: number; y: number }, c: Camera): { x: number; y: number } {
  const vsb = viewportScreenBounds()
  return { x: (page.x + c.x) * c.z + vsb.x, y: (page.y + c.y) * c.z + vsb.y }
}

function viewportPageBounds() {
  const c = store.camera()
  const vsb = viewportScreenBounds()
  const w = vsb.w / c.z
  const h = vsb.h / c.z
  const x = -c.x
  const y = -c.y
  return { x, y, w, h, center: { x: x + w / 2, y: y + h / 2 } }
}

// session-scope change feed (camera + interaction state), the substrate for
// the session-scope store feed that ReactHost.viewport() uses.
const sessionListeners = new Set<() => void>()
effect(() => {
  // touch the signals so this effect re-runs whenever they change
  store.camera()
  const i = store.instance()
  void i.tool
  void i.hoveredId
  void i.selectedIds
  for (const cb of sessionListeners) cb()
})

// The editor object. Method names match the original API so the ported
// components need only trivial edits (shape.type → shape.shapeType where the
// record field differs).
export const editor = {
  // --- camera & coords ---
  getCamera: (): Camera => store.camera(),
  setCamera: (cam: Partial<Camera>, _opts?: any) => {
    const c = store.camera()
    store.camera({ x: cam.x ?? c.x, y: cam.y ?? c.y, z: cam.z ?? c.z })
  },
  getZoomLevel: () => store.camera().z,
  getViewportScreenBounds: viewportScreenBounds,
  getViewportPageBounds: viewportPageBounds,
  screenToPage,
  pageToScreen,
  getContainer: () => containerEl,

  // --- shapes ---
  getShape: (id: Id) => store.get(id),
  // No drawing layer yet: nothing sits "on top of" a panel, so hit-testing a
  // point returns undefined and drawingOnTop() is always false (panels own the
  // pointer under the select tool). Replaced by the rbush index in M2/M8.
  getShapeAtPoint: (_pt: { x: number; y: number }, _opts?: any) => undefined,

  // --- interaction state ---
  getCurrentToolId: () => store.instance().tool,
  setCurrentTool: (tool: string) => store.transact('local', () => store.instance({ ...store.instance(), tool: tool as Tool })),
  getHoveredShapeId: () => store.instance().hoveredId,
  getSelectedShapeIds: () => store.instance().selectedIds,

  // --- instance / theme ---
  getInstanceState: () => {
    const i = store.instance()
    return { isReadonly: i.readOnly, isGridMode: i.gridOn }
  },
  updateInstanceState: (patch: { isReadonly?: boolean; isGridMode?: boolean }) => {
    const i = store.instance()
    store.instance({
      ...i,
      readOnly: patch.isReadonly ?? i.readOnly,
      gridOn: patch.isGridMode ?? i.gridOn,
    })
  },
  user: {
    getIsDarkMode: () => store.instance().darkMode,
    updateUserPreferences: (prefs: { colorScheme?: 'dark' | 'light' }) => {
      if (prefs.colorScheme) {
        const i = store.instance()
        store.instance({ ...i, darkMode: prefs.colorScheme === 'dark' })
        try {
          localStorage.setItem('pc-theme', prefs.colorScheme)
        } catch {
          /* private mode */
        }
      }
    },
  },

  // session change subscription (camera + interaction). Returns an unsubscribe.
  store: {
    listen: (cb: () => void, _opts?: { scope?: string }) => {
      sessionListeners.add(cb)
      return () => sessionListeners.delete(cb)
    },
  },

  // remote batch (Python-driven). Bridge uses store.transact directly; exposed
  // here for parity with the old applyRemote.
  remote: (fn: () => void) => store.transact('remote', fn),
}

export type Editor = typeof editor
