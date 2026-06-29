// The store: the single source of truth. Records live in per-id alien-signals so
// a panel re-renders only when its own record changes; an id-list signal drives
// add/remove. Every mutation is wrapped in transact(source, fn), which tags the
// batch's origin — the load-bearing rule that keeps Python-driven (remote)
// updates from echoing back as user (local) edits.
//
// This is the v1 backing; the Store surface is deliberately small so a later
// swap to Yjs (origins = source) touches only this file.
import { signal, startBatch, endBatch, pauseTracking, resumeTracking } from 'alien-signals'
import { generateKeyBetween } from 'fractional-indexing'
import type { Camera, CanvasRecord, Change, Id, InstanceState, Source, WriteSignal } from './types'

// One undo step: each touched record's state before and after the gesture.
type HistEntry = Map<Id, { before?: CanvasRecord; after?: CanvasRecord }>

const DEFAULT_INSTANCE: InstanceState = {
  darkMode: true,
  readOnly: false,
  gridOn: false,
  lockedCamera: false,
  zoomLimits: { min: 0.1, max: 8 },
  tool: 'select',
  style: { color: 'blue', size: 'm', fill: 'none', dash: 'solid', opacity: 1 },
  hoveredId: null,
  selectedIds: [],
  editingId: null,
}

function untracked<T>(fn: () => T): T {
  pauseTracking()
  try {
    return fn()
  } finally {
    resumeTracking()
  }
}

export class Store {
  private recs = new Map<Id, WriteSignal<CanvasRecord | undefined>>()
  private idList = signal<Id[]>([]) as WriteSignal<Id[]>

  readonly camera = signal<Camera>({ x: 0, y: 0, z: 1 }) as WriteSignal<Camera>
  readonly instance = signal<InstanceState>(DEFAULT_INSTANCE) as WriteSignal<InstanceState>

  private listeners = new Set<(c: Change[], s: Source) => void>()
  private depth = 0
  private batchSource: Source = 'remote'
  private pending: Change[] = []

  // --- local-only undo/redo -------------------------------------------------
  // Each history entry merges a gesture's changes per record into a before/after
  // pair. Only source:'local' changes are recorded (Python-driven 'remote'
  // updates never enter history — that's the echo-suppression rule applied to
  // undo). A drag is one entry: the gesture brackets it with begin/endGroup so
  // its many per-frame patches coalesce.
  private undoStack: HistEntry[] = []
  private redoStack: HistEntry[] = []
  private isApplyingHistory = false
  private inGroup = false
  private openGroup: HistEntry | null = null
  private readonly MAX_HISTORY = 100

  // Batch a set of mutations and tag their origin. Effects (useValue) and the
  // change listeners both fire once, after the batch closes.
  transact(source: Source, fn: () => void): void {
    if (this.depth === 0) {
      this.batchSource = source
      this.pending = []
    }
    this.depth++
    startBatch()
    try {
      fn()
    } finally {
      endBatch()
      this.depth--
      if (this.depth === 0 && this.pending.length) {
        const changes = this.pending
        this.pending = []
        const s = this.batchSource
        if (s === 'local' && !this.isApplyingHistory) this.recordForHistory(changes)
        for (const cb of this.listeners) cb(changes, s)
      }
    }
  }

  private recordForHistory(changes: Change[]): void {
    const target: HistEntry = this.inGroup ? (this.openGroup ??= new Map()) : new Map()
    for (const ch of changes) {
      const e = target.get(ch.id)
      if (!e) target.set(ch.id, { before: ch.prev, after: ch.next }) // earliest before
      else e.after = ch.next // latest after
    }
    if (!this.inGroup) this.pushUndo(target)
  }

  private pushUndo(entry: HistEntry): void {
    // Drop records whose before === after (e.g. a drag that returned to start).
    for (const [id, e] of [...entry]) {
      if (JSON.stringify(e.before) === JSON.stringify(e.after)) entry.delete(id)
    }
    if (!entry.size) return
    this.undoStack.push(entry)
    if (this.undoStack.length > this.MAX_HISTORY) this.undoStack.shift()
    this.redoStack.length = 0 // a fresh action invalidates the redo branch
  }

  // Bracket a gesture so all its changes coalesce into one undo entry.
  beginGroup(): void {
    this.inGroup = true
    this.openGroup = new Map()
  }
  endGroup(): void {
    if (this.openGroup) this.pushUndo(this.openGroup)
    this.inGroup = false
    this.openGroup = null
  }

  private applyHistory(entry: HistEntry, pick: 'before' | 'after'): void {
    this.isApplyingHistory = true
    // source:'local' so the bridge reports the result to Python (layout/restore/
    // graveyard frames), but isApplyingHistory keeps it out of the history.
    this.transact('local', () => {
      for (const [id, e] of entry) {
        const rec = e[pick]
        if (rec) this.put(rec)
        else if (this.recs.has(id)) this.remove(id)
      }
    })
    this.isApplyingHistory = false
  }

  undo(): void {
    const entry = this.undoStack.pop()
    if (!entry) return
    this.applyHistory(entry, 'before')
    this.redoStack.push(entry)
  }
  redo(): void {
    const entry = this.redoStack.pop()
    if (!entry) return
    this.applyHistory(entry, 'after')
    this.undoStack.push(entry)
  }
  canUndo(): boolean {
    return this.undoStack.length > 0
  }
  canRedo(): boolean {
    return this.redoStack.length > 0
  }

  // The z-index of a record (the fractional-indexing key). idList is kept sorted by
  // this so z-order is a single, PERSISTED source of truth — survives a reload and
  // round-trips through draw-sync, instead of relying on insertion order.
  private indexOf(id: Id): string {
    return untracked(() => this.recs.get(id)?.())?.index ?? ''
  }
  // Lowest position in the (already sorted) idList where `key` belongs.
  private lowerBound(ids: Id[], key: string): number {
    let lo = 0
    let hi = ids.length
    while (lo < hi) {
      const mid = (lo + hi) >> 1
      if (this.indexOf(ids[mid]) < key) lo = mid + 1
      else hi = mid
    }
    return lo
  }
  // Re-sort idList by record index (called when an index changes). No-op if order
  // is already correct, so we don't churn the signal needlessly.
  private resortIdList(): void {
    const cur = untracked(() => this.idList())
    const sorted = [...cur].sort((a, b) => {
      const ai = this.indexOf(a)
      const bi = this.indexOf(b)
      return ai < bi ? -1 : ai > bi ? 1 : 0
    })
    if (sorted.some((id, k) => id !== cur[k])) this.idList(sorted)
  }

  // --- mutations (call inside transact) -------------------------------------
  put(rec: CanvasRecord): void {
    let s = this.recs.get(rec.id)
    if (!s) {
      const ids = untracked(() => this.idList())
      // A record with no explicit index (user-drawn shapes are created with '')
      // goes ON TOP — assign a key above the current max. Records that carry a real
      // index (Python-registered, replayed, undo) keep it, so z-order is restored.
      if (!rec.index) {
        const maxKey = ids.length ? this.indexOf(ids[ids.length - 1]) : ''
        rec = { ...rec, index: generateKeyBetween(maxKey || null, null) }
      }
      s = signal<CanvasRecord | undefined>(rec) as WriteSignal<CanvasRecord | undefined>
      this.recs.set(rec.id, s)
      // Insert keeping idList sorted by index, so a replayed/peer record lands in
      // its z-order slot regardless of arrival order.
      const at = this.lowerBound(ids, rec.index)
      this.idList([...ids.slice(0, at), rec.id, ...ids.slice(at)])
      this.pending.push({ op: 'add', id: rec.id, next: rec })
    } else {
      const prev = untracked(() => s!())
      s(rec)
      this.pending.push({ op: 'update', id: rec.id, prev, next: rec })
      if (prev && prev.index !== rec.index) this.resortIdList()
    }
  }

  // Shallow-merge top-level fields; deep-merge props and meta (props are merged, not
  // replaced, matching the wire's shape-update semantics).
  patch(id: Id, partial: Partial<CanvasRecord> & { props?: any; meta?: any }): void {
    const s = this.recs.get(id)
    if (!s) return
    const cur = untracked(() => s())
    if (!cur) return
    const curMeta = (cur as any).meta
    const next = {
      ...cur,
      ...partial,
      props: partial.props ? { ...cur.props, ...partial.props } : cur.props,
      ...((partial as any).meta ? { meta: { ...curMeta, ...(partial as any).meta } } : {}),
    } as CanvasRecord
    s(next)
    this.pending.push({ op: 'update', id, prev: cur, next })
    // An index change (z-order) means idList must re-sort to match.
    if (partial.index !== undefined && partial.index !== cur.index) this.resortIdList()
  }

  remove(id: Id): void {
    const s = this.recs.get(id)
    if (!s) return
    const prev = untracked(() => s())
    s(undefined)
    this.recs.delete(id)
    this.idList(untracked(() => this.idList()).filter((x) => x !== id))
    if (prev) this.pending.push({ op: 'remove', id, prev })
  }

  // --- reads ----------------------------------------------------------------
  // Reactive read: when called inside a useValue effect, subscribes to the
  // record's signal. Imperative callers (bridge) read outside any effect, so no
  // accidental subscription occurs.
  get(id: Id): CanvasRecord | undefined {
    return this.recs.get(id)?.()
  }

  // Untracked read for imperative callers (bridge) — never subscribes, even if
  // invoked from within a reactive context.
  peek(id: Id): CanvasRecord | undefined {
    return untracked(() => this.recs.get(id)?.())
  }

  // Reactive id list (tracked inside an effect → PanelLayer re-renders on
  // add/remove).
  getIds(): Id[] {
    return this.idList()
  }

  // Untracked snapshot of the ids, for imperative iteration.
  ids(): Id[] {
    return untracked(() => [...this.idList()])
  }

  has(id: Id): boolean {
    return this.recs.has(id)
  }

  // Z-order = the record's fractional `index`; idList is kept sorted by it. Reorder
  // computes a new index between the target slot's neighbours and PATCHES it, so the
  // change flows through the normal feed: draw-sync persists it for user drawings
  // (survives a reload) and it becomes one undo step. `source` is 'remote' for the
  // Python-driven `order` frame (no echo / no history), 'local' for a user arrange.
  // front/back jump to the ends; forward/backward step one place.
  reorder(id: Id, op: 'front' | 'back' | 'forward' | 'backward', source: Source = 'local'): void {
    const ids = untracked(() => [...this.idList()]) // sorted by index
    const i = ids.indexOf(id)
    if (i < 0) return
    let j: number // target position in the full list
    if (op === 'front') j = ids.length - 1
    else if (op === 'back') j = 0
    else if (op === 'forward') j = Math.min(i + 1, ids.length - 1)
    else j = Math.max(i - 1, 0)
    if (j === i) return // already there
    const others = ids.filter((x) => x !== id) // id removed; neighbours at slot j
    const leftKey = j > 0 ? this.indexOf(others[j - 1]) : null
    const rightKey = j < others.length ? this.indexOf(others[j]) : null
    const newIndex = generateKeyBetween(leftKey, rightKey)
    this.transact(source, () => this.patch(id, { index: newIndex }))
  }

  clear(): void {
    const ids = this.ids()
    if (!ids.length) return
    this.transact('remote', () => {
      for (const id of ids) this.remove(id)
    })
  }

  // Change feed for source-tagged read-back (geometry/draw/graveyard). Local
  // handlers ignore source==='remote' to break the echo loop.
  subscribe(cb: (changes: Change[], source: Source) => void): () => void {
    this.listeners.add(cb)
    return () => this.listeners.delete(cb)
  }
}

export const store = new Store()
