<script setup>
/**
 * ThinkingBlock — displays the LLM's thinking content for a step.
 *
 * Rendering context:
 *   - This block only renders AFTER the LLM has returned its thinking
 *     content (TurnContent gates it on `step.thinking` being truthy).
 *   - The "waiting for thinking" affordance is owned by the separate
 *     `.thinking-loading` placeholder in TurnContent, NOT this block.
 *
 * Therefore this block has NO in-flight / loading state — adding one
 * would mislead the user (the thinking is already loaded by the time
 * this block paints). The block just renders the content cleanly.
 */

defineProps({
  text: { type: String, required: true },
})

const open = defineModel('open', { type: Boolean, default: false })
</script>

<template>
  <details class="flat-thinking" :open="open" @toggle="open = $event.target.open">
    <summary class="flat-summary">
      <span class="flat-icon" aria-hidden="true">🧠</span>
      <span>Thinking</span>
    </summary>
    <div class="flat-block-body">
      <pre class="thinking-text">{{ text }}</pre>
    </div>
  </details>
</template>