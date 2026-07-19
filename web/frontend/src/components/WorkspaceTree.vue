<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api/client.js'

const props = defineProps({
  // When true, the tree collapses to a thin 32px strip with a single
  // expand button. The strip is still functional — clicking expands it
  // back to the previous width (the parent owns the width state, this
  // component just toggles the flag).
  collapsed: { type: Boolean, default: false },
})
const emit = defineEmits(['fileSelect', 'update:collapsed'])

const tree = ref({ workspace: '', entries: [] })
const loading = ref(false)
const error = ref('')
// Set of directory paths the user has expanded. Using a Set keeps
// the watch cheap (we trigger reactivity by reassigning the ref).
const expanded = ref(new Set(['.']))

async function loadTree() {
  loading.value = true
  error.value = ''
  try {
    tree.value = await api.workspaceTree()
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
}

// Exposed so the parent (App.vue) can force a refresh after a step_end
// — the agent just wrote a file, the tree should reflect it.
defineExpose({ refresh: loadTree })

onMounted(loadTree)

function toggleDir(path) {
  const next = new Set(expanded.value)
  if (next.has(path)) next.delete(path)
  else next.add(path)
  expanded.value = next
}

function isExpanded(path) {
  return expanded.value.has(path)
}

// Flatten the nested tree into a single list of {entry, depth} rows,
// honouring the current `expanded` set. This avoids needing a
// self-referential component for a tree that, in practice, has < 50
// rows in this app's workspace.
function flatten(entries, depth, exp, out) {
  for (const entry of entries) {
    out.push({ entry, depth })
    if (entry.type === 'directory' && exp.has(entry.path)) {
      flatten(entry.children || [], depth + 1, exp, out)
    }
  }
  return out
}

const flatEntries = computed(() => {
  return flatten(tree.value.entries || [], 0, expanded.value, [])
})

function handleFileClick(entry) {
  emit('fileSelect', { path: entry.path, name: entry.name, size: entry.size })
}

function formatSize(bytes) {
  if (bytes == null) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function dirName(path) {
  if (path === '.') return 'workspace'
  return path.split('/').pop()
}

// File-icon by extension. Keeps the tree scannable when the agent
// dumps a mix of .py / .md / .html / .png into the workspace.
function fileIcon(name) {
  const ext = name.split('.').pop().toLowerCase()
  const map = {
    html: '🌐', htm: '🌐',
    css: '🎨',
    js: '📜', ts: '📜', mjs: '📜', jsx: '⚛️', tsx: '⚛️', vue: '📜', svelte: '📜',
    json: '📋', yaml: '📋', yml: '📋', toml: '📋', xml: '📋',
    md: '📝', txt: '📝', rst: '📝',
    py: '🐍', rb: '💎', go: '🦫', rs: '🦀', java: '☕', sh: '🐚',
    png: '🖼️', jpg: '🖼️', jpeg: '🖼️', gif: '🖼️', webp: '🖼️', svg: '🖼️',
    pdf: '📕',
    doc: '📘', docx: '📘',
    csv: '📊', tsv: '📊',
    zip: '📦', tar: '📦', gz: '📦', '7z': '📦', rar: '📦',
  }
  return map[ext] || '📄'
}

function toggleCollapse() {
  emit('update:collapsed', !props.collapsed)
}
</script>

<template>
  <aside class="workspace-tree" :class="{ 'workspace-tree-collapsed': collapsed }">
    <!-- Collapsed: a single full-strip button. The whole 32px column is
         the click target — no need to aim for the small arrow. The
         icon is purely decorative; the real affordance is the
         button's hover state covering the entire column. The tree
         data is kept in memory so expanding is instant (no refetch). -->
    <button
      v-if="collapsed"
      class="tree-collapsed-trigger"
      @click="toggleCollapse"
      title="展开 Workspace"
      aria-label="展开 Workspace"
    >
      <span class="tree-collapsed-icon" aria-hidden="true">◀</span>
      <!-- Vertical "Workspace" label gives a visual hint about what the
           strip is. Rotated with `writing-mode` so it reads bottom-up
           without taking horizontal space. -->
      <span class="tree-collapsed-label" aria-hidden="true">Workspace</span>
    </button>

    <!-- Expanded: full tree -->
    <template v-else>
      <header class="tree-header">
        <span class="tree-title">📁 Workspace</span>
        <div class="tree-header-actions">
          <button
            class="tree-refresh"
            @click="loadTree"
            :disabled="loading"
            :title="loading ? '刷新中…' : '刷新'"
            :class="{ spinning: loading }"
          >↻</button>
          <button
            class="tree-collapse"
            @click="toggleCollapse"
            title="收起 Workspace"
            aria-label="收起 Workspace"
          >▶</button>
        </div>
      </header>

      <div v-if="error" class="tree-status error">⚠️ {{ error }}</div>
      <div v-else-if="loading && tree.entries.length === 0" class="tree-status">加载中…</div>
      <div v-else-if="tree.entries.length === 0" class="tree-status">暂无文件</div>

      <ul v-else class="tree-list">
        <li
          v-for="row in flatEntries"
          :key="row.entry.path"
          class="tree-item"
          :class="`tree-${row.entry.type}`"
          :style="{ paddingLeft: `${row.depth * 14 + 8}px` }"
        >
          <button
            v-if="row.entry.type === 'directory'"
            class="tree-toggle"
            @click="toggleDir(row.entry.path)"
            :aria-label="isExpanded(row.entry.path) ? '折叠' : '展开'"
          >{{ isExpanded(row.entry.path) ? '▼' : '▶' }}</button>
          <span v-else class="tree-toggle-spacer"></span>

          <span class="tree-icon">{{ row.entry.type === 'directory' ? '📁' : fileIcon(row.entry.name) }}</span>

          <span
            v-if="row.entry.type === 'directory'"
            class="tree-name tree-name-dir"
            @click="toggleDir(row.entry.path)"
          >{{ dirName(row.entry.path) }}</span>
          <span
            v-else
            class="tree-name"
            @click="handleFileClick(row.entry)"
            :title="row.entry.name"
          >
            <span class="tree-name-text">{{ row.entry.name }}</span>
            <span class="tree-size">{{ formatSize(row.entry.size) }}</span>
          </span>
        </li>
      </ul>
    </template>
  </aside>
</template>
