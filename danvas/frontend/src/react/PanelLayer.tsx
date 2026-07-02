// The panel renderer: a single camera-transformed container that hosts one
// absolutely-positioned wrapper per panel record. The camera is applied ONCE to
// the inner layer (translate+scale), never per-panel — so pan/zoom (M2) is a
// single transform update, not N repositions. Each Panel subscribes to just its
// own record signal, so a Python update to one panel re-renders only that panel.
//
// The theme vars (--pc-*) live on the `.tl-container` root (kept from the old
// build) with `tl-theme__dark` toggled by instance.darkMode, so the ported panel
// CSS resolves unchanged.
import { useEffect, useRef, useState } from 'preact/hooks'
import { memo } from 'preact/compat'
import { effect } from 'alien-signals'
import { store } from '../engine/store'
import type { Camera, PanelRecord } from '../engine/types'
import { setContainer } from '../engine/editor'
import { attachCameraInput } from '../engine/input'
import { attachInteraction } from '../engine/interaction'
import { exportTargets } from '../engine/export'
import { setupCursorReporting, componentIdOf, isSourceTagHidden, tagOfComponentId } from '../bridge'
import { useValue } from './EngineContext'
import { PanelForShape } from './panels'
import { SelectionOverlay } from './SelectionOverlay'
import { DrawingLayer, ArrowMarkerDefs } from './DrawingLayer'

// Unmount a panel's content once it's more than ~half a viewport outside the
// visible area, so a canvas of hundreds of panels only pays for the visible ones.
// The wrapper keeps the panel's w×h whether or not its content is mounted, so
// bounds, layout and the observer geometry stay stable across the unmount. State
// integrity is owned by Python, so dropping a culled panel's local React state is
// safe (it re-mounts from the record + replayed value on scroll-in).
const CULL_MARGIN = '60%'

function Panel({ id }: { id: string }) {
  const shape = useValue('rec:' + id, () => store.get(id) as PanelRecord | undefined, [id])
  const ref = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(true)

  useEffect(() => {
    const el = ref.current
    if (!el || typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) setVisible(e.isIntersecting)
      },
      { root: null, rootMargin: `${CULL_MARGIN} ${CULL_MARGIN} ${CULL_MARGIN} ${CULL_MARGIN}`, threshold: 0 },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [])

  // A screenshot force-mounts its target panels even if they're culled, so they
  // exist in the DOM to be captured.
  const exportSet = useValue('export-targets', () => exportTargets(), [])
  // Merge eye toggle: a panel from a hidden source is display:none'd but STAYS
  // mounted, so its local React state (a table's sort, a custom panel's useState)
  // survives a hide/show and it keeps updating live. On a plain canvas the id is a
  // bare uuid → empty tag → never hidden, so this is inert off a merge view.
  const srcHidden = useValue('src-hidden:' + id, () => isSourceTagHidden(tagOfComponentId(componentIdOf(id))), [id])
  if (!shape) return null
  const { x, y, rotation, opacity, props } = shape
  const show = (exportSet ? exportSet.has(id) : false) || visible
  // A decorative ghost panel (grabbable=False + operable=False) is fully
  // click-through: its Card + content already set pointer-events:none, but this
  // wrapper would still catch the click over the panel's box (it has no handler,
  // so the click is just swallowed and never reaches a panel beneath). Drop the
  // wrapper out of hit-testing too so the gesture falls through to whatever is
  // under it — e.g. a slider the orb is floating over.
  const ghost = !!shape.meta?.noGrab && !!shape.meta?.lockInput && !shape.isLocked
  return (
    <div
      ref={ref}
      data-pc-panel-id={id}
      style={{
        position: 'absolute',
        left: 0,
        top: 0,
        width: props.w,
        height: props.h,
        transform: `translate(${x}px, ${y}px) rotate(${rotation}rad)`,
        // rotate around the panel CENTRE (matches drawings + the selection box).
        // For rotation=0 this is identical to '0 0', so unrotated panels are
        // unaffected; rotated panels now spin about their middle.
        transformOrigin: 'center',
        opacity,
        display: srcHidden ? 'none' : undefined,
        pointerEvents: ghost ? 'none' : undefined,
        // the UI inspector floats above the drawing SVG (a positive z-index beats
        // the later-in-DOM, z-auto DrawingLayer in this stacking context).
        zIndex: shape.meta?.topmost ? 50 : undefined,
      }}
    >
      {show ? <PanelForShape shape={shape} /> : null}
    </div>
  )
}

// Memoised so a camera change (which re-renders PanelLayer) does NOT re-render
// every panel + drawing: the camera is one transform on the wrapper, and panels/
// shapes live in page coords, so they only need to re-render on their OWN record
// signals (which fire inside, independent of props). This is the big mobile
// pan/pinch win — pan/zoom updates one transform instead of the whole subtree.
const MemoPanel = memo(Panel)
const MemoDrawings = memo(DrawingLayer)

export function PanelLayer() {
  const rootRef = useRef<HTMLDivElement>(null)
  const camLayerRef = useRef<HTMLDivElement>(null)
  // Only panels render in the DOM layer; drawings/arrows render in the SVG
  // DrawingLayer below. (typeName never changes per record, so peeking it
  // untracked is fine — the filter re-runs only when the id list changes.)
  const ids = useValue('ids', () => store.getIds().filter((id) => store.peek(id)?.typeName === 'panel'), [])
  const dark = useValue('dark', () => store.instance().darkMode, [])
  const grid = useValue('grid', () => store.instance().gridOn, [])
  // Background grid (toggled in Settings): a CSS line grid on the static root that
  // tracks the camera. Only the image (depends on theme/on-off) is set in render;
  // its camera-derived size/position are written imperatively below so a pan/zoom
  // never re-renders this component.
  const gridLine = dark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.07)'
  const gridStyle: any = grid
    ? {
        backgroundImage: `linear-gradient(to right, ${gridLine} 1px, transparent 1px), linear-gradient(to bottom, ${gridLine} 1px, transparent 1px)`,
      }
    : {}
  // Latest grid flag for the (mount-time) camera effect's closure to read.
  const gridOnRef = useRef(grid)
  gridOnRef.current = grid

  // The camera is one transform on the inner layer + the grid's bg size/position.
  // Writing these straight to the DOM (instead of through a tracked render) keeps
  // Preact OUT of the pan/zoom hot path: a board of N panels no longer diffs N
  // memoised vnodes every frame — the camera moves with a single style write.
  const writeCamera = (c: Camera) => {
    const cl = camLayerRef.current
    if (cl) cl.style.transform = `translate(${c.x * c.z}px, ${c.y * c.z}px) scale(${c.z})`
    const root = rootRef.current
    if (!root) return
    if (gridOnRef.current) {
      const cell = 40 * c.z
      root.style.backgroundSize = `${cell}px ${cell}px`
      root.style.backgroundPosition = `${(c.x * c.z) % cell}px ${(c.y * c.z) % cell}px`
    } else {
      root.style.backgroundSize = ''
      root.style.backgroundPosition = ''
    }
  }

  useEffect(() => {
    setContainer(rootRef.current)
    const el = rootRef.current
    const detachCam = el ? attachCameraInput(el) : undefined
    const detachGestures = el ? attachInteraction(el) : undefined
    const detachCursor = el ? setupCursorReporting(el) : undefined
    // Subscribe directly to the camera signal and update the DOM imperatively.
    // Runs once immediately (priming the transform), then on every camera write.
    const stopCam = effect(() => writeCamera(store.camera()))
    return () => {
      stopCam()
      detachCam?.()
      detachGestures?.()
      detachCursor?.()
      setContainer(null)
    }
  }, [])

  // Toggling the grid (or theme) changes the bg image in render; re-sync its
  // camera-derived size/position to the current camera (untracked read).
  useEffect(() => writeCamera(store.camera()), [grid, dark])

  // Initial transform for first paint (the camera effect keeps it live after).
  const cam0 = store.camera()
  const layerTransform = `translate(${cam0.x * cam0.z}px, ${cam0.y * cam0.z}px) scale(${cam0.z})`

  return (
    <div
      ref={rootRef}
      className={dark ? 'tl-container tl-theme__dark' : 'tl-container'}
      style={{
        position: 'absolute',
        inset: 0,
        overflow: 'hidden',
        backgroundColor: dark ? '#1d1d20' : '#fbfbfb',
        ...gridStyle,
        touchAction: 'none',
        // Don't let canvas drags select panel text; inputs/editables opt back in
        // (theme.css). Matches typical canvas behaviour.
        userSelect: 'none',
      }}
    >
      {/* The arrowhead marker, defined once for both drawing layers (see
          ArrowMarkerDefs) — a marker renders in the referencing path's own
          coordinate space, so it needn't sit inside the camera transform. */}
      <ArrowMarkerDefs />
      <div
        ref={camLayerRef}
        data-pc-camera-layer=""
        style={{ position: 'absolute', left: 0, top: 0, transform: layerTransform, transformOrigin: '0 0' }}
      >
        {/* Drawings sent behind the panels (send-to-back) render below them. */}
        <MemoDrawings below />
        {ids.map((id) => (
          <MemoPanel key={id} id={id} />
        ))}
        {/* Drawings/arrows render ABOVE the panels by default so user ink/arrows
            can sit on top of panel content (e.g. an arrow over a slider). The SVG
            is pointer-transparent, so the panels underneath stay interactive. */}
        <MemoDrawings />
      </div>
      <SelectionOverlay />
    </div>
  )
}
