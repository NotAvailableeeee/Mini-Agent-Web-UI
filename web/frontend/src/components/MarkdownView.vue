<script setup>
/**
 * Markdown renderer for tool results and step content.
 *
 * Pipeline: source → markdown-it (parse + hljs highlight) →
 *           DOMPurify (sanitize) → v-html.
 *
 * Why DOMPurify even though `html: false` already escapes raw HTML in source?
 *   - markdown-it still produces its OWN HTML for things like links, images,
 *     headings — which we WANT, but we also want to make sure no attribute
 *     injection happens via crafted markdown.
 *   - Defense-in-depth: the source comes from tool execution, which is
 *     local + trusted, but a stray `javascript:` URL in a link or an
 *     onerror="" on a broken image is one bug away.
 *
 * Syntax highlighting: markdown-it's `highlight` option runs once per
 * fenced code block. We delegate to highlight.js; unknown languages
 * fall through to an unhighlighted `<pre><code>` (md will auto-escape
 * the content for us). The `hljs` CSS classes (defined in style.css)
 * are colored via CSS variables, so the same markdown-it output
 * re-skins automatically when the user flips light/dark.
 *
 * Links: we override the `link_open` render rule to set `target=_blank` +
 * `rel=noopener noreferrer` directly on the token. This is cleaner than
 * post-processing the sanitized HTML string.
 */

import { computed, inject } from 'vue'
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import hljs from 'highlight.js/lib/core'

// Register the languages we expect to see in tool outputs / model
// replies. `highlight.js/lib/core` is the bare-bones build (no
// auto-loaded languages, no theme CSS) — we add only what we need
// to keep the bundle small.
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
import markdown from 'highlight.js/lib/languages/markdown'
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
hljs.registerLanguage('markdown', markdown)
// Common aliases LLM-generated code fences might use.
hljs.registerLanguage('js', javascript)
hljs.registerLanguage('ts', typescript)
hljs.registerLanguage('py', python)
hljs.registerLanguage('sh', bash)
hljs.registerLanguage('yml', yaml)
hljs.registerLanguage('html', xml)
hljs.registerLanguage('vue', xml)

const props = defineProps({
  source: { type: String, default: '' },
})

const md = new MarkdownIt({
  html: false,        // escape raw HTML in source — output sanitized anyway
  linkify: true,      // auto-link bare URLs
  breaks: true,       // single newline → <br> (matches our previous <pre> behavior)
  typographer: false, // keep ASCII quotes/punctuation as-is
  highlight(str, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return hljs.highlight(str, { language: lang, ignoreIllegals: true }).value
      } catch {
        return ''  // let markdown-it default-escape the body
      }
    }
    return ''  // unknown / no language → plain escaped block
  },
})

// Open all links in a new tab. Apply at render-time via token attributes so
// sanitization doesn't strip them.
const defaultLinkOpen = md.renderer.rules.link_open
  || function (tokens, idx, options, env, self) {
    return self.renderToken(tokens, idx, options)
  }
md.renderer.rules.link_open = function (tokens, idx, options, env, self) {
  tokens[idx].attrSet('target', '_blank')
  tokens[idx].attrSet('rel', 'noopener noreferrer')
  return defaultLinkOpen(tokens, idx, options, env, self)
}

// Inline rule: turn recognised workspace-relative paths in the assistant
// reply text into <a class="file-link" data-file-path="…"> tokens the click
// handler can open in FilePreviewModal.
//
// Why this lives here (and not in ThinkingBlock / ActionBlock etc.):
// MarkdownView is the ONLY place assistant reply text gets rendered
// through markdown-it; ThinkingBlock uses raw <pre> and ActionBlock uses
// <ToolArgs>, neither go through this pipeline.
//
// Why only multi-segment paths: bare filenames like `app.py` collide with
// prose too often. We require at least one `/` plus a known file
// extension, so mentions like "let me read mini_agent/agent.py" become a
// clickable link while inline-code, code-block, and prose sentence
// fragments don't. markdown-it tokenises code_inline / code_block into
// their own token types BEFORE inline `text` rules run, so mentions inside
// backticks or fenced blocks are naturally skipped.
//
// We run this before `emphasis` (markdown-it tries higher-priority rules
// first; emphasis uses `_`/`*` markers that would otherwise fragment a
// path like `mini_agent/agent.py` into `mini_` + `agent/agent.py`). We
// also bail when already inside a markdown link so a path nested inside
// another [text](url) doesn't produce invalid <a>-in-<a> HTML.
md.inline.ruler.before('emphasis', 'file_link', function fileLink(state, silent) {
  // Bail when nested inside an outer markdown link's text region.
  let depth = 0
  for (const t of state.tokens) {
    if (t.type === 'link_open') depth++
    else if (t.type === 'link_close') depth--
  }
  if (depth > 0) return false

  const start = state.pos
  const max = state.posMax
  if (start >= max) return false

  // Require the previous char to be whitespace or a punctuation char that
  // breaks a path token — avoids matching the tail of words like
  // "myapp.py/baz". If we're at the start of the text region, no check.
  if (start > 0) {
    const prev = state.src.charCodeAt(start - 1)
    const isBoundary =
      prev === 0x5F /* _ */ ||
      prev === 0x20 || prev === 0x09 || prev === 0x0A || prev === 0x0D || // whitespace
      prev === 0x28 /* ( */ || prev === 0x5B /* [ */ || prev === 0x7B /* { */ ||
      prev === 0x22 /* " */ || prev === 0x27 /* ' */ || prev === 0x60 /* ` */ ||
      prev === 0x3C /* < */ || prev === 0x3E /* > */ || prev === 0x3D /* = */ ||
      prev === 0x2C /* , */ || prev === 0x3B /* ; */ || prev === 0x2A /* * */
    if (!isBoundary) return false
  }

  // Greedily consume chars that could appear in a path segment or
  // extension separator. Stop on the first non-path char.
  let len = 0
  while (start + len < max) {
    const c = state.src.charCodeAt(start + len)
    if (
      (c >= 0x30 && c <= 0x39) || // 0-9
      (c >= 0x41 && c <= 0x5A) || // A-Z
      (c >= 0x61 && c <= 0x7A) || // a-z
      c === 0x2F /* / */ ||
      c === 0x5F /* _ */ ||
      c === 0x2D /* - */
    ) {
      len++
    } else if (c === 0x2E /* . */ && len > 0) {
      // Allow a `.` separator only if we're not already on a `./` or `..`.
      const prevC = state.src.charCodeAt(start + len - 1)
      if (prevC === 0x2E || prevC === 0x2F) break
      len++
    } else {
      break
    }
  }
  if (len === 0) return false

  const candidate = state.src.slice(start, start + len)
  // Hard-required: at least one `/`, and a known extension at the end.
  // The extension allowlist mirrors the workspace tree's code/text kinds
  // in `web/backend/routes/workspace.py` so we never link binary files.
  if (!candidate.includes('/')) return false
  const extMatch = candidate.match(
    /^[\w][\w-]*(?:\/[\w][\w.-]*){1,3}\.(py|js|jsx|ts|tsx|json|md|txt|yaml|yml|toml|csv|html|css|scss|vue|sh|sql|go|rs|java|c|cpp|h|hpp|xml|log|env|lock)$/,
  )
  if (!extMatch) return false

  // Reject `..` as a path segment (defense in depth — backend _safe_resolve
  // already 403s these, but we'd rather produce no link than a 403 click).
  if (/(^|\/)\.\.(\/|$)/.test(candidate)) return false

  if (silent) return true

  const linkOpen = state.push('link_open', 'a', 1)
  linkOpen.attrs = [
    ['href', '#'],
    ['class', 'file-link'],
    ['data-file-path', candidate],
  ]
  const textToken = state.push('text', '', 0)
  textToken.content = candidate
  state.push('link_close', 'a', -1)

  state.pos = start + len
  return true
})

// Injected by App.vue — `openWorkspaceFile(path)` opens the preview modal.
// Null-safe so MarkdownView still works when used standalone (e.g. inside
// FilePreviewModal which renders MarkdownView for markdown files; that
// case deliberately has no opener).
const openFile = inject('openWorkspaceFile', null)

function onMdClick(e) {
  const a = e.target?.closest && e.target.closest('a.file-link')
  if (!a) return
  e.preventDefault()
  const path = a.getAttribute('data-file-path')
  if (!path) return
  openFile?.({
    path,
    name: path.split('/').pop(),
    size: 0,
  })
}

const html = computed(() => {
  if (!props.source) return ''
  const raw = md.render(props.source)
  return DOMPurify.sanitize(raw, {
    // hljs wraps tokens in spans with class="hljs-keyword" etc.
    // DOMPurify's default config would drop unknown attrs, so we
    // whitelist `class` on every element to keep the highlighting.
    ADD_ATTR: ['target', 'rel', 'class', 'data-file-path'],
  })
})
</script>

<template>
  <!-- eslint-disable-next-line vue/no-v-html -->
  <div class="md" v-if="source" v-html="html" @click="onMdClick"></div>
</template>

<style scoped>
.md {
  /* Typography for rendered markdown. Slightly smaller than typical page
   * defaults because these blocks live inside compact tool/step cards. */
  font-size: 13px;
  line-height: 1.6;
  color: var(--text);
  word-break: break-word;
}
.md :deep(h1),
.md :deep(h2),
.md :deep(h3),
.md :deep(h4) {
  margin: 14px 0 6px;
  font-weight: 600;
  line-height: 1.3;
}
.md :deep(h1) { font-size: 16px; }
.md :deep(h2) { font-size: 15px; }
.md :deep(h3) { font-size: 14px; }
.md :deep(h4) { font-size: 13px; color: var(--text-dim); }
.md :deep(h1:first-child),
.md :deep(h2:first-child),
.md :deep(h3:first-child) { margin-top: 0; }

.md :deep(p) { margin: 0 0 8px; }
.md :deep(p:last-child) { margin-bottom: 0; }

.md :deep(ul),
.md :deep(ol) {
  margin: 0 0 8px;
  padding-left: 20px;
}
.md :deep(li) { margin-bottom: 2px; }
.md :deep(li::marker) { color: var(--text-dim); }

.md :deep(a) {
  color: var(--accent);
  text-decoration: none;
  border-bottom: 1px solid transparent;
}
.md :deep(a:hover) { border-bottom-color: var(--accent); }

.md :deep(code) {
  font-family: var(--font-mono);
  font-size: 12px;
  padding: 1px 5px;
  border-radius: 3px;
  background: var(--panel-2);
  border: 1px solid var(--border);
  color: var(--text);
}
.md :deep(pre) {
  margin: 6px 0;
  padding: 10px 12px;
  /* Code-block background / text — theme-aware via the same
   * `--hljs-bg` / `--text` variables the global hljs theme uses, so a
   * fenced code block in a tool's `content` argument sits inside a
   * surface that matches the current theme instead of an always-dark
   * band that fights the surrounding card. */
  background: var(--hljs-bg);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow-x: auto;
  font-size: 12px;
  line-height: 1.55;
}
.md :deep(pre code) {
  padding: 0;
  background: transparent;
  border: 0;
  color: inherit;
  font-size: inherit;
}

.md :deep(blockquote) {
  margin: 6px 0;
  padding: 4px 12px;
  border-left: 3px solid var(--accent);
  background: var(--accent-soft);
  color: var(--text);
  border-radius: 0 4px 4px 0;
}

.md :deep(table) {
  border-collapse: collapse;
  margin: 6px 0;
  font-size: 12px;
}
.md :deep(th),
.md :deep(td) {
  border: 1px solid var(--border);
  padding: 4px 8px;
  text-align: left;
}
.md :deep(th) {
  background: var(--panel-2);
  font-weight: 600;
}

.md :deep(hr) {
  border: 0;
  border-top: 1px solid var(--border);
  margin: 12px 0;
}

.md :deep(img) {
  max-width: 100%;
  border-radius: 4px;
}

/* File-path tokens emitted by the markdown-it `file_link` inline rule.
   Dashed underline differentiates them from regular hyperlinks (which use
   a solid border-bottom on hover) so the user can spot them at a glance in
   a multi-paragraph assistant reply. Click handling lives on the wrapper
   `.md` div (event delegation); see onMdClick in <script setup>. */
.md :deep(a.file-link) {
  color: var(--accent);
  text-decoration: none;
  border-bottom: 1px dashed var(--accent);
  cursor: pointer;
}
.md :deep(a.file-link:hover) {
  background: var(--accent-soft);
  border-bottom-style: solid;
}
</style>