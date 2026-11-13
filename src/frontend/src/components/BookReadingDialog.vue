<script setup lang="ts">
// Starts, updates or records one read-through of a book: which edition, when
// it started and finished, and how far it got in pages or percent.
import { computed, ref } from "vue";
import UiModal from "./UiModal.vue";
import PartialDateInput from "./PartialDateInput.vue";
import { addReading, updateReading } from "../services/books";
import type {
  Book,
  BookReading,
  BookReadingInput,
  ProgressUnit,
} from "../types/book";
import {
  BOOK_DATE_YEARS,
  PROGRESS_UNIT_OPTIONS,
  READ_OUTCOME_OPTIONS,
  bookFormatLabel,
} from "../utils/bookLabels";
import { convertProgress, progressMax } from "../utils/bookProgress";

const props = defineProps<{ book: Book; reading?: BookReading | null }>();
const emit = defineEmits<{ saved: [book: Book]; closed: [] }>();

const r = props.reading;
const fields = ref({
  editionId: r ? r.editionId : (props.book.activeEditionId ?? null),
  startedOn: r?.startedOn ?? "",
  finishedOn: r?.finishedOn ?? "",
  progressUnit: (r?.progressUnit ?? "page") as ProgressUnit,
  progressValue: r?.progressValue ?? (null as number | null),
  outcome: r?.outcome ?? "reading",
});
const initial = JSON.stringify(fields.value);
const datesValid = ref({ started: true, finished: true });
const saving = ref(false);
const error = ref<string | null>(null);

const edition = computed(
  () =>
    props.book.editions.find((e) => e.id === fields.value.editionId) ?? null,
);
const max = computed(() =>
  progressMax(fields.value.progressUnit, edition.value),
);
const valueProblem = computed(() => {
  const v = fields.value.progressValue;
  if (v === null || Number.isNaN(v)) return null;
  if (v < 0) return "Progress cannot be negative.";
  if (v > max.value)
    return fields.value.progressUnit === "percent"
      ? "Percent is at most 100."
      : `This edition has ${max.value} pages.`;
  return null;
});
const dateProblem = computed(() => {
  const { startedOn, finishedOn } = fields.value;
  return startedOn && finishedOn && finishedOn < startedOn
    ? "A read cannot finish before it started."
    : null;
});
const dirty = computed(() => JSON.stringify(fields.value) !== initial);
const canSave = computed(
  () =>
    datesValid.value.started &&
    datesValid.value.finished &&
    !valueProblem.value &&
    !dateProblem.value &&
    !saving.value,
);

function setUnit(unit: ProgressUnit) {
  const f = fields.value;
  f.progressValue = convertProgress(
    f.progressValue,
    f.progressUnit,
    unit,
    edition.value?.pageCount,
  );
  f.progressUnit = unit;
}

function editionLabel(id: string): string {
  const e = props.book.editions.find((x) => x.id === id);
  if (!e) return "";
  return [bookFormatLabel(e.format), e.publisher, e.isbn13]
    .filter(Boolean)
    .join(" · ");
}

function toInput(): BookReadingInput {
  const f = fields.value;
  return {
    editionId: f.editionId,
    startedOn: f.startedOn || null,
    finishedOn: f.finishedOn || null,
    progressUnit: f.progressUnit,
    progressValue:
      f.progressValue === null || Number.isNaN(f.progressValue)
        ? null
        : f.progressValue,
    outcome: f.outcome,
  };
}

async function submit() {
  if (!canSave.value) return;
  saving.value = true;
  error.value = null;
  try {
    const book = props.reading
      ? await updateReading(props.book.id, props.reading.id, toInput())
      : await addReading(props.book.id, toInput());
    emit("saved", book);
  } catch (err) {
    error.value =
      err instanceof Error ? err.message : "Could not save the read.";
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <UiModal
    :title="reading ? 'Edit read' : 'Start a read'"
    :dirty="dirty"
    @close="emit('closed')"
  >
    <form class="editor-grid" @submit.prevent="submit">
      <label class="field full">
        <span>Edition</span>
        <select v-model="fields.editionId">
          <option :value="null">No particular edition</option>
          <option v-for="e in book.editions" :key="e.id" :value="e.id">
            {{ editionLabel(e.id) }}
          </option>
        </select>
      </label>
      <div class="field">
        <span>Started</span>
        <PartialDateInput
          v-model:date="fields.startedOn"
          :partial="false"
          :years="BOOK_DATE_YEARS"
          @update:valid="datesValid.started = $event"
        />
      </div>
      <div class="field">
        <span>Finished</span>
        <PartialDateInput
          v-model:date="fields.finishedOn"
          :partial="false"
          :years="BOOK_DATE_YEARS"
          @update:valid="datesValid.finished = $event"
        />
      </div>
      <fieldset class="field unit-switch">
        <legend>Progress in</legend>
        <div class="unit-options" role="radiogroup">
          <button
            v-for="option in PROGRESS_UNIT_OPTIONS"
            :key="option.value"
            type="button"
            role="radio"
            class="ui-btn ui-btn-sm"
            :class="
              fields.progressUnit === option.value
                ? 'ui-btn-primary'
                : 'ui-btn-secondary'
            "
            :aria-checked="fields.progressUnit === option.value"
            @click="setUnit(option.value)"
          >
            {{ option.label }}
          </button>
        </div>
      </fieldset>
      <label class="field">
        <span>{{
          fields.progressUnit === "percent" ? "Percent read" : "Page reached"
        }}</span>
        <input
          v-model.number="fields.progressValue"
          type="number"
          min="0"
          :max="max"
          :step="fields.progressUnit === 'percent' ? 0.1 : 1"
          :aria-invalid="!!valueProblem || undefined"
        />
        <small v-if="valueProblem" class="field-hint problem">{{
          valueProblem
        }}</small>
        <small
          v-else-if="fields.progressUnit === 'page' && edition?.pageCount"
          class="field-hint"
          >of {{ edition.pageCount }}</small
        >
      </label>
      <label class="field">
        <span>Outcome</span>
        <select v-model="fields.outcome">
          <option
            v-for="option in READ_OUTCOME_OPTIONS"
            :key="option.value"
            :value="option.value"
          >
            {{ option.label }}
          </option>
        </select>
      </label>
      <p v-if="dateProblem" class="ui-error full" role="alert">
        {{ dateProblem }}
      </p>
      <p v-if="error" class="ui-error full" role="alert">{{ error }}</p>
    </form>
    <template #footer>
      <button type="button" class="ui-btn ui-btn-ghost" @click="emit('closed')">
        Cancel
      </button>
      <button
        type="button"
        class="ui-btn ui-btn-primary"
        :disabled="!canSave"
        @click="submit"
      >
        {{ saving ? "Saving…" : reading ? "Save" : "Start read" }}
      </button>
    </template>
  </UiModal>
</template>

<style scoped src="../styles/shared/editorForm.css"></style>
<style scoped>
.unit-switch {
  border: 0;
  margin: 0;
  padding: 0;
}
.unit-switch legend {
  color: var(--ui-dim);
  font-size: 0.8rem;
  margin-bottom: 6px;
  padding: 0;
}
.unit-options {
  display: flex;
  gap: 6px;
}
.problem {
  color: var(--ui-danger);
}
</style>
