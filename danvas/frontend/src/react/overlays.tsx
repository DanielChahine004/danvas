// Floating chrome over the canvas (port of App.jsx): peer cursors, the live-
// viewer presence badge lives in App, and the Inspector / Graveyard / Sign-out
// buttons + the kiosk hand tool. These render as siblings outside the panel host
// (.tl-container), so they use self-contained colours rather than the --pc-* vars.
import { useEffect, useState } from 'preact/hooks'
import { pageToScreen } from '../engine/editor'
import { store } from '../engine/store'
import { stylePanelOpen } from './uistate'
import { snapEnabled, setSnapEnabled, duplicateSelection } from '../engine/interaction'
import { Icon } from './icons'
import { useEditor, useValue } from './EngineContext'
import {
  subscribePeerCursors,
  subscribeUiInspector,
  toggleUiInspector,
  subscribeGraveyard,
  toggleGraveyard,
  sendRestore,
  subscribeAuth,
  signOut,
  subscribeMerge,
  mergeAdd,
  mergeAuth,
  mergeRemove,
  setSourceHidden,
} from '../bridge'

// --- peer cursors ------------------------------------------------------------
export function CursorLayer() {
  const [cursors, setCursors] = useState<any[]>([])
  const [, force] = useState(0)
  useEffect(() => subscribePeerCursors(setCursors), [])
  // Follow the camera: re-read page->screen each frame while cursors exist.
  useEffect(() => {
    if (!cursors.length) return
    let raf = requestAnimationFrame(function loop() {
      force((n) => n + 1)
      raf = requestAnimationFrame(loop)
    })
    return () => cancelAnimationFrame(raf)
  }, [cursors.length])

  if (!cursors.length) return null
  return (
    <div data-pc-cursors="" style={{ position: 'fixed', inset: 0, pointerEvents: 'none', zIndex: 400 }}>
      {cursors.map((c) => {
        const p = pageToScreen({ x: c.x, y: c.y })
        return (
          <div
            key={c.id}
            data-pc-cursor={c.id}
            style={{ position: 'absolute', left: p.x, top: p.y, transform: 'translate(-2px, -2px)', display: 'flex', alignItems: 'flex-start', gap: 4, whiteSpace: 'nowrap' }}
          >
            <svg width="20" height="20" viewBox="0 0 24 24" style={{ filter: 'drop-shadow(0 1px 1.5px rgba(0,0,0,0.45))' }}>
              <path d="M4 2 L4 19 L9 14.5 L12.3 21 L15 19.7 L11.8 13.5 L18.5 13 Z" fill={c.color || '#3b82f6'} stroke="white" strokeWidth="1.3" strokeLinejoin="round" />
            </svg>
            <span style={{ marginTop: 12, background: c.color || '#3b82f6', color: 'white', fontSize: 11, fontWeight: 600, fontFamily: 'system-ui, sans-serif', padding: '1px 6px', borderRadius: 6, boxShadow: '0 1px 3px rgba(0,0,0,0.3)' }}>
              {c.name}
            </span>
          </div>
        )
      })}
    </div>
  )
}

// --- undo / redo (top-left) --------------------------------------------------
export function UndoRedoButtons() {
  const [, bump] = useState(0)
  // re-render on any store change so enabled state stays current
  useEffect(() => store.subscribe(() => bump((n) => n + 1)), [])
  // selection lives on the instance signal (not record changes), so track it
  // reactively or the bin wouldn't appear when a panel is picked.
  const sel = useValue('urb-sel', () => store.instance().selectedIds, [])
  const canUndo = store.canUndo()
  const canRedo = store.canRedo()
  const b = (label: string, icon: string, can: boolean, onClick: () => void, left: number): any => (
    <button
      onClick={onClick}
      disabled={!can}
      title={label}
      style={{
        position: 'absolute',
        top: 10,
        left,
        zIndex: 300,
        width: 36,
        height: 36,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        borderRadius: 9,
        border: '1px solid var(--ui-border)',
        background: 'var(--ui-bg)',
        color: can ? 'var(--ui-fg)' : 'var(--ui-muted)',
        cursor: can ? 'pointer' : 'default',
        boxShadow: 'var(--ui-shadow-sm)',
        backdropFilter: 'blur(6px)',
      }}
    >
      <Icon name={icon} size={19} />
    </button>
  )
  // A red bin appears whenever something deletable is selected, for quick deletion
  // without opening the style panel. On desktop it sits by Redo; on phones it moves
  // to the left of the floating style-panel toggle (see .pc-panel-delete in CSS).
  const hasDeletable = sel.some((id) => {
    const r = store.peek(id) as any
    return r && !(r.typeName === 'panel' && (r.isLocked || r.meta?.noGrab))
  })
  const deleteSelection = () =>
    store.transact('local', () => {
      for (const id of store.instance().selectedIds) {
        const r = store.peek(id) as any
        if (r && !(r.typeName === 'panel' && r.isLocked)) store.remove(id)
      }
      store.instance({ ...store.instance(), selectedIds: [] })
    })
  // Duplicate sits next to the bin when a drawing (not a panel) is selected.
  const canDuplicate = sel.some((id) => store.peek(id)?.typeName === 'drawing')
  return (
    <>
      {b('Undo (Ctrl+Z)', 'undo', canUndo, () => store.undo(), 52)}
      {b('Redo (Ctrl+Y)', 'redo', canRedo, () => store.redo(), 94)}
      {canDuplicate && (
        <button
          class="pc-panel-duplicate"
          onClick={duplicateSelection}
          title="Duplicate (Ctrl+D)"
          style={{
            position: 'absolute',
            top: 10,
            left: 178,
            zIndex: 322,
            width: 36,
            height: 36,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: 9,
            border: '1px solid var(--ui-border)',
            background: 'var(--ui-bg)',
            color: 'var(--ui-fg)',
            cursor: 'pointer',
            boxShadow: 'var(--ui-shadow-sm)',
            backdropFilter: 'blur(6px)',
          }}
        >
          <Icon name="duplicate" size={18} />
        </button>
      )}
      {hasDeletable && (
        <button
          class="pc-panel-delete"
          onClick={deleteSelection}
          title="Delete selection (Del)"
          style={{
            position: 'absolute',
            top: 10,
            left: 136,
            zIndex: 322,
            width: 36,
            height: 36,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            borderRadius: 9,
            border: '1px solid var(--ui-danger-border)',
            background: 'var(--ui-danger-bg)',
            color: 'var(--ui-danger-fg)',
            cursor: 'pointer',
            boxShadow: 'var(--ui-shadow-sm)',
            backdropFilter: 'blur(6px)',
          }}
        >
          <Icon name="trash" size={18} />
        </button>
      )}
    </>
  )
}

// A two-state rocker toggle: one button with a sliding highlight, click switches to
// the other option (replaces a pair of separate buttons for binary settings).
function Rocker({ left, right, isRight, onToggle }: { left: any; right: any; isRight: boolean; onToggle: () => void }) {
  const side = (active: boolean, content: any) => (
    <span style={{ position: 'relative', flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 5, fontSize: 12.5, fontWeight: 600, color: active ? '#fff' : 'var(--ui-fg-dim)', zIndex: 1, transition: 'color .16s' }}>{content}</span>
  )
  return (
    <button
      onClick={onToggle}
      style={{ position: 'relative', width: '100%', height: 30, borderRadius: 8, border: 'none', cursor: 'pointer', background: 'var(--ui-btn)', display: 'flex', padding: 0, overflow: 'hidden' }}
    >
      <div style={{ position: 'absolute', top: 3, bottom: 3, left: isRight ? '50%' : 3, right: isRight ? 3 : '50%', borderRadius: 6, background: 'var(--ui-accent)', transition: 'left .16s ease, right .16s ease' }} />
      {side(!isRight, left)}
      {side(isRight, right)}
    </button>
  )
}

// --- settings (top-left cog): theme + background grid ------------------------
export function SettingsButton() {
  const editor = useEditor()
  const [open, setOpen] = useState(false)
  const dark = useValue('set-dark', () => editor.user.getIsDarkMode(), [editor])
  const grid = useValue('set-grid', () => editor.getInstanceState().isGridMode, [editor])
  const snap = useValue('set-snap', () => snapEnabled(), [])
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false)
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const label = { fontSize: 10, fontWeight: 700, letterSpacing: '0.05em', textTransform: 'uppercase', color: 'var(--ui-label)', margin: '10px 0 5px' } as any

  return (
    <>
      <button
        data-pc-settings-btn=""
        title="Settings"
        onClick={() => setOpen((o) => !o)}
        style={{
          position: 'absolute',
          top: 10,
          left: 10,
          zIndex: 301,
          width: 36,
          height: 36,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: 9,
          border: '1px solid var(--ui-border)',
          background: open ? 'var(--ui-accent-grad)' : 'var(--ui-bg)',
          color: open ? '#fff' : 'var(--ui-fg)',
          cursor: 'pointer',
          boxShadow: 'var(--ui-shadow-sm)',
          backdropFilter: 'blur(6px)',
        }}
      >
        <Icon name="settings" size={19} />
      </button>
      {open && (
        <>
          <div onPointerDown={() => setOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 301 }} />
          <div
            data-pc-settings-panel=""
            style={{ position: 'absolute', top: 54, left: 10, zIndex: 302, width: 184, padding: 11, borderRadius: 12, background: 'var(--ui-bg)', border: '1px solid var(--ui-border)', boxShadow: 'var(--ui-shadow)', backdropFilter: 'blur(6px)', fontFamily: 'system-ui, sans-serif', color: 'var(--ui-fg-dim)' }}
          >
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--ui-fg)' }}>Settings</div>
            <div style={label}>Theme</div>
            <Rocker left="Light" right="Dark" isRight={dark} onToggle={() => editor.user.updateUserPreferences({ colorScheme: dark ? 'light' : 'dark' })} />
            <div style={label}>Background grid</div>
            <Rocker left="Off" right={<><Icon name="grid" size={14} /> On</>} isRight={grid} onToggle={() => editor.updateInstanceState({ isGridMode: !grid })} />
            <div style={label}>Snap to edges &amp; centres</div>
            <Rocker left="Off" right="On" isRight={snap} onToggle={() => setSnapEnabled(!snap)} />
          </div>
        </>
      )}
    </>
  )
}

// --- floating style-panel toggle (phones, bottom-right) ----------------------
export function StyleToggle() {
  const open = useValue('st-open', () => stylePanelOpen(), [])
  return (
    <button
      class="pc-style-toggle"
      data-pc-style-toggle=""
      title="Edit styles"
      onClick={() => stylePanelOpen(!stylePanelOpen())}
      style={{
        position: 'absolute',
        zIndex: 321,
        width: 46,
        height: 46,
        alignItems: 'center',
        justifyContent: 'center',
        borderRadius: 12,
        border: '1px solid var(--ui-border)',
        background: open ? 'var(--ui-accent-grad)' : 'var(--ui-bg)',
        color: open ? '#fff' : 'var(--ui-fg)',
        cursor: 'pointer',
        boxShadow: 'var(--ui-shadow)',
        backdropFilter: 'blur(6px)',
      }}
    >
      <Icon name="sliders" size={22} />
    </button>
  )
}

const btnStyle = (extra: any): any => ({
  position: 'absolute',
  zIndex: 300,
  display: 'flex',
  alignItems: 'center',
  gap: 6,
  padding: '6px 12px',
  borderRadius: 8,
  cursor: 'pointer',
  color: 'var(--ui-fg)',
  border: '1px solid var(--ui-border)',
  boxShadow: 'var(--ui-shadow-sm)',
  fontFamily: 'system-ui, sans-serif',
  fontSize: 12,
  fontWeight: 600,
  userSelect: 'none',
  ...extra,
})

// --- Inspector button --------------------------------------------------------
export function InspectorButton() {
  const [state, setState] = useState({ enabled: false, open: false })
  useEffect(() => subscribeUiInspector(setState), [])
  if (!state.enabled) return null
  return (
    <button
      data-pc-inspector-btn=""
      class="pc-chrome-inspector"
      onClick={toggleUiInspector}
      title={state.open ? 'Remove the inspector panel' : 'Add an inspector panel to query the canvas'}
      style={btnStyle({ background: state.open ? 'var(--ui-accent)' : 'var(--ui-bg)', color: state.open ? '#fff' : 'var(--ui-fg)' })}
    >
      <span style={{ fontSize: 14, lineHeight: 1 }}>🔍</span>
      {state.open ? 'Inspector ✕' : 'Inspector'}
    </button>
  )
}

// --- Sign-out button (password-protected canvas) ----------------------------
export function SignOutButton() {
  const [enabled, setEnabled] = useState(false)
  useEffect(() => subscribeAuth(setEnabled), [])
  if (!enabled) return null
  return (
    <button
      onClick={signOut}
      title="Sign out and return to the password page"
      style={btnStyle({ bottom: 54, right: 8, background: 'var(--ui-bg)', color: '#ef4444' })}
    >
      <span style={{ fontSize: 14, lineHeight: 1 }}>🔓</span>
      Sign out
    </button>
  )
}

// --- Graveyard button + panel ------------------------------------------------
export function GraveyardButton() {
  const [state, setState] = useState({ enabled: false, open: false, items: [] as any[] })
  useEffect(() => subscribeGraveyard(setState), [])
  if (!state.enabled) return null
  const count = state.items.length
  return (
    <>
      <button
        data-pc-graveyard-btn=""
        data-pc-graveyard-count={count}
        class="pc-chrome-graveyard"
        onClick={toggleGraveyard}
        title={state.open ? 'Close the graveyard panel' : 'Show panels deleted from the canvas'}
        style={btnStyle({ background: state.open ? 'var(--ui-accent)' : 'var(--ui-bg)', color: state.open ? '#fff' : 'var(--ui-fg)' })}
      >
        <span style={{ fontSize: 14, lineHeight: 1 }}>🗑️</span>
        Graveyard{count > 0 ? ` (${count})` : ''}
        {state.open ? ' ✕' : ''}
      </button>
      {state.open && <GraveyardPanel items={state.items} />}
    </>
  )
}

function GraveyardPanel({ items }: { items: any[] }) {
  return (
    <div
      class="pc-graveyard-panel"
      style={{
        position: 'absolute',
        left: 8,
        zIndex: 299,
        width: 280,
        maxHeight: 320,
        overflowY: 'auto',
        background: 'var(--ui-bg)',
        border: '1px solid var(--ui-border)',
        borderRadius: 10,
        boxShadow: 'var(--ui-shadow)',
        backdropFilter: 'blur(6px)',
        fontFamily: 'system-ui, sans-serif',
        color: 'var(--ui-fg)',
      }}
    >
      <div style={{ padding: '8px 12px 6px', fontSize: 11, fontWeight: 700, letterSpacing: '0.06em', textTransform: 'uppercase', color: 'var(--ui-label)', borderBottom: '1px solid var(--ui-divider)' }}>
        Deleted panels
      </div>
      {items.length === 0 ? (
        <div style={{ padding: '10px 12px', fontSize: 12, color: 'var(--ui-muted)', fontStyle: 'italic' }}>No deleted panels</div>
      ) : (
        items.map((item) => (
          <div key={item.id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 12px', borderBottom: '1px solid var(--ui-divider)' }}>
            <span style={{ flex: 1, fontSize: 12, fontFamily: 'ui-monospace, monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {item.label || item.id}
            </span>
            <button
              data-pc-restore={item.id}
              onClick={() => sendRestore(item.id)}
              style={{ flexShrink: 0, padding: '3px 10px', fontSize: 11, fontWeight: 600, cursor: 'pointer', background: '#2563eb', color: '#fff', border: 'none', borderRadius: 5, fontFamily: 'system-ui, sans-serif' }}
            >
              Restore
            </button>
          </div>
        ))
      )}
    </div>
  )
}

// --- merge: the standing merge server's source panel ------------------------
// Shown on a merge view (welcome.mergeHost). Lists this connection's composed
// sources (live/offline, with an eye toggle + remove), takes a canvas URL to add,
// and prompts for a password when a source reports it's protected.
export function MergeHostPanel() {
  const [state, setState] = useState<any>({ isHost: false, sources: [], prompts: [] })
  const [open, setOpen] = useState(true)
  const [addUrl, setAddUrl] = useState('')
  useEffect(() => subscribeMerge(setState), [])
  if (!state.isHost) return null
  const count = state.sources.length
  const submitAdd = () => {
    const u = addUrl.trim()
    if (u) {
      mergeAdd(u)
      setAddUrl('')
    }
  }
  return (
    <>
      <button
        data-pc-merge-btn=""
        class="pc-chrome-merge"
        onClick={() => setOpen((o) => !o)}
        title={open ? 'Close the merge panel' : 'Add or hide merged canvases'}
        style={btnStyle({ background: open ? 'var(--ui-accent)' : 'var(--ui-bg)', color: open ? '#fff' : 'var(--ui-fg)' })}
      >
        <span style={{ fontSize: 14, lineHeight: 1 }}>🧩</span>
        Merge{count > 0 ? ` (${count})` : ''}{open ? ' ✕' : ''}
      </button>
      {open && (
        <div class="pc-merge-panel" style={mergePanelStyle}>
          <div style={mergeHeaderStyle}>Merged canvases</div>
          {state.sources.length === 0 && state.prompts.length === 0 ? (
            <div style={{ padding: '10px 12px', fontSize: 12, color: 'var(--ui-muted)', fontStyle: 'italic' }}>No sources yet — add a canvas URL below.</div>
          ) : null}
          {state.sources.map((s: any) => {
            const hidden = (state.hidden || []).includes(s.sid)
            return (
              <div key={s.sid} style={mergeRowStyle}>
                <span title={s.status} style={{ flexShrink: 0, width: 8, height: 8, borderRadius: '50%', background: s.status === 'live' ? '#22c55e' : '#9ca3af' }} />
                <span style={{ flex: 1, fontSize: 12, fontFamily: 'ui-monospace, monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', opacity: hidden ? 0.5 : 1 }}>{s.label}</span>
                <button title={hidden ? 'Show' : 'Hide (this view only)'} onClick={() => setSourceHidden(s.sid, !hidden)} style={mergeIconBtnStyle}>{hidden ? '🙈' : '👁'}</button>
                <button title="Remove" onClick={() => mergeRemove(s.sid)} style={mergeIconBtnStyle}>✕</button>
              </div>
            )
          })}
          {state.prompts.map((p: any) => (
            <MergeAuthPrompt key={p.uri} prompt={p} />
          ))}
          <div style={{ display: 'flex', gap: 6, padding: '8px 12px', borderTop: '1px solid var(--ui-divider)' }}>
            <input
              value={addUrl}
              onInput={(e: any) => setAddUrl(e.currentTarget.value)}
              onKeyDown={(e: any) => e.key === 'Enter' && submitAdd()}
              placeholder="canvas URL (host:port)"
              style={mergeInputStyle}
            />
            <button onClick={submitAdd} style={mergeAddBtnStyle}>Add</button>
          </div>
        </div>
      )}
    </>
  )
}

function MergeAuthPrompt({ prompt }: { prompt: any }) {
  const [pw, setPw] = useState('')
  const submit = () => {
    if (pw) mergeAuth(prompt.uri, pw)
  }
  return (
    <div style={{ ...mergeRowStyle, flexWrap: 'wrap' }}>
      <span style={{ flex: '1 1 100%', fontSize: 12 }}>
        🔒 {prompt.label}
        {prompt.error ? <span style={{ color: '#f87171', marginLeft: 6 }}>wrong password</span> : null}
      </span>
      <input
        type="password"
        value={pw}
        onInput={(e: any) => setPw(e.currentTarget.value)}
        onKeyDown={(e: any) => e.key === 'Enter' && submit()}
        placeholder="password"
        style={mergeInputStyle}
      />
      <button onClick={submit} style={mergeAddBtnStyle}>Unlock</button>
    </div>
  )
}

// --- merge: the launch button on a plain canvas -----------------------------
// Shown on a canvas that was served with serve(merge_server=...). Collects other
// canvas URLs and navigates to the merge server, pre-seeded with this canvas + the
// pasted ones, so merging is reachable from within a canvas (not just the CLI).
export function MergeLaunchButton() {
  const [state, setState] = useState<any>({ server: null, selfUrl: null })
  const [open, setOpen] = useState(false)
  const [urls, setUrls] = useState('')
  useEffect(() => subscribeMerge(setState), [])
  if (!state.server || !state.selfUrl) return null
  const go = () => {
    const others = urls.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean)
    const params = new URLSearchParams()
    params.set('sources', [state.selfUrl, ...others].join(','))
    window.location.href = `${String(state.server).replace(/\/+$/, '')}/?${params.toString()}`
  }
  return (
    <>
      <button
        data-pc-merge-launch=""
        class="pc-chrome-merge"
        onClick={() => setOpen((o) => !o)}
        title="Merge this canvas with others on the merge server"
        style={btnStyle({ background: open ? 'var(--ui-accent)' : 'var(--ui-bg)', color: open ? '#fff' : 'var(--ui-fg)' })}
      >
        <span style={{ fontSize: 14, lineHeight: 1 }}>🧩</span>
        Merge{open ? ' ✕' : '…'}
      </button>
      {open && (
        <div class="pc-merge-panel" style={mergePanelStyle}>
          <div style={mergeHeaderStyle}>Merge with…</div>
          <div style={{ padding: '8px 12px', fontSize: 12, color: 'var(--ui-muted)' }}>
            Other canvas URLs (one per line or comma-separated). This canvas is included.
          </div>
          <textarea
            value={urls}
            onInput={(e: any) => setUrls(e.currentTarget.value)}
            placeholder="127.0.0.1:8002&#10;https://friend.loca.lt"
            rows={3}
            style={{ ...mergeInputStyle, margin: '0 12px', width: 'calc(100% - 24px)', resize: 'vertical', fontFamily: 'ui-monospace, monospace' }}
          />
          <div style={{ display: 'flex', justifyContent: 'flex-end', padding: '8px 12px' }}>
            <button onClick={go} style={mergeAddBtnStyle}>Open merged view</button>
          </div>
        </div>
      )}
    </>
  )
}

const mergePanelStyle: any = {
  position: 'absolute', left: 8, zIndex: 299, width: 300, maxHeight: 360, overflowY: 'auto',
  background: 'var(--ui-bg)', border: '1px solid var(--ui-border)', borderRadius: 10,
  boxShadow: 'var(--ui-shadow)', backdropFilter: 'blur(6px)', fontFamily: 'system-ui, sans-serif', color: 'var(--ui-fg)',
}
const mergeHeaderStyle: any = {
  padding: '8px 12px 6px', fontSize: 11, fontWeight: 700, letterSpacing: '0.06em',
  textTransform: 'uppercase', color: 'var(--ui-label)', borderBottom: '1px solid var(--ui-divider)',
}
const mergeRowStyle: any = { display: 'flex', alignItems: 'center', gap: 8, padding: '7px 12px', borderBottom: '1px solid var(--ui-divider)' }
const mergeIconBtnStyle: any = { flexShrink: 0, padding: '2px 6px', fontSize: 12, cursor: 'pointer', background: 'transparent', color: 'var(--ui-fg)', border: 'none', borderRadius: 4 }
const mergeInputStyle: any = { flex: 1, minWidth: 0, padding: '5px 8px', fontSize: 12, borderRadius: 6, border: '1px solid var(--ui-border)', background: 'var(--ui-bg)', color: 'var(--ui-fg)', boxSizing: 'border-box' }
const mergeAddBtnStyle: any = { flexShrink: 0, padding: '5px 12px', fontSize: 12, fontWeight: 600, cursor: 'pointer', background: '#2563eb', color: '#fff', border: 'none', borderRadius: 6 }

// --- kiosk hand tool (touch, ui:false) --------------------------------------
// Under a chrome-free view a touch user is stuck on select (one finger drags the
// panel); keep the hand tool armed so one finger pans.
export function KioskHandTool() {
  const editor = useEditor()
  const toolId = useValue('kiosk-tool', () => editor.getCurrentToolId(), [editor])
  useEffect(() => {
    const isPhone = !!window.matchMedia?.('(pointer: coarse)').matches
    if (isPhone && toolId !== 'hand') editor.setCurrentTool('hand')
  }, [editor, toolId])
  return null
}
