<script setup lang="ts">
// A compact "where does this value come from" label beside a metadata field.
// It appears when sources disagree, when the field is locked, or when a
// preferred source is not the one shown; selecting it lists what each source
// reports, with the current value marked and a "Prefer this source" action
// (#348, #349). A lock still wins: the list says so instead of hiding it.
import { computed, onBeforeUnmount, ref, watch, type CSSProperties } from "vue";
import {
  clearFieldSource,
  preferFieldSource,
  type FieldSourceEntry,
  type PreferenceResult,
} from "../../services/gameFieldSources";
import {
  FIELD_LABELS,
  currentSourceLabel,
  formatSourceValue,
  preferenceAwaitingValue,
  preferenceNotInEffect,
  showsFieldSource,
  sourceLabelFor,
} from "../../utils/fieldSources";

const props = defineProps<{
  gameId: string;
  field: string;
  entry?: FieldSourceEntry;
}>();
const emit = defineEmits<{ changed: [] }>();

const open = ref(false);
const chip = ref<HTMLButtonElement | null>(null);
// The list floats above the page (position: fixed) so opening it never moves
// the title, the credits or a facts grid, and the hero's clipping cannot cut
// it off. It sits under the label, kept inside the window; on a phone the
// stylesheet docks it to the bottom of the screen instead.
const listStyle = ref<CSSProperties>({});
function placeList(): void {
  const el = chip.value;
  if (!el || window.matchMedia?.("(max-width: 640px)").matches) {
    listStyle.value = {};
    return;
  }
  const rect = el.getBoundingClientRect();
  const width = Math.min(448, window.innerWidth - 32);
  const left = Math.min(
    Math.max(16, rect.left),
    window.innerWidth - width - 16,
  );
  const below = window.innerHeight - rect.bottom - 22;
  const above = rect.top - 22;
  const flip = below < 200 && above > below;
  listStyle.value = {
    left: `${left}px`,
    width: `${width}px`,
    maxHeight: `${Math.max(120, Math.min(384, flip ? above : below))}px`,
    ...(flip
      ? { bottom: `${window.innerHeight - rect.top + 6}px` }
      : { top: `${rect.bottom + 6}px` }),
  };
}
watch(open, (isOpen) => {
  if (isOpen) {
    placeList();
    window.addEventListener("scroll", placeList, true);
    window.addEventListener("resize", placeList);
  } else {
    window.removeEventListener("scroll", placeList, true);
    window.removeEventListener("resize", placeList);
  }
});
onBeforeUnmount(() => {
  window.removeEventListener("scroll", placeList, true);
  window.removeEventListener("resize", placeList);
});
const busy = ref<string | null>(null);
const error = ref<string | null>(null);
// the server saved a preference but kept the value shown (applied: false):
// what to tell the person, from the PUT's `outcome`
const notApplied = ref<string | null>(null);

const visible = computed(() => showsFieldSource(props.entry));
const fieldName = computed(() => FIELD_LABELS[props.field] ?? props.field);
const ignored = computed(() => preferenceNotInEffect(props.entry));
const awaiting = computed(() => preferenceAwaitingValue(props.entry));
const preferredLabel = computed(() =>
  props.entry?.preferred
    ? sourceLabelFor(props.entry, props.entry.preferred)
    : "",
);
const chipTitle = computed(() => {
  if (props.entry?.locked) return `${fieldName.value}: locked`;
  if (ignored.value)
    return `${fieldName.value}: ${preferredLabel.value} is preferred but not shown`;
  return `${fieldName.value}: sources disagree`;
});

function notAppliedMessage(result: PreferenceResult, label: string): string {
  if (result.outcome === "unchanged")
    return "Preference saved. The value shown already matches it.";
  if (result.outcome === "locked")
    return "Preference saved. This field is locked, so the value shown stays until you unlock it.";
  if (result.outcome === "awaiting_value")
    return `Preference saved. ${label} has not reported a value for this field yet; the next refresh that reports one applies it.`;
  return "Preference saved. The value shown did not change.";
}

async function run(
  key: string,
  action: () => Promise<PreferenceResult | void>,
): Promise<void> {
  busy.value = key;
  error.value = null;
  notApplied.value = null;
  try {
    const result = await action();
    if (result && !result.applied)
      notApplied.value = notAppliedMessage(
        result,
        props.entry ? sourceLabelFor(props.entry, key) : key,
      );
    emit("changed");
  } catch (cause) {
    error.value =
      cause instanceof Error ? cause.message : "That change did not work.";
  } finally {
    busy.value = null;
  }
}

const prefer = (key: string) =>
  run(key, () => preferFieldSource(props.gameId, props.field, key));
const clear = () =>
  run("clear", () => clearFieldSource(props.gameId, props.field));
</script>

<template>
  <span
    v-if="visible && entry"
    class="field-source"
    @keydown.esc.stop="open = false"
  >
    <button
      :id="`field-source-${field}`"
      ref="chip"
      type="button"
      class="source-chip"
      :class="{ locked: entry.locked, warn: ignored && !entry.locked }"
      :aria-expanded="open"
      :title="chipTitle"
      @click="open = !open"
    >
      <svg
        v-if="entry.locked"
        class="chip-icon"
        viewBox="0 0 24 24"
        width="11"
        height="11"
        fill="none"
        stroke="currentColor"
        stroke-width="2.4"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <rect x="5" y="11" width="14" height="10" rx="2" />
        <path d="M8 11V7a4 4 0 0 1 8 0v4" />
      </svg>
      <span v-if="entry.locked" class="visually-hidden">Locked, </span>
      {{ currentSourceLabel(entry) }}
    </button>
    <div
      v-if="open"
      class="source-list"
      :style="listStyle"
      role="group"
      :aria-label="`${fieldName} sources`"
    >
      <p v-if="entry.locked" class="note">
        This field is locked, so a refresh keeps the current value.
      </p>
      <p v-else-if="ignored && entry.origin === 'user'" class="note">
        You prefer {{ preferredLabel }} here, but the value shown was edited by
        hand.
      </p>
      <p v-else-if="ignored && awaiting" class="note">
        You prefer {{ preferredLabel }} here, but it has not reported a value
        for this field yet. The next refresh that reports one applies it.
      </p>
      <p v-else-if="ignored" class="note">
        You prefer {{ preferredLabel }} here, but the value shown comes from
        {{ currentSourceLabel(entry) }}.
      </p>
      <ul>
        <li v-for="source in entry.sources" :key="source.sourceKey">
          <div class="row-head">
            <strong>{{ source.label }}</strong>
            <span v-if="source.current" class="tag">Current</span>
            <span
              v-if="entry.preferred === source.sourceKey"
              class="tag preferred"
              >Preferred</span
            >
          </div>
          <p class="value">{{ formatSourceValue(source.value) }}</p>
          <button
            v-if="entry.preferred !== source.sourceKey"
            type="button"
            class="ui-btn ui-btn-secondary"
            :disabled="busy !== null"
            @click="prefer(source.sourceKey)"
          >
            {{ busy === source.sourceKey ? "Saving…" : "Prefer this source" }}
          </button>
        </li>
      </ul>
      <button
        v-if="entry.preferred"
        type="button"
        class="ui-btn ui-btn-secondary"
        :disabled="busy !== null"
        @click="clear"
      >
        Clear preference
      </button>
      <p v-if="notApplied" class="note" role="status">{{ notApplied }}</p>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
    </div>
  </span>
</template>

<style scoped>
.field-source {
  display: inline-block;
  margin-left: 6px;
  position: relative;
  vertical-align: middle;
}
.source-chip {
  align-items: center;
  display: inline-flex;
  gap: 4px;
  background: transparent;
  border: 1px solid var(--ui-border);
  border-radius: 999px;
  color: var(--ui-dim);
  cursor: pointer;
  font: inherit;
  font-size: 0.72rem;
  font-weight: 600;
  line-height: 1.4;
  /* a label, not a control row: keep it the height of the text it sits in,
     wherever a page gives buttons a minimum control height */
  min-height: 0;
  min-width: 0;
  padding: 1px 8px;
}
.source-chip.locked {
  border-color: var(--ui-border-strong);
  color: var(--ui-text);
}
.source-chip.warn {
  background: var(--ui-warning-soft);
  border-color: var(--ui-warning);
  color: var(--ui-warning);
}
.chip-icon {
  flex: none;
}
.visually-hidden {
  clip: rect(0 0 0 0);
  height: 1px;
  overflow: hidden;
  position: absolute;
  white-space: nowrap;
  width: 1px;
}
/* placed by placeList(); on a phone it docks to the bottom of the screen */
.source-list {
  background: var(--ui-surface, var(--ui-bg));
  border: 1px solid var(--ui-border);
  border-radius: 10px;
  box-shadow: 0 10px 30px rgb(0 0 0 / 18%);
  display: grid;
  font-size: 0.85rem;
  gap: 8px;
  max-height: min(24rem, 70vh);
  overflow-y: auto;
  padding: 10px 12px;
  position: fixed;
  z-index: 60;
  /* inside a section title or the hero credits, keep body text */
  color: var(--ui-text);
  font-weight: 400;
  letter-spacing: normal;
  text-align: left;
  text-transform: none;
}

@media (max-width: 640px) {
  .source-list {
    bottom: 16px;
    left: 16px;
    max-height: 60vh;
    right: 16px;
    top: auto;
    width: auto;
  }
}

ul {
  display: grid;
  gap: 8px;
  list-style: none;
  margin: 0;
  padding: 0;
}
.row-head {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.value {
  color: var(--ui-dim);
  margin: 2px 0 4px;
  overflow-wrap: anywhere;
}
.note {
  color: var(--ui-dim);
  margin: 0;
}
.tag {
  border: 1px solid var(--ui-border);
  border-radius: 999px;
  color: var(--ui-dim);
  font-size: 0.72rem;
  font-weight: 600;
  padding: 1px 8px;
}
.tag.preferred {
  background: var(--ui-accent-soft);
  border-color: var(--ui-accent-line);
  color: var(--ui-text);
}
.error {
  color: var(--ui-danger);
  margin: 0;
}
</style>
