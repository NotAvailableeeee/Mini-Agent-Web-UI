<script setup>
/**
 * 紧凑能力 pill 条 —— 紧贴 ChatPanel 输入框上方。
 *
 * 形态由"横向滚动卡片" → "icon + 标题 pill,自动换行 (2 行 3 列)";
 * 顶部 1 行 header(🚀 试试这些能力 + 折叠 ▲/▼),折叠态只留 header。
 *
 * 位置从 App.vue 的 layout-center-rail (中间列顶部, 独立一段) 迁移到这里:
 *   - 不再依赖 App 中转 presetMessage props
 *   - 直接 emit('select', card) 给父组件 (ChatPanel)
 *
 * 折叠状态持久化到 localStorage, 避免每次 reload 都得手动收起。
 */
import { onMounted, ref } from 'vue'
import { api } from '../api/client.js'

const emit = defineEmits(['select'])

const cards = ref([])
const loading = ref(true)
const error = ref('')

// 折叠状态 —— 默认展开, 用户收起后 localStorage 记住
const COLLAPSED_KEY = 'mini-agent-web:showcase-collapsed'
const collapsed = ref(false)

function toggleCollapsed() {
  collapsed.value = !collapsed.value
  try {
    localStorage.setItem(COLLAPSED_KEY, collapsed.value ? '1' : '0')
  } catch {
    // localStorage 不可用则不持久化
  }
}

function handlePick(card) {
  // 折叠收起: 用户点过之后, 体验上不需要再展开
  if (!collapsed.value) {
    collapsed.value = true
    try {
      localStorage.setItem(COLLAPSED_KEY, '1')
    } catch {
      // ignore
    }
  }
  emit('select', card)
}

onMounted(async () => {
  try {
    const v = localStorage.getItem(COLLAPSED_KEY)
    if (v === '1') collapsed.value = true
  } catch {
    // ignore
  }
  try {
    cards.value = await api.listCards()
  } catch (err) {
    error.value = err.message
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <section class="showcase" :class="{ 'showcase-collapsed': collapsed }">
    <button
      type="button"
      class="showcase-header"
      :aria-expanded="!collapsed"
      @click="toggleCollapsed"
    >
      <span class="showcase-icon" aria-hidden="true">🚀</span>
      <span class="showcase-title">试试这些能力</span>
      <span class="showcase-sub" v-if="!collapsed">点击直接发起，或在下方输入你自己的任务</span>
      <span class="showcase-chevron" aria-hidden="true">{{ collapsed ? '▲' : '▼' }}</span>
    </button>

    <div v-if="!collapsed" class="showcase-body">
      <div v-if="loading" class="showcase-status">加载中…</div>
      <div v-else-if="error" class="showcase-status error">⚠️ {{ error }}</div>

      <div v-else class="showcase-pills">
        <button
          v-for="card in cards"
          :key="card.id"
          type="button"
          class="showcase-pill"
          :title="card.prompt"
          @click="handlePick(card)"
        >
          <span class="showcase-pill-icon" aria-hidden="true">{{ card.icon }}</span>
          <span class="showcase-pill-title">{{ card.title }}</span>
        </button>
      </div>
    </div>
  </section>
</template>

<style scoped>
.showcase {
  flex: 0 0 auto;
  border-top: 1px solid var(--border);
  background: var(--panel);
  font-family: var(--font-sans);
}

/* Header —— 整行可点击, 收起 / 展开 */
.showcase-header {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 14px;
  background: transparent;
  border: none;
  border-radius: 0;
  color: var(--text);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s;
}
.showcase-header:hover {
  background: var(--panel-2);
}
.showcase-icon {
  font-size: 13px;
  flex-shrink: 0;
}
.showcase-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--text);
  flex-shrink: 0;
}
.showcase-sub {
  font-size: 11px;
  font-weight: 400;
  color: var(--text-dim);
  margin-left: 4px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}
.showcase-chevron {
  font-size: 9px;
  color: var(--text-dim);
  flex-shrink: 0;
  margin-left: auto;
  padding: 2px 4px;
  user-select: none;
}

/* Body —— 展开后才出现,放 pill 列表 */
.showcase-body {
  padding: 4px 12px 10px;
}

.showcase-status {
  font-size: 11px;
  color: var(--text-dim);
  padding: 6px 4px;
}
.showcase-status.error {
  color: var(--error);
}

/* Pill grid —— 2 ~ 3 列自动 wrap, 每列等宽,所有 pill 高度一致 */
.showcase-pills {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 6px;
}

.showcase-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 10px;
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 14px;
  color: var(--text);
  font-size: 12px;
  font-family: var(--font-sans);
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s, border-color 0.15s, transform 0.1s;
  text-align: left;
  min-width: 0;
  max-width: 100%;
  overflow: hidden;
  white-space: nowrap;
}

.showcase-pill:hover {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--text);
}

.showcase-pill:active {
  transform: scale(0.98);
}

.showcase-pill-icon {
  font-size: 13px;
  line-height: 1;
  flex-shrink: 0;
}

.showcase-pill-title {
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}
</style>
