// Monaco-based editor body for the Repl panel. This module is imported lazily
// (see ReplShapeUtil), so all of Monaco lands in its own chunk that loads only
// when a Repl is first shown -- the initial app bundle stays lean.
//
// Monaco is self-hosted (bundled), not loaded from a CDN, so pycanvas keeps
// working offline. We wire the editor web worker through Vite's ?worker import
// and point @monaco-editor/react at the bundled `monaco` instance.
import * as monaco from 'monaco-editor'
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import Editor, { loader } from '@monaco-editor/react'

// Python needs only the base editor worker (no dedicated language service).
self.MonacoEnvironment = { getWorker: () => new editorWorker() }
loader.config({ monaco })

export default function MonacoRepl({ value, onChange, onRun }) {
  return (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        border: '1px solid #e2e2e2',
        borderRadius: 4,
        overflow: 'hidden',
        pointerEvents: 'all',
      }}
      // Keep tldraw from hijacking pointer/keyboard meant for the editor.
      onPointerDown={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
    >
      <Editor
        language="python"
        value={value}
        theme="vs"
        onChange={(v) => onChange(v ?? '')}
        onMount={(editor, m) => {
          // Ctrl/Cmd+Enter runs the cell with the editor's current text.
          editor.addCommand(m.KeyMod.CtrlCmd | m.KeyCode.Enter, () => {
            onRun(editor.getValue())
          })
        }}
        options={{
          minimap: { enabled: false },
          fontSize: 13,
          lineNumbers: 'on',
          scrollBeyondLastLine: false,
          automaticLayout: true,
          tabSize: 4,
          padding: { top: 6, bottom: 6 },
          overviewRulerLanes: 0,
        }}
      />
    </div>
  )
}
