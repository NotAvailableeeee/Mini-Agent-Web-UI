<script setup>
import { computed, onMounted, onUnmounted, watch } from 'vue'
import MarkdownView from './MarkdownView.vue'
import hljs from 'highlight.js/lib/core'
import bash from 'highlight.js/lib/languages/bash'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import python from 'highlight.js/lib/languages/python'
import json from 'highlight.js/lib/languages/json'
import yaml from 'highlight.js/lib/languages/yaml'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import sql from 'highlight.js/lib/languages/sql'
import go from 'highlight.js/lib/languages/go'
import rust from 'highlight.js/lib/languages/rust'
import java from 'highlight.js/lib/languages/java'
// Register the same set the rest of the app uses, so a file marked
// `code` with `language=python` here gets the same colors as a code
// fence inside markdown.
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('python', python)
hljs.registerLanguage('json', json)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('css', css)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('go', go)
hljs.registerLanguage('rust', rust)
hljs.registerLanguage('java', java)
hljs.registerLanguage('js', javascript)
hljs.registerLanguage('ts', typescript)
hljs.registerLanguage('py', python)
hljs.registerLanguage('sh', bash)
hljs.registerLanguage('yml', yaml)
hljs.registerLanguage('html', xml)
hljs.registerLanguage('vue', xml)

const props = defineProps({
  // Either a FileContent object from /api/workspace/file, or `null`.
  // When null, the modal renders nothing (used to gate the transition).
  file: { type: Object, default: null },
  // Boolean that controls the modal's visibility independently of
  // `file` so the parent can drive open/close without juggling refs.
  open: { type: Boolean, default: false },
})
const emit = defineEmits(['close'])

function handleClose() {
  emit('close')
}

function onKeydown(e) {
  if (e.key === 'Escape' && props.open) {
    handleClose()
  }
}

onMounted(() => window.addEventListener('keydown', onKeydown))
onUnmounted(() => window.removeEventListener('keydown', onKeydown))

// Lock body scroll while the modal is open so the page underneath
// doesn't shift. Restore on close.
watch(() => props.open, (isOpen) => {
  document.body.style.overflow = isOpen ? 'hidden' : ''
})

// Human-readable kind label for the header badge. Code files show
// the language (e.g. "PYTHON") so the user knows what they're
// looking at without squinting at the file extension.
const kindLabel = computed(() => {
  if (!props.file) return ''
  const f = props.file
  if (f.kind === 'code' && f.language) {
    return f.language.toUpperCase()
  }
  const map = {
    text: 'TEXT',
    markdown: 'MARKDOWN',
    json: 'JSON',
    jsonl: 'JSONL',
    csv: 'CSV',
    code: 'CODE',
    html: 'HTML',
    image: 'IMAGE',
    pdf: 'PDF',
    binary: 'BINARY',
  }
  return map[f.kind] || f.kind.toUpperCase()
})

const binaryDataUrl = computed(() => {
  if (!props.file) return ''
  return `data:${props.file.mime};base64,${props.file.content}`
})

// True when the load error looks like a "file not found" — used to
// swap the generic error block for a friendlier hint that points the
// user at why the long-term memory file might be missing.
const isNotFoundError = computed(() => {
  const msg = props.file?.error || ''
  return /not\s*found|404|文件.*不存在/i.test(msg)
})

// Pretty-print JSON if the content parses. Falls back to the raw
// string if it's not valid JSON (e.g. a JSON5 file or a fragment).
const prettyJson = computed(() => {
  if (!props.file || props.file.kind !== 'json') return ''
  try {
    return JSON.stringify(JSON.parse(props.file.content), null, 2)
  } catch {
    return props.file.content
  }
})

// Pretty-print one JSONL record for the per-row view. The backend
// already parsed each line into `data`, so JSON.stringify won't
// throw — guard anyway so a stray null/undefined doesn't crash the
// row (render the original raw line instead).
function formatJsonlRecord(data) {
  try {
    return JSON.stringify(data, null, 2)
  } catch {
    return String(data)
  }
}

// Parse CSV text into a 2D array of cells. Handles RFC 4180 basics:
//   - comma as the field separator
//   - double-quoted fields can contain commas and newlines
//   - "" inside a quoted field is an escaped double-quote
//   - empty lines are skipped
// No library — the format is small enough that a 30-line state
// machine is fine and avoids a parse dep for a one-off viewer.
function parseCSV(text) {
  const rows = []
  let row = []
  let cell = ''
  let inQuotes = false
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') { cell += '"'; i++ }
        else { inQuotes = false }
      } else {
        cell += c
      }
    } else {
      if (c === '"') {
        inQuotes = true
      } else if (c === ',') {
        row.push(cell); cell = ''
      } else if (c === '\r') {
        // ignore — \r\n and lone \r both close a row
      } else if (c === '\n') {
        row.push(cell); cell = ''
        rows.push(row); row = []
      } else {
        cell += c
      }
    }
  }
  // Last cell/row (no trailing newline)
  if (cell.length > 0 || row.length > 0) {
    row.push(cell)
    rows.push(row)
  }
  return rows
}

const csvRows = computed(() => {
  if (!props.file || props.file.kind !== 'csv') return []
  return parseCSV(props.file.content)
})
const csvHeaders = computed(() => csvRows.value[0] || [])
const csvBody = computed(() => csvRows.value.slice(1))

// Highlight the file's code body and split it into per-line
// `<div class="hljs-line">` rows so the CSS counter can render the
// gutter. We split-then-highlight (rather than highlight-then-split)
// because hljs's output HTML can span multiple lines — a top-down
// split avoids breaking those spans. The trade-off: a multi-line
// construct (e.g. a Python triple-quoted string) might lose some
// of its context from line to line. Acceptable for a viewer.
function highlightWithLines(source, language) {
  const lines = source.split('\n')
  return lines.map((line) => {
    let html
    if (language && hljs.getLanguage(language)) {
      try {
        html = hljs.highlight(line, { language, ignoreIllegals: true }).value
      } catch {
        html = escapeHtml(line)
      }
    } else {
      html = escapeHtml(line)
    }
    // Empty lines need a non-breaking space so the `<div>` keeps
    // its height (otherwise the line number sits on the previous
    // row's line).
    return `<div class="hljs-line">${html || '&nbsp;'}</div>`
  }).join('')
}

function escapeHtml(s) {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

const highlightedCode = computed(() => {
  if (!props.file || props.file.kind !== 'code') return ''
  return highlightWithLines(props.file.content, props.file.language)
})

function formatSize(bytes) {
  if (bytes == null) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
</script>

<template>
  <Transition name="modal">
    <div
      v-if="open && file"
      class="modal-backdrop"
      @click.self="handleClose"
    >
      <div class="modal-container" role="dialog" aria-modal="true" :aria-label="file.name">
        <header class="modal-header">
          <div class="modal-title">
            <div class="modal-title-main">
              <span class="modal-name">{{ file.name }}</span>
              <span class="modal-kind-badge" :class="`kind-${file.kind}`">{{ kindLabel }}</span>
              <span class="modal-size">{{ formatSize(file.size) }}</span>
            </div>
            <!-- Absolute path of the file on disk, shown below the name
                 when the caller provides `displayPath` (e.g. the
                 long-term memory button passes the full workspace path
                 so the user can locate the file in their terminal). -->
            <div v-if="file.displayPath" class="modal-path" :title="file.displayPath">
              {{ file.displayPath }}
            </div>
          </div>
          <button class="modal-close" @click="handleClose" aria-label="关闭" title="关闭 (Esc)">×</button>
        </header>

        <div class="modal-body">
          <!-- text → monospace pre, no decoration -->
          <pre v-if="file.kind === 'text'" class="modal-text"><code>{{ file.content }}</code></pre>

          <!-- markdown → render with MarkdownView (headings, lists,
               code spans, etc. all formatted as proper UI) -->
          <div v-else-if="file.kind === 'markdown'" class="modal-markdown">
            <MarkdownView :source="file.content" />
          </div>

          <!-- json → pretty-print with 2-space indent so nested
               objects/arrays are readable. Same monospace pre. -->
          <pre v-else-if="file.kind === 'json'" class="modal-text"><code>{{ prettyJson }}</code></pre>

          <!-- jsonl → one record per row. Each row carries:
                 - line number (so the user can map back to the raw file)
                 - record type chip (entity / relation / …)
                 - pretty-printed JSON of that single line
                 - or an error banner when the line didn't parse
               Bad rows (parse errors or missing `type` field) are
               highlighted so the user can see *which* entries the
               graph silently ignored and why. -->
          <div v-else-if="file.kind === 'jsonl'" class="modal-jsonl">
            <div v-if="!file.records || file.records.length === 0" class="modal-jsonl-empty">
              空文件
            </div>
            <div
              v-for="r in file.records"
              v-else
              :key="r.line"
              class="modal-jsonl-row"
              :class="{
                'modal-jsonl-row-err': !r.ok,
                'modal-jsonl-row-entity': r.ok && r.type === 'entity',
                'modal-jsonl-row-relation': r.ok && r.type === 'relation',
              }"
            >
              <div class="modal-jsonl-meta">
                <span class="modal-jsonl-line" :title="`第 ${r.line} 行`">L{{ r.line }}</span>
                <span
                  class="modal-jsonl-type"
                  :class="`modal-jsonl-type-${r.ok ? r.type || 'unknown' : 'error'}`"
                >{{ r.ok ? (r.type || 'unknown') : 'parse error' }}</span>
              </div>
              <pre v-if="r.ok" class="modal-jsonl-data"><code>{{ formatJsonlRecord(r.data) }}</code></pre>
              <div v-else class="modal-jsonl-err-block">
                <div class="modal-jsonl-err-msg">⚠ {{ r.error }}</div>
                <pre class="modal-jsonl-data"><code>{{ r.raw }}</code></pre>
              </div>
            </div>
          </div>

          <!-- csv → render as a real table. First row becomes the
               sticky <thead>, remaining rows become <tbody>. The
               whole table scrolls horizontally + vertically so a
               wide 1200-row sales sheet still fits. -->
          <div v-else-if="file.kind === 'csv'" class="modal-csv">
            <table v-if="csvRows.length" class="modal-csv-table">
              <thead>
                <tr>
                  <th v-for="(h, i) in csvHeaders" :key="'h' + i">{{ h || ' ' }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, ri) in csvBody" :key="'r' + ri">
                  <td v-for="(cell, ci) in row" :key="'c' + ri + '-' + ci">{{ cell }}</td>
                </tr>
              </tbody>
            </table>
            <pre v-else class="modal-text"><code>{{ file.content }}</code></pre>
          </div>

          <!-- code → GitHub-style highlighted block with a line
               gutter. Each line is its own `.hljs-line` div so the
               CSS counter renders a real line number per row. -->
          <pre
            v-else-if="file.kind === 'code'"
            class="modal-text code-with-lines"
            :data-language="file.language"
            v-html="highlightedCode"
          ></pre>

          <!-- Sandboxed iframe for HTML. `sandbox=""` (no allow-* tokens)
               means no scripts, no same-origin, no top navigation — the
               agent-generated HTML can't reach back into the parent app. -->
          <iframe
            v-else-if="file.kind === 'html'"
            class="modal-html"
            sandbox=""
            :srcdoc="file.content"
            referrerpolicy="no-referrer"
          ></iframe>

          <div v-else-if="file.kind === 'image'" class="modal-image-wrap">
            <img :src="binaryDataUrl" :alt="file.name" class="modal-image" />
          </div>

          <iframe
            v-else-if="file.kind === 'pdf'"
            class="modal-pdf"
            :src="binaryDataUrl"
          ></iframe>

          <!-- Error state: the fetch failed (e.g. file not found, 404,
               500). Show the actual error message instead of the
               generic "unknown mime type" fallback, plus a hint for
               the not-found case so the long-term file button doesn't
               feel broken when the agent hasn't written any notes yet. -->
          <div v-else-if="file.error" class="modal-binary">
            <p>⚠️ {{ file.error }}</p>
            <p v-if="isNotFoundError" class="modal-binary-hint">
              长期记忆文件会在 Agent 调用 <code>record_note</code> 工具时自动创建。
            </p>
          </div>
          <div v-else class="modal-binary">
            <p>无法预览此文件类型（{{ file.mime }}）</p>
            <a :href="binaryDataUrl" :download="file.name" class="modal-download">下载 {{ file.name }}</a>
          </div>

          <div v-if="file.truncated" class="modal-truncated">
            ⚠️ 文件超过 2 MB，仅显示前 {{ formatSize(file.truncated_size) }}
          </div>
        </div>
      </div>
    </div>
  </Transition>
</template>

