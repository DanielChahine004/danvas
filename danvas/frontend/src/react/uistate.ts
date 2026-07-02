// Small bits of UI-only state shared between chrome components. `stylePanelOpen`
// drives the mobile flow: on phones the top-right style panel is hidden until the
// toolbar's style toggle opens it (on desktop it's always shown).
import { signal } from 'alien-signals'
import type { WriteSignal } from '../engine/types'

export const stylePanelOpen = signal(false) as WriteSignal<boolean>

// Which bottom-left flyout chrome is expanded. The graveyard and merge panels both
// open upward from the bottom-left corner, so they're mutually exclusive — opening
// one collapses the other, and their panels never overlap. null = all collapsed.
export type ChromePanel = 'graveyard' | 'merge' | null
export const openChrome = signal<ChromePanel>(null) as WriteSignal<ChromePanel>
export function toggleChrome(which: Exclude<ChromePanel, null>): void {
  openChrome(openChrome() === which ? null : which)
}
