// The reactivity shims that let the ported Preact panel components talk to the
// engine exactly as they talked to tldraw:
//
//   useEditor()                       -> the single engine `editor` handle
//   useValue(key, selector, deps)     -> re-renders when the signals the selector
//                                        reads actually change value
//
// tldraw's useValue is a memoised reactive selector; we reproduce it with an
// alien-signals effect (which tracks exactly the signals the selector reads) plus
// a Preact state bump, gated by an Object.is equality check so a write that
// doesn't change the selected value never re-renders.
import { useEffect, useRef, useState } from 'preact/hooks'
import { effect, pauseTracking, resumeTracking } from 'alien-signals'
import { editor } from '../engine/editor'

export function useEditor() {
  return editor
}

function untracked<T>(fn: () => T): T {
  pauseTracking()
  try {
    return fn()
  } finally {
    resumeTracking()
  }
}

export function useValue<T>(_key: string, selector: () => T, deps: any[] = []): T {
  const [, bump] = useState(0)
  const valueRef = useRef<T>(untracked(selector))

  useEffect(() => {
    let mounted = true
    // Re-prime in case the selector's inputs changed since the last render.
    const primed = untracked(selector)
    if (!Object.is(primed, valueRef.current)) {
      valueRef.current = primed
      bump((n) => n + 1)
    }
    let first = true
    const stop = effect(() => {
      const v = selector() // tracks the signals read inside
      if (first) {
        first = false
        return
      }
      if (!mounted) return
      if (!Object.is(v, valueRef.current)) {
        valueRef.current = v
        bump((n) => n + 1)
      }
    })
    return () => {
      mounted = false
      stop()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return valueRef.current
}
