import { Tldraw, createShapeId } from 'tldraw'
import 'tldraw/tldraw.css'
import { shapeUtils } from './canvas'
import { setEditor } from './bridge'

export default function App() {
  return (
    <div style={{ position: 'fixed', inset: 0 }}>
      <Tldraw
        shapeUtils={shapeUtils}
        onMount={(editor) => {
          // ?demo seeds sample shapes so the frontend can be checked
          // standalone (vite dev) without a running Python backend.
          if (location.search.includes('demo')) {
            seedDemo(editor)
          }
          setEditor(editor)
        }}
      />
    </div>
  )
}

function seedDemo(editor) {
  editor.createShape({
    id: createShapeId('demo_slider'),
    type: 'pcSlider',
    x: 80,
    y: 80,
    props: { label: 'servo_1', min: 0, max: 180, value: 90 },
  })
  editor.createShape({
    id: createShapeId('demo_label'),
    type: 'pcLabel',
    x: 360,
    y: 80,
    props: { label: 'status', value: 'idle' },
  })
  editor.createShape({
    id: createShapeId('demo_video'),
    type: 'pcVideo',
    x: 80,
    y: 220,
    props: { label: 'camera' },
  })
}
