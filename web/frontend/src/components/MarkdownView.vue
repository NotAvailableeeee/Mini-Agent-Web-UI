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

import { computed } from 'vue'
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

const html = computed(() => {
  if (!props.source) return ''
  const raw = md.render(props.source)
  return DOMPurify.sanitize(raw, {
    // hljs wraps tokens in spans with class="hljs-keyword" etc.
    // DOMPurify's default config would drop unknown attrs, so we
    // whitelist `class` on every element to keep the highlighting.
    ADD_ATTR: ['target', 'rel', 'class'],
  })
})
</script>

<template>
  <!-- eslint-disable-next-line vue/no-v-html -->
  <div class="md" v-if="source" v-html="html"></div>
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
</style>