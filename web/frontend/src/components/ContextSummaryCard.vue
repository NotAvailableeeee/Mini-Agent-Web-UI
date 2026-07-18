<script setup>
/**
 * ContextSummaryCard — 上下文压缩事件卡。
 *
 * 当 Agent 触发 _summarize_messages() 把多条历史消息压缩成一段
 * "Assistant Execution Summary" 时,后端会发出:
 *   - context_summary_round  (每条摘要,可能多条)
 *   - context_summary_done   (汇总,带 before/after tokens)
 *
 * 本组件显示一个折叠卡片(默认折叠,跟 TurnSummaryCard 一致):
 *   - 折叠态:  📦 上下文压缩 · 合并 N 轮 · 节省 X tokens (before → after)
 *   - 展开态:  每轮的 "Round N · M 条消息" + 摘要原文
 *
 * 数据契约 (props.compression):
 *   {
 *     before_tokens: number,         // 压缩前 token 数
 *     after_tokens:  number,         // 压缩后 token 数
 *     summary_count: number,         // 生成了几条摘要
 *     user_round_count: number,      // 当前有 N 轮 user 消息
 *     rounds: [
 *       { round: number, compressed_message_count: number, summary_text: string }
 *     ],
 *     ts: number,                    // 前端收到 done 的时间戳
 *   }
 *
 * 边界 case:
 *   - rounds 为空 → 仍渲染 KPI 行,展开区显示占位文案
 *   - 多张卡片堆叠 → 每张独立折叠(用 index 作 default-open key 太重,
 *     简单用 v-if 每次新建 <details>,默认 collapsed)
 *   - summary_text 可能很长 → max-height + overflow-y 限制
 */

import { computed, ref } from 'vue'

const props = defineProps({
  compression: { type: Object, required: true },
  // 该 turn 内的第几张压缩卡(从 1 开始),用于 title 编号
  index: { type: Number, default: 1 },
})

const expanded = ref(false)

function toggleExpand() {
  expanded.value = !expanded.value
}

const savedTokens = computed(() => {
  const b = props.compression.before_tokens ?? 0
  const a = props.compression.after_tokens ?? 0
  return Math.max(0, b - a)
})

function formatTokens(n) {
  if (n == null || !Number.isFinite(n)) return '0'
  const abs = Math.abs(n)
  if (abs < 1000) return String(Math.round(n))
  if (abs < 10000) return (n / 1000).toFixed(2) + 'k'
  return (n / 1000).toFixed(1) + 'k'
}

// 渲染时刻 — 用于显示"刚刚 / 12s 前"之类;MVP 先显示 HH:MM:SS
const stamp = computed(() => {
  const ts = props.compression.ts
  if (!ts) return ''
  const d = new Date(ts)
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
})
</script>

<template>
  <details class="ctx-summary" :open="expanded" @toggle="(e) => (expanded = e.target.open)">
    <summary class="ctx-summary-head" :aria-label="expanded ? '收起上下文压缩详情' : '展开上下文压缩详情'">
      <span class="ctx-summary-icon" aria-hidden="true">📦</span>
      <span class="ctx-summary-label">上下文压缩 · 第 {{ index }} 段</span>
      <span class="ctx-summary-kpis">
        <span class="kpi" :title="`共 ${compression.user_round_count ?? 0} 轮 user 消息`">
          <span class="kpi-key">轮次</span>
          <span class="kpi-val">{{ compression.summary_count ?? 0 }}</span>
        </span>
        <span class="kpi" :title="`${savedTokens} tokens 节省`">
          <span class="kpi-key">节省</span>
          <span class="kpi-val kpi-saved">{{ formatTokens(savedTokens) }}</span>
        </span>
        <span class="kpi" :title="`压缩前 ${compression.before_tokens ?? 0} → 压缩后 ${compression.after_tokens ?? 0}`">
          <span class="kpi-key">Tokens</span>
          <span class="kpi-val">
            {{ formatTokens(compression.before_tokens) }}
            <span class="arrow" aria-hidden="true">→</span>
            {{ formatTokens(compression.after_tokens) }}
          </span>
        </span>
        <span v-if="stamp" class="kpi" :title="`前端收到 done 事件的时间`">
          <span class="kpi-key">@</span>
          <span class="kpi-val">{{ stamp }}</span>
        </span>
      </span>
    </summary>

    <div v-show="expanded" class="ctx-summary-body">
      <div v-if="(compression.rounds ?? []).length === 0" class="empty-rounds">
        本次压缩未生成可读的摘要内容(可能是边缘条件下触发的空压缩)
      </div>
      <div v-for="r in compression.rounds ?? []" :key="r.round" class="round-block">
        <div class="round-head">
          <span class="round-tag">Round {{ r.round }}</span>
          <span class="round-meta">{{ r.compressed_message_count ?? 0 }} 条消息被合并</span>
        </div>
        <pre class="round-text">{{ r.summary_text }}</pre>
      </div>
    </div>
  </details>
</template>

<style scoped>
/* 外层容器 —— 与 TurnSummaryCard / StepRawDataPanel 视觉一致:
 * 虚线边框 + 透明底 + edge-to-edge */
.ctx-summary {
  margin: 6px 0;
  background: transparent;
  border: 1px dashed var(--border);
  border-radius: 4px;
  overflow: hidden;
  align-self: stretch;
  /* 刚出现的瞬间轻微高亮,让用户注意到"刚发生了压缩" */
  animation: ctx-flash 1.6s ease-out 1;
}
@keyframes ctx-flash {
  0%   { box-shadow: 0 0 0 3px rgba(234, 179, 8, 0.35); border-color: rgba(234, 179, 8, 0.6); }
  100% { box-shadow: 0 0 0 0   rgba(234, 179, 8, 0);     border-color: var(--border); }
}

/* 折叠 summary 行 */
.ctx-summary-head {
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
  transition: background 0.15s;
}
.ctx-summary-head:hover { background: var(--panel-2); }
.ctx-summary-head::-webkit-details-marker { display: none; }
/* Chevron */
.ctx-summary-head::before {
  content: '▶';
  font-size: 9px;
  color: var(--text-dim);
  margin-right: 2px;
  flex-shrink: 0;
  transition: transform 0.2s;
}
.ctx-summary[open] > .ctx-summary-head::before { content: '▼'; }

.ctx-summary-icon {
  font-size: 12px;
  flex-shrink: 0;
}
.ctx-summary-label {
  font-weight: 600;
  font-size: 11px;
  color: var(--text);
  flex-shrink: 0;
}

.ctx-summary-kpis {
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
/* "节省" 用强调色,呼应压缩事件本身是正向的(释放空间) */
.kpi-val.kpi-saved {
  color: #22c55e; /* green-500 — 跟 var(--accent) 不冲突 */
}
.arrow {
  color: var(--text-dim);
  font-weight: 400;
  margin: 0 3px;
}

/* 展开态内容区 */
.ctx-summary-body {
  padding: 10px 14px 14px;
  border-top: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.empty-rounds {
  padding: 8px 4px;
  text-align: center;
  color: var(--text-dim);
  font-size: 11px;
  font-style: italic;
}

.round-block {
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 8px 10px;
}
.round-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.round-tag {
  display: inline-block;
  padding: 1px 6px;
  border-radius: 3px;
  background: var(--accent-soft);
  color: var(--accent);
  font-size: 10px;
  font-weight: 600;
  font-family: var(--font-mono);
  letter-spacing: 0.03em;
}
.round-meta {
  color: var(--text-dim);
  font-size: 11px;
  font-family: var(--font-mono);
}
.round-text {
  margin: 0;
  padding: 8px 10px;
  background: var(--bg);
  border-radius: 3px;
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.55;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 280px;
  overflow-y: auto;
}
</style>