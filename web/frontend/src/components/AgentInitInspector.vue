<script setup>
// 5 步 Agent 初始化进度条。
//
// 视觉结构：
//   1. 外层单按钮 "Agent 初始化…" + 进度摘要（默认展开）
//   2. 展开后,5 个步骤并排成 5 个槽,每个槽有 3 种状态：
//        - done     ✓ + accent 边框 + 可点击展开详情
//        - loading  pulse-dot + accent dashed 边框 + 不可点
//        - pending  ○ + dim 灰 + 不可点
//   3. 已完成状态下,点击某个槽展开该步骤的内容（与之前完全一致）
//   4. 全部 5 步完成 + 用户没展开任何 tab → 1.2s 后自动平滑收起
//      （如果用户展开了某个 tab 想看详情,就不自动收起,等用户手动）
//   5. 正在 loading 时,如果没展开任何 tab,显示"正在 XXX…"提示
//
// 本地化策略：
//   - 所有说明性文字 → 中文
//   - 代码（路径、函数名、参数值、源码片段） → 保留英文

import { computed, ref, watch } from 'vue'
import MarkdownView from './MarkdownView.vue'

const props = defineProps({
  steps:   { type: Array,  default: () => [] },
  loading: { type: Boolean, default: false },
  error:   { type: String, default: '' },
})

// 外层按钮的展开状态（默认展开,初始化完成后会自动收起）
const expanded = ref(true)
function toggleOuter() { expanded.value = !expanded.value }

// 5 个步骤的硬编码标题（与后端 _ensure_agent 步骤顺序一致）
const STEP_TITLES = [
  { n: 1, title: 'Load config',                                   zh: '加载配置' },
  { n: 2, title: 'Build LLM client',                              zh: '构造 LLM' },
  { n: 3, title: 'Build tools',                                   zh: '构造 Tools' },
  { n: 4, title: 'Load system prompt + inject skills metadata',   zh: '注入skill元数据构造SP' },
  { n: 5, title: 'Construct Agent',                               zh: '构造 Agent' },
]

// 当前选中的步骤槽（只能选 done 的槽）
const activeStep = ref(null)
function toggleTab(n) {
  // done 状态以外的槽不允许展开
  const slot = stepSlots.value.find(s => s.n === n)
  if (!slot || slot.state !== 'done') return
  activeStep.value = activeStep.value === n ? null : n
}

// 计算每个槽的状态
const stepSlots = computed(() => {
  const done = new Set(props.steps.map(s => s.n))
  const total = STEP_TITLES.length
  const currentLoadingN = (props.loading && done.size < total) ? done.size + 1 : null
  return STEP_TITLES.map(s => ({
    n: s.n,
    title: s.zh,
    state: done.has(s.n) ? 'done' : (s.n === currentLoadingN ? 'loading' : 'pending'),
    data: props.steps.find(x => x.n === s.n) || null,
  }))
})

// 当前选中的步骤完整数据（来自 props.steps 中匹配 n 的那条）
const activeStepData = computed(() => {
  if (activeStep.value == null) return null
  return props.steps.find(s => s.n === activeStep.value) || null
})

// 当前正在 loading 的槽（保留备用,目前不再用于下方提示）
// const currentLoadingSlot = computed(() => {
//   return stepSlots.value.find(s => s.state === 'loading') || null
// })

// 是否全部完成
const allDone = computed(() => props.steps.length >= 5 && !props.loading)

// 全部完成 + 用户没展开任何 tab → 等 1.2s 让用户看到末态,然后平滑收起
watch(allDone, (isDone) => {
  if (isDone && activeStep.value == null) {
    setTimeout(() => { expanded.value = false }, 1200)
  }
})

// 源码行号范围（用于在 header 显示"文件:起始-结束",类似 IDE 的 goto-line）
const sourceLineRange = computed(() => {
  const call = activeStepData.value?.call
  if (!call?.source_code) return null
  const start = call.source_line_start
  const end = call.source_line_end || (start + call.source_code.split('\n').length - 1)
  if (!start) return null
  return start === end ? `${start}` : `${start}-${end}`
})

// Tools family 名翻译（保持"MCP"为英文术语）
const FAMILY_ZH = {
  'Base tools':      '基础 Tools',
  'Skills':          'Skills',
  'MCP':             'MCP',
  'Workspace tools': '工作区 Tools',
}
function familyZh(f) { return FAMILY_ZH[f] || f }

// header 摘要（中文）
const buttonSummary = computed(() => {
  if (!props.steps || !props.steps.length) return ''
  const parts = []
  // Loading 中显示 "X/5 步完成"
  if (props.loading) {
    parts.push(`${props.steps.length}/5 步完成`)
    return parts.join(' · ')
  }
  // 完成态：原有摘要逻辑
  const s3 = props.steps[2]
  const s3f = s3?.call?.return?.fields
  if (s3f?.tools_count != null) {
    parts.push(`${s3f.tools_count} 个 Tools`)
  }
  if (s3f?.skills_loaded != null) {
    parts.push(`${s3f.skills_loaded} 个 Skills`)
  }
  const s4 = props.steps[3]
  if (s4?.call?.return?.fields) {
    const sysChars = s4.call.return.fields.system_prompt_chars
    if (sysChars != null) parts.push(`系统提示 ${sysChars} 字符`)
  }
  return parts.join(' · ')
})

function totalTools(step) {
  if (!step?.families) return 0
  return step.families.reduce((acc, f) => acc + (f.items?.length || 0), 0)
}

// 把 system prompt 切分为 [前, 注入的元数据, 后] 三段,
// 中间那段(.kind === 'injected')前端会用 .injected-skills 高亮包起来。
const systemPromptParts = computed(() => {
  const sp = activeStepData.value?.system_prompt
  if (!sp) return []
  const meta = activeStepData.value?.skills_metadata
  if (!meta || !meta.trim()) return [{ kind: 'plain', text: sp }]
  const idx = sp.indexOf(meta)
  if (idx === -1) return [{ kind: 'plain', text: sp }]
  const parts = []
  if (idx > 0) parts.push({ kind: 'plain', text: sp.slice(0, idx) })
  parts.push({ kind: 'injected', text: meta })
  const tail = sp.slice(idx + meta.length)
  if (tail) parts.push({ kind: 'plain', text: tail })
  return parts
})

// 把 args/return 渲染成 JSON（GitHub 风格代码块的友好格式）
function formatJSON(obj) {
  if (obj == null) return 'null'
  if (typeof obj !== 'object') return String(obj)
  try {
    return JSON.stringify(obj, null, 2)
  } catch {
    return String(obj)
  }
}
</script>

<template>
  <!-- 出错：覆盖所有状态 -->
  <div v-if="error" class="init-button init-button-error">
    <span class="init-button-error-icon">⚠️</span>
    <span>Agent 初始化失败：{{ error }}</span>
  </div>

  <!-- 正常：始终渲染 5 槽 + header -->
  <div v-else class="init-button">
    <button
      class="init-button-header"
      :class="{ expanded }"
      @click="toggleOuter"
      :aria-expanded="expanded"
    >
      <span class="init-button-icon">{{ allDone ? '✓' : '⏳' }}</span>
      <span class="init-button-label">
        {{ loading ? '正在初始化 Agent…' : 'Agent 初始化已完成' }}
      </span>
      <span v-if="buttonSummary" class="init-button-summary">{{ buttonSummary }}</span>
      <span class="init-button-hint">{{ expanded ? '点击收起' : '点击展开详情' }}</span>
    </button>

    <Transition name="collapse">
      <div v-show="expanded" class="init-button-body">
        <!-- 5 个步骤的横排槽（自适应铺满宽度,3 态视觉） -->
        <div class="init-tabs" role="tablist">
          <template v-for="(slot, idx) in stepSlots" :key="slot.n">
            <button
              class="init-tab"
              :class="[`state-${slot.state}`, { active: activeStep === slot.n }]"
              :disabled="slot.state !== 'done'"
              :aria-selected="activeStep === slot.n"
              role="tab"
              @click="toggleTab(slot.n)"
            >
              <span class="init-tab-icon">
                <span v-if="slot.state === 'loading'" class="pulse-dot" aria-hidden="true"></span>
                <span v-else-if="slot.state === 'pending'">○</span>
                <span v-else><!-- done: 不画对勾,靠颜色+实线边框区分 --></span>
              </span>
              <span class="init-tab-title">{{ slot.title }}</span>
            </button>
            <span
              v-if="idx < stepSlots.length - 1"
              class="init-tab-arrow"
              aria-hidden="true"
            >→</span>
          </template>
        </div>

        <!-- 选中 done 步骤的内容 -->
        <div v-if="activeStepData" class="init-tab-content">
          <!-- 读取的文件 -->
          <div v-if="activeStepData.files_read && activeStepData.files_read.length" class="init-section">
            <h4>读取的文件</h4>
            <ul class="init-file-list">
              <li v-for="f in activeStepData.files_read" :key="f.path">
                <span class="init-file-path">{{ f.path }}</span>
                <span v-if="f.size != null" class="init-file-meta"> · {{ f.size }} 字节</span>
                <span v-else-if="f.kind === 'directory'" class="init-file-meta"> · 目录</span>
              </li>
            </ul>
          </div>

          <!-- 生成的文件 -->
          <div v-if="activeStepData.files_produced && activeStepData.files_produced.length" class="init-section">
            <h4>生成的文件</h4>
            <ul class="init-file-list">
              <li v-for="f in activeStepData.files_produced" :key="f.path">
                <span class="init-file-path">{{ f.path }}</span>
                <span v-if="!f.exists" class="init-file-meta"> · 懒加载</span>
                <span v-if="f.note" class="init-file-note"> — {{ f.note }}</span>
              </li>
            </ul>
          </div>

          <!-- Tools 列表（第 3 步） -->
          <div v-if="activeStepData.families && activeStepData.families.length" class="init-section">
            <h4>已加载 Tools（共 {{ totalTools(activeStepData) }} 个）</h4>
            <div v-for="fam in activeStepData.families" :key="fam.name" class="init-tool-family">
              <span class="init-tool-family-name">{{ familyZh(fam.name) }}</span>
              <ul class="tool-tag-list">
                <li
                  v-for="t in fam.items"
                  :key="t.name"
                  class="tool-tag"
                  :class="`tag-${t.kind}`"
                  :title="t.name"
                >
                  <span class="tool-tag-name">{{ t.name }}</span>
                </li>
              </ul>
            </div>
          </div>

          <!-- 调用详情：函数 / 真实源码 / 参数 / 返回值 -->
          <div v-if="activeStepData.call" class="init-section">
            <h4>调用详情</h4>

            <div class="init-call">
              <div class="init-call-header">
                <span class="init-call-function">{{ activeStepData.call.function }}()</span>
                <span class="init-call-module">{{ activeStepData.call.module }}</span>
              </div>

              <div v-if="activeStepData.call.source_code" class="init-call-body">
                <div class="init-call-section-title">
                  <span class="init-call-source-path">
                    {{ activeStepData.call.source_file
                    }}<span v-if="sourceLineRange" class="init-call-source-lineno">:{{ sourceLineRange }}</span>
                  </span>
                  <span class="init-call-source-label">运行源码</span>
                </div>
                <pre class="init-code-block init-code-source"><code class="lang-python">{{ activeStepData.call.source_code }}</code></pre>
              </div>

              <div class="init-call-body">
                <div class="init-call-section-title">调用参数</div>
                <pre class="init-code-block"><code class="lang-json">{{ formatJSON(activeStepData.call.args) }}</code></pre>
              </div>

              <div v-if="activeStepData.call.return" class="init-call-body">
                <div class="init-call-section-title">返回值</div>
                <pre class="init-code-block"><code class="lang-json">{{ formatJSON(activeStepData.call.return) }}</code></pre>
              </div>
            </div>
          </div>

          <!-- 第 4 步独有：完整 prompt -->
          <details
            v-if="activeStepData.system_prompt"
            class="init-section init-prompts"
          >
            <summary>查看完整 system prompt</summary>
            <div v-if="activeStepData.system_prompt">
              <h5>System Prompt</h5>
              <template v-for="(part, i) in systemPromptParts" :key="i">
                <div v-if="part.kind === 'injected'" class="injected-skills">
                  <span class="injected-skills-tag">从 skills 注入</span>
                  <MarkdownView :source="part.text" />
                </div>
                <MarkdownView v-else :source="part.text" />
              </template>
            </div>
          </details>
        </div>
      </div>
    </Transition>
  </div>
</template>