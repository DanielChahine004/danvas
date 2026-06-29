// True while a two-finger pinch/pan is in progress, so the single-pointer
// interaction handler (draw/select/move) yields to the camera gesture instead of
// fighting it. Set by input.ts (enablePinch), read by interaction.ts.
import { signal } from 'alien-signals'
import type { WriteSignal } from './types'

export const gesturing = signal(false) as WriteSignal<boolean>
