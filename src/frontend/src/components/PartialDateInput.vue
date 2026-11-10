<script setup lang="ts">
// A release date typed in the person's own date format (SPEC-UI-POLISH-PASS
// §7): a year, a month or a full day. The placeholder shows the order the
// person uses, a "Read as" line echoes how the text was understood, and an
// input that reads two ways in that order (03/04/2011) asks which was meant
// before the form can save it. The value stays ISO: `date` is the first day
// of the period ("2011-04-01") and `precision` says how much is known.
import { computed, ref, useId, watch } from "vue";
import {
  formatDay,
  parseEntry,
  placeholder,
  type DatePrecision,
  type EntryResult,
  type PartialValue,
} from "../utils/dates";

const props = withDefaults(
  defineProps<{
    // "YYYY-MM-DD", or a partial "YYYY" / "YYYY-MM"; "" for none
    date: string;
    precision?: DatePrecision | null;
    // false: a full day only
    partial?: boolean;
    // the years a typed date may fall in (a book's first publication can be
    // centuries old); the formatter's own range when left out
    years?: [number, number];
    id?: string;
  }>(),
  { precision: null, partial: true, years: undefined, id: undefined },
);
const emit = defineEmits<{
  "update:date": [value: string];
  "update:precision": [value: DatePrecision | null];
  // false while the text cannot be saved (ambiguous or not a date)
  "update:valid": [value: boolean];
}>();

const fallbackId = useId();
const inputId = computed(() => props.id ?? fallbackId);
const hintId = `${fallbackId}-hint`;

// what a value looks like in the person's format, as precise as it is known
const shown = (date: string, precision: DatePrecision | null) =>
  formatDay(date, precision);
// the unambiguous long form, used to tell two readings apart
const spelled = (value: PartialValue) =>
  formatDay(value.text, value.precision, {
    date_format: "long",
    time_format: null,
    date_format_chosen: true,
  });

// a stored value is already settled: it is shown, never asked about again
function stored(date: string, precision: DatePrecision | null): EntryResult {
  if (!date) return { kind: "empty" };
  const known: DatePrecision =
    precision === "year" || date.length === 4
      ? "year"
      : precision === "month" || date.length === 7
        ? "month"
        : "day";
  const text = date.slice(0, { year: 4, month: 7, day: 10 }[known]);
  const start = `${text}-01-01`.slice(0, 10);
  return { kind: "ok", value: { text, date: start, precision: known } };
}

const text = ref(props.date ? shown(props.date, props.precision) : "");
const result = ref<EntryResult>(stored(props.date, props.precision));
const picked = ref<string | null>(null);
let lastEmitted = props.date;

function publish(value: PartialValue | null) {
  const date = value?.date ?? "";
  const precision = value ? value.precision : null;
  lastEmitted = date;
  emit("update:date", date);
  emit("update:precision", precision === "day" ? null : precision);
}

function read(input: string) {
  const parsed = parseEntry(input, undefined, {
    partial: props.partial,
    ...(props.years
      ? { partialYears: props.years, dayYears: props.years }
      : {}),
  });
  result.value = parsed;
  picked.value = null;
  if (parsed.kind === "ok") publish(parsed.value);
  else if (parsed.kind === "empty") publish(null);
  emit("update:valid", parsed.kind === "ok" || parsed.kind === "empty");
}

function choose(value: PartialValue) {
  picked.value = value.text;
  publish(value);
  emit("update:valid", true);
}

// a value set from outside (a metadata suggestion, a reset) replaces the text
watch(
  () => [props.date, props.precision] as const,
  ([date, precision]) => {
    if (date === lastEmitted) return;
    lastEmitted = date;
    text.value = date ? shown(date, precision) : "";
    result.value = stored(date, precision);
    picked.value = null;
    emit("update:valid", true);
  },
);

const hint = computed(() => {
  const r = result.value;
  if (r.kind === "ok") return `Read as ${spelled(r.value)}`;
  if (r.kind === "error") return r.message;
  if (r.kind === "ambiguous")
    return picked.value
      ? `Read as ${spelled(r.options.find((o) => o.text === picked.value)!)}`
      : "This could be either date. Choose one:";
  return props.partial ? "Enter only what is known." : "";
});
</script>

<template>
  <div class="partial-date" :class="`is-${result.kind}`">
    <input
      :id="inputId"
      v-model="text"
      type="text"
      inputmode="text"
      maxlength="40"
      autocomplete="off"
      :placeholder="placeholder(undefined, partial)"
      :aria-describedby="hintId"
      :aria-invalid="result.kind === 'error' || undefined"
      @input="read(text)"
    />
    <small :id="hintId" class="partial-date-hint" aria-live="polite">{{
      hint
    }}</small>
    <fieldset
      v-if="result.kind === 'ambiguous'"
      class="partial-date-choice"
      :aria-describedby="hintId"
    >
      <legend class="sr-only">Which date did you mean?</legend>
      <label v-for="option in result.options" :key="option.text">
        <input
          type="radio"
          :name="`${fallbackId}-reading`"
          :value="option.text"
          :checked="picked === option.text"
          @change="choose(option)"
        />
        {{ spelled(option) }}
      </label>
    </fieldset>
  </div>
</template>

<style scoped>
.partial-date {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}
.partial-date > input {
  box-sizing: border-box;
  width: 100%;
  min-height: var(--ui-control-height);
  padding: 8px 11px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  background: var(--ui-surface);
  color: var(--ui-text);
  font: inherit;
  font-size: 0.875rem;
}
.partial-date > input:focus {
  outline: none;
  border-color: var(--ui-accent-line);
  box-shadow: 0 0 0 1px var(--ui-accent-line);
}
.partial-date.is-error > input {
  border-color: var(--ui-danger);
}
.partial-date-hint {
  color: var(--ui-dim);
  font-size: 0.78rem;
  line-height: 1.5;
}
.partial-date.is-error .partial-date-hint {
  color: var(--ui-danger);
}
.partial-date-choice {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 16px;
  margin: 0;
  padding: 8px 10px;
  border: 1px solid var(--ui-accent-line);
  border-radius: var(--ui-radius-control);
  background: var(--ui-surface-2);
  font-size: 0.875rem;
}
.partial-date-choice label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  cursor: pointer;
}
.partial-date-choice input {
  accent-color: var(--ui-accent);
}
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
}
</style>
