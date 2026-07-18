<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { api } from '../api/client.js'
import UserBlock from './UserBlock.vue'
import TurnContent from './TurnContent.vue'
import TurnSummaryCard from './TurnSummaryCard.vue'
import ContextSummaryCard from './ContextSummaryCard.vue'
import ShowcaseGallery from './ShowcaseGallery.vue'

const props = defineProps({
  // Optional callback fired whenever a `step_end` event arrives. The
  // parent (App.vue) uses it to refresh the workspace file tree right
  // after the agent finishes a step — that's when new files may have
  // been written or existing ones modified.
  onStepEnd: { type: Function, default: null },
  // Optional async function that runs the actual clear (calls
  // api.clearChat() + bumps the memory-refresh trigger in the parent).
  // Centralised in App.vue so the memory panel reliably re-fetches
  // when the conversation is reset. Should return the backend's
  // response on success; throw on failure (so the chat can surface
  // the error).
  onClear: { type: Function, default: null },
})

// 用户点击能力 pill 时直接发预设 prompt —— 不再依赖 App 传中转
function handleShowcasePick(card) {
  send(card.prompt)
}

// ----- State -----
// A `turn` is one user→assistant exchange. It owns a `steps` array (one entry
// per agent step) plus the user's input text. The whole turn renders as one
// block in the scroll view.
const input = ref('')
const busy = ref(false)
// turns[].steps[].tool_calls[] has shape: {id, name, arguments, result?}
// Per-block open/closed state lives inside <TurnContent> now (each turn
// component manages its own collapse map), so this component no longer
// tracks expansion state itself.
const turns = ref([])

// ----- Context-window indicator (sticky progress bar) -----
// `contextTokens` reflects the *current* size of the agent's prompt window
// at the most recent LLM call — i.e. the value that drives the
// `api_total_tokens > token_limit` trigger on the backend. We update it
// from every `llm_response` event (which carries the API-reported usage).
//
// `tokenLimit` is a frontend mirror of `Agent.token_limit` (default 80000,
// set in `agent_runner.py:643` when constructing the Agent). Kept as a
// constant ref so a future PR can wire it to a real config knob without
// rewriting the progress bar.
//
// `flashState`:
//   - null         : idle
//   - 'over'       : currently > tokenLimit (red, persistent pulse)
//   - 'compressed' : a `context_summary_done` event just arrived (green,
//                    1.6s, then clears back to idle / over depending on
//                    the new value)
const TOKEN_LIMIT = 80000
const contextTokens = ref(0)
const tokenLimit = ref(TOKEN_LIMIT)
const flashState = ref(null)
let flashTimer = null

function setFlash(state, durationMs = 1600) {
  flashState.value = state
  if (flashTimer) clearTimeout(flashTimer)
  if (durationMs > 0) {
    flashTimer = setTimeout(() => {
      flashState.value = null
      flashTimer = null
    }, durationMs)
  }
}

// True between the first `context_summary_round` event and the matching
// `context_summary_done` event. The placeholder thinking card uses this to
// swap its label to "正在折叠上下文，请稍后" and hide the step number, so
// users see a distinct "compression in progress" affordance instead of
// the generic "Step N · 正在思考…" shimmer during a compaction pass.
const isCompacting = ref(false)

function formatTokens(n) {
  if (n == null || !Number.isFinite(n)) return '0'
  const abs = Math.abs(n)
  if (abs < 1000) return String(Math.round(n))
  if (abs < 10000) return (n / 1000).toFixed(2) + 'k'
  return (n / 1000).toFixed(1) + 'k'
}

const usagePercent = computed(() =>
  tokenLimit.value > 0
    ? Math.min(100, (contextTokens.value / tokenLimit.value) * 100)
    : 0,
)
const isOverLimit = computed(() => contextTokens.value > tokenLimit.value)

// `barClass` controls the visual state of the sticky bar:
//   .idle      : normal, under threshold
//   .over      : red, persistent pulse — context overflowed
//   .compress  : green flash — just got compressed
// `compress` wins over `over` so a compression event that drops us back
// under the limit shows the celebratory green even if briefly the new
// value still sits above threshold (defensive: should never happen since
// the whole point of summary is to drop below the limit, but cheap to
// cover).
const barClass = computed(() => {
  if (flashState.value === 'compressed') return 'compress'
  if (flashState.value === 'over' || isOverLimit.value) return 'over'
  return 'idle'
})

// Fill style for the context bar. Width tracks usage; the color sweeps a
// smooth green → yellow → orange → red gradient as the window fills, using an
// HSL hue that runs from 140° (green, empty) down to 0° (red, full). The
// color is only applied in the neutral 'idle' state — the `.over` (red pulse)
// and `.compress` (green flash) states keep their own class-driven colors, so
// we omit `background` there and let the stylesheet win.
const fillStyle = computed(() => {
  const style = { width: usagePercent.value + '%' }
  if (barClass.value === 'idle') {
    const p = Math.min(1, Math.max(0, usagePercent.value / 100))
    const hue = 140 * (1 - p)
    style.background = `hsl(${Math.round(hue)} 72% 45%)`
  }
  return style
})

// Ref to `.chat-panel` itself — it IS the scroll container now (since
// the sticky-bar restructure moved scrolling up from `.chat-messages`).
// scrollToBottom() uses this to keep the chat pinned to the latest step
// as new events stream in.
const panelEl = ref(null)
const textareaEl = ref(null)

// ----- Autoresize textarea -----
// CSS gives the box a min/max range (36–120px) and disables manual
// resize; we set the inline `height` to the content's scrollHeight so
// the box grows with multi-line input and shrinks back to one line when
// cleared. Without this, the textarea stayed stuck at the min and long
// messages were hidden behind `overflow: hidden` until the user
// scrolled inside the single-line box.
const TEXTAREA_MIN = 36
const TEXTAREA_MAX = 120

function autoresizeTextarea() {
  const el = textareaEl.value
  if (!el) return
  // Reset to 'auto' first so scrollHeight reflects the full content
  // (otherwise the height we just set caps the measurement).
  el.style.height = 'auto'
  const next = Math.max(TEXTAREA_MIN, Math.min(el.scrollHeight, TEXTAREA_MAX))
  el.style.height = next + 'px'
}

// Re-measure whenever the bound value changes (typing, clearing after
// send, presetMessage injection). nextTick guarantees the DOM has
// applied the v-model update before we read scrollHeight.
watch(input, () => nextTick(autoresizeTextarea))

// ----- Helpers -----
function scrollToBottom() {
  nextTick(() => {
    if (panelEl.value) {
      panelEl.value.scrollTop = panelEl.value.scrollHeight
    }
  })
}

// Exposed for App.vue to call from sibling components (e.g. MemoryPanel
// emits when its graph section collapses so the user lands on the
// latest message instead of wherever they were scrolled before).
defineExpose({ scrollToBottom })

function ensureStep(turnIdx, stepNum) {
  const turn = turns.value[turnIdx]
  if (!turn) return null
  let step = turn.steps.find((s) => s.step === stepNum)
  if (!step) {
    step = {
      step: stepNum,
      thinking: '',
      content: '',
      tool_calls: [],
      // Boundary anchor for the wait clock. Source depends on step number:
      //   - step 1: turn.sent_at (user click)
      //   - step N>1: previous step's `ended_at` (when prev step_end arrived)
      // Drives the live in-flight timer display.
      started_at: null,
      // Frozen when frontend sees `step_end`; equals Date.now()-started_at at
      // that instant. Takes over display from the live calculation.
      elapsed_ms: null,
      // Wall-clock when `step_end` arrived on the frontend. Stamped here so
      // the NEXT step can use it as its own `started_at` boundary.
      ended_at: null,
      // Filled by `llm_request` SSE event — the exact Anthropic wire payload
      // (model, max_tokens, system, messages, tools) sent to the LLM for
      // this step. Drives the "📤 发送给 LLM" panel.
      request_payload: null,
      // Filled by `llm_response` SSE event — the raw LLMResponse
      // (content/thinking/tool_calls/finish_reason/usage) returned by the
      // model. Drives the "📥 LLM 原始响应" panel.
      response_payload: null,
    }
    turn.steps.push(step)
  }
  return step
}

function ensureToolCall(turnIdx, stepNum, toolCallId) {
  const step = ensureStep(turnIdx, stepNum)
  if (!step) return null
  let tc = step.tool_calls.find((t) => t.id === toolCallId)
  if (!tc) {
    tc = { id: toolCallId, name: '', arguments: {}, result: null }
    step.tool_calls.push(tc)
  }
  return tc
}

const hasMessages = computed(() => turns.value.length > 0)

// ----- Thinking placeholder (between user send and first step_start) -----
// Renders a skeleton step-card with a pulsing dot + shimmer bars + rotating
// phase label so users see liveness while waiting on the LLM round-trip.
const THINKING_PHASES = ['正在连接模型…', '分析任务…', '正在思考…', '等待响应…']
const tick = ref(0)              // drives phase rotation; bumped every ~2.2s
let phaseInterval = null

const isThinking = computed(() => {
  if (!busy.value) return false
  const last = turns.value[turns.value.length - 1]
  if (!last || last.error) return false
  return last.steps.length === 0
})

// Between-steps waiting state: the previous step has closed (its tool
// results are all in / it was a content-only final step) but the next
// `step_start` hasn't arrived yet. The backend may be waiting on the LLM
// to produce the next round; the user shouldn't see an empty gap.
const isBetweenSteps = computed(() => {
  if (!busy.value) return false
  const last = turns.value[turns.value.length - 1]
  if (!last || last.error || last.steps.length === 0) return false
  return last.steps[last.steps.length - 1].elapsed_ms != null
})

// Step number to show on the placeholder card. 1 before the first step
// arrives, otherwise (last step's number) + 1.
const placeholderStepNum = computed(() => {
  const last = turns.value[turns.value.length - 1]
  if (!last || last.steps.length === 0) return 1
  return last.steps[last.steps.length - 1].step + 1
})

const phase = computed(() => THINKING_PHASES[tick.value % THINKING_PHASES.length])

watch(isThinking, (active) => {
  if (active) {
    tick.value = 0
    if (phaseInterval) clearInterval(phaseInterval)
    phaseInterval = setInterval(() => { tick.value++ }, 2200)
  } else if (phaseInterval) {
    clearInterval(phaseInterval)
    phaseInterval = null
  }
})

// ----- Live step timer -----
// Bumps every 100ms whenever the user is waiting on something — either a
// step in flight, the initial thinking placeholder, or the between-steps
// placeholder. The header span reads `nowTick.value` inside its render
// path, so Vue re-runs the live duration calculation on each tick.
//
// `isBetweenSteps` was missing here: when step N closes and step N+1
// hasn't started yet, the placeholder card is shown with a live timer
// (anchor = lastStep.ended_at) but the interval was NOT running, so the
// rendered value was frozen at ~0ms for the entire LLM round-trip.
const nowTick = ref(0)
let nowTickInterval = null

function shouldTick() {
  if (isThinking.value) return true
  if (isBetweenSteps.value) return true
  for (const t of turns.value) {
    for (const s of t.steps) {
      if (s.elapsed_ms == null && s.started_at != null) return true
    }
  }
  return false
}

watch(shouldTick, (active) => {
  if (active) {
    if (nowTickInterval) return
    nowTickInterval = setInterval(() => { nowTick.value++ }, 100)
  } else if (nowTickInterval) {
    clearInterval(nowTickInterval)
    nowTickInterval = null
  }
})

onUnmounted(() => {
  if (phaseInterval) clearInterval(phaseInterval)
  if (nowTickInterval) clearInterval(nowTickInterval)
})

// ----- Event handlers (called from the SSE consumer) -----
function handleEvent(turnIdx, event) {
  switch (event.type) {
    case 'user':
      // Turn was already created in `send()`; nothing to do.
      break
    case 'step_start': {
      const step = ensureStep(turnIdx, event.step)
      if (step && step.started_at == null) {
        // Anchor the wait clock so the timer shows end-to-end user wait:
        //   - Step 1:  from user-send (turn.sent_at)
        //   - Step N>1: from previous step's `step_end` arrival (ended_at)
        // Falls back to `Date.now()` only if the boundary is missing (e.g.
        // out-of-order events, which shouldn't happen but we don't want
        // `NaN` in the timer).
        const turn = turns.value[turnIdx]
        const prev = turn.steps.find((s) => s.step === event.step - 1)
        if (prev && prev.ended_at != null) {
          step.started_at = prev.ended_at
        } else if (event.step === 1 && turn.sent_at != null) {
          step.started_at = turn.sent_at
        } else {
          step.started_at = Date.now()
        }
      }
      break
    }
    case 'thinking':
      ensureStep(turnIdx, event.step).thinking += event.content
      break
    case 'llm_request': {
      // Snapshot of what was sent to the LLM for this step. Emitted by the
      // backend immediately after step_start. Stored on the step so
      // UserBlock can render it under the user message (the LAST step's
      // request_payload is the most complete picture for the turn).
      const step = ensureStep(turnIdx, event.step)
      if (step) step.request_payload = event.payload
      break
    }
    case 'llm_response': {
      // Raw response (finish_reason + usage + full tool_calls) from the LLM.
      // The Message we already store loses these — this event captures them
      // before they're discarded.
      const step = ensureStep(turnIdx, event.step)
      if (step) step.response_payload = event.payload
      // Drive the sticky context-window indicator. `usage.total_tokens` is
      // the LLM's own count for THIS call (input + output) — that's the
      // best signal we have for "how full is the context right now", and
      // it's exactly what `agent.api_total_tokens` reflects on the backend.
      const usage = event.payload?.usage
      if (usage && Number.isFinite(usage.total_tokens)) {
        contextTokens.value = usage.total_tokens
      }
      break
    }
    case 'content':
      ensureStep(turnIdx, event.step).content += event.content
      break
    case 'tool_call': {
      const tc = ensureToolCall(turnIdx, event.step, event.id)
      if (tc) {
        tc.name = event.name
        tc.arguments = event.arguments
      }
      break
    }
    case 'tool_result': {
      const tc = ensureToolCall(turnIdx, event.step, event.id)
      if (tc) {
        tc.result = {
          success: event.success,
          content: event.content,
          error: event.error,
        }
      }
      break
    }
    case 'step_end': {
      const step = ensureStep(turnIdx, event.step)
      if (step) {
        // Freeze the wall-clock wait using the frontend's own Date.now(),
        // not the backend's perf_counter, so the value reflects what the
        // user actually experienced (includes SSE transit + any inter-step
        // gaps before step_end arrived). Also stamp `ended_at` so the NEXT
        // step can use it as its own `started_at` boundary.
        if (step.started_at != null) {
          step.elapsed_ms = Date.now() - step.started_at
        }
        step.ended_at = Date.now()
      }
      // Notify the parent so it can refresh derived state — e.g. the
      // workspace file tree, which may have just gained or changed a file.
      // Wrapped in try/catch so a misbehaving parent can't break the
      // chat's own state machine.
      if (typeof props.onStepEnd === 'function') {
        try {
          props.onStepEnd()
        } catch (err) {
          console.error('onStepEnd handler threw:', err)
        }
      }
      break
    }
    case 'done': {
      busy.value = false
      // Defensive: if the runner somehow skipped `step_end` for the last
      // step (it shouldn't, but we don't want a runaway counter), freeze it.
      const now = Date.now()
      for (const s of turns.value[turnIdx].steps) {
        if (s.elapsed_ms == null && s.started_at != null) {
          s.elapsed_ms = now - s.started_at
        }
        if (s.elapsed_ms != null && s.ended_at == null) {
          s.ended_at = now
        }
      }
      // Final reply lives on the last step's `content`; nothing else to do.
      break
    }
    case 'context_summary_round': {
      // Per-round incremental update. The server emits one of these for
      // EACH summarized round before the final done event; we just stash it
      // into a `pendingSummary` slot so the done handler can fold everything
      // into a single card. This gives the user the impression of streaming
      // progress even though the card itself only renders once complete.
      const turn = turns.value[turnIdx]
      if (!turn.pendingSummary) {
        turn.pendingSummary = { rounds: [] }
      }
      turn.pendingSummary.rounds.push({
        round: event.round,
        compressed_message_count: event.compressed_message_count,
        summary_text: event.summary_text || '',
      })
      isCompacting.value = true
      break
    }
    case 'context_summary_done': {
      // Full compression event — render one card. Server may have already
      // streamed per-round events; if not (e.g. zero rounds), still render
      // with an empty rounds array so the user sees the token savings.
      const turn = turns.value[turnIdx]
      const compression = {
        before_tokens: event.before_tokens ?? 0,
        after_tokens: event.after_tokens ?? 0,
        summary_count: event.summary_count ?? 0,
        user_round_count: event.user_round_count ?? 0,
        // Server-side summary_payloads is the source of truth; fall back to
        // whatever per-round events we buffered client-side.
        rounds: event.summaries && event.summaries.length
          ? event.summaries.map((s) => ({
              round: s.round,
              compressed_message_count: s.compressed_message_count,
              summary_text: s.summary_text || '',
            }))
          : (turn.pendingSummary?.rounds ?? []),
        ts: Date.now(),
      }
      if (!turn.compressions) turn.compressions = []
      turn.compressions.push(compression)
      turn.pendingSummary = null
      // Drive the sticky bar: snap to the post-compression value (which is
      // what the agent actually has now) and flash green so the user sees
      // the drop happen. The next llm_response event will overwrite
      // contextTokens with the authoritative API-reported number.
      if (Number.isFinite(event.after_tokens)) {
        contextTokens.value = event.after_tokens
      }
      setFlash('compressed', 1600)
      isCompacting.value = false
      break
    }
    case 'error': {
      busy.value = false
      turns.value[turnIdx].error = event.message
      // Freeze any step that was still ticking so the UI doesn't keep
      // counting forever after a failed run.
      const now = Date.now()
      for (const s of turns.value[turnIdx].steps) {
        if (s.elapsed_ms == null && s.started_at != null) {
          s.elapsed_ms = now - s.started_at
        }
        if (s.elapsed_ms != null && s.ended_at == null) {
          s.ended_at = now
        }
      }
      break
    }
  }
  // Auto-scroll as events stream in.
  scrollToBottom()
}

// ----- Send -----
async function send(text) {
  const content = (text ?? input.value).trim()
  if (!content || busy.value) return
  input.value = ''

  const turnIdx = turns.value.length
  turns.value.push({
    user: content,
    // Frontend wall-clock when the user clicked Send. Becomes the anchor for
    // step 1's `started_at` so the timer counts the full wait including the
    // round-trip before the first SSE event arrives.
    sent_at: Date.now(),
    steps: [],
    // Context-summary events emitted by the agent's _summarize_messages().
    // Each entry is the payload of a `context_summary_done` event; round-level
    // events stream in before the done event but are folded into it server-
    // side via the `summaries` field. Cards render at the top of the turn,
    // before any steps, in arrival order.
    compressions: [],
    error: null,
    final: null,  // populated on `done`
  })
  busy.value = true
  scrollToBottom()

  try {
    const final = await api.chatStream(content, (event) => handleEvent(turnIdx, event))
    if (final && final.type === 'done') {
      turns.value[turnIdx].final = final.reply
    } else if (final && final.type === 'error') {
      turns.value[turnIdx].error = final.message
    }
  } catch (err) {
    turns.value[turnIdx].error = err.message
  } finally {
    busy.value = false
    scrollToBottom()
  }
}

// Send on Enter, newline on Shift+Enter.
// `isComposing` guards against the IME candidate-confirm case: while
// the user is composing Chinese / Japanese / Korean text, Enter picks
// a candidate from the IME popup — it must NOT be treated as "send".
// Without this check, typing "ni hao" + Enter would fire send() with
// the raw pinyin and dismiss the IME's selection step.
function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
    e.preventDefault()
    send()
  }
}

function formatMs(ms) {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

// ----- Clear conversation -----
// Mirrors the CLI's /clear: drop the entire frontend turn history and tell
// the backend to reset Agent.messages to [system_prompt]. Workspace files
// and long-term notes (.agent_memory.json) are untouched — only the
// short-term conversation context is wiped.
//
// The actual network call lives in App.vue (props.onClear) so the
// parent's memory-refresh trigger can be bumped in lockstep —
// `clear` doesn't fire a `step_end` event, so without an explicit
// signal the memory panel would keep showing the stale conversation.
//
// The backend refuses with 409 if a run is in flight (see
// AgentRunner.clear / POST /api/chat/clear). We also disable the button
// when busy, so the 409 path is purely defensive.
async function clearConversation() {
  if (busy.value) return
  // Only prompt when there's actually something to clear — first-time
  // visitors shouldn't see a confirm dialog for an empty screen.
  if (turns.value.length > 0) {
    const ok = window.confirm(
      '清空当前会话？\n\nLLM 端的对话历史会被丢弃（短期记忆）。\n工作区文件和长期笔记不受影响。',
    )
    if (!ok) return
  }
  try {
    if (typeof props.onClear === 'function') {
      // Parent runs the network call + memory refresh trigger.
      await props.onClear()
    } else {
      // Fallback for unit tests / standalone usage: call API directly.
      await api.clearChat()
    }
    turns.value = []
    // Reset the sticky bar — /clear wipes messages on the backend, so the
    // context window is back to just the system prompt.
    contextTokens.value = 0
    setFlash(null, 0)
    scrollToBottom()
  } catch (err) {
    // 409 from backend (busy) bubbles up with the FastAPI `detail` as the
    // message; everything else is a generic network/server error.
    alert(`清空失败：${err.message}`)
  }
}

// Timer for the placeholder card (covers both `isThinking` and
// `isBetweenSteps`).
//   - isThinking: anchor = turn.sent_at (user click)
//   - isBetweenSteps: anchor = previous step's `ended_at` (when step_end
//     arrived on the frontend). Without this anchor the gap between
//     step_end(N) and step_start(N+1) would render as 0ms, which reads
//     as "instant" — wrong, since the LLM is genuinely working.
function formatPlaceholderElapsed() {
  void nowTick.value
  const turn = turns.value[turns.value.length - 1]
  if (!turn) return ''
  let anchor = null
  if (isBetweenSteps.value) {
    const lastStep = turn.steps[turn.steps.length - 1]
    anchor = lastStep ? lastStep.ended_at : null
  } else {
    anchor = turn.sent_at
  }
  if (anchor == null) return ''
  return formatMs(Date.now() - anchor)
}

// Final-reply preview (first 80 chars) used in the empty-state CTA row.
const lastFinalReply = computed(() => {
  for (let i = turns.value.length - 1; i >= 0; i--) {
    if (turns.value[i].final) return turns.value[i].final
  }
  return null
})
</script>

<template>
  <section class="chat-panel">
    <!-- Context-window indicator — moved OUT of the scroll region so it sits
         directly under the "试试这些能力" 能力卡片栏: a fixed strip at the
         very top of the chat panel, NOT inside the scrolling conversation.
         Three visual states:
           - .idle      : under the 80k threshold, neutral accent
           - .over      : over the threshold, persistent red pulse
           - .compress  : just received a `context_summary_done` event,
                          green flash for 1.6s to make the drop visible
         Token numbers can be misleading (see long discussion about
         double-counting in TurnSummaryCard), so we DON'T show any
         "savings" KPI here — only the raw current value vs limit. -->
    <div class="ctx-window" :class="barClass" :title="`当前上下文 ${formatTokens(contextTokens)} / ${formatTokens(tokenLimit)} tokens (驱动后端 summary 触发的 api_total_tokens 字段)`">
      <div class="ctx-window-row">
        <span class="ctx-window-icon" aria-hidden="true">📖</span>
        <span class="ctx-window-label">上下文</span>
        <div class="ctx-window-bar">
          <div
            class="ctx-window-fill"
            :style="fillStyle"
          ></div>
        </div>
        <span class="ctx-window-num">
          {{ formatTokens(contextTokens) }} / {{ formatTokens(tokenLimit) }}
        </span>
        <span class="ctx-window-pct">{{ Math.round(usagePercent) }}%</span>
        <span v-if="barClass === 'over'" class="ctx-window-tag over" title="超过 token_limit,本应在下一步触发 summary">
          ⚠ 超阈值
        </span>
        <span v-else-if="barClass === 'compress'" class="ctx-window-tag compress" title="刚刚发生了一次上下文压缩">
          ✓ 已压缩
        </span>
      </div>
    </div>

    <!-- Scroll region: holds all turns. This is the
         ONLY scrolling element now, so the `.chat-input` footer below stays
         pinned to the bottom of `.chat-panel` regardless of content height
         (previously the input relied on `position: sticky`, which didn't hold
         reliably in the flex-scroll layout). `panelEl` (used by
         scrollToBottom) points here. -->
    <div class="chat-scroll" ref="panelEl">
    <div class="chat-messages">
      <!-- Empty state -->
      <div v-if="!hasMessages && !busy" class="empty-state">
        <div class="empty-icon">💬</div>
        <div class="empty-title">从一个问题开始</div>
        <div class="empty-sub">
          输入任何任务描述，Agent 会自己决定怎么调工具完成任务。<br>
          不知道问什么？点下方任一能力卡片直接发起。
        </div>
      </div>

      <!-- One block per user→assistant exchange -->
      <div v-for="(turn, ti) in turns" :key="ti" class="turn">
        <!-- User bubble rendered directly here (NOT inside TurnContent) so
             .msg.user's `align-self: flex-end` aligns it to the right edge
             of the full chat width. Per-step raw I/O is now in
             <TurnContent>'s <StepRawDataPanel> entries, so the user bubble
             no longer carries its own raw-data toggle. -->
        <UserBlock :user="turn.user" />

        <!-- Context-summary cards: one per compression event in this turn.
             Rendered ABOVE the steps so the user immediately sees that
             memory was compressed before reading the new step output.
             Multiple compressions in a single turn stack vertically.
             v-if 守卫: 没有压缩过就不渲染(默认情况下大多数 turn 不会触发)。 -->
        <ContextSummaryCard
          v-for="(comp, ci) in (turn.compressions || [])"
          :key="`ctx-sum-${ci}`"
          :compression="comp"
          :index="ci + 1"
        />

        <!-- Flattened assistant-side conversation: a flat sequence of
             Thinking / Action / Observation / Assistant blocks, with one
             <StepRawDataPanel> after each step. -->
        <TurnContent :turn="turn" :turn-idx="ti" />

        <!-- Error placeholder (turn-level failure) -->
        <div v-if="turn.error" class="msg error">
          <div class="msg-role">⚠️ Error</div>
          <div>{{ turn.error }}</div>
        </div>

        <!-- Turn 总结卡: 每个 turn 末尾挂一份,默认折叠,展开看 token 柱状图
             + 工具分布。v-if 守卫: 至少 1 step 才渲染(0 step 由 error placeholder
             单独负责); error turn 也渲染,status 字段会显示"部分失败"。
             位置在所有 step-card 之后、placeholder 之前 —— 这样跑的过程中用户
             能看到 partial 数据,跑完后看到最终汇总。 -->
        <TurnSummaryCard
          v-if="turn.steps.length > 0"
          :steps="turn.steps"
          :error="turn.error || ''"
          :user-sent-at="turn.sent_at"
          @expand="scrollToBottom"
        />

        <!-- Placeholder card: shown both before the first step_start
             (isThinking) and between steps (isBetweenSteps, e.g. after
             step N's tool results are in but before step N+1's step_start
             arrives from the LLM round-trip). Auto-hides when a real step
             takes its place, the call errors, or the user cancels. -->
        <div
          v-if="(isThinking || isBetweenSteps) && ti === turns.length - 1"
          class="step-card thinking-card"
        >
          <div class="step-header thinking-header">
            <span class="pulse-dot" aria-hidden="true"></span>
            <span v-if="!isCompacting" class="step-num">Step {{ placeholderStepNum }}</span>
            <span class="thinking-phase">{{ isCompacting ? '正在折叠上下文，请稍后' : phase }}</span>
            <span class="step-elapsed ticking">{{ formatPlaceholderElapsed() }}</span>
          </div>
          <div class="step-body thinking-body">
            <div class="shimmer-row" style="width: 92%"></div>
            <div class="shimmer-row" style="width: 68%"></div>
            <div class="shimmer-row" style="width: 81%"></div>
            <div class="shimmer-row" style="width: 54%"></div>
          </div>
        </div>
      </div>
    </div>
    </div><!-- /.chat-scroll -->

    <!-- 紧凑能力 pill 条 —— 紧贴输入框上方, flex-shrink:0 让消息滚动时它不动 -->
    <ShowcaseGallery @select="handleShowcasePick" />

    <form class="chat-input" @submit.prevent="send()">
      <textarea
        ref="textareaEl"
        v-model="input"
        placeholder="输入任务描述（Enter 发送，Shift+Enter 换行）"
        @keydown="onKeydown"
        :disabled="busy"
        rows="1"
      />
      <!-- Clear button: disabled while a run is in flight so the user
           can't trigger the 409 path. Destructive action — sits to the
           left of the primary send button so the visual reading order
           is "destructive → primary", matching common OS conventions. -->
      <button
        type="button"
        class="clear-btn"
        :disabled="busy"
        :title="hasMessages ? '清空当前会话（短期记忆）' : '当前没有会话可清空'"
        @click="clearConversation"
      >🧹 清空</button>
      <button type="submit" :disabled="busy || !input.trim()">发送</button>
    </form>
  </section>
</template>