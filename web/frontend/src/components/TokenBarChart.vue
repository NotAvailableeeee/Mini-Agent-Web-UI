<script setup>
/**
 * TokenBarChart — 单 turn 内 step-by-step token 用量堆叠柱状图。
 *
 * 每个柱子代表一个 agent step。柱子由下到上堆叠 prompt (--accent 绿) →
 * completion (--tool 金黄)。Cache 段保留接口但当前永远 0 高(后端
 * agent_runner.py:281-295 已知 TODO),图例在父组件里标"暂未启用"。
 *
 * 视觉规则:
 *   - viewBox 720×200,preserveAspectRatio 缩放,容器宽度自适应
 *   - 内边距: top=16, bottom=28(Step 标签 + total 数值), left=40(y 刻度), right=16
 *   - 柱子宽自适应: clamp((plotW / N) × 0.65, 8, 60),N 大时柱间 gap 趋近 0
 *   - 4 条横向网格线 + 5 个 y 轴刻度(0 / 0.25 / 0.5 / 0.75 / 1.0 × yMax)
 *   - x 轴 Step 标签 N > 24 时只显示偶数步,避免文字重叠
 *   - 柱子顶部显示 total 数值(k 缩写)
 *   - 入场动画: bar-grow keyframe(350ms ease-out),stagger 30ms × index
 *
 * 空状态: steps.length === 0 → 整张 SVG 居中文本 "无数据"。
 */

import { computed } from 'vue'

const props = defineProps({
  // 形如 [{ idx: 1, prompt: 500, completion: 200, total: 700, toolCount: 1 }, ...]
  steps:  { type: Array,  required: true },
  width:  { type: Number, default: 720 },
  height: { type: Number, default: 200 },
})

// ----- 内边距常量(viewBox 坐标系) -----
const PAD_T = 16
const PAD_B = 28
const PAD_L = 40
const PAD_R = 16

// ----- 派生尺寸 -----
const plotW = computed(() => props.width - PAD_L - PAD_R)
const plotH = computed(() => props.height - PAD_T - PAD_B)
const baseline = computed(() => PAD_T + plotH.value)

// ----- y 轴 -----
// yMax 至少为 1,空数据时给一个最小画布高度。顶部留 15% 给柱子顶上的 total 文字。
const yMax = computed(() => {
  const max = Math.max(...props.steps.map((s) => (s.prompt || 0) + (s.completion || 0)), 1)
  return max * 1.15
})

// 5 档刻度(0 / 25% / 50% / 75% / 100% × yMax)
const yLabels = computed(() => [
  0,
  yMax.value * 0.25,
  yMax.value * 0.5,
  yMax.value * 0.75,
  yMax.value,
])

// 网格线的 y 坐标(从顶往下排,顶部对应 yMax,底部对应 0)
const yLinePositions = computed(() => {
  return yLabels.value.map((v) => PAD_T + plotH.value - (v / yMax.value) * plotH.value)
})

// ----- x 轴 -----
// 柱子宽度,clamp 在 [8, 60]。柱子 = (plotW / N) × 0.65,留出柱间 gap。
const barWidth = computed(() => {
  const n = Math.max(props.steps.length, 1)
  const ideal = (plotW.value / n) * 0.65
  return Math.max(8, Math.min(60, ideal))
})

// 每根柱子的 x 中心,等距排列,首尾各留 24px 内边距防止贴边。
const xPositions = computed(() => {
  const n = props.steps.length
  if (!n) return []
  const sidePad = 24
  const usable = plotW.value - sidePad * 2
  const step = usable / n
  return Array.from({ length: n }, (_, i) => PAD_L + sidePad + step * (i + 0.5))
})

// ----- 柱子高度 / y 坐标 -----
// 统一以"占 plotH 的比例"表达。SVG y 向下增长,所以柱子底部 = baseline,
// 顶部 = baseline - totalH。
function hPrompt(s) {
  return ((s.prompt || 0) / yMax.value) * plotH.value
}
function hCompletion(s) {
  return ((s.completion || 0) / yMax.value) * plotH.value
}
// prompt 段的 y(SVG 坐标系:从 baseline 向上长 hPrompt)
function rectPromptY(s) {
  return baseline.value - hPrompt(s)
}
// completion 段的 y(堆在 prompt 上方)
function rectCompletionY(s) {
  return baseline.value - hPrompt(s) - hCompletion(s)
}

// ----- 数字格式化 -----
// 千分位 + k 缩写。0 直接显示 0,负数走兜底。
function formatNum(n) {
  if (n == null || !Number.isFinite(n)) return '0'
  const abs = Math.abs(n)
  if (abs < 1000) return String(Math.round(n))
  if (abs < 10000) return (n / 1000).toFixed(2) + 'k'
  return (n / 1000).toFixed(1) + 'k'
}

// 是否显示该步的 x 轴标签(N <= 24 全显,> 24 只显示偶数 idx)
function showStepLabel(s) {
  if (props.steps.length <= 24) return true
  return (s.idx || 0) % 2 === 0
}
</script>

<template>
  <svg
    :viewBox="`0 0 ${width} ${height}`"
    class="token-chart"
    preserveAspectRatio="xMidYMid meet"
    role="img"
    aria-label="每步 token 用量堆叠柱状图"
  >
    <!-- 网格线 -->
    <g class="chart-grid">
      <line
        v-for="(y, i) in yLinePositions"
        :key="`grid-${i}`"
        :x1="PAD_L"
        :x2="width - PAD_R"
        :y1="y"
        :y2="y"
      />
    </g>

    <!-- y 轴刻度标签(右对齐到 PAD_L 左侧) -->
    <g class="chart-y-labels">
      <text
        v-for="(y, i) in yLinePositions"
        :key="`ylab-${i}`"
        :x="PAD_L - 6"
        :y="y + 3"
        text-anchor="end"
      >{{ formatNum(yLabels[i]) }}</text>
    </g>

    <!-- 底部 baseline -->
    <line
      :x1="PAD_L"
      :x2="width - PAD_R"
      :y1="baseline"
      :y2="baseline"
      class="chart-baseline"
    />

    <!-- 柱子 + 顶部 total + x 轴 step 标签 -->
    <g
      v-for="(s, i) in steps"
      :key="s.idx"
      :transform="`translate(${xPositions[i]}, 0)`"
    >
      <!-- prompt 段(堆在下) -->
      <rect
        v-if="hPrompt(s) > 0"
        :x="-barWidth / 2"
        :y="rectPromptY(s)"
        :width="barWidth"
        :height="hPrompt(s)"
        class="bar bar-prompt"
        :style="{ animationDelay: `${i * 30}ms` }"
      />
      <!-- completion 段(堆在上) -->
      <rect
        v-if="hCompletion(s) > 0"
        :x="-barWidth / 2"
        :y="rectCompletionY(s)"
        :width="barWidth"
        :height="hCompletion(s)"
        class="bar bar-completion"
        :style="{ animationDelay: `${i * 30}ms` }"
      />
      <!-- x 轴 Step 标签 -->
      <text
        v-if="showStepLabel(s)"
        :x="0"
        :y="height - PAD_B + 14"
        text-anchor="middle"
        class="axis-step"
      >S{{ s.idx }}</text>
      <!-- 柱子顶部 total 数值(只有非零才显示,避免 1px 的视觉噪音) -->
      <text
        v-if="(s.prompt || 0) + (s.completion || 0) > 0"
        :x="0"
        :y="rectCompletionY(s) - 4"
        text-anchor="middle"
        class="bar-total"
      >{{ formatNum((s.prompt || 0) + (s.completion || 0)) }}</text>
    </g>

    <!-- 空状态 -->
    <text
      v-if="!steps.length"
      :x="width / 2"
      :y="height / 2"
      text-anchor="middle"
      class="empty-text"
    >无数据</text>
  </svg>
</template>

<style scoped>
.token-chart {
  display: block;
  width: 100%;
  height: auto;
  max-width: 100%;
  font-family: var(--font-mono);
}

/* 网格线:虚线 + 半透明 */
.chart-grid line {
  stroke: var(--border);
  stroke-width: 1;
  stroke-dasharray: 2 3;
  opacity: 0.6;
  fill: none;
}

/* 底部 baseline:实线,比网格略粗 */
.chart-baseline {
  stroke: var(--border);
  stroke-width: 1.2;
  fill: none;
}

/* y 轴刻度标签 */
.chart-y-labels text {
  fill: var(--text-dim);
  font-size: 9px;
  font-family: var(--font-mono);
}

/* 柱子入场动画:从 baseline 长到真实高度。
 * SVG rect 在某些浏览器对 transform-origin 支持不一致,所以用
 * 直接的 height + y 做动画更稳 —— 但 SVG 属性不支持 CSS transition,
 * 因此 fallback 用 transform scaleY(0→1)+ transform-origin bottom。
 * bar-grow keyframe 在 style.css 里定义。 */
.bar {
  transform-box: fill-box;
  transform-origin: bottom;
  animation: bar-grow 350ms ease-out backwards;
}

.bar-prompt {
  fill: var(--accent);
}

.bar-completion {
  fill: var(--tool);
}

/* x 轴 step 标签 */
.axis-step {
  fill: var(--text-dim);
  font-size: 9px;
  font-family: var(--font-mono);
}

/* 柱子顶部 total 数字 */
.bar-total {
  fill: var(--text-dim);
  font-size: 9px;
  font-family: var(--font-mono);
  font-weight: 500;
}

/* 空状态 */
.empty-text {
  fill: var(--text-dim);
  font-size: 12px;
  font-family: var(--font-sans);
}
</style>