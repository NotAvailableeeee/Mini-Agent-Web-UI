<script setup>
import { computed } from 'vue'
import ToolArgs from './ToolArgs.vue'

const props = defineProps({
  toolCall: { type: Object, required: true },
})

// Tool-specific icon (used inside the body, next to the tool name).
// The summary row always uses 🔧 — see the template.
const TOOL_ICONS = {
  // 工作区
  bash: '💻',
  read_file: '📄',
  write_file: '📝',
  edit_file: '✏️',
  record_note: '📌',
  recall_notes: '📖',
  // 基础 / bash 家族
  bash_output: '📤',
  bash_kill: '⏹️',
  // 联网
  search: '🔍',
  browse: '🌐',
  // 知识图谱（memory）
  create_entities: '🧩',
  create_relations: '🔗',
  add_observations: '🗒️',
  delete_entities: '🗑️',
  delete_observations: '❌',
  delete_relations: '✂️',
  read_graph: '🕸️',
  search_nodes: '🔎',
  open_nodes: '📂',
  // Skills
  get_skill: '📚',
}

const toolIcon = computed(() => {
  const n = (props.toolCall?.name || '').toLowerCase()
  return TOOL_ICONS[n] || '🔧'
})

// Always render when a toolCall is provided, even if its arguments
// object is empty — a tool with no args (e.g. `recall_notes()`,
// `read_graph()`) is still a real action worth showing. <ToolArgs>
// renders nothing when args is empty; the surrounding action card
// (tool chip + summary) already communicates what the call was.
const hasArgs = computed(() => props.toolCall != null)

const open = defineModel('open', { type: Boolean, default: false })
</script>

<template>
  <details v-if="hasArgs" class="flat-action" :open="open"
           @toggle="open = $event.target.open">
    <summary class="flat-summary">
      <span class="flat-icon" aria-hidden="true">🔧</span>
      <span class="flat-label">Action</span>
    </summary>
    <div class="flat-block-body">
      <!-- Tool identity chip — sits at the top of the body so the user
           sees "which tool was it" once they expand the block. The
           summary above stays generic (🔧 Action) to keep the visual
           rhythm consistent across action blocks. -->
      <div class="action-tool-chip">
        <span class="action-tool-chip-icon" aria-hidden="true">{{ toolIcon }}</span>
        <span class="action-tool-chip-name">{{ toolCall.name || 'tool' }}</span>
      </div>
      <ToolArgs :args="toolCall.arguments" />
    </div>
  </details>
</template>