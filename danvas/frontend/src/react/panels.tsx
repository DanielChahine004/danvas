// Maps a panel record's shapeType to its renderer. pcReact (now nearly every
// built-in) mounts the ReactHost inside the Card; pcLabel is the vestigial native
// label; pcHtml is the Custom iframe (minimal here — the postMessage push/binary
// channels land in M6).
import { Card, CardLabel } from './Card'
import { CustomView } from './CustomView'
import ReactHost from './ReactHost'

function LabelPanel({ shape }: { shape: any }) {
  return (
    <Card shape={shape}>
      <CardLabel shape={shape} />
      <div style={{ fontSize: 20, fontWeight: 600, color: 'var(--pc-text)' }}>{shape.props.value}</div>
    </Card>
  )
}

function HtmlPanel({ shape }: { shape: any }) {
  return (
    // ghostable: grabbable=False + operable=False makes the iframe click-through.
    <Card shape={shape} grab ghostable>
      {/* Header has no pointerEvents, so dragging it moves the panel. */}
      <CardLabel shape={shape} />
      <CustomView shape={shape} />
    </Card>
  )
}

function ReactPanel({ shape }: { shape: any }) {
  return (
    <Card shape={shape} handle ghostable>
      <CardLabel shape={shape} />
      <ReactHost shape={shape} />
    </Card>
  )
}

export function PanelForShape({ shape }: { shape: any }) {
  switch (shape.shapeType) {
    case 'pcLabel':
      return <LabelPanel shape={shape} />
    case 'pcHtml':
      return <HtmlPanel shape={shape} />
    case 'pcReact':
      return <ReactPanel shape={shape} />
    default:
      return null
  }
}
