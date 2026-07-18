<script setup>
/**
 * Recursive structured renderer for tool-call arguments.
 *
 * Every entry is rendered as a stacked pair: the key sits on its own
 * line as a small label, and the value sits below it. The vertical
 * separation means a long value never pushes the key off the side, and
 * the user can always tell "this is `path`" vs "this is `command`"
 * even when the values look identical.
 *
 * All primitive values — strings, numbers, booleans, null — render
 * inside the same `.v-pre` code block so the action card reads as a
 * single visual unit regardless of which argument types a tool
 * surfaces. Nested objects recurse with the same component and grow
 * naturally.
 *
 * Special keys:
 *   - `command` → code block (monospace pre, hljs-coloured, scrolls)
 *   - `path`    → code block (monospace pre, scrolls)
 *   - `content` → markdown block (MarkdownView, scrolls)
 *
 * Empty / missing arg sets render nothing — the surrounding
 * action card already tells the user which tool was called, so a
 * "(none)" placeholder under it is just noise (especially for
 * tools like `read_graph` that never take args). If the parent
 * wants to surface an empty-state explicitly, it can do so
 * itself before deciding to render this component.
 *
 * Syntax highlighting: for the `command` key (the bash tool's
 * command-line argument) we run highlight.js to color keywords,
 * strings, flags. Other string values stay unhighlighted — the
 * key name above the block already carries the type identity
 * (path / count / etc.), and highligting a file path is just noise.
 */

import MarkdownView from './MarkdownView.vue'
import hljs from 'highlight.js/lib/core'
import bash from 'highlight.js/lib/languages/bash'
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('sh', bash)

const props = defineProps({
  args: { type: Object, default: () => ({}) },
})

// Keys that get a dedicated renderer. Other keys fall through to the
// generic pre / span renderers.
const MARKDOWN_KEYS = new Set(['content'])

// Map of key name → hljs language. Right now only `command` is
// highlighted (bash). Extend this when more tools surface typed
// arguments that benefit from coloring.
const HLJS_BY_KEY = { command: 'bash' }

function entries(obj) {
  return Object.entries(obj || {})
}

function isObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
}

// HTML-escape for safe `v-html` use. Used when the value is rendered
// without going through hljs (i.e. not in HLJS_BY_KEY).
function escapeHtml(s) {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

// Highlight a string value with hljs when its key has a known
// language, otherwise return the escaped string (so the same
// `v-html` binding is safe regardless of path).
function highlightValue(key, value) {
  if (typeof value !== 'string') return ''
  const lang = HLJS_BY_KEY[key]
  if (lang && hljs.getLanguage(lang)) {
    try {
      return hljs.highlight(value, { language: lang, ignoreIllegals: true }).value
    } catch {
      return escapeHtml(value)
    }
  }
  return escapeHtml(value)
}
</script>

<template>
  <div v-if="entries(args).length" class="v-arg-list">
    <div v-for="[key, value] in entries(args)" :key="key" class="v-arg">
      <div class="v-block-key">{{ key }}</div>
      <div class="v-block-value">
        <!-- content → markdown block (always rendered with its own
             formatting; not just a plain pre) -->
        <div
          v-if="MARKDOWN_KEYS.has(key) && typeof value === 'string'"
          class="v-content"
        >
          <MarkdownView :source="value" />
        </div>

        <!-- nested object → recurse, no height cap (let it grow) -->
        <ToolArgs v-else-if="isObject(value)" :args="value" class="nested" />

        <!-- array of primitives / objects — every item renders in the
             same unified `.v-pre` code block as top-level values, so a
             list of strings/numbers/nulls reads identically to a list
             of arguments at the top level. -->
        <ol v-else-if="Array.isArray(value)" class="v-array">
          <li v-for="(item, i) in value" :key="i">
            <ToolArgs v-if="isObject(item)" :args="item" class="nested" />
            <pre
              v-else-if="typeof item === 'string'"
              class="v-pre"
              v-html="escapeHtml(item)"
            ></pre>
            <pre v-else class="v-pre v-pre-primitive">{{ item === null ? 'null' : item }}</pre>
          </li>
        </ol>

        <!-- Any string value — command, path, content (if not
             markdown), or a generic key — renders as a plain
             monospace pre inside the contained, scrollable value
             area. No per-type label, no per-type tint: the key name
             above the block already says "this is `path`" / "this
             is `command`" / etc. Keys listed in HLJS_BY_KEY (e.g.
             `command`) get highlight.js coloring via v-html;
             everything else stays as plain escaped text. -->
        <pre
          v-else-if="typeof value === 'string'"
          class="v-pre"
          :class="{ 'hljs-code': HLJS_BY_KEY[key] }"
          v-html="highlightValue(key, value)"
        ></pre>

        <!-- null / undefined / number / boolean — same code-block
             chrome as strings so every primitive reads as a value,
             not a styled pill. -->
        <pre v-else class="v-pre v-pre-primitive">{{ value === null ? 'null' : value }}</pre>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* The list of key:value pairs in a tool call. Each pair is its own
 * block with a small key label on top and the value below, so the
 * user can always read "this is the value of `path`" — not just an
 * inline pill on a `<pre>` of unknown origin. */
.v-arg-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.v-arg {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.v-block-key {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-dim);
  text-transform: lowercase;
  letter-spacing: 0.02em;
}
/* The value container is just a layout slot — no height cap here, so
 * nested objects can grow naturally. The cap lives on the .v-pre /
 * .v-content children so only primitive values (strings, markdown)
 * are constrained. */
.v-block-value { min-width: 0; }
.v-block-value > .v-pre {
  max-height: 200px;
  overflow-y: auto;
}
.v-block-value > .v-content {
  max-height: 200px;
  overflow-y: auto;
  /* Match the .v-pre chrome so the markdown "content" block sits in
   * a same-style framed box as the surrounding code blocks. Padding
   * is a bit looser than .v-pre since MarkdownView brings its own
   * typography (p, ul, h1…) that needs room. */
  border: 1px solid var(--border);
  background: var(--panel-2);
  border-radius: 6px;
  padding: 6px 12px;
}

/* Nested object: visually indent the recursive render and use a
 * hairline left border so the eye groups it with its parent key. */
.v-block-value > .nested {
  padding-left: 10px;
  border-left: 2px solid var(--border);
  margin-top: 2px;
}

/* Inline pill styles for primitive values used to live here
 * (.v-string, .v-number, .v-null). They've been removed because
 * every primitive value now renders in the same `.v-pre` code block
 * as strings — so a list of booleans or nulls reads the same as a
 * list of strings, and the visual rhythm of the action card stays
 * consistent across tool calls. */
.v-array {
  margin: 0;
  padding-left: 18px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.v-array li {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text);
}
.v-array li::marker {
  color: var(--text-dim);
}

/* (Dead v-code-block / v-code-label / v-code-dot rules removed —
 * the labelled code block was replaced with a plain `<pre>`; the
 * key name above the value already carries the type identity.) */

/* Scrollable code/markdown block (no label). Capped at 200px so a
 * single huge value doesn't blow out the tool card. */
.v-pre {
  margin: 0;
  padding: 8px 10px;
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 6px;
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
  color: var(--text);
  min-width: 0;
  width: 100%;
  box-sizing: border-box;
}
</style>
