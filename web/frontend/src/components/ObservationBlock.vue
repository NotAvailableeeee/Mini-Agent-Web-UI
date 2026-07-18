<script setup>
import { computed } from 'vue'

const props = defineProps({
  toolCall: { type: Object, required: true },
})

// Class only — the icon is always 👀, but the CSS still distinguishes
// pending (dashed) / success (default) / error (red) via border + bg.
const state = computed(() => {
  if (!props.toolCall.result) return 'pending'
  return props.toolCall.result.success ? 'success' : 'error'
})

const stateLabel = computed(() => {
  if (state.value === 'pending') return 'Observation (waiting…)'
  return 'Observation'
})

// Trim leading blank lines from the rendered observation text so a stray
// "\n" at the very top of a tool result doesn't push the first useful
// line down with an empty line of wasted space. Only strips the
// leading run — internal blank lines are preserved.
const trimmedContent = computed(() => {
  const raw = props.toolCall?.result?.content
  if (typeof raw !== 'string') return ''
  return raw.replace(/^(?:\r?\n)+/, '')
})

const trimmedError = computed(() => {
  const raw = props.toolCall?.result?.error
  if (typeof raw !== 'string') return ''
  return raw.replace(/^(?:\r?\n)+/, '')
})

// Shimmer widths for the pending body — fixed at three rows of varying
// lengths so the visual "is still loading" hint is stable across calls
// (no jitter on re-render). Mirrors the `.shimmer-row` widths used by
// the thinking-loading placeholder in TurnContent.vue.
const PENDING_SHIMMER_WIDTHS = ['78%', '55%', '64%']

const open = defineModel('open', { type: Boolean, default: false })
</script>

<template>
  <details class="flat-observation" :class="state" :open="open"
           @toggle="open = $event.target.open">
    <summary class="flat-summary">
      <span class="flat-icon" aria-hidden="true">👀</span>
      <span>{{ stateLabel }}</span>
    </summary>
    <div class="flat-block-body">
      <pre v-if="toolCall.result && toolCall.result.success"
           class="v-pre result-pre">{{ trimmedContent }}</pre>
      <pre v-else-if="toolCall.result" class="v-pre result-pre">{{ trimmedError }}</pre>
      <div v-else class="result-pending">
        <div class="result-pending-header">
          <span class="pulse-dot" aria-hidden="true"></span>
          <span>Waiting for result…</span>
        </div>
        <!-- Shimmer rows mirror the thinking-loading placeholder so the
             "still loading" affordance reads the same across both. Widths
             are static (no random) so re-renders don't jitter. -->
        <div
          v-for="w in PENDING_SHIMMER_WIDTHS"
          :key="w"
          class="shimmer-row"
          :style="{ width: w }"
        ></div>
      </div>
    </div>
  </details>
</template>