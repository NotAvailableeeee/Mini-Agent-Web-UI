<script setup>
defineProps({
  user: { type: String, required: true },
})
</script>

<template>
  <div class="msg user">
    <div class="msg-role">You</div>
    <div class="msg-body">{{ user }}</div>
  </div>
</template>