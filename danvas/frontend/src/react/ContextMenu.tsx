// The right-click context menu: export the target (a panel, a drawing/arrow, or
// the current selection) to PNG/SVG, plus quick arrange/duplicate/delete. Opened
// by input.ts via the contextMenu signal; a backdrop catches outside clicks.
import { useEffect } from 'preact/hooks'
import { contextMenu, closeContextMenu } from '../engine/contextmenu'
import { store } from '../engine/store'
import { exportRecordsToFile, copyRecordsToClipboard } from '../engine/export'
import { arrangeSelection, duplicateSelection, groupSelection, ungroupSelection, selectionHasGroup } from '../engine/interaction'
import { useValue } from './EngineContext'
import { Icon } from './icons'

const CSS = `
.pc-ctx-item{display:flex;align-items:center;gap:9px;width:100%;padding:7px 11px;border:none;background:transparent;color:#dbe2ea;font:500 13px/1 system-ui,sans-serif;text-align:left;cursor:pointer;border-radius:7px}
.pc-ctx-item:hover{background:rgba(255,255,255,0.09)}
.pc-ctx-item.pc-danger:hover{background:#b91c1c;color:#fff}
`

export function ContextMenu() {
  const menu = useValue('ctx', () => contextMenu(), [])

  useEffect(() => {
    if (!menu) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeContextMenu()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [menu])

  if (!menu) return null
  const ids = menu.ids
  const hasDrawing = ids.some((id) => store.peek(id)?.typeName === 'drawing')
  const onlyPanelsLocked = ids.every((id) => {
    const r = store.peek(id) as any
    return r && r.typeName === 'panel' && r.isLocked
  })
  const run = (fn: () => void) => () => {
    fn()
    closeContextMenu()
  }
  const del = () =>
    store.transact('local', () => {
      for (const id of ids) {
        const r = store.peek(id) as any
        if (r && !(r.typeName === 'panel' && r.isLocked)) store.remove(id)
      }
      store.instance({ ...store.instance(), selectedIds: [] })
    })

  const W = 196
  const left = Math.min(menu.x, window.innerWidth - W - 8)
  const top = Math.min(menu.y, window.innerHeight - 250)

  const item = (label: string, onClick: () => void, icon?: string, danger = false) => (
    <button class={danger ? 'pc-ctx-item pc-danger' : 'pc-ctx-item'} onClick={onClick}>
      <span style={{ width: 16, display: 'inline-flex', justifyContent: 'center' }}>{icon ? <Icon name={icon} size={15} /> : null}</span>
      <span>{label}</span>
    </button>
  )
  const divider = <div style={{ height: 1, margin: '5px 8px', background: 'rgba(255,255,255,0.10)' }} />

  return (
    <>
      <div
        onPointerDown={() => closeContextMenu()}
        onContextMenu={(e: any) => {
          e.preventDefault()
          closeContextMenu()
        }}
        style={{ position: 'fixed', inset: 0, zIndex: 399 }}
      />
      <div
        style={{
          position: 'fixed',
          left,
          top,
          zIndex: 400,
          width: W,
          padding: 5,
          borderRadius: 12,
          background: 'rgba(24,24,27,0.97)',
          border: '1px solid rgba(255,255,255,0.12)',
          boxShadow: '0 10px 30px rgba(0,0,0,0.5)',
          backdropFilter: 'blur(6px)',
        }}
      >
        <style>{CSS}</style>
        {item('Copy as PNG', run(() => void copyRecordsToClipboard(ids)), 'copy')}
        {item('Export as PNG', run(() => void exportRecordsToFile(ids, 'png')), 'image')}
        {item('Export as SVG', run(() => void exportRecordsToFile(ids, 'svg')), 'image')}
        {divider}
        {ids.filter((id) => store.peek(id)?.typeName === 'drawing').length > 1 && item('Group', run(groupSelection), 'group')}
        {selectionHasGroup() && item('Ungroup', run(ungroupSelection), 'ungroup')}
        {item('Bring to front', run(() => arrangeSelection('front')), 'toFront')}
        {item('Send to back', run(() => arrangeSelection('back')), 'toBack')}
        {hasDrawing && item('Duplicate', run(duplicateSelection), 'duplicate')}
        {!onlyPanelsLocked && item('Delete', run(del), 'trash', true)}
      </div>
    </>
  )
}
