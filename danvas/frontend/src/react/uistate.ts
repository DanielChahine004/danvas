// Small bits of UI-only state shared between chrome components. `stylePanelOpen`
// drives the mobile flow: on phones the top-right style panel is hidden until the
// toolbar's style toggle opens it (on desktop it's always shown).
import { signal } from 'alien-signals'
import type { WriteSignal } from '../engine/types'

export const stylePanelOpen = signal(false) as WriteSignal<boolean>
