<script setup lang="ts">
// The one segmented pill control used everywhere a small set of options
// is switched: the Movies / TV / Anime switcher, the Lists button, the
// library layout tabs, and the tab rows on Calendar, Statistics,
// Notifications and Lists. Same 4px container, 30px options, 7px option
// radius and type size every time. An option is a button, or a link when
// it has `to`. `icon` is inner SVG markup from our own code, drawn 15px.
export interface SegmentOption {
  value: string;
  label: string;
  icon?: string;
  count?: number;
  to?: string;
  title?: string;
}

import { computed } from "vue";
import { onRovingKeydown, rovingTabIndex } from "../utils/rovingFocus";

const props = defineProps<{
  options: SegmentOption[];
  modelValue?: string;
  ariaLabel?: string;
  // show only the icons; the label stays as the accessible name
  iconOnly?: boolean;
}>();
const emit = defineEmits<{ "update:modelValue": [value: string] }>();
// Buttons are one Tab stop with arrow keys between them (UX-AUDIT-APP A15);
// links stay ordinary links, each its own stop.
const buttons = computed(() => props.options.every((option) => !option.to));
const activeIndex = computed(() =>
  props.options.findIndex((option) => option.value === props.modelValue),
);
</script>

<template>
  <div
    class="seg"
    :class="{ 'icon-only': iconOnly }"
    :role="buttons ? 'toolbar' : 'group'"
    :aria-label="ariaLabel"
    @keydown="buttons && onRovingKeydown($event)"
  >
    <template v-for="(o, index) in options" :key="o.value">
      <RouterLink
        v-if="o.to"
        :to="o.to"
        class="seg-tab"
        :class="{ active: modelValue === o.value }"
        :title="o.title"
        :aria-label="iconOnly ? o.label : undefined"
      >
        <svg
          v-if="o.icon"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
          v-html="o.icon"
        ></svg>
        <span class="seg-label">{{ o.label }}</span>
        <span v-if="o.count" class="seg-count">{{ o.count }}</span>
      </RouterLink>
      <button
        v-else
        type="button"
        class="seg-tab"
        :class="{ active: modelValue === o.value }"
        :title="o.title"
        :aria-pressed="modelValue === o.value"
        :aria-label="iconOnly ? o.label : undefined"
        :tabindex="buttons ? rovingTabIndex(index, activeIndex) : undefined"
        data-roving
        @click="emit('update:modelValue', o.value)"
      >
        <svg
          v-if="o.icon"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="2"
          stroke-linecap="round"
          stroke-linejoin="round"
          aria-hidden="true"
          v-html="o.icon"
        ></svg>
        <span class="seg-label">{{ o.label }}</span>
        <span v-if="o.count" class="seg-count">{{ o.count }}</span>
      </button>
    </template>
  </div>
</template>

<style scoped>
.seg {
  display: inline-flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: 4px;
  background: var(--ui-surface);
  border-radius: var(--ui-radius-row);
}
.seg-tab {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  box-sizing: border-box;
  min-height: var(--ui-control-height);
  padding: 0 14px;
  border: none;
  border-radius: var(--ui-radius-control);
  background: transparent;
  color: var(--ui-dim);
  font-family: inherit;
  font-size: 0.8rem;
  font-weight: 700;
  line-height: 1;
  white-space: nowrap;
  text-decoration: none;
  cursor: pointer;
  transition:
    background 0.15s ease,
    color 0.15s ease;
}
.seg-tab:hover {
  color: var(--ui-text);
}
.seg-tab.active {
  background: var(--ui-accent);
  color: var(--ui-on-accent);
}
.seg-tab svg {
  width: 15px;
  height: 15px;
  flex-shrink: 0;
  display: block;
}
.icon-only .seg-tab {
  padding-inline: 10px;
}
.icon-only .seg-label {
  display: none;
}
.seg-count {
  font-size: 0.68rem;
  opacity: 0.7;
}
</style>
