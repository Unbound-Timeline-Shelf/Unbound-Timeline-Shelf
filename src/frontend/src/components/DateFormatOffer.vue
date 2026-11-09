<script setup lang="ts">
// The one-time first-visit date-format offer (SPEC-UI-POLISH-PASS §7.4).
// Everyone sees dates as dd/mm/yyyy until they choose; a browser that looks
// American (en-US, or a US time zone) is asked once which format to use.
// Any answer, closing the dialog included, is saved with the account's
// preferences, so it never comes back on any device.
import { ref } from "vue";
import UiModal from "./UiModal.vue";
import { preferences } from "../state/preferences";
import { currentUser } from "../state/auth";
import { queuePreferences } from "../services/preferences";
import type { Preferences } from "../services/preferences";
import { formatDay, type DateStyle } from "../utils/dates";

const accountId = currentUser.value?.id;
// 14 March 2011: day and month cannot be confused in the examples
const SAMPLE = "2011-03-14";
const OPTIONS: { style: DateStyle; name: string }[] = [
  { style: "mdy", name: "Month / day / year" },
  { style: "long", name: "Day, month name, year" },
  { style: "dmy", name: "Day / month / year" },
  { style: "iso", name: "ISO (year-month-day)" },
];
const example = (style: DateStyle) =>
  formatDay(SAMPLE, null, {
    date_format: style,
    time_format: null,
    date_format_chosen: true,
  });

const saving = ref(false);
const error = ref("");

async function save(changes: Partial<Preferences>) {
  if (saving.value || currentUser.value?.id !== accountId) return;
  saving.value = true;
  error.value = "";
  try {
    const result = await queuePreferences({
      ...changes,
      date_format_offered: true,
    });
    if (currentUser.value?.id === accountId && result.latest)
      preferences.value = result.prefs;
  } catch (reason) {
    if (currentUser.value?.id === accountId)
      error.value =
        reason instanceof Error
          ? reason.message
          : "Could not save your choice.";
  } finally {
    saving.value = false;
  }
}

const choose = (style: DateStyle) =>
  save({ date_format: style, date_format_chosen: true });
// closing keeps the default and never asks again
const dismiss = () => save({});
</script>

<template>
  <UiModal
    class="date-offer"
    title="How should dates look?"
    description="Your browser looks like it is set up for the United States. Choose how every date in the app is shown. You can change this any time in Settings, Media Preferences."
    @close="dismiss"
  >
    <div class="offer-options" role="group" aria-label="Date format">
      <button
        v-for="option in OPTIONS"
        :key="option.style"
        type="button"
        class="offer-option"
        :class="{ current: preferences.date_format === option.style }"
        :disabled="saving"
        :data-style="option.style"
        @click="choose(option.style)"
      >
        <strong>{{ example(option.style) }}</strong>
        <span
          >{{ option.name
          }}<template v-if="preferences.date_format === option.style">
            · current</template
          ></span
        >
      </button>
    </div>
    <p class="offer-note">
      Typing a date with only numbers is easier to misread month first:
      03/04/2011 is 4 March in one order and 3 April in the other. The app asks
      when a typed date could be either, and always shows a month on its own by
      name, as "March 2011", never as 03/2011.
    </p>
    <p v-if="error" class="offer-error" role="alert">{{ error }}</p>
  </UiModal>
</template>

<style scoped>
.offer-options {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}
@media (max-width: 520px) {
  .offer-options {
    grid-template-columns: minmax(0, 1fr);
  }
}
.offer-option {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  min-height: 64px;
  padding: 12px 14px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  background: var(--ui-surface-2);
  color: var(--ui-text);
  font: inherit;
  text-align: left;
  cursor: pointer;
}
.offer-option strong {
  font-size: 1.05rem;
  font-variant-numeric: tabular-nums;
}
.offer-option span {
  color: var(--ui-dim);
  font-size: var(--ui-font-small);
}
.offer-option:hover:not(:disabled),
.offer-option:focus-visible {
  border-color: var(--ui-accent-line);
  outline: none;
  box-shadow: 0 0 0 1px var(--ui-accent-line);
}
.offer-option.current {
  border-color: var(--ui-accent-line);
  background: var(--ui-accent-soft);
}
.offer-note {
  margin: 14px 0 0;
  color: var(--ui-dim);
  font-size: var(--ui-font-small);
  line-height: 1.5;
}
.offer-error {
  margin: 10px 0 0;
  color: var(--ui-danger);
}
</style>
