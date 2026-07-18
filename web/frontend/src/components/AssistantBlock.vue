<script setup>
import MarkdownView from './MarkdownView.vue'

defineProps({
  text: { type: String, required: true },
  // Kept on the prop signature so callers don't have to change, but
  // intentionally unused — per-step raw I/O now lives in
  // StepRawDataPanel (rendered after each step in TurnContent). This
  // component renders only the markdown content.
  responsePayload: { type: Object, default: null },
})
</script>

<template>
  <div class="msg assistant">
    <div class="msg-role">Assistant</div>
    <div class="msg-body">
      <MarkdownView :source="text" />
    </div>
  </div>
</template>