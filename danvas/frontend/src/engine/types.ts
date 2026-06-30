// Core record + signal types for the engine. The store is the single source of
// truth; every consumer (bridge, panel renderer, drawing renderer, overlays)
// reads it. Records mirror what the old build kept as shapes, but the
// engine is framework-free TypeScript.

export type Id = string
export type Source = 'local' | 'remote'

// The three live panel shape types (see canvas.jsx COMPONENT_TO_SHAPE). pcLabel
// is vestigial in the Python build (Label now ships as a React panel), but kept
// here as a cheap native renderer for any frame that still uses it.
export type PanelShapeType = 'pcLabel' | 'pcHtml' | 'pcReact'

// Per-shape lock / chrome flags, derived from Python movable/resizable/etc.
// (see bridge.js lockMeta). All optional; absent = default (interactive).
export interface PanelMeta {
  lockMove?: boolean    // movable=False  -> user can't drag
  lockResize?: boolean  // resizable=False -> user can't resize
  lockInput?: boolean   // operable=False -> controls inert (shape still updates)
  noGrab?: boolean      // grabbable=False -> never hover/select by body click
  noFrame?: boolean     // frame=False -> strip card chrome
  frameColor?: string   // tint card chrome to an accent hex
  wheelLocal?: boolean  // forward_wheel=False -> wheel stays in the panel, no canvas zoom
  topmost?: boolean     // render above the drawing layer (the UI inspector panel)
}

export interface PanelRecord {
  typeName: 'panel'
  id: Id // 'shape:<componentId>'
  shapeType: PanelShapeType
  x: number
  y: number
  rotation: number // radians
  opacity: number
  isLocked: boolean
  index: string // fractional z-index
  props: Record<string, any>
  meta: PanelMeta
}

// Python-managed canvas shapes (canvas.geo/text/line/frame/note/...). Rendered on
// the SVG drawing layer below the panels. Props use the wire schema, interpreted
// by the renderer (geo/color/fill, line points, frame label, ...).
export type DrawingShapeType = 'geo' | 'text' | 'note' | 'line' | 'draw' | 'frame' | 'highlight' | 'image'
export interface DrawingRecord {
  typeName: 'drawing'
  id: Id
  shapeType: DrawingShapeType
  x: number
  y: number
  rotation: number
  opacity: number
  index: string
  props: Record<string, any>
}

// A connector arrow (canvas.connect). start/end are the bound shapes' ids
// ('shape:<id>'); the arrow reroutes as they move. v1 stores the endpoints on the
// arrow rather than as separate binding records.
export interface ArrowRecord {
  typeName: 'arrow'
  id: Id
  start?: Id
  end?: Id
  opacity: number
  index: string
  props: Record<string, any>
}

export type CanvasRecord = PanelRecord | DrawingRecord | ArrowRecord

export interface Camera {
  x: number
  y: number
  z: number
}

// Editor tools (the toolbar). 'select'/'hand' manipulate; the rest draw.
export type Tool =
  | 'select'
  | 'hand'
  | 'draw' // freehand pen
  | 'rectangle'
  | 'ellipse'
  | 'line'
  | 'arrow'
  | 'text'
  | 'note' // sticky note
  | 'eraser'

// Style for newly-drawn shapes (and applied to the selection from the panel).
export interface DrawStyle {
  color: string // palette name (black/blue/red/green/orange/violet/yellow/grey)
  size: 's' | 'm' | 'l' // stroke thickness / font size
  fill: 'none' | 'semi' | 'solid'
  dash: 'solid' | 'dashed' | 'dotted' // stroke style for geo/line/arrow edges
  opacity: number // 0..1 (top-level record opacity for new shapes)
  arrowKind?: 'straight' | 'elbow' | 'curved' // routing for new arrows/lines
}

export interface InstanceState {
  darkMode: boolean
  readOnly: boolean
  gridOn: boolean
  lockedCamera: boolean
  zoomLimits: { min: number; max: number }
  tool: Tool
  style: DrawStyle
  hoveredId: Id | null
  selectedIds: Id[]
  editingId: Id | null // a text/note shape being edited inline
}

export interface Change {
  op: 'add' | 'update' | 'remove'
  id: Id
  prev?: CanvasRecord
  next?: CanvasRecord
}

// alien-signals' signal/computed are callable: read with `s()`, write with
// `s(v)`. This is the shape we store them as.
export type ReadSignal<T> = () => T
export type WriteSignal<T> = { (): T; (v: T): void }
