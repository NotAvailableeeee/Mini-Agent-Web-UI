<script setup>
/**
 * TurnSummaryCard — 每轮对话结束后的总结卡(默认折叠)。
 *
 * 折叠态: 一行 KPI(S steps / Tools / 消耗TOKEN量 / 用时 / 状态)
 * 展开态:
 *   1. TokenBarChart 堆叠柱状图(prompt 绿 / completion 金 / cache 灰占位)
 *   2. 图例 + cache 字段标注"暂未启用"
 *   3. 工具调用分布 Top 5(成功条 + 失败标记)
 *
 * 视觉与 AgentInitInspector / StepLLMIOPanel 一致:
 *   - 折叠按钮 chrome(浅色 --accent-soft 底 + chevron)
 *   - KPI label:value 复用 StepLLMIOPanel 风格
 *
 * 聚合完全在组件内做(纯前端),不动后端 SSE 协议。
 *
 * 边界 case:
 *   - 0 step → 父组件 v-if 守卫,本组件假设 steps 至少 1 个
 *   - usage 字段缺失 → ?? 0 兜底
 *   - error turn → status='error',放掉 !error 守卫仍渲染(部分跑完也能看)
 *   - in-flight turn → status='partial',elapsedMs 显示"⏳ 仍在跑"
 *   - 展开状态持久化到 localStorage (全局开关,不是 per-turn)
 */

import { computed, onMounted, ref } from 'vue'
import TokenBarChart from './TokenBarChart.vue'

const props = defineProps({
  // turn.steps
  steps:      { type: Array,  required: true },
  // turn.error(SSE error event 触发时填值)
  error:      { type: String, default: '' },
  // turn.sent_at(用户点击 Send 的前端时间戳,作为 0-step 边界的耗时锚点)
  userSentAt: { type: Number, default: null },
})

// `expand` is emitted when the user opens/closes this card so the parent
// (ChatPanel) can scroll the chat to the bottom — otherwise the
// just-expanded chart body stays hidden below the input footer. Only
// fires on the open edge (expanded = true) since collapse doesn't need
// to push the view.
const emit = defineEmits(['expand'])

// ----- 展开状态(全局开关,持久化) -----
const EXPANDED_KEY = 'mini-agent-web:turn-summary-expanded'
const expanded = ref(false)

onMounted(() => {
  try {
    const v = localStorage.getItem(EXPANDED_KEY)
    if (v === '1') expanded.value = true
  } catch {
    // localStorage 可能不可用(private mode),静默忽略
  }
})

function toggleExpand() {
  expanded.value = !expanded.value
  try {
    localStorage.setItem(EXPANDED_KEY, expanded.value ? '1' : '0')
  } catch {
    // ignore
  }
}

// Native <details>'s `toggle` event — fires after the element opens/
// closes (whether by user click or programmatically). Sync our `expanded`
// ref so the chart body (v-show) and the localStorage persistence stay
// in lockstep with the DOM state. On the OPEN edge, also emit `expand`
// so the parent (ChatPanel) can scroll the chat down to reveal the
// just-unfolded chart body — otherwise the user has to scroll
// manually, and the input footer hides most of the chart.
function onToggle(e) {
  const wasOpen = expanded.value
  expanded.value = e.target.open
  try {
    localStorage.setItem(EXPANDED_KEY, expanded.value ? '1' : '0')
  } catch {
    // ignore
  }
  if (expanded.value && !wasOpen) {
    emit('expand')
  }
}

// ----- 1. 每步数据 → 图表数据 -----
function buildPerStepData(steps) {
  return steps
    .filter((s) => s.step != null)
    .sort((a, b) => a.step - b.step)
    .map((s) => {
      const u = s.response_payload?.usage || {}
      const prompt = u.prompt_tokens ?? 0
      const completion = u.completion_tokens ?? 0
      const total = u.total_tokens ?? prompt + completion
      return {
        idx: s.step,
        prompt,
        completion,
        cacheRead: u.cache_read_input_tokens ?? 0,
        cacheCreation: u.cache_creation_input_tokens ?? 0,
        total,
        toolCount: s.tool_calls?.length ?? 0,
      }
    })
}

const perStepData = computed(() => buildPerStepData(props.steps))

// ----- 2. KPI 聚合 -----
// status:
//   - 'ok'      : 所有 step 都有 step_end,无 error event
//   - 'error'   : turn.error 被设值(SSE error event)
//   - 'partial' : 还有 in-flight step(等所有 step_end 收到后再变 ok)
const kpi = computed(() => {
  const perStep = perStepData.value
  const toolStats = buildToolStats(props.steps)

  let status = 'ok'
  if (props.error) status = 'error'
  else if (props.steps.some((s) => s.elapsed_ms == null && s.started_at != null)) status = 'partial'

  return {
    stepCount:     props.steps.length,
    toolCount:     toolStats.reduce((a, t) => a + t.calls, 0),
    toolFailCount: toolStats.reduce((a, t) => a + t.failures, 0),
    promptTokens:      perStep.reduce((a, s) => a + s.prompt, 0),
    completionTokens:  perStep.reduce((a, s) => a + s.completion, 0),
    totalTokens:       perStep.reduce((a, s) => a + s.total, 0),
    elapsedMs:         computeElapsedMs(props.steps, props.userSentAt),
    status,
  }
})

// ----- 3. 总耗时(避免被 in-flight step 拖住) -----
function computeElapsedMs(steps, userSentAt) {
  if (!steps.length || !userSentAt) return null
  // 优先用最后一个 closed step 的 ended_at
  const lastClosed = [...steps].reverse().find((s) => s.ended_at != null)
  if (lastClosed) return lastClosed.ended_at - userSentAt
  // 退化:用第一个 step 的 started_at - sent_at
  const firstStart = steps[0]?.started_at
  return firstStart != null ? firstStart - userSentAt : null
}

// ----- 4. 工具分布(Top 5) -----
function buildToolStats(steps) {
  const map = new Map()
  for (const s of steps) {
    for (const tc of s.tool_calls ?? []) {
      const name = tc.name || 'unknown'
      const cur = map.get(name) ?? { name, calls: 0, failures: 0 }
      cur.calls++
      if (tc.result && tc.result.success === false) cur.failures++
      map.set(name, cur)
    }
  }
  return [...map.values()]
    .sort((a, b) => b.calls - a.calls)
    .slice(0, 5)
    .map((t) => ({
      ...t,
      successRate: t.calls ? (1 - t.failures / t.calls) * 100 : 0,
    }))
}

const toolStats = computed(() => buildToolStats(props.steps))

// ----- 格式化 helper -----
function formatMs(ms) {
  if (ms == null) return ''
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function formatTokens(n) {
  if (n == null || !Number.isFinite(n)) return '0'
  const abs = Math.abs(n)
  if (abs < 1000) return String(Math.round(n))
  if (abs < 10000) return (n / 1000).toFixed(2) + 'k'
  return (n / 1000).toFixed(1) + 'k'
}

function statusIcon(s) {
  if (s === 'ok') return '📊'
  if (s === 'error') return '⚠️'
  return '⏳'
}

function statusLabel(s) {
  if (s === 'ok') return '已完成'
  if (s === 'error') return '部分失败'
  return '仍在跑'
}
</script>

<template>
  <details class="turn-summary" :open="expanded" @toggle="onToggle">
    <!-- 折叠态:一行 KPI -->
    <summary
      class="turn-summary-summary"
      :class="`status-${kpi.status}`"
      :aria-label="expanded ? '收起调用量小结' : '展开调用量小结'"
    >
      <span class="turn-summary-icon" aria-hidden="true">{{ statusIcon(kpi.status) }}</span>
      <span class="turn-summary-label">调用量小结</span>
      <span class="turn-summary-kpis">
        <span class="kpi" :title="`共 ${kpi.stepCount} 步`">
          <span class="kpi-key">Steps</span>
          <span class="kpi-val">{{ kpi.stepCount }}</span>
        </span>
        <span class="kpi" :title="`调用工具 ${kpi.toolCount} 次`">
          <span class="kpi-key">Tools</span>
          <span class="kpi-val">
            {{ kpi.toolCount }}
            <span v-if="kpi.toolFailCount" class="kpi-fail">({{ kpi.toolFailCount }} ❌)</span>
          </span>
        </span>
        <span class="kpi" :title="`${kpi.totalTokens} tokens (prompt ${kpi.promptTokens} + completion ${kpi.completionTokens})`">
          <span class="kpi-key">消耗TOKEN量</span>
          <span class="kpi-val">{{ formatTokens(kpi.totalTokens) }}</span>
        </span>
        <span class="kpi" :title="kpi.elapsedMs != null ? `${kpi.elapsedMs} ms` : '等待 step 关闭'">
          <span class="kpi-key">用时</span>
          <span class="kpi-val">
            <template v-if="kpi.elapsedMs != null">{{ formatMs(kpi.elapsedMs) }}</template>
            <template v-else>⏳ 仍在跑</template>
          </span>
        </span>
        <span class="kpi status-badge" :title="statusLabel(kpi.status)">
          <span class="kpi-val">{{ statusLabel(kpi.status) }}</span>
        </span>
      </span>
    </summary>

    <!-- 展开态 -->
    <div v-show="expanded" class="turn-summary-body">
      <!-- 1. Token 柱状图 -->
      <TokenBarChart
        v-if="perStepData.length > 0"
        :steps="perStepData"
      />
      <div v-else class="empty-chart">本轮未产生 LLM 调用,无 token 数据</div>

      <!-- 2. 图例 -->
      <div class="chart-legend">
        <span class="legend-item">
          <span class="swatch swatch-prompt" aria-hidden="true"></span>
          <span>Prompt</span>
        </span>
        <span class="legend-item">
          <span class="swatch swatch-completion" aria-hidden="true"></span>
          <span>Completion</span>
        </span>
        <span
          class="legend-item disabled"
          title="Cache 字段待后端 anthropic_client.py 暴露 cache_read/cache_creation 子项;届时会自动点亮"
        >
          <span class="swatch swatch-cache" aria-hidden="true"></span>
          <span>Cache (暂未启用)</span>
        </span>
      </div>

      <!-- 3. 工具调用分布 -->
      <div v-if="toolStats.length" class="tool-stats">
        <h4 class="tool-stats-title">工具调用分布</h4>
        <div class="tool-stat-row-list">
          <div v-for="t in toolStats" :key="t.name" class="tool-stat-row">
            <span class="tool-stat-name" :title="t.name">{{ t.name }}</span>
            <span class="tool-stat-bar" aria-hidden="true">
              <span class="tool-stat-bar-fill" :style="{ width: t.successRate + '%' }"></span>
            </span>
            <span class="tool-stat-count">
              {{ t.calls }} 次
              <span v-if="t.failures" class="kpi-fail">({{ t.failures }} 失败)</span>
            </span>
          </div>
        </div>
      </div>
      <div v-else class="empty-tool-stats">本轮未调用工具</div>
    </div>
  </details>
</template>

<style scoped>
/* 外层容器 —— 视觉与 .step-raw-data 对齐:虚线框 + 透明底 + 左右顶到头。
 * collapse/expand 由原生 <details>/<summary> 处理(同 step-raw-data)。 */
.turn-summary {
  margin-top: 4px;
  background: transparent;
  border: 1px dashed var(--border);
  border-radius: 4px;
  overflow: hidden;
  /* Edge-to-edge within the chat width (no max-width cap) — matches
   * the per-step raw-data panel above. */
  align-self: stretch;
}

/* 折叠态 summary 行 —— 等宽小字 + chevron 在最左(由 ::before 注入) */
.turn-summary-summary {
  cursor: pointer;
  font-size: 11px;
  font-family: var(--font-mono);
  color: var(--text-dim);
  user-select: none;
  list-style: none;
  padding: 6px 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}
.turn-summary-summary:hover { background: var(--panel-2); }
.turn-summary-summary::-webkit-details-marker { display: none; }
/* Chevron: ▶ collapsed, ▼ expanded — placed at the LEFT of the row */
.turn-summary-summary::before {
  content: '▶';
  font-size: 9px;
  color: var(--text-dim);
  margin-right: 2px;
  flex-shrink: 0;
}
.turn-summary[open] > .turn-summary-summary::before { content: '▼'; }

/* Partial state sweep — diagonal gray stripe across the summary row.
 * Same animation as before, just retargeted to .turn-summary-summary. */
.turn-summary-summary.status-partial {
  background-image: linear-gradient(
    100deg,
    transparent 30%,
    rgba(107, 114, 128, 0.22) 50%,
    transparent 70%
  );
  background-size: 220% 100%;
  background-position: -50% 0;
  animation: partial-sweep 1.8s ease-in-out infinite;
}
@keyframes partial-sweep {
  0%   { background-position: -50% 0; }
  100% { background-position: 150% 0; }
}

/* Icon (status emoji), label, and KPIs all flow inline with the chevron */
.turn-summary-icon {
  font-size: 12px;
  flex-shrink: 0;
}
/* Partial state — make the status icon pulse in sync with the sweep
 * animation so the "live" signal is visible even at small sizes. */
.turn-summary-summary.status-partial .turn-summary-icon {
  animation: partial-icon-pulse 1.4s ease-in-out infinite;
}
@keyframes partial-icon-pulse {
  0%, 100% { opacity: 0.5; }
  50%      { opacity: 1; }
}

.turn-summary-label {
  font-weight: 600;
  font-size: 11px;
  color: var(--text);
  flex-shrink: 0;
}

/* KPI 行:5 个 metric 横向铺开,wrap 到下一行 */
.turn-summary-kpis {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 14px;
  flex: 1;
  margin-left: 4px;
}

.kpi {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1;
  white-space: nowrap;
}

.kpi-key {
  color: var(--text-dim);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.kpi-val {
  color: var(--text);
  font-weight: 600;
  font-size: 11px;
}

.kpi-fail {
  color: var(--error);
  font-weight: 500;
  margin-left: 2px;
}

/* 状态徽章单独一种样式 */
.kpi.status-badge .kpi-val {
  display: inline-block;
  padding: 2px 6px;
  border-radius: 4px;
  background: var(--panel);
  border: 1px solid var(--border);
  font-weight: 500;
  font-size: 10px;
  text-transform: none;
  letter-spacing: 0;
}
.turn-summary-summary.status-ok .kpi.status-badge .kpi-val {
  color: var(--accent);
  border-color: var(--accent);
}
.turn-summary-summary.status-error .kpi.status-badge .kpi-val {
  color: var(--error);
  border-color: var(--error);
}
.turn-summary-summary.status-partial .kpi.status-badge .kpi-val {
  color: var(--text-dim);
}

/* 展开态内容区 */
.turn-summary-body {
  padding: 12px 14px 14px;
  border-top: 1px solid var(--border);
}

/* 空 chart 占位 */
.empty-chart {
  padding: 30px 0;
  text-align: center;
  color: var(--text-dim);
  font-size: 12px;
  font-style: italic;
}

/* 图例:横向三栏 */
.chart-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px dashed var(--border);
  font-size: 11px;
  color: var(--text-dim);
}

.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.legend-item.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.swatch {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 2px;
  border: 1px solid transparent;
}
.swatch-prompt     { background: var(--accent); }
.swatch-completion { background: var(--tool); }
.swatch-cache      { background: var(--text-dim); opacity: 0.4; }

/* 工具调用分布 */
.tool-stats {
  margin-top: 12px;
}
.tool-stats-title {
  margin: 0 0 8px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-dim);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.tool-stat-row-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.tool-stat-row {
  display: grid;
  grid-template-columns: minmax(80px, auto) 1fr auto;
  align-items: center;
  gap: 8px;
  font-family: var(--font-mono);
  font-size: 11px;
}

.tool-stat-name {
  color: var(--text);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 160px;
}

.tool-stat-bar {
  height: 8px;
  border-radius: 4px;
  background: var(--panel);
  border: 1px solid var(--border);
  overflow: hidden;
  position: relative;
}

.tool-stat-bar-fill {
  display: block;
  height: 100%;
  background: var(--accent);
  border-radius: 4px;
  transition: width 350ms ease-out;
}

.tool-stat-count {
  color: var(--text-dim);
  font-size: 10px;
  white-space: nowrap;
}

.empty-tool-stats {
  margin-top: 12px;
  padding: 8px 0;
  color: var(--text-dim);
  font-size: 11px;
  font-style: italic;
}
</style>