// Inline SVG icons for the editor chrome (toolbar + style panel). These are
// crisp 24×24 stroke glyphs (Lucide-derived, MIT) so the tools read clearly at
// any DPI — replacing the earlier emoji/unicode labels that rendered
// inconsistently across platforms. `select` is the one filled glyph (a cursor).
import type { JSX } from 'preact'

const FILLED = new Set(['select'])

const PATHS: Record<string, JSX.Element> = {
  // --- tools ---
  select: <path d="M4.037 4.688a.495.495 0 0 1 .651-.651l16 6.5a.5.5 0 0 1-.063.947l-6.124 1.58a2 2 0 0 0-1.438 1.435l-1.579 6.126a.5.5 0 0 1-.947.063z" />,
  hand: (
    <>
      <path d="M18 11V6a2 2 0 0 0-2-2 2 2 0 0 0-2 2" />
      <path d="M14 10V4a2 2 0 0 0-2-2 2 2 0 0 0-2 2v2" />
      <path d="M10 10.5V6a2 2 0 0 0-2-2 2 2 0 0 0-2 2v8" />
      <path d="M18 8a2 2 0 1 1 4 0v6a8 8 0 0 1-8 8h-2c-2.8 0-4.5-.86-5.99-2.34l-3.6-3.6a2 2 0 0 1 2.83-2.82L7 15" />
    </>
  ),
  draw: (
    <>
      <path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z" />
      <path d="m15 5 4 4" />
    </>
  ),
  rectangle: <rect x="4" y="5.5" width="16" height="13" rx="2" />,
  ellipse: <circle cx="12" cy="12" r="8.5" />,
  line: <path d="M5 19 19 5" />,
  arrow: (
    <>
      <path d="M7 17 17 7" />
      <path d="M7 7h10v10" />
    </>
  ),
  text: (
    <>
      <path d="M4 7V4h16v3" />
      <path d="M9 20h6" />
      <path d="M12 4v16" />
    </>
  ),
  note: (
    <>
      <path d="M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5V14l-6 6H5.5A1.5 1.5 0 0 1 4 18.5z" />
      <path d="M20 14h-4.5a1.5 1.5 0 0 0-1.5 1.5V20" />
    </>
  ),
  eraser: (
    <>
      <path d="m7 21-4.3-4.3a1 1 0 0 1 0-1.4l10-10a1 1 0 0 1 1.4 0l5 5a1 1 0 0 1 0 1.4L13 21" />
      <path d="M22 21H7" />
      <path d="m5 11 9 9" />
    </>
  ),

  // --- style-panel actions ---
  toBack: (
    <>
      <path d="M5 20h14" />
      <path d="M8 9l4 4 4-4" />
      <path d="M12 4v9" />
    </>
  ),
  backward: <path d="M7 10l5 5 5-5" />,
  forward: <path d="M7 14l5-5 5 5" />,
  toFront: (
    <>
      <path d="M5 4h14" />
      <path d="M8 15l4-4 4 4" />
      <path d="M12 20v-9" />
    </>
  ),
  duplicate: (
    <>
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5 15a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2" />
    </>
  ),
  trash: (
    <>
      <path d="M3 6h18" />
      <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
      <path d="M10 11v5" />
      <path d="M14 11v5" />
    </>
  ),

  image: (
    <>
      <rect width="18" height="18" x="3" y="3" rx="2" ry="2" />
      <circle cx="9" cy="9" r="2" />
      <path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21" />
    </>
  ),
  copy: (
    <>
      <rect width="8" height="4" x="8" y="2" rx="1" ry="1" />
      <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
    </>
  ),

  group: (
    <>
      <path d="M3 7V5a2 2 0 0 1 2-2h2" />
      <path d="M17 3h2a2 2 0 0 1 2 2v2" />
      <path d="M21 17v2a2 2 0 0 1-2 2h-2" />
      <path d="M7 21H5a2 2 0 0 1-2-2v-2" />
      <rect width="7" height="5" x="7" y="7" rx="1" />
      <rect width="7" height="5" x="10" y="12" rx="1" />
    </>
  ),
  ungroup: (
    <>
      <rect width="8" height="6" x="5" y="4" rx="1" />
      <rect width="8" height="6" x="11" y="14" rx="1" />
    </>
  ),
  settings: (
    <>
      <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  grid: (
    <>
      <rect width="18" height="18" x="3" y="3" rx="2" />
      <path d="M3 9h18" />
      <path d="M3 15h18" />
      <path d="M9 3v18" />
      <path d="M15 3v18" />
    </>
  ),
  undo: (
    <>
      <path d="M9 14 4 9l5-5" />
      <path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H10" />
    </>
  ),
  redo: (
    <>
      <path d="m15 14 5-5-5-5" />
      <path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H14" />
    </>
  ),

  // --- dash style ---
  dashSolid: <path d="M3 12h18" />,
  dashDashed: <path d="M3 12h18" strokeDasharray="5 4" />,
  dashDotted: <path d="M3 12h18" strokeDasharray="0.5 4" />,

  // --- arrow routing style ---
  arrowStraight: <path d="M4 20 L20 4" />,
  arrowElbow: <path d="M4 5 L4 19 L20 19" />,
  arrowCurved: <path d="M4 20 Q4 6 20 6" />,

  // mobile: open the style panel
  sliders: (
    <>
      <line x1="21" x2="14" y1="4" y2="4" />
      <line x1="10" x2="3" y1="4" y2="4" />
      <line x1="21" x2="12" y1="12" y2="12" />
      <line x1="8" x2="3" y1="12" y2="12" />
      <line x1="21" x2="16" y1="20" y2="20" />
      <line x1="12" x2="3" y1="20" y2="20" />
      <line x1="14" x2="14" y1="2" y2="6" />
      <line x1="8" x2="8" y1="10" y2="14" />
      <line x1="16" x2="16" y1="18" y2="22" />
    </>
  ),

  // --- align (group selections) ---
  alignLeft: (
    <>
      <path d="M4 4v16" />
      <rect x="7" y="6.5" width="12" height="4" rx="1" />
      <rect x="7" y="13.5" width="7" height="4" rx="1" />
    </>
  ),
  alignCenterX: (
    <>
      <path d="M12 4v16" />
      <rect x="6" y="6.5" width="12" height="4" rx="1" />
      <rect x="8.5" y="13.5" width="7" height="4" rx="1" />
    </>
  ),
  alignRight: (
    <>
      <path d="M20 4v16" />
      <rect x="5" y="6.5" width="12" height="4" rx="1" />
      <rect x="10" y="13.5" width="7" height="4" rx="1" />
    </>
  ),
  alignTop: (
    <>
      <path d="M4 4h16" />
      <rect x="6.5" y="7" width="4" height="12" rx="1" />
      <rect x="13.5" y="7" width="4" height="7" rx="1" />
    </>
  ),
  alignMiddleY: (
    <>
      <path d="M4 12h16" />
      <rect x="6.5" y="6" width="4" height="12" rx="1" />
      <rect x="13.5" y="8.5" width="4" height="7" rx="1" />
    </>
  ),
  alignBottom: (
    <>
      <path d="M4 20h16" />
      <rect x="6.5" y="5" width="4" height="12" rx="1" />
      <rect x="13.5" y="10" width="4" height="7" rx="1" />
    </>
  ),

  // --- text alignment (lines within a caption/box) ---
  textAlignLeft: (
    <>
      <path d="M4 6h16" />
      <path d="M4 12h10" />
      <path d="M4 18h13" />
    </>
  ),
  textAlignCenter: (
    <>
      <path d="M4 6h16" />
      <path d="M7 12h10" />
      <path d="M5.5 18h13" />
    </>
  ),
  textAlignRight: (
    <>
      <path d="M4 6h16" />
      <path d="M10 12h10" />
      <path d="M7 18h13" />
    </>
  ),

  // --- fill swatches (variable fill) ---
  fillNone: <rect x="4" y="4" width="16" height="16" rx="3" />,
  fillSemi: (
    <>
      <rect x="4" y="4" width="16" height="16" rx="3" fill="currentColor" fillOpacity="0.32" stroke="none" />
      <rect x="4" y="4" width="16" height="16" rx="3" />
    </>
  ),
  fillSolid: <rect x="4" y="4" width="16" height="16" rx="3" fill="currentColor" stroke="none" />,
}

export function Icon({ name, size = 22 }: { name: string; size?: number }) {
  const filled = FILLED.has(name)
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={filled ? 'currentColor' : 'none'}
      stroke={filled ? 'none' : 'currentColor'}
      strokeWidth={1.9}
      strokeLinecap="round"
      strokeLinejoin="round"
      style={{ display: 'block' }}
    >
      {PATHS[name]}
    </svg>
  )
}
