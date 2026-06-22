// Monaco-based editor body for the Repl panel. This module is imported lazily
// (see ReplShapeUtil), so all of Monaco lands in its own chunk that loads only
// when a Repl is first shown -- the initial app bundle stays lean.
//
// Monaco is self-hosted (bundled), not loaded from a CDN, so danvas keeps
// working offline. We wire the editor web worker through Vite's ?worker import
// and point @monaco-editor/react at the bundled `monaco` instance.
import { useEditor, useValue } from 'tldraw'
import * as monaco from 'monaco-editor'
// The bare `monaco-editor` entry is the core editor API only -- no language
// grammars -- so without this the Python `language` id has no tokenizer and
// nothing gets syntax-highlighted. Pull in just Python's Monarch grammar
// (main-thread tokenizer, no extra worker needed) rather than all languages.
import 'monaco-editor/esm/vs/basic-languages/python/python.contribution'
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker'
import Editor, { loader } from '@monaco-editor/react'

// Python needs only the base editor worker (no dedicated language service).
self.MonacoEnvironment = { getWorker: () => new editorWorker() }
loader.config({ monaco })

// Autocomplete is wired to the live kernel namespace: each editor registers an
// async completer (keyed by its model uri) that round-trips to Python; one
// shared provider dispatches to whichever editor asked. Monaco's provider is
// global per language, so we register it exactly once.
const completers = new Map() // model uri -> async (prefix) => string[]
let providerRegistered = false

function ensureProvider(m) {
  if (providerRegistered) return
  providerRegistered = true
  m.languages.registerCompletionItemProvider('python', {
    triggerCharacters: ['.'],
    async provideCompletionItems(model, position) {
      const complete = completers.get(model.uri.toString())
      if (!complete) return { suggestions: [] }
      // The identifier/attribute chain immediately left of the cursor.
      const line = model.getValueInRange({
        startLineNumber: position.lineNumber,
        startColumn: 1,
        endLineNumber: position.lineNumber,
        endColumn: position.column,
      })
      const match = line.match(/[A-Za-z_][A-Za-z0-9_.]*$/)
      const prefix = match ? match[0] : ''
      const names = await complete(prefix)
      if (!names || !names.length) return { suggestions: [] }
      // Replace only the part after the last dot (Python returns final segments).
      const dot = prefix.lastIndexOf('.')
      const partialLen = dot >= 0 ? prefix.length - dot - 1 : prefix.length
      const range = {
        startLineNumber: position.lineNumber,
        startColumn: position.column - partialLen,
        endLineNumber: position.lineNumber,
        endColumn: position.column,
      }
      return {
        suggestions: names.map((n) => ({
          label: n,
          kind: m.languages.CompletionItemKind.Variable,
          insertText: n,
          range,
        })),
      }
    },
  })
}

export default function MonacoRepl({ value, dark, onChange, onRun, onComplete }) {
  const editor = useEditor()
  const toolIsSelect = useValue('pc-tool', () => editor.getCurrentToolId() === 'select', [editor])
  return (
    <div
      style={{
        flex: 1,
        minHeight: 0,
        border: '1px solid var(--pc-border)',
        borderRadius: 4,
        overflow: 'hidden',
        pointerEvents: toolIsSelect ? 'all' : 'none',
      }}
      // Keep tldraw from hijacking pointer/keyboard meant for the editor.
      onPointerDown={toolIsSelect ? (e) => e.stopPropagation() : undefined}
      onKeyDown={toolIsSelect ? (e) => e.stopPropagation() : undefined}
    >
      <Editor
        language="python"
        value={value}
        theme={dark ? 'vs-dark' : 'vs'}
        onChange={(v) => onChange(v ?? '')}
        onMount={(editor, m) => {
          // Ctrl/Cmd+Enter runs the cell with the editor's current text.
          editor.addCommand(m.KeyMod.CtrlCmd | m.KeyCode.Enter, () => {
            onRun(editor.getValue())
          })
          // Wire this editor's namespace-backed autocomplete.
          if (onComplete) {
            ensureProvider(m)
            const uri = editor.getModel().uri.toString()
            completers.set(uri, onComplete)
            editor.onDidDispose(() => completers.delete(uri))
          }
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
