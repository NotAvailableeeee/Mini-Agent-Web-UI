<script setup>
import { computed, reactive } from 'vue'
import ThinkingBlock from './ThinkingBlock.vue'
import ActionBlock from './ActionBlock.vue'
import ObservationBlock from './ObservationBlock.vue'
import AssistantBlock from './AssistantBlock.vue'
import StepRawDataPanel from './StepRawDataPanel.vue'

const props = defineProps({
  turn: { type: Object, required: true },
  turnIdx: { type: Number, required: true },
})

// Flatten ONE step's data into a `events[]` array for rendering.
// Order per step: Thinking → Assistant → (Action → Observation)*
//
// Action/Observation are paired by sharing the same toolCall reference;
// the user reads them as adjacent items in the list.
//
// `stepInFlight` is intentionally NOT passed to ThinkingBlock anymore —
// the block only renders after the LLM has returned its thinking
// content, so any "still loading" affordance on it would be misleading
// (thinking is already done; the step might just be waiting for tool
// results). The "waiting for thinking" state is handled separately by
// the `.thinking-loading` placeholder above. ObservationBlock gets its
// own in-flight signal via CSS (`.flat-observation.pending`) so we don't
// need to thread `stepInFlight` through here either.
function eventsForStep(step) {
  const out = []
  if (step.thinking) {
    out.push({ kind: 'thinking', step })
  }
  if (step.content) {
    out.push({ kind: 'assistant', step })
  }
  for (const tc of step.tool_calls) {
    out.push({ kind: 'action', step, toolCall: tc })
    out.push({ kind: 'observation', step, toolCall: tc })
  }
  return out
}

// A step is "loading" once step_start has arrived but no `thinking`
// content has streamed yet. The placeholder renders shimmer bars so
// the user sees the round-trip is in progress; once `thinking` arrives
// it's replaced by the real <ThinkingBlock>.
function isStepLoading(step) {
  return (
    step.started_at != null &&
    step.elapsed_ms == null &&
    !step.thinking
  )
}

function hasRawData(step) {
  return step.request_payload != null || step.response_payload != null
}

// Per-block open/closed state for the React-flow blocks, keyed by
// `${stepIdx}-${eventIdx}` so collapse state survives across turns
// but doesn't collide between steps.
const blockOpen = reactive({})

// Per-step raw-data panel state. One entry per step so collapse state
// is independent across steps in the same turn.
const rawDataOpen = reactive({})
</script>

<template>
  <div class="turn-content">
    <!-- For each step: render the React-flow blocks. Before the step's
         content appears, show a loading thinking placeholder (pulse +
         shimmer) so the user always sees "this step is in progress"
         from the moment step_start arrives, until thinking content
         streams in. -->
    <template v-for="(step, si) in turn.steps" :key="`step-${si}`">
      <!-- Per-step loading placeholder. Renders ONLY when the step is
           in flight AND no thinking has arrived yet — once thinking
           streams in, the real <ThinkingBlock> takes its place. -->
      <div v-if="isStepLoading(step)" class="thinking-loading">
        <div class="thinking-loading-header">
          <span class="pulse-dot" aria-hidden="true"></span>
          <span class="flat-icon" aria-hidden="true">🧠</span>
          <span>Thinking</span>
          <!-- Right-aligned waiting label, mirrors
               `.result-pending-header` in ObservationBlock so both
               "still loading" affordances read identically. -->
          <span class="thinking-loading-waiting">Waiting for thinking…</span>
        </div>
        <div class="thinking-loading-body">
          <!-- Three shimmer rows, widths match the observation-pending
               pending body so the "still waiting" affordance reads the
               same across both blocks. Static widths — no jitter on
               re-render. -->
          <div class="shimmer-row" style="width: 78%"></div>
          <div class="shimmer-row" style="width: 55%"></div>
          <div class="shimmer-row" style="width: 64%"></div>
        </div>
      </div>

      <template v-for="(ev, bi) in eventsForStep(step)" :key="`ev-${si}-${bi}`">
        <ThinkingBlock
          v-if="ev.kind === 'thinking'"
          :text="ev.step.thinking"
          v-model:open="blockOpen[`${si}-${bi}`]"
        />
        <ActionBlock
          v-else-if="ev.kind === 'action'"
          :tool-call="ev.toolCall"
          v-model:open="blockOpen[`${si}-${bi}`]"
        />
        <ObservationBlock
          v-else-if="ev.kind === 'observation'"
          :tool-call="ev.toolCall"
          v-model:open="blockOpen[`${si}-${bi}`]"
        />
        <AssistantBlock
          v-else-if="ev.kind === 'assistant'"
          :text="ev.step.content"
        />
      </template>

      <!-- One raw-data panel per step, rendered AFTER that step's
           events. Default collapsed; expanding shows the request +
           response side by side. -->
      <StepRawDataPanel
        v-if="hasRawData(step)"
        :request="step.request_payload"
        :response="step.response_payload"
        v-model:open="rawDataOpen[si]"
      />
    </template>
  </div>
</template>