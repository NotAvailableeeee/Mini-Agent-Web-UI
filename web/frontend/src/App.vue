<script setup>
import { nextTick, onBeforeUnmount, onMounted, provide, ref } from 'vue'
import ChatPanel from './components/ChatPanel.vue'
import WorkspaceTree from './components/WorkspaceTree.vue'
import FilePreviewModal from './components/FilePreviewModal.vue'
import MemoryPanel from './components/MemoryPanel.vue'
import AgentInitInspector from './components/AgentInitInspector.vue'
import { api } from './api/client.js'
import { useTheme } from './composables/useTheme.js'

// useTheme() is module-level state, so this returns the same
// `theme` ref that useTheme() in any other component would see.
// We just need a way to toggle it from the header button.
const { theme, toggle: toggleTheme } = useTheme()

// ----- Sidebar width (resizable divider) -----
// Stored in localStorage so the user's chosen width persists across
// reloads. Defaults narrow (was ~33% of the row before; 280px is much
// more comfortable for the tree on a typical 1280px screen).
const TREE_WIDTH_KEY = 'mini-agent-web:tree-width'
const TREE_WIDTH_DEFAULT = 280
const TREE_WIDTH_MIN = 180
const TREE_WIDTH_MAX = 600
// Width used when the sidebar is collapsed. Kept small but big enough
// for the expand button to be a comfortable click target.
const TREE_COLLAPSED_WIDTH = 32

const treeWidth = ref(TREE_WIDTH_DEFAULT)
// Whether the sidebar is collapsed to a thin strip. Persisted so the
// state survives a reload. The resizer hides itself in this state.
const treeCollapsed = ref(false)

const TREE_COLLAPSED_KEY = 'mini-agent-web:tree-collapsed'

// ----- Memory panel width (resizable on the LEFT side) -----
// Mirrors the workspace-tree resizer: persisted in localStorage, the
// drag inverts the delta so pulling the handle right widens the right
// tree. Here we keep the natural mapping (mouse-left = narrower, since
// the panel is on the left side of the screen).
const MEMORY_WIDTH_KEY = 'mini-agent-web:memory-width'
const MEMORY_WIDTH_DEFAULT = 280
const MEMORY_WIDTH_MIN = 200
const MEMORY_WIDTH_MAX = 480
// Same thin-strip width the workspace tree uses when collapsed.
// Kept small but big enough for the expand button to be a comfortable
// click target.
const MEMORY_COLLAPSED_WIDTH = 32
const MEMORY_COLLAPSED_KEY = 'mini-agent-web:memory-collapsed'
const memoryWidth = ref(MEMORY_WIDTH_DEFAULT)
// Whether the memory panel is collapsed to a thin strip. Persisted so
// the state survives a reload — same UX as the right-hand workspace
// tree's collapse.
const memoryCollapsed = ref(false)
let memoryResizeStartX = 0
let memoryResizeStartWidth = 0

function clampMemoryWidth(v) {
  return Math.max(MEMORY_WIDTH_MIN, Math.min(MEMORY_WIDTH_MAX, v))
}

function startMemoryResize(e) {
  const point = e.touches ? e.touches[0] : e
  memoryResizeStartX = point.clientX
  memoryResizeStartWidth = memoryWidth.value
  document.addEventListener('mousemove', onMemoryResizeMove)
  document.addEventListener('mouseup', stopMemoryResize)
  document.addEventListener('touchmove', onMemoryResizeMove, { passive: false })
  document.addEventListener('touchend', stopMemoryResize)
  document.body.style.userSelect = 'none'
  document.body.style.cursor = 'col-resize'
  e.preventDefault()
}

function onMemoryResizeMove(e) {
  const point = e.touches ? e.touches[0] : e
  const delta = point.clientX - memoryResizeStartX
  // Natural mapping for the LEFT panel: drag right widens it.
  memoryWidth.value = clampMemoryWidth(memoryResizeStartWidth + delta)
}

function stopMemoryResize() {
  document.removeEventListener('mousemove', onMemoryResizeMove)
  document.removeEventListener('mouseup', stopMemoryResize)
  document.removeEventListener('touchmove', onMemoryResizeMove)
  document.removeEventListener('touchend', stopMemoryResize)
  document.body.style.userSelect = ''
  document.body.style.cursor = ''
  try {
    localStorage.setItem(MEMORY_WIDTH_KEY, String(memoryWidth.value))
  } catch {
    // ignore
  }
}

function resetMemoryWidth() {
  memoryWidth.value = MEMORY_WIDTH_DEFAULT
  try {
    localStorage.setItem(MEMORY_WIDTH_KEY, String(memoryWidth.value))
  } catch {
    // ignore
  }
}

// Drag state. Kept in module scope (not reactive) because they update
// on every mousemove and don't need to drive any rendering directly.
let resizeStartX = 0
let resizeStartWidth = 0

function clampTreeWidth(v) {
  return Math.max(TREE_WIDTH_MIN, Math.min(TREE_WIDTH_MAX, v))
}

function startResize(e) {
  const point = e.touches ? e.touches[0] : e
  resizeStartX = point.clientX
  resizeStartWidth = treeWidth.value
  // Listen on document so the drag continues even if the cursor
  // leaves the resizer element. `passive: false` lets us call
  // preventDefault on touchmove to suppress page scroll.
  document.addEventListener('mousemove', onResizeMove)
  document.addEventListener('mouseup', stopResize)
  document.addEventListener('touchmove', onResizeMove, { passive: false })
  document.addEventListener('touchend', stopResize)
  document.body.style.userSelect = 'none'
  document.body.style.cursor = 'col-resize'
  e.preventDefault()
}

function onResizeMove(e) {
  const point = e.touches ? e.touches[0] : e
  // Inverted on purpose: the resizer is the tree's left edge, so
  // pulling the handle *away* from the tree (toward the left) should
  // give the tree more room — i.e. mouse-left = wider, mouse-right = narrower.
  // `delta = currentX - startX` is positive when the user drags right,
  // so we subtract it to flip the mapping.
  const delta = point.clientX - resizeStartX
  treeWidth.value = clampTreeWidth(resizeStartWidth - delta)
}

function stopResize() {
  document.removeEventListener('mousemove', onResizeMove)
  document.removeEventListener('mouseup', stopResize)
  document.removeEventListener('touchmove', onResizeMove)
  document.removeEventListener('touchend', stopResize)
  document.body.style.userSelect = ''
  document.body.style.cursor = ''
  try {
    localStorage.setItem(TREE_WIDTH_KEY, String(treeWidth.value))
  } catch {
    // localStorage may be disabled (private browsing); not fatal.
  }
}

function resetTreeWidth() {
  treeWidth.value = TREE_WIDTH_DEFAULT
  try {
    localStorage.setItem(TREE_WIDTH_KEY, String(treeWidth.value))
  } catch {
    // ignore
  }
}

onBeforeUnmount(stopResize)
onBeforeUnmount(stopMemoryResize)

// ----- Existing state -----
const info = ref(null)
const workspaceTreeRef = ref(null)
// Ref to the chat panel so sibling panels (currently MemoryPanel's
// graph-section collapse) can ask it to scroll to the bottom. We
// could plumb a callback instead, but a ref keeps the wiring in one
// place and avoids prop-drilling a function through three components.
const chatPanelRef = ref(null)
// Bumped on every step_end so the MemoryPanel can re-fetch via a
// watch. Using a counter (instead of a callback) keeps the panel
// decoupled from App's internals — the panel just imports this
// prop and watches it.
const memoryRefreshTrigger = ref(0)
const selectedFile = ref(null)
const showPreviewModal = ref(false)
const previewError = ref('')

// ----- Agent 初始化 trace -----
// 在 ChatPanel 顶部作为 preamble 展示（5 步：加载配置 → 构造 LLM → 构造工具
// → 加载系统提示 → 构造 Agent）。在挂载时一次性拉取，传给 ChatPanel。
const preambleSteps = ref([])
const preambleLoading = ref(false)
const preambleError = ref('')

onMounted(async () => {
  // Restore sidebar width + collapsed state first so the first paint
  // matches the user's last session.
  try {
    const stored = localStorage.getItem(TREE_WIDTH_KEY)
    if (stored) {
      const v = parseInt(stored, 10)
      if (Number.isFinite(v)) {
        treeWidth.value = clampTreeWidth(v)
      }
    }
  } catch {
    // ignore
  }
  try {
    const collapsedStored = localStorage.getItem(TREE_COLLAPSED_KEY)
    if (collapsedStored === '1') treeCollapsed.value = true
  } catch {
    // ignore
  }
  try {
    const memStored = localStorage.getItem(MEMORY_WIDTH_KEY)
    if (memStored) {
      const v = parseInt(memStored, 10)
      if (Number.isFinite(v)) {
        memoryWidth.value = clampMemoryWidth(v)
      }
    }
  } catch {
    // ignore
  }
  try {
    const memoryCollapsedStored = localStorage.getItem(MEMORY_COLLAPSED_KEY)
    if (memoryCollapsedStored === '1') memoryCollapsed.value = true
  } catch {
    // ignore
  }

  // 立即把 banner 设为 loading 状态，避免"页面先空白再出现 banner"。
  preambleLoading.value = true
  await nextTick()

  // 轮询 initTraceState（200ms 间隔）—— 完全绕开 SSE / Vite proxy。
  //
  // 为什么不用 SSE:Vite dev server 的 http-proxy 默认会把整个 HTTP
  // 响应缓冲完才转发,即使配 selfHandleResponse + 手动 pipe 也
  // 经常被 Vite 自家中间件拦掉。前端看到的就是"卡在 step 1 然后
  // 突然全部完成"。轮询每个请求都是独立的短响应,根本不会被任何
  // 中间代理缓冲。
  //
  // 后端 init_state 是非阻塞的:首次调用 kick off _ensure_agent 在
  // 后台跑,后续调用直接读 _init_events 返回当前已捕获的事件列表。
  // 列表随着 5 步执行逐渐增长,前端轮询就能看到 step 1/2/3/4/5
  // 依次出现。
  const POLL_INTERVAL_MS = 200
  let elapsed = 0
  const startedAt = Date.now()
  try {
    while (true) {
      const state = await api.initTraceState()
      // 用后端返回的 events 增量更新(后端已经把 source_code 富化了)
      if (state.ready === false && state.error) {
        preambleError.value = state.error
        break
      }
      // 去重按 n 排序(防御性:理论上后端已经按顺序返回)
      const sorted = (state.events || []).slice().sort((a, b) => a.n - b.n)
      preambleSteps.value = sorted

      // ready 表示 _ensure_agent 全部跑完 → 退出轮询
      if (state.ready) {
        // done 事件携带的 model/workspace 写到 info
        if (state.model) {
          info.value = {
            model: state.model,
            api_base: state.api_base,
            workspace: state.workspace,
            workspace_abs: state.workspace_abs || '',
            tools: [],
            max_steps: 0,
            message_count: 0,
          }
        }
        break
      }

      // 还在 init 中 → 等 200ms 再拉
      await new Promise(r => setTimeout(r, POLL_INTERVAL_MS))
      elapsed = Date.now() - startedAt
    }
  } catch (err) {
    preambleError.value = err.message
  } finally {
    const totalElapsed = Date.now() - startedAt
    if (totalElapsed < 120) {
      await new Promise(r => setTimeout(r, 120 - totalElapsed))
    }
    preambleLoading.value = false
  }

  // 头部完整元数据：放在轮询结束之后兜底(以防 init_state 没带 model 等字段)
  if (!info.value || !info.value.model) {
    try {
      const meta = await api.info()
      // 后端 /api/info 现在是 lazy 的: agent 未初始化时返回 {ready:false}.
      // 不要用一个空对象覆盖掉 info.value,否则轮询刚补上的 model 会被
      // 下一次 fallback 抹掉。只有当 meta 真带数据时才采用。
      if (meta && meta.model) {
        info.value = meta
      } else if (!info.value) {
        info.value = { error: 'agent not ready' }
      }
    } catch (err) {
      // 已经通过 polling 拿到基本 model,info() 失败不影响
      if (!info.value) info.value = { error: err.message }
    }
  }
})

// Persist collapsed state whenever it flips. Wrap in try/catch so a
// disabled localStorage (private mode) doesn't break the toggle.
function onCollapsedChange(v) {
  treeCollapsed.value = v
  try {
    localStorage.setItem(TREE_COLLAPSED_KEY, v ? '1' : '0')
  } catch {
    // ignore
  }
}

// Same persistence pattern for the memory panel's collapsed state.
function onMemoryCollapsedChange(v) {
  memoryCollapsed.value = v
  try {
    localStorage.setItem(MEMORY_COLLAPSED_KEY, v ? '1' : '0')
  } catch {
    // ignore
  }
}

function handleStepEnd() {
  workspaceTreeRef.value?.refresh()
  memoryRefreshTrigger.value++
}

// Fired by MemoryPanel after the graph section's collapse arrow is
// clicked. We then nudge the chat to its bottom — collapsing a section
// usually means the user wants to land on the latest turn rather than
// wherever the scroll position happened to be.
function handleGraphCollapsed() {
  chatPanelRef.value?.scrollToBottom()
}

// Called by ChatPanel after the user confirms + the backend
// `POST /api/chat/clear` succeeds. Centralised here so the memory
// panel can be nudged to re-fetch — clear doesn't fire a `step_end`
// event (it just resets messages), so the memory panel would otherwise
// keep showing the stale conversation.
async function handleClearConversation() {
  const res = await api.clearChat()
  memoryRefreshTrigger.value++
  return res
}

// Triggered by the "📄" button in the long-term memory section
// header. Opens the existing FilePreviewModal in json mode pointing
// at workspace/.agent_memory.json. We pass `displayPath` (the
// absolute path) separately so the modal can show "you are looking
// at <abs path>" — the file's own `path` is the workspace-relative
// path used by the API, which is less useful for the user.
async function handleViewLongTermFile(relPath) {
  const ws = info.value?.workspace || ''
  const fullPath = ws && relPath ? `${ws}/${relPath}` : relPath
  const name = relPath.split('/').pop() || relPath
  previewError.value = ''
  selectedFile.value = {
    path: relPath,
    displayPath: fullPath,
    name,
    size: 0,
    kind: 'text',
    content: '',
    mime: 'text/plain',
    loading: true,
  }
  showPreviewModal.value = true
  try {
    const file = await api.workspaceFile(relPath)
    // Preserve our displayPath through the API response (which only
    // includes the relative path).
    selectedFile.value = { ...file, displayPath: fullPath }
  } catch (err) {
    selectedFile.value = {
      path: relPath,
      displayPath: fullPath,
      name,
      size: 0,
      kind: 'binary',
      content: '',
      mime: '',
      error: err.message,
    }
    previewError.value = err.message
  }
}

async function handleFileSelect(meta) {
  previewError.value = ''
  selectedFile.value = {
    name: meta.name,
    size: meta.size,
    kind: 'text',
    content: '',
    mime: 'text/plain',
    loading: true,
  }
  showPreviewModal.value = true
  try {
    const file = await api.workspaceFile(meta.path)
    selectedFile.value = file
  } catch (err) {
    selectedFile.value = {
      name: meta.name,
      size: meta.size,
      kind: 'binary',
      content: '',
      mime: '',
      error: err.message,
    }
    previewError.value = err.message
  }
}

// Expose handleFileSelect via provide/inject so MarkdownView (nested 4
// levels deep in AssistantBlock → TurnContent → ChatPanel → App) can open
// the preview modal when the user clicks a file-path token in the LLM's
// assistant reply. The contract matches WorkspaceTree's @file-select
// emitter ({ path, name, size }); MarkdownView populates `name` from the
// path's basename and `size: 0` since it has no other signal.
provide('openWorkspaceFile', handleFileSelect)

function handlePreviewClose() {
  showPreviewModal.value = false
  setTimeout(() => {
    selectedFile.value = null
    previewError.value = ''
  }, 200)
}
</script>

<template>
  <header class="app-header">
    <div class="brand">
      <div class="logo">🔪 解剖一个Agent 🧠</div>
      <div class="subtitle">
        ——可视化的
        <a
          class="brand-link"
          href="https://github.com/MiniMax-AI/Mini-Agent"
          target="_blank"
          rel="noopener noreferrer"
        >Mini-Agent</a>
      </div>
    </div>
    <div class="meta" v-if="info && !info.error">
      model: {{ info.model }}
    </div>
    <div class="meta error" v-else-if="info && info.error">
      ⚠️ {{ info.error }}
    </div>
    <button
      class="theme-toggle"
      :title="theme === 'light' ? '切换到深色' : '切换到浅色'"
      :aria-label="theme === 'light' ? '切换到深色' : '切换到浅色'"
      @click="toggleTheme"
    >{{ theme === 'light' ? '🌙' : '☀️' }}</button>
  </header>

  <!-- Agent 初始化一栏 + Showcase + Chat 三段竖向堆叠在中间列；
       Memory / Workspace 顶天立地贴左右两侧。
       顶部的 <header> 不参与这套布局。 -->
  <div class="app-layout">
    <!-- 左侧记忆栏：顶天立地，宽由 resizer 决定 -->
    <MemoryPanel
      class="layout-left"
      :refresh-trigger="memoryRefreshTrigger"
      :style="{
        width: (memoryCollapsed ? MEMORY_COLLAPSED_WIDTH : memoryWidth) + 'px',
      }"
      :collapsed="memoryCollapsed"
      :on-view-raw-file="handleViewLongTermFile"
      @update:collapsed="onMemoryCollapsedChange"
      @graph-collapsed="handleGraphCollapsed"
    />

    <!-- 左侧 resizer -->
    <div
      v-if="!memoryCollapsed"
      class="resizer"
      @mousedown="startMemoryResize"
      @touchstart="startMemoryResize"
      @dblclick="resetMemoryWidth"
      role="separator"
      aria-orientation="vertical"
      title="拖动调整宽度 · 双击重置"
    ></div>

    <!-- 中间列：banner → showcase → chat 三段纵向堆叠 -->
    <div class="layout-center">
      <!-- Agent 初始化一栏：trace 加载中显示过渡条，加载完后保持折叠按钮。
           AgentInitInspector 是 fragment-root 组件，class 不会穿透到内部，
           所以这里必须用真实 DOM 节点包一层来承载 layout-center-banner 样式。 -->
      <div
        v-if="preambleLoading || preambleError || (preambleSteps && preambleSteps.length)"
        class="layout-center-banner"
      >
        <AgentInitInspector
          :steps="preambleSteps"
          :loading="preambleLoading"
          :error="preambleError"
        />
      </div>

      <!-- 对话框 + 输入框（占据中间列完整高度）。
           能力 pill 条已经搬到 ChatPanel 内部、紧贴输入框上方。 -->
      <ChatPanel
        ref="chatPanelRef"
        class="layout-center-chat"
        :on-step-end="handleStepEnd"
        :on-clear="handleClearConversation"
      />
    </div>

    <!-- 右侧 resizer -->
    <div
      v-if="!treeCollapsed"
      class="resizer"
      @mousedown="startResize"
      @touchstart="startResize"
      @dblclick="resetTreeWidth"
      role="separator"
      aria-orientation="vertical"
      title="拖动调整宽度 · 双击重置"
    ></div>

    <!-- 右侧 workspace：顶天立地，宽由 resizer 决定 -->
    <WorkspaceTree
      ref="workspaceTreeRef"
      class="layout-right workspace-tree-resizable"
      :style="{
        width: (treeCollapsed ? TREE_COLLAPSED_WIDTH : treeWidth) + 'px',
      }"
      :collapsed="treeCollapsed"
      @update:collapsed="onCollapsedChange"
      @file-select="handleFileSelect"
    />
  </div>

  <FilePreviewModal
    :file="selectedFile"
    :open="showPreviewModal"
    @close="handlePreviewClose"
  />
</template>
