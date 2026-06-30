// The drawing toolbar (bottom-centre). Picks the active tool. The current tool
// drives engine/interaction.ts. Icons come from ./icons (crisp SVG glyphs).
import { store } from '../engine/store'
import { useValue } from './EngineContext'
import { Icon } from './icons'
import type { Tool } from '../engine/types'

// A small gap is inserted after these tools to group select/pan | shapes | text.
const TOOLS: { tool: Tool; title: string; divider?: boolean }[] = [
  { tool: 'select', title: 'Select — V' },
  { tool: 'hand', title: 'Pan — H', divider: true },
  { tool: 'draw', title: 'Draw — D' },
  { tool: 'rectangle', title: 'Rectangle — R' },
  { tool: 'ellipse', title: 'Ellipse — O' },
  { tool: 'line', title: 'Line — L' },
  { tool: 'arrow', title: 'Arrow — A', divider: true },
  { tool: 'text', title: 'Text — T' },
  { tool: 'note', title: 'Sticky note — N' },
  { tool: 'eraser', title: 'Eraser — E' },
]

const CSS = `
/* flex-centred in a full-width wrapper instead of left:50% + translateX(-50%): the
   latter lands an odd-width bar on a half-pixel, and its backdrop-filter compositing
   layer then rasterises the icons blurry. */
.pc-toolbar-wrap{position:absolute;left:0;right:0;bottom:16px;z-index:320;display:flex;justify-content:center;pointer-events:none}
.pc-toolbar{pointer-events:auto;display:flex;align-items:center;gap:3px;padding:6px;border-radius:14px;background:var(--ui-bg);border:1px solid var(--ui-border);box-shadow:var(--ui-shadow);backdrop-filter:blur(6px);max-width:calc(100vw - 12px)}
.pc-tool-btn{position:relative;width:42px;height:42px;border-radius:10px;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;flex:0 0 auto;background:transparent;color:var(--ui-muted);transition:background .12s,color .12s,transform .08s}
.pc-tool-btn:hover{background:var(--ui-hover);color:var(--ui-fg)}
.pc-tool-btn.pc-active{background:var(--ui-accent-grad);color:#fff;box-shadow:0 0 0 1px rgba(255,255,255,0.25) inset,0 2px 8px rgba(37,99,235,0.55);transform:translateY(-1px)}
.pc-tool-btn.pc-active:hover{background:linear-gradient(180deg,#4b8ff7,#2f6bf0)}
.pc-tool-div{width:1px;height:26px;margin:0 3px;flex:0 0 auto;background:var(--ui-divider)}
/* the style-panel toggle only exists on phones (desktop shows the panel always) */
.pc-mobile-only{display:none}
/* phones: lift off the bottom edge (clear the home bar), shrink to fit, scroll */
@media (max-width:680px){
  .pc-toolbar-wrap{bottom:calc(env(safe-area-inset-bottom, 0px) + 22px)}
  .pc-toolbar{gap:1px;padding:3px;overflow-x:auto;overflow-y:hidden;-webkit-overflow-scrolling:touch}
  .pc-tool-btn{width:34px;height:34px}
  .pc-tool-div{margin:0 1px}
  .pc-mobile-only{display:flex}
}
`

export function Toolbar() {
  const tool = useValue('tb-tool', () => store.instance().tool, [])
  const set = (t: Tool) => store.transact('local', () => store.instance({ ...store.instance(), tool: t }))
  return (
    <div class="pc-toolbar-wrap">
      <style>{CSS}</style>
      <div class="pc-toolbar">
        {TOOLS.flatMap(({ tool: t, title, divider }) => {
        const btn = (
          <button
            key={t}
            class={tool === t ? 'pc-tool-btn pc-active' : 'pc-tool-btn'}
            data-pc-tool={t}
            title={title}
            // Blur after clicking so the button doesn't keep DOM focus: its focus
            // ring lingered and read as "still highlighted" after switching tools
            // with a keyboard shortcut.
            onClick={(e) => { set(t); (e.currentTarget as HTMLElement).blur() }}
          >
            <Icon name={t} size={23} />
          </button>
        )
          return divider ? [btn, <div key={t + '-div'} class="pc-tool-div" />] : [btn]
        })}
      </div>
    </div>
  )
}
