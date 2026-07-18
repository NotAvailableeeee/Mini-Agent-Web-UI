<script setup>
/**
 * Single-payload JSON inspector — used inside <StepRawDataPanel>'s two
 * columns. Renders the request OR response (exactly one side, no nested
 * collapsibles) for one agent step:
 *
 *   - Request payload → meta strip (model / max_tokens / messages / tools
 *     counts) + highlighted JSON dump of the full wire payload.
 *   - Response payload → optional one-line usage summary + highlighted
 *     JSON dump of the full LLMResponse.
 *
 * Flat by design: the outer "查看本轮原始数据" wrapper in
 * <StepRawDataPanel> is the only collapsible layer. We deliberately
 * don't nest System Prompt / Messages / Tools details here — StepRawDataPanel
 * is the entry point, and inside it the user wants to see JSON directly,
 * not another tree of collapsible sections.
 */

import { computed } from 'vue'

const props = defineProps({
  // Anthropic wire payload sent to the LLM for this step. Mutually
  // exclusive with `response` at the call site — pass exactly one.
  request: { type: Object, default: null },
  // Raw LLMResponse (content / thinking / tool_calls / usage).
  response: { type: Object, default: null },
})

const mode = computed(() => (props.request ? 'request' : 'response'))

// One-line summary for the request meta strip.
const requestMeta = computed(() => {
  const r = props.request
  if (!r) return []
  return [
    { key: 'model', val: r.model ?? '—' },
    { key: 'max_tokens', val: r.max_tokens ?? '—' },
    { key: 'messages', val: Array.isArray(r.messages) ? r.messages.length : 0 },
    { key: 'tools', val: Array.isArray(r.tools) ? r.tools.length : 0 },
  ]
})

// One-line token-usage summary for the response header.
const usageSummary = computed(() => {
  const u = props.response?.usage
  if (!u) return null
  const parts = [`${u.total_tokens} total`, `${u.prompt_tokens} in`, `${u.completion_tokens} out`]
  if (u.cache_read_input_tokens > 0) parts.push(`${u.cache_read_input_tokens} cache hit`)
  if (u.cache_creation_input_tokens > 0) parts.push(`${u.cache_creation_input_tokens} cache write`)
  return parts.join(' · ')
})

// Pretty + lightly-highlighted JSON dump. The input is always our own
// JSON.stringify output (not user content), but we still HTML-escape
// before tokenising as defence-in-depth — `v-html` renders the result.
function highlightJSON(obj) {
  const json = obj == null ? '' : JSON.stringify(obj, null, 2)
  if (!json) return ''
  const esc = json
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
  const tokenRe = /("(?:\\.|[^"\\])*")(\s*:)?|\b(?:true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|[{}\[\],]|[^"]+/g
  let out = ''
  for (const m of esc.matchAll(tokenRe)) {
    const [tok, str, colon] = m
    if (str !== undefined) {
      out += colon
        ? `<span class="json-key">${str}</span><span class="json-punct">${colon}</span>`
        : `<span class="json-string">${str}</span>`
    } else if (/^(?:true|false|null)$/.test(tok)) {
      out += `<span class="json-keyword">${tok}</span>`
    } else if (/^-?\d/.test(tok)) {
      out += `<span class="json-number">${tok}</span>`
    } else if (/^[{}\[\],]$/.test(tok)) {
      out += `<span class="json-punct">${tok}</span>`
    } else {
      out += tok
    }
  }
  return out
}

const highlightedPayload = computed(() => {
  const src = mode.value === 'request' ? props.request : props.response
  return highlightJSON(src)
})
</script>

<template>
  <div v-if="mode" class="io-flat">
    <!-- Meta strip (request only) — quick glance at model + counts
         without forcing the user to scroll the JSON dump. -->
    <div v-if="mode === 'request'" class="io-flat-meta">
      <span v-for="m in requestMeta" :key="m.key" class="io-flat-meta-item">
        <span class="io-flat-meta-key">{{ m.key }}</span>
        <code>{{ m.val }}</code>
      </span>
    </div>

    <!-- Usage summary (response only) — one-line token breakdown. -->
    <div v-else-if="usageSummary" class="io-flat-usage">
      {{ usageSummary }}
    </div>

    <!-- JSON dump. Single scrollable block; no further collapsibles. -->
    <pre class="io-flat-json v-pre"><code v-html="highlightedPayload"></code></pre>
  </div>
</template>

<style scoped>
.io-flat {
  font-size: 11.5px;
  line-height: 1.55;
}

.io-flat-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  padding: 6px 10px;
  margin-bottom: 8px;
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 6px;
}
.io-flat-meta-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--text);
}
.io-flat-meta-key {
  font-family: var(--font-mono);
  font-size: 10px;
  color: var(--text-dim);
  text-transform: lowercase;
}
.io-flat-meta-item code {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text);
}

.io-flat-usage {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--text-dim);
  padding: 4px 10px;
  margin-bottom: 8px;
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 6px;
}

.io-flat-json {
  margin: 0;
  padding: 10px 12px;
  max-height: 460px;
  overflow: auto;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.5;
  white-space: pre;
  color: var(--text);
}

/* JSON syntax colours. Light + dark themes share these — the values
 * were picked to read on both var(--bg) backgrounds. */
:deep(.json-key)    { color: #8250df; }
:deep(.json-string) { color: #0a3069; }
:deep(.json-number) { color: #0550ae; }
:deep(.json-keyword){ color: #cf222e; }
:deep(.json-punct)  { color: #6b7280; }
[data-theme="dark"] :deep(.json-key)    { color: #d2a8ff; }
[data-theme="dark"] :deep(.json-string) { color: #a5d6ff; }
[data-theme="dark"] :deep(.json-number) { color: #79c0ff; }
[data-theme="dark"] :deep(.json-keyword){ color: #ff7b72; }
[data-theme="dark"] :deep(.json-punct)  { color: #8b949e; }
</style>