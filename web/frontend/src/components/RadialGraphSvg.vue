<script setup>
defineProps({
  // The geometry + edge data computed by the parent's
  // `radialLayout`. We don't compute anything ourselves — this is a
  // pure rendering component so it can be reused at any size (the
  // SVG is responsive via viewBox).
  layout: { type: Object, required: true },
  // Center entity name — used as the SVG's aria-label and as the
  // text inside the center node circle. The parent owns the
  // selection state.
  selectedEntityName: { type: String, required: true },
})

const emit = defineEmits([
  'select-entity',
  // Fired when the pointer enters/leaves the center node circle.
  // The parent uses these to show/hide the observation tooltip
  // via the shared `centerTooltip` Teleport. We expose them as
  // events (rather than letting the parent put a hover listener
  // on the SVG itself) because the user expects the tooltip to
  // follow the *center circle*, not the whole SVG bounding box.
  'center-hover-enter',
  'center-hover-leave',
])

function onCenterEnter(evt) {
  emit('center-hover-enter', evt)
}
function onCenterLeave(evt) {
  emit('center-hover-leave', evt)
}
</script>

<template>
  <svg
    class="radial-graph"
    :viewBox="layout.viewBox"
    preserveAspectRatio="xMidYMid meet"
    role="img"
    :aria-label="`以 ${selectedEntityName} 为中心的知识图谱,${layout.edges.length} 条关系`"
  >
    <!-- Arrowhead marker, used by every edge below.
         refX=9 places the tip at the line endpoint; the marker
         fills the same color as the edge so it reads as a
         continuation of the line. -->
    <defs>
      <marker
        id="radial-arrow"
        viewBox="0 0 10 10"
        refX="9"
        refY="5"
        markerWidth="5"
        markerHeight="5"
        orient="auto-start-reverse"
      >
        <path d="M 0 0 L 10 5 L 0 10 z" class="radial-arrow-fill" />
      </marker>
    </defs>

    <!-- Edges first so nodes render on top. Endpoints are swapped
         for incoming relations so the arrowhead (marker-end)
         always points AT the relation's `to` entity. -->
    <g class="radial-edges">
      <line
        v-for="(e, i) in layout.edges"
        :key="`edge-${i}`"
        :x1="e.x1" :y1="e.y1"
        :x2="e.x2" :y2="e.y2"
        marker-end="url(#radial-arrow)"
        :class="['radial-edge', { incoming: e.incoming }]"
      />
      <text
        v-for="(e, i) in layout.edges"
        :key="`edge-label-${i}`"
        :x="e.midX" :y="e.midY"
        class="radial-edge-label"
        :transform="`rotate(${e.rotation} ${e.midX} ${e.midY})`"
        text-anchor="middle"
        dominant-baseline="middle"
      >{{ e.label }}</text>
    </g>

    <!-- Center node: bigger, accented, with a subtle halo ring so
         it visually owns the center. Hover/focus pops up the
         observation tooltip via the same `<Teleport to="body">`
         mechanism used by the section-info icons. -->
    <g
      class="radial-center"
      tabindex="0"
      role="button"
      :aria-label="`${selectedEntityName} 的 observation`"
      @mouseenter="onCenterEnter"
      @mouseleave="onCenterLeave"
      @focus="onCenterEnter"
      @blur="onCenterLeave"
    >
      <circle
        :cx="layout.center.x"
        :cy="layout.center.y"
        :r="layout.center.r + 7"
        class="radial-center-halo"
      />
      <circle
        :cx="layout.center.x"
        :cy="layout.center.y"
        :r="layout.center.r"
      />
      <text
        :x="layout.center.x"
        :y="layout.center.y"
        text-anchor="middle"
        dominant-baseline="middle"
        class="radial-center-name"
      >{{ selectedEntityName }}</text>
    </g>

    <!-- Outer nodes: one per unique neighbor. Click to re-center
         the radial on that entity. Each node gets a deterministic
         color from `nodeColor()` so the same entity always renders
         the same hue. -->
    <g class="radial-outer">
      <g
        v-for="(n, i) in layout.outerNodes"
        :key="`outer-${i}`"
        :transform="`translate(${n.x}, ${n.y})`"
        class="radial-outer-node"
        :style="{ '--node-color': n.color }"
        @click="emit('select-entity', n.name)"
        role="button"
        tabindex="0"
        @keydown.enter="emit('select-entity', n.name)"
        @keydown.space.prevent="emit('select-entity', n.name)"
      >
        <circle :r="layout.outerR" />
        <text text-anchor="middle" dominant-baseline="middle">{{ n.name }}</text>
      </g>
    </g>
  </svg>
</template>