<script setup>
import StepLLMIOPanel from './StepLLMIOPanel.vue'

defineProps({
  // Anthropic wire payload sent to the LLM for this step.
  request: { type: Object, default: null },
  // Raw LLMResponse (content / thinking / tool_calls / usage) for this step.
  response: { type: Object, default: null },
})

const open = defineModel('open', { type: Boolean, default: false })
</script>

<template>
  <details v-if="request || response" class="step-raw-data" :open="open"
           @toggle="open = $event.target.open">
    <summary class="step-raw-data-summary">
      <span class="step-raw-data-icon" aria-hidden="true">📋</span>
      <span>查看本轮原始数据</span>
    </summary>
    <div class="step-raw-data-body">
      <div class="step-raw-data-col">
        <div class="step-raw-data-col-title">
          <span class="step-raw-data-col-icon" aria-hidden="true">📤</span>
          <span>发送给 LLM 的请求</span>
        </div>
        <StepLLMIOPanel :request="request" :response="null" />
      </div>
      <div class="step-raw-data-col">
        <div class="step-raw-data-col-title">
          <span class="step-raw-data-col-icon" aria-hidden="true">📥</span>
          <span>LLM 原始响应</span>
        </div>
        <StepLLMIOPanel :request="null" :response="response" />
      </div>
    </div>
  </details>
</template>