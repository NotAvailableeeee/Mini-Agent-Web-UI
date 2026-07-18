/**
 * Light/dark theme composable.
 *
 * Single source of truth: `theme` ref. Persists to localStorage and
 * writes `data-theme` to <html> on change so CSS variables can flip
 * the entire palette. highlight.js picks up the same `data-theme`
 * attribute via its own CSS rules.
 *
 * Module-level state (not provided) so the theme is shared across
 * every component that calls useTheme() — the toggle button and any
 * reactive code that depends on the current theme see the same value.
 */

import { ref, watch } from 'vue'

const KEY = 'mini-agent-web:theme'
const VALID = new Set(['light', 'dark'])

function readInitial() {
  try {
    const stored = localStorage.getItem(KEY)
    if (stored && VALID.has(stored)) return stored
  } catch {
    // localStorage may be disabled in private browsing.
  }
  // Honor OS preference on first visit.
  if (typeof window !== 'undefined' && window.matchMedia) {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }
  return 'light'
}

const theme = ref(readInitial())

// Apply on first import so the page never flashes the wrong theme.
// { immediate: true } runs the watcher once on registration.
watch(theme, (t) => {
  if (typeof document !== 'undefined') {
    document.documentElement.setAttribute('data-theme', t)
  }
  try {
    localStorage.setItem(KEY, t)
  } catch {
    // ignore
  }
}, { immediate: true })

export function useTheme() {
  function toggle() {
    theme.value = theme.value === 'light' ? 'dark' : 'light'
  }
  function setTheme(t) {
    if (VALID.has(t)) theme.value = t
  }
  return { theme, toggle, setTheme }
}
