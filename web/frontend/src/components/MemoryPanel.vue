<script setup>
/**
 * Left-side memory panel.
 *
 * Two vertically-split sections (per the user's spec: "上下两栏"):
 *   1. 短期记忆 (Short-term) — agent.messages minus the system prompt.
 *      Each entry shows the role (👤 / 🤖 / 🔧 + tool name), the
 *      content preview, and any tool calls as chips. Same data the
 *      LLM sees on its next turn.
 *   2. 长期记忆 (Long-term) — workspace/.agent_memory.json, the
 *      notes written by the agent's `record_note` tool. Each note
 *      shows its category, content, and relative timestamp.
 *
 * Auto-refreshes on `step_end` (parent passes the same `onStepEnd`
 * callback the workspace tree uses) so the panel tracks the agent's
 * progress without any user action.
 */

import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { api } from '../api/client.js'
import RadialGraphSvg from './RadialGraphSvg.vue'

const props = defineProps({
  // Counter the parent bumps on every step_end. We just `watch` it
  // and re-fetch — same pattern the workspace tree uses.
  refreshTrigger: { type: Number, default: 0 },
  // Optional callback invoked when the user clicks the "查看原文件"
  // button in the long-term section header. Receives the workspace-
  // relative path to open. App.vue uses it to fetch the file and open
  // the existing FilePreviewModal in json mode.
  onViewRawFile: { type: Function, default: null },
  // When true, the panel collapses to a thin 32px strip with a single
  // expand button — mirrors the workspace-tree collapse UX on the
  // right side of the layout. Data stays in memory so expanding is
  // instant (no refetch).
  collapsed: { type: Boolean, default: false },
})
const emit = defineEmits(['update:collapsed', 'graph-collapsed'])

function toggleCollapsed() {
  emit('update:collapsed', !props.collapsed)
}

const shortMessages = ref([])
const longNotes = ref([])
const graphEntities = ref([])
const graphRelations = ref([])
const loading = ref(false)
const error = ref('')

// ----- Per-tool-call expanded state -----
// Each tool chip is a click target that toggles its arguments panel.
// Keyed by `<msgIndex>-<tcIndex>` so two messages with two tool calls
// each keep their own expand/collapse state across re-renders.
const expandedToolCalls = ref(new Set())

function toolCallKey(msgIndex, tcIndex) {
  return `${msgIndex}-${tcIndex}`
}

function isToolCallExpanded(msgIndex, tcIndex) {
  return expandedToolCalls.value.has(toolCallKey(msgIndex, tcIndex))
}

function toggleToolCall(msgIndex, tcIndex) {
  const key = toolCallKey(msgIndex, tcIndex)
  const next = new Set(expandedToolCalls.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  expandedToolCalls.value = next
}

// ----- Floating info tooltip -----
// The icon's description text is teleported to <body> so it escapes
// the memory panel's `overflow: hidden` (which would otherwise clip
// the bubble the moment it crosses the section's right edge into the
// chat panel area). Position is computed from the icon's viewport
// rect on enter/focus, cleared on leave/blur.
const infoTooltip = ref({ show: false, text: '', x: 0, y: 0 })

function showInfo(text, evt) {
  const r = evt.currentTarget.getBoundingClientRect()
  infoTooltip.value = {
    show: true,
    text,
    // 8px gap to the right of the icon, vertically centered.
    x: r.right + 8,
    y: r.top + r.height / 2,
  }
}

function hideInfo() {
  infoTooltip.value.show = false
}

// ----- Center-node observation tooltip -----
// Shown when the user hovers the center node of the radial graph.
// Reuses the same `<Teleport to="body">` strategy as `infoTooltip`
// (so it escapes the memory panel's `overflow: hidden`) but with
// structured content — entity name + type chip + bulleted
// observations — instead of a single text line.
const centerTooltip = ref({
  show: false,
  name: '',
  entityType: '',
  observations: [],
  x: 0,
  y: 0,
})

function showCenterTooltip(evt) {
  const name = selectedEntityName.value
  if (!name) return
  const entity = graphEntities.value.find((e) => e.name === name)
  // If the center is a ghost node (only referenced by a relation
  // endpoint, not in `graphEntities`), there's nothing to show —
  // bail silently rather than rendering an empty tooltip.
  if (!entity) return
  const obs = entity.observations || []
  if (obs.length === 0) return
  const r = evt.currentTarget.getBoundingClientRect()
  centerTooltip.value = {
    show: true,
    name: entity.name,
    entityType: entity.entityType || '',
    observations: obs,
    // Anchor to the right edge of the center node + 10px gap.
    // For typical sidebar widths (280–480px) this puts the
    // tooltip in the chat-panel area where it has the most
    // breathing room; no flip logic needed.
    x: r.right + 10,
    y: r.top + r.height / 2,
  }
}

function hideCenterTooltip() {
  centerTooltip.value.show = false
}

// ----- Fullscreen preview modal -----
// When the user clicks the fullscreen icon overlaid on the radial
// graph, this opens a centered modal showing the same graph at a
// much larger size. Same pills + same hover interactions; just
// more room to see the relationships.
const previewOpen = ref(false)

function closePreview() {
  previewOpen.value = false
  // Hide any open tooltips so they don't linger after the modal
  // closes (they're teleported to body, so they'd otherwise stay
  // until their next mouseenter/blur cycle).
  hideCenterTooltip()
}

// Escape-to-close. Bound globally while the modal is open. We use
// `keydown` (capture) so we can preempt the chat input's Escape
// handler if it happens to be focused at the same time.
function onPreviewKey(evt) {
  if (evt.key === 'Escape') closePreview()
}

watch(previewOpen, (open) => {
  if (open) {
    document.addEventListener('keydown', onPreviewKey)
  } else {
    document.removeEventListener('keydown', onPreviewKey)
  }
})

onUnmounted(() => {
  // Defensive: clean up if the panel unmounts while the modal is
  // still open (e.g. user closes the whole sidebar).
  document.removeEventListener('keydown', onPreviewKey)
})

// ----- Helpers -----
function preview(s, max = 80) {
  if (!s) return ''
  const oneLine = s.replace(/\s+/g, ' ').trim()
  return oneLine.length > max ? oneLine.slice(0, max) + '…' : oneLine
}

/**
 * Render the arguments dict of a tool call as a compact monospace
 * preview. Truncates long string values and oversized arrays so the
 * card stays scannable in the narrow left rail — the full dict is
 * still available in `entry.tool_calls` if we ever want an expander.
 */
function formatArgs(args) {
  if (!args || typeof args !== 'object' || Object.keys(args).length === 0) return ''
  const trunc = (s) => (s.length > 80 ? s.slice(0, 80) + '…' : s)
  const out = {}
  for (const [k, v] of Object.entries(args)) {
    if (Array.isArray(v)) {
      out[k] = v.length > 3 ? [...v.slice(0, 3), `…+${v.length - 3}`] : v
    } else if (typeof v === 'string') {
      out[k] = trunc(v)
    } else {
      out[k] = v
    }
  }
  return JSON.stringify(out)
}

// Reactive "now" that ticks every second while the panel is mounted.
// Using a ref (not Date.now()) means the template re-renders on each
// tick and the "X 秒前" labels stay current without the user having
// to interact with the panel.
const now = ref(Date.now())
let nowInterval = null

onMounted(() => {
  // 1s is fine for the "X 秒前" granularity. For "X 分钟前" /
  // "X 小时前" the user won't notice the slight lag.
  nowInterval = setInterval(() => { now.value = Date.now() }, 1000)
})
onUnmounted(() => {
  if (nowInterval) clearInterval(nowInterval)
  nowInterval = null
})

function relTime(iso) {
  if (!iso) return ''
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return ''
  // Read `now.value` so this function's caller (the template render)
  // registers a reactive dependency on `now`. Without that read, the
  // "X 秒前" text would only update when something else triggered a
  // re-render — the user would see a stale timestamp.
  const diff = now.value - then
  const sec = Math.floor(diff / 1000)
  if (sec < 60) return `${sec} 秒前`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min} 分钟前`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr} 小时前`
  const day = Math.floor(hr / 24)
  if (day < 30) return `${day} 天前`
  return new Date(iso).toLocaleDateString('zh-CN')
}

// Map a free-form `note.category` string into a CSS class slug.
// Returns 'general' for missing input; lowercases + replaces any
// non-alphanumeric chars with '-' so agent-invented categories
// (e.g. "user_preference", "Task State!", "bug.history") still
// produce a valid class name and degrade gracefully to the base
// green accent when no per-category rule matches.
function categorySlug(raw) {
  if (!raw) return 'general'
  return String(raw)
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '') || 'general'
}

// Graph helpers — `otherSide` is used by both the radial layout
// (centerRelations / radialLayout) to flip relation endpoints so
// the layout always reads "center → other". Kept as a plain
// function so it can be called from inside computeds and templates
// without binding overhead.
function otherSide(self, relation) {
  return relation.from === self ? relation.to : relation.from
}

// ----- Radial graph layout -----
// The long-term graph panel renders the data as a radial SVG:
// one entity in the center, its directly-connected entities spread
// evenly around it on a circle, with the relation type drawn as a
// label on each connecting edge. The user can switch the center
// entity via ◀ / ▶ buttons or by clicking a pill below the SVG.
//
// Geometry is computed from a 320×320 viewBox so it scales cleanly
// to whatever width the sidebar happens to be. Coordinates:
//   - center node at (160, 160)
//   - outer ring at radius 105 (leaves ~50px on each side for labels)
//   - center node radius 28 (visually dominant)
//   - outer node radius 20
// All positions are pure functions of (selectedEntity, entities,
// relations) — recomputed by Vue's reactivity when the user clicks
// a pill, no manual layout passes.

// Which entity is currently in the center. Empty string = nothing
// selected yet (panel auto-selects the first entity with relations
// on data load).
const selectedEntityName = ref('')

// Names of entities that have at least one relation touching them.
// Used by ◀ / ▶ to cycle through "interesting" centers, skipping
// isolated nodes that would render an empty radial.
const entitiesWithRelations = computed(() => {
  const names = new Set()
  for (const r of graphRelations.value) {
    if (r.from) names.add(r.from)
    if (r.to) names.add(r.to)
  }
  // Intersect with actual entities (when present) so ghost-only
  // names from relation endpoints don't become selectable centers
  // — they have no observations/type to display in the center label.
  const entityNames = new Set(graphEntities.value.map((e) => e.name))
  const fromEntities = [...names].filter((n) => entityNames.has(n))
  return fromEntities.length > 0 ? fromEntities : [...names]
})

// When `selectedEntityName` is empty, default to the first entity
// with relations so the user always sees a populated graph on load.
watch(
  [entitiesWithRelations, selectedEntityName],
  ([names, current]) => {
    if (!current && names.length > 0) {
      selectedEntityName.value = names[0]
    } else if (current && !names.includes(current) && names.length > 0) {
      // The previously-selected entity is no longer in the graph
      // (data was refreshed and that entity was removed) — fall
      // back to the first available one.
      selectedEntityName.value = names[0]
    }
  },
  { immediate: true },
)

function selectEntity(name) {
  selectedEntityName.value = name
}

// All relations touching the center entity. Each becomes an edge
// from the center node to an outer node. Self-loops (a → a) are
// filtered out because they have no meaningful radial position.
const centerRelations = computed(() => {
  const name = selectedEntityName.value
  if (!name) return []
  return graphRelations.value.filter(
    (r) =>
      (r.from === name || r.to === name) &&
      r.from !== r.to &&
      otherSide(name, r),
  )
})

// Outer-node geometry. One entry per *unique* other-side entity,
// positioned evenly around the circle. If the same target appears
// in multiple relations (e.g. 张三 → 灵犀 是, 张三 → 灵犀 创始人),
// the edges merge visually but each relation keeps its own label.
//
// Scale notes: viewBox = 400×400 (was 320). Everything else
// scaled ~30% so the graph uses more of the sidebar width and
// the nodes/labels read larger on screen. Geometry constants are
// pulled out so the SVG label `<text>` placement and the JS
// geometry stay in sync.
const radialLayout = computed(() => {
  const VB = 400
  const cx = VB / 2
  const cy = VB / 2
  const ringR = 135
  const centerR = 36
  const outerR = 26

  const rels = centerRelations.value
  const centerName = selectedEntityName.value
  // Preserve relation order from the source data so labels render
  // in a stable order (important when there are duplicate targets).
  const outerNodes = []
  const seen = new Set()
  for (const r of rels) {
    const target = otherSide(centerName, r)
    if (!target || seen.has(target)) continue
    seen.add(target)
    outerNodes.push({
      name: target,
      color: nodeColor(target),
    })
  }

  const n = outerNodes.length
  // Start at -π/2 (12 o'clock) and go clockwise so the layout reads
  // left-to-right naturally. With 1 node, place it at the top; with
  // 0, leave positions empty.
  outerNodes.forEach((node, i) => {
    if (n === 1) {
      node.x = cx
      node.y = cy - ringR
    } else {
      const theta = -Math.PI / 2 + (i * 2 * Math.PI) / n
      node.x = cx + ringR * Math.cos(theta)
      node.y = cy + ringR * Math.sin(theta)
    }
    // Outward direction (center → node), used to place the label
    // beyond the node and rotate edge-label text.
    node.angle = Math.atan2(node.y - cy, node.x - cx)
  })

  const edges = rels.map((r) => {
    const target = otherSide(centerName, r)
    const node = outerNodes.find((n) => n.name === target)
    if (!node) return null
    const incoming = r.to === centerName
    // For outgoing: line goes center → outer. For incoming: line
    // goes outer → center so the marker-end arrowhead (always at
    // x2,y2) points AT `r.to`. Either way, we shorten both ends
    // so the line doesn't disappear under the node circles.
    const srcX = incoming ? node.x : cx
    const srcY = incoming ? node.y : cy
    const dstX = incoming ? cx : node.x
    const dstY = incoming ? cy : node.y
    const srcR = incoming ? outerR : centerR
    const dstR = incoming ? centerR : outerR
    const dx = dstX - srcX
    const dy = dstY - srcY
    const len = Math.hypot(dx, dy) || 1
    const x1 = srcX + (dx / len) * srcR
    const y1 = srcY + (dy / len) * srcR
    const x2 = dstX - (dx / len) * dstR
    const y2 = dstY - (dy / len) * dstR
    // Midpoint = label position. Text rotates with the edge so it
    // reads outward; we flip if the edge points "upward" so text
    // never appears upside-down.
    const midX = (x1 + x2) / 2
    const midY = (y1 + y2) / 2
    const angleDeg = (Math.atan2(y2 - y1, x2 - x1) * 180) / Math.PI
    const flipped = angleDeg > 90 || angleDeg < -90
    return {
      x1,
      y1,
      x2,
      y2,
      midX,
      midY,
      label: r.relationType || '',
      rotation: flipped ? angleDeg + 180 : angleDeg,
      incoming,
    }
  }).filter(Boolean)

  return {
    viewBox: `0 0 ${VB} ${VB}`,
    center: { x: cx, y: cy, r: centerR },
    outerR,
    outerNodes,
    edges,
  }
})

// Stable, name-driven color assignment for outer nodes. The hash
// picks from a 6-color palette (defined in style.css as
// --node-color-N), so the same entity always renders the same hue
// across reloads / switches — even after the user re-centers on a
// different entity. djb2 is good enough for "spread values evenly".
function nodeColor(name) {
  if (!name) return 'var(--node-color-1)'
  let hash = 5381
  for (let i = 0; i < name.length; i++) {
    hash = ((hash << 5) + hash + name.charCodeAt(i)) | 0
  }
  const idx = (Math.abs(hash) % 6) + 1
  return `var(--node-color-${idx})`
}

// ----- Long-term section drag-to-reorder -----
// The 短期记忆 section is anchored at the top (it's the live context
// window — moving it would be confusing). The two long-term sections
// (笔记 / 图谱) can be reordered freely: drag a section header onto
// another to swap. The order persists across reloads via localStorage.
//
// Drag mechanics (native HTML5, no library):
//   - Header element gets `draggable="true"` + `cursor: grab`
//   - dragstart captures which key is being dragged + sets effectAllowed
//   - dragover on a target marks `dragOverKey` so the template renders
//     a blue insertion line on the appropriate side
//   - drop reorders the array; dragend clears state regardless of outcome
const SECTION_ORDER_KEY = 'mini-agent-web:long-term-section-order'
const VALID_SECTIONS = ['notes', 'graph']
const sectionOrder = ref(['notes', 'graph'])
const draggingSection = ref(null)
// Which section header is currently the drop target (highlighted).
// `null` when no drag is in progress.
const dragOverKey = ref(null)

function loadSectionOrder() {
  try {
    const raw = localStorage.getItem(SECTION_ORDER_KEY)
    if (!raw) return
    const parsed = JSON.parse(raw)
    // Defensive: ignore anything malformed (wrong shape, unknown keys,
    // duplicates) and fall back to the default. Avoids a single bad
    // localStorage entry permanently breaking the panel layout.
    if (
      Array.isArray(parsed) &&
      parsed.length === VALID_SECTIONS.length &&
      new Set(parsed).size === VALID_SECTIONS.length &&
      parsed.every((s) => VALID_SECTIONS.includes(s))
    ) {
      sectionOrder.value = parsed
    }
  } catch {
    // localStorage disabled or value corrupt — fall back to default.
  }
}

function saveSectionOrder() {
  try {
    localStorage.setItem(
      SECTION_ORDER_KEY,
      JSON.stringify(sectionOrder.value),
    )
  } catch {
    // ignore
  }
}

function onDragStart(e, key) {
  draggingSection.value = key
  // Firefox refuses to start the drag without data on the transfer.
  // Use a plain string payload; we don't actually read it on drop.
  e.dataTransfer.effectAllowed = 'move'
  e.dataTransfer.setData('text/plain', key)
}

function onDragOver(e, key) {
  // preventDefault is REQUIRED to allow the subsequent `drop` event.
  e.preventDefault()
  e.dataTransfer.dropEffect = 'move'
  if (dragOverKey.value !== key) {
    dragOverKey.value = key
  }
}

function onDragLeave() {
  // Clear highlight on dragleave so the indicator doesn't get stuck
  // if the cursor exits the target without dropping.
  dragOverKey.value = null
}

function onDrop(e, targetKey) {
  e.preventDefault()
  const fromKey = draggingSection.value
  draggingSection.value = null
  dragOverKey.value = null
  if (!fromKey || fromKey === targetKey) return
  const fromIdx = sectionOrder.value.indexOf(fromKey)
  const toIdx = sectionOrder.value.indexOf(targetKey)
  if (fromIdx === -1 || toIdx === -1) return
  // Move `fromKey` to `targetKey`'s position. We splice + insert
  // rather than swap so "drop on top of B" puts A where B was,
  // regardless of which was higher originally.
  const next = sectionOrder.value.slice()
  next.splice(fromIdx, 1)
  next.splice(toIdx, 0, fromKey)
  sectionOrder.value = next
  saveSectionOrder()
}

function onDragEnd() {
  // Fires whether or not the drop succeeded — always reset state.
  draggingSection.value = null
  dragOverKey.value = null
}

const ROLE_ICON = {
  user: '👤',
  assistant: '🤖',
  tool: '🔧',
}

// ----- Data load -----
async function loadMemory() {
  loading.value = true
  error.value = ''
  try {
    const [short_, long_, graph_] = await Promise.all([
      api.memoryShort(),
      api.memoryLong(),
      api.memoryGraph().catch(() => ({ entities: [], relations: [] })),
    ])
    shortMessages.value = short_.messages || []
    longNotes.value = long_.notes || []
    graphEntities.value = graph_.entities || []
    graphRelations.value = graph_.relations || []
  } catch (err) {
    error.value = err.message || '加载失败'
  } finally {
    loading.value = false
  }
}

defineExpose({ refresh: loadMemory })
onMounted(() => {
  // Restore collapse preference first so the first paint matches.
  try {
    const v = localStorage.getItem(LONG_TERM_COLLAPSED_KEY)
    if (v === '1') longTermCollapsed.value = true
  } catch {
    // ignore
  }
  try {
    const g = localStorage.getItem(GRAPH_COLLAPSED_KEY)
    if (g === '1') graphCollapsed.value = true
  } catch {
    // ignore
  }
  // Restore drag-reorder preference (which long-term section comes first).
  loadSectionOrder()
  loadMemory()
})
watch(() => props.refreshTrigger, loadMemory)

// ----- Long-term collapse state -----
// Persisted in localStorage so the user's preference survives reloads.
// When collapsed, the long-term section shrinks to just its header
// (chevron + title + count) and the short-term section above it
// grows to fill the rest of the panel.
const LONG_TERM_COLLAPSED_KEY = 'mini-agent-web:long-term-collapsed'
const longTermCollapsed = ref(false)

function toggleLongTermCollapsed() {
  longTermCollapsed.value = !longTermCollapsed.value
  try {
    localStorage.setItem(
      LONG_TERM_COLLAPSED_KEY,
      longTermCollapsed.value ? '1' : '0',
    )
  } catch {
    // localStorage may be disabled; collapse state just won't persist.
  }
}

// ----- Graph (MCP memory) collapse state -----
// Independent of the notes section so users can fold either side
// without affecting the other. Same localStorage pattern.
const GRAPH_COLLAPSED_KEY = 'mini-agent-web:long-term-graph-collapsed'
const graphCollapsed = ref(false)
// Template ref for the <section> element so we can scroll it into
// view after toggling. Lets the user see the section anchor at the
// bottom of the layout when collapsed (and re-anchor at its new
// expanded position when re-opened). `null` until the component mounts.
const graphSectionEl = ref(null)

function toggleGraphCollapsed() {
  graphCollapsed.value = !graphCollapsed.value
  try {
    localStorage.setItem(
      GRAPH_COLLAPSED_KEY,
      graphCollapsed.value ? '1' : '0',
    )
  } catch {
    // ignore
  }
  // Scroll the section into view at the bottom of the layout after
  // toggling. `nextTick` waits for the v-show / collapsed class to
  // commit (so we measure the post-toggle size, not the pre-toggle
  // one). `block: 'end'` aligns the section's bottom with the bottom
  // of the nearest scrollable ancestor — useful when the user has
  // scrolled the page mid-conversation and wants the freshly-toggled
  // section to land back in view.
  nextTick(() => {
    const el = graphSectionEl.value
    if (!el) return
    el.scrollIntoView({ block: 'end', behavior: 'smooth' })
  })
  // Bubble up to App.vue so it can pin the chat to its bottom —
  // collapsing a section usually means the user wants to land on the
  // latest message rather than wherever the scroll position happened
  // to be. We emit unconditionally (both collapse and expand) so the
  // same "pin to bottom" affordance fires whenever the graph section
  // is toggled; the parent decides whether to act.
  emit('graph-collapsed')
}
</script>

<template>
  <aside
    class="memory-panel"
    :class="{ 'memory-panel-collapsed': collapsed }"
  >
    <!-- Collapsed: a single full-strip button. The whole 32px column
         is the click target — no need to aim for a small arrow. The
         shortMessages / longNotes refs are kept alive in setup so
         expanding is instant (no refetch, just re-render). -->
    <button
      v-if="collapsed"
      class="memory-collapsed-trigger"
      @click="toggleCollapsed"
      title="展开记忆栏"
      aria-label="展开记忆栏"
    >
      <span class="memory-collapsed-icon" aria-hidden="true">▶</span>
      <!-- Vertical "记忆" label gives a visual hint about what the
           strip is. Rotated with `writing-mode` so it reads bottom-up
           without taking horizontal space. -->
      <span class="memory-collapsed-label" aria-hidden="true">Memory</span>
    </button>

    <!-- Expanded: short-term + long-term sections stacked -->
    <template v-else>
      <!-- 短期记忆 (Short-term) — agent's working context -->
      <section class="memory-section short-term">
        <header class="memory-section-header">
          <span class="memory-section-title">
            📖 短期记忆
            <span
              class="memory-info-icon"
              tabindex="0"
              aria-label="本项目中，短期记忆和上下文本质相同，只存在于进程内存中"
              @mouseenter="showInfo('本项目中，短期记忆和上下文本质相同，只存在于进程内存中', $event)"
              @mouseleave="hideInfo"
              @focus="showInfo('本项目中，短期记忆和上下文本质相同，只存在于进程内存中', $event)"
              @blur="hideInfo"
            >
              <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                <circle cx="6" cy="6" r="5.25" fill="none" stroke="currentColor" stroke-width="1" />
                <circle cx="6" cy="3.6" r="0.7" fill="currentColor" />
                <rect x="5.5" y="5.3" width="1" height="3.4" rx="0.4" fill="currentColor" />
              </svg>
            </span>
          </span>
          <div class="memory-section-actions">
            <span class="memory-section-count" :title="`${shortMessages.length} 条消息`">
              {{ shortMessages.length }}
            </span>
            <!-- Top-level collapse: shrinks the whole panel to a thin
                 strip. Lives in the first section's header so it's
                 visible without scrolling, and pairs with the
                 long-term-only chevron below. -->
            <button
              class="memory-collapse-btn"
              aria-label="收起记忆栏"
              title="收起记忆栏"
              @click="toggleCollapsed"
            >◀</button>
          </div>
        </header>

        <div v-if="error" class="memory-empty error">⚠️ {{ error }}</div>
        <div v-else-if="shortMessages.length === 0" class="memory-empty">
          暂无对话
        </div>
        <ul v-else class="memory-list">
          <li
            v-for="(msg, i) in shortMessages"
            :key="i"
            class="memory-item"
            :class="`role-${msg.role}`"
          >
            <span class="memory-role" :title="msg.role">{{ ROLE_ICON[msg.role] || '·' }}</span>
            <div class="memory-body">
              <!--
                Block order per message type:
                  assistant (with tool_calls):
                    1. thinking      (LLM rationale before acting)
                    2. tool_use chips (what the LLM emitted)
                    3. text preview  (often empty when only emitting tool calls)
                  tool result:
                    1. thinking      (rare on tool results — renders as a small block if present)
                    2. tool_result label
                    3. text preview  (the actual tool output)
                  user / plain assistant: just text preview.
              -->
              <details v-if="msg.thinking" class="memory-thinking">
                <summary class="memory-thinking-summary">💭 thinking</summary>
                <pre class="memory-thinking-text">{{ msg.thinking }}</pre>
              </details>
              <div v-if="msg.tool_calls && msg.tool_calls.length" class="memory-tool-calls">
                <div
                  v-for="(tc, j) in msg.tool_calls"
                  :key="j"
                  class="memory-tool-call"
                >
                  <button
                    type="button"
                    class="memory-tool-tag"
                    :class="{ 'memory-tool-tag-open': isToolCallExpanded(i, j) }"
                    :aria-expanded="isToolCallExpanded(i, j)"
                    :aria-label="`${tc.name} 参数`"
                    :title="`查看 ${tc.name} 工具的调用参数`"
                    @click="toggleToolCall(i, j)"
                  >
                    {{ tc.name }}
                  </button>
                  <pre
                    v-if="formatArgs(tc.arguments) && isToolCallExpanded(i, j)"
                    class="memory-tool-args-block"
                  >{{ formatArgs(tc.arguments) }}</pre>
                </div>
              </div>
              <div v-if="msg.name" class="memory-tool-name">tool_result: {{ msg.name }}</div>
              <div class="memory-text" :title="msg.content">{{ preview(msg.content) }}</div>
            </div>
          </li>
        </ul>
      </section>

      <!-- 长期记忆(笔记) — linear notes from .agent_memory.json.
           Header has a chevron that toggles `longTermCollapsed`; when
           collapsed the body (list / empty placeholder) is hidden and
           the section shrinks to just its header. The short-term
           section above automatically fills the freed space. -->
      <section
        :class="['memory-section', 'long-term', { collapsed: longTermCollapsed }]"
      >
        <header
          class="memory-section-header draggable"
          :class="{ 'is-dragging': draggingSection === 'notes', 'drop-target': dragOverKey === 'notes' }"
          draggable="true"
          @dragstart="onDragStart($event, 'notes')"
          @dragover="onDragOver($event, 'notes')"
          @dragleave="onDragLeave"
          @drop="onDrop($event, 'notes')"
          @dragend="onDragEnd"
          @click="toggleLongTermCollapsed"
        >
          <span class="memory-section-title">
            📌 长期记忆(笔记)
            <span
              class="memory-info-icon"
              tabindex="0"
              aria-label="长期记忆(笔记) — 通过内置 tool 读写,适合记录零碎笔记"
              @mouseenter="showInfo('通过内置 tool 读写,适合记录零碎笔记', $event)"
              @mouseleave="hideInfo"
              @focus="showInfo('通过内置 tool 读写,适合记录零碎笔记', $event)"
              @blur="hideInfo"
              @click.stop
            >
              <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                <circle cx="6" cy="6" r="5.25" fill="none" stroke="currentColor" stroke-width="1" />
                <circle cx="6" cy="3.6" r="0.7" fill="currentColor" />
                <rect x="5.5" y="5.3" width="1" height="3.4" rx="0.4" fill="currentColor" />
              </svg>
            </span>
          </span>
          <div class="memory-section-actions" @click.stop>
            <span class="memory-section-count" :title="`${longNotes.length} 条笔记`">
              {{ longNotes.length }}
            </span>
            <button
              v-if="typeof onViewRawFile === 'function'"
              class="memory-view-btn"
              aria-label="查看 .agent_memory.json 原文件"
              title="查看原文件"
              @click="onViewRawFile('.agent_memory.json')"
            >📄</button>
          </div>
        </header>

        <template v-if="!longTermCollapsed">
          <div v-if="longNotes.length === 0" class="memory-empty">
            暂无笔记
          </div>
          <ul v-else class="memory-list">
            <li v-for="(note, i) in longNotes" :key="i" class="memory-item long-note">
              <div class="note-meta">
                <!-- Each known category gets its own hue via the
                     `.cat-<slug>` modifier class (defined in style.css).
                     Unknown categories fall through to the base style,
                     which uses --accent (green) — fine as a generic
                     default; users will start seeing distinct colors
                     the moment the agent uses one of the documented
                     names. -->
                <span :class="['note-category', `cat-${categorySlug(note.category)}`]">
                  {{ note.category || 'general' }}
                </span>
                <span class="note-time">{{ relTime(note.timestamp) }}</span>
              </div>
              <div class="note-content">{{ note.content }}</div>
            </li>
          </ul>
        </template>
      </section>

      <!-- 长期记忆(图谱) — knowledge graph from the memory MCP server
           (workspace/.mcp_memory.jsonl). Independent of the notes
           section above: different storage, different tool surface
           (create_entities / search_nodes / read_graph via MCP), best
           for modeling "who is connected to what" rather than flat facts.
           Independent collapse state so users can hide either side
           without affecting the other. -->
      <section
        ref="graphSectionEl"
        :class="['memory-section', 'long-term-graph', { collapsed: graphCollapsed }]"
      >
        <header
          class="memory-section-header draggable"
          :class="{ 'is-dragging': draggingSection === 'graph', 'drop-target': dragOverKey === 'graph' }"
          draggable="true"
          @dragstart="onDragStart($event, 'graph')"
          @dragover="onDragOver($event, 'graph')"
          @dragleave="onDragLeave"
          @drop="onDrop($event, 'graph')"
          @dragend="onDragEnd"
          @click="toggleGraphCollapsed"
        >
          <span class="memory-section-title">
            🕸️ 长期记忆(图谱)
            <span
              class="memory-info-icon"
              tabindex="0"
              aria-label="长期记忆(图谱) — 通过 MCP 接入外部知识图谱服务,适合建立实体关系"
              @mouseenter="showInfo('通过 MCP 接入外部知识图谱服务,适合建立实体关系', $event)"
              @mouseleave="hideInfo"
              @focus="showInfo('通过 MCP 接入外部知识图谱服务,适合建立实体关系', $event)"
              @blur="hideInfo"
              @click.stop
            >
              <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
                <circle cx="6" cy="6" r="5.25" fill="none" stroke="currentColor" stroke-width="1" />
                <circle cx="6" cy="3.6" r="0.7" fill="currentColor" />
                <rect x="5.5" y="5.3" width="1" height="3.4" rx="0.4" fill="currentColor" />
              </svg>
            </span>
          </span>
          <div class="memory-section-actions" @click.stop>
            <span class="memory-section-count" :title="`${graphEntities.length} 个实体 / ${graphRelations.length} 条关系`">
              {{ graphEntities.length }}<span class="count-sep">｜</span>{{ graphRelations.length }}
            </span>
            <!-- Same "view raw file" affordance as the notes section
                 above: opens the existing FilePreviewModal pointing at
                 `.mcp_memory.jsonl`. The path may not exist yet
                 (MCP never invoked); the API will 404 and the modal
                 shows the error gracefully. -->
            <button
              v-if="typeof onViewRawFile === 'function'"
              class="memory-view-btn"
              aria-label="查看 .mcp_memory.jsonl 原文件"
              title="查看原文件"
              @click="onViewRawFile('.mcp_memory.jsonl')"
            >📄</button>
          </div>
        </header>

        <div v-if="!graphCollapsed" class="graph-body">
          <div v-if="graphEntities.length === 0 && graphRelations.length === 0" class="memory-empty">
            暂无图谱节点 — 需要在 mcp.json 配置 memory MCP 并由 Agent 写入
          </div>
          <div v-else class="radial-graph-wrap">
            <!-- Entity picker as a horizontally-scrolling chip row,
                 sitting just under the section header. Click a chip
                 to re-center the radial on that entity. The current
                 center is filled with the accent color so the user
                 always knows what they're looking at. -->
            <div
              v-if="entitiesWithRelations.length > 0"
              class="radial-picker"
              role="tablist"
              aria-label="切换中心实体"
            >
              <button
                v-for="name in entitiesWithRelations"
                :key="name"
                :class="['radial-pill', { active: name === selectedEntityName }]"
                :title="name"
                role="tab"
                :aria-selected="name === selectedEntityName"
                @click="selectEntity(name)"
              >
                <span
                  class="radial-pill-dot"
                  :style="{ background: nodeColor(name) }"
                  aria-hidden="true"
                ></span>
                <span class="radial-pill-name">{{ name }}</span>
              </button>
            </div>

            <!-- Canvas wrapper for the SVG + the floating fullscreen icon.
                 `position: relative` on this wrapper makes it the
                 containing block for the icon's `position: absolute`,
                 so the icon's `top/left` resolve against the SVG's
                 box (not the wrap that includes the pills above).
                 Without this wrapper, the icon would either overlap
                 the pills or fly out to a higher ancestor. The
                 `v-if` flips between the populated canvas and the
                 empty-state hint below — they are adjacent siblings,
                 so `v-else` resolves correctly. -->
            <div v-if="centerRelations.length > 0" class="radial-graph-canvas">
              <button
                class="radial-fullscreen-btn"
                aria-label="放大预览图谱"
                title="放大预览"
                @click="previewOpen = true"
              >
                <svg
                  viewBox="0 0 16 16"
                  width="13"
                  height="13"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="1.6"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  aria-hidden="true"
                >
                  <path d="M2 5 L2 2 L5 2" />
                  <path d="M11 2 L14 2 L14 5" />
                  <path d="M2 11 L2 14 L5 14" />
                  <path d="M11 14 L14 14 L14 11" />
                </svg>
              </button>

              <!-- Radial SVG (extracted into RadialGraphSvg.vue so the
                   same render is reused in the preview modal below). -->
              <RadialGraphSvg
                :layout="radialLayout"
                :selected-entity-name="selectedEntityName"
                @select-entity="selectEntity"
                @center-hover-enter="showCenterTooltip"
                @center-hover-leave="hideCenterTooltip"
              />
            </div>

            <!-- Empty radial: selected entity has no relations.
                 Show a hint so the user knows the data exists but
                 this particular entity is isolated. -->
            <div v-else class="memory-empty radial-empty">
              <span class="radial-empty-name">{{ selectedEntityName }}</span>
              <span>暂无关系</span>
            </div>
          </div>
        </div>
      </section>
    </template>

    <!-- Floating info tooltip. Teleported to <body> so it escapes the
         memory panel's `overflow: hidden` and any z-index stacking
         context that would otherwise clip or hide it behind the chat
         panel. Position is set from `infoTooltip` (x = icon right edge
         + 8px, y = icon vertical center) on icon mouseenter/focus. -->
    <Teleport to="body">
      <div
        v-if="infoTooltip.show"
        class="memory-info-tooltip-floating"
        role="tooltip"
        :style="{
          left: infoTooltip.x + 'px',
          top: infoTooltip.y + 'px',
        }"
      >{{ infoTooltip.text }}</div>
    </Teleport>

    <!-- Center-node observation tooltip. Same teleport strategy as
         the info tooltip above, but with structured content: header
         (entity name + type chip) + bulleted observation list. -->
    <Teleport to="body">
      <div
        v-if="centerTooltip.show"
        class="center-tooltip-floating"
        role="tooltip"
        :style="{
          left: centerTooltip.x + 'px',
          top: centerTooltip.y + 'px',
        }"
      >
        <div class="center-tooltip-header">
          <span class="center-tooltip-name">{{ centerTooltip.name }}</span>
          <span v-if="centerTooltip.entityType" class="center-tooltip-type">
            {{ centerTooltip.entityType }}
          </span>
        </div>
        <ul class="center-tooltip-list">
          <li
            v-for="(obs, i) in centerTooltip.observations"
            :key="`obs-${i}`"
          >{{ obs }}</li>
        </ul>
      </div>
    </Teleport>

    <!-- Fullscreen preview modal. Teleported to body so it sits
         above all other overlays regardless of the memory panel's
         stacking context. The backdrop catches click-to-close; the
         card itself stops propagation so clicks inside the modal
         don't bubble to the backdrop. Escape key also closes
         (handled by the `keydown` listener registered when
         `previewOpen` flips true). -->
    <Teleport to="body">
      <div
        v-if="previewOpen"
        class="graph-preview-backdrop"
        role="dialog"
        aria-modal="true"
        aria-label="图谱放大预览"
        @click.self="closePreview"
      >
        <div class="graph-preview-card" @click.stop>
          <header class="graph-preview-header">
            <span class="graph-preview-title">🕸️ 长期记忆(图谱) · 放大预览</span>
            <button
              class="graph-preview-close"
              aria-label="关闭预览"
              title="关闭 (Esc)"
              @click="closePreview"
            >
              <svg
                viewBox="0 0 16 16"
                width="14"
                height="14"
                fill="none"
                stroke="currentColor"
                stroke-width="1.6"
                stroke-linecap="round"
                aria-hidden="true"
              >
                <path d="M3 3 L13 13" />
                <path d="M13 3 L3 13" />
              </svg>
            </button>
          </header>
          <div class="graph-preview-body">
            <!-- Pills row inside the modal too — lets the user
                 switch center entity from the preview without
                 closing it first. -->
            <div
              v-if="entitiesWithRelations.length > 0"
              class="radial-picker"
              role="tablist"
              aria-label="切换中心实体"
            >
              <button
                v-for="name in entitiesWithRelations"
                :key="name"
                :class="['radial-pill', { active: name === selectedEntityName }]"
                :title="name"
                role="tab"
                :aria-selected="name === selectedEntityName"
                @click="selectEntity(name)"
              >
                <span
                  class="radial-pill-dot"
                  :style="{ background: nodeColor(name) }"
                  aria-hidden="true"
                ></span>
                <span class="radial-pill-name">{{ name }}</span>
              </button>
            </div>
            <RadialGraphSvg
              v-if="centerRelations.length > 0"
              class="graph-preview-svg"
              :layout="radialLayout"
              :selected-entity-name="selectedEntityName"
              @select-entity="selectEntity"
              @center-hover-enter="showCenterTooltip"
              @center-hover-leave="hideCenterTooltip"
            />
          </div>
        </div>
      </div>
    </Teleport>
  </aside>
</template>
