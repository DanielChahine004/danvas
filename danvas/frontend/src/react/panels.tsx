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
    // `handle` (a corner drag grip) instead of `grab` (a full-cover overlay): the
    // overlay sat on top of the iframe whenever the panel wasn't selected, so a
    // click hit it (select+drag) instead of the iframe's content. With a handle the
    // iframe is interactive immediately and you drag via the grip / header — same as
    // React panels. ghostable: grabbable=False + operable=False = click-through.
    <Card shape={shape} handle ghostable>
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
