<script setup lang="ts">
// Adds or edits one edition of a book: the physical or digital thing that is
// read, with its ISBN, format, page count and whether it is owned.
import { computed, ref } from "vue";
import UiModal from "./UiModal.vue";
import PartialDateInput from "./PartialDateInput.vue";
import { addEdition, updateEdition } from "../services/books";
import type { Book, BookEdition, BookEditionInput } from "../types/book";
import type { DatePrecision } from "../utils/dates";
import {
  BOOK_DATE_YEARS,
  BOOK_FORMAT_OPTIONS,
  BOOK_OWNERSHIP_OPTIONS,
} from "../utils/bookLabels";
import { MAX_PAGES } from "../utils/bookProgress";

const props = defineProps<{ bookId: string; edition?: BookEdition | null }>();
const emit = defineEmits<{ saved: [book: Book]; closed: [] }>();

const e = props.edition;
const fields = ref({
  isbn: e?.isbn13 ?? e?.isbn10 ?? "",
  format: e?.format ?? "paperback",
  publisher: e?.publisher ?? "",
  publishDate: e?.publishDate ?? "",
  publishDatePrecision: (e?.publishDatePrecision ??
    null) as DatePrecision | null,
  pageCount: e?.pageCount ?? (null as number | null),
  language: e?.language ?? "",
  ownership: e?.ownership ?? "none",
  acquiredAt: e?.acquiredAt ?? "",
  note: e?.note ?? "",
});
const initial = JSON.stringify(fields.value);
const datesValid = ref({ publish: true, acquired: true });
const saving = ref(false);
const error = ref<string | null>(null);
const dirty = computed(() => JSON.stringify(fields.value) !== initial);
const canSave = computed(
  () => datesValid.value.publish && datesValid.value.acquired && !saving.value,
);

const text = (v: string) => v.trim() || null;

function toInput(): BookEditionInput {
  const f = fields.value;
  // one ISBN box: its length says which field it is; the API checks it and
  // fills in the other form
  const isbn = f.isbn.replace(/[\s-]/g, "");
  return {
    isbn13: isbn.length === 10 ? null : isbn || null,
    isbn10: isbn.length === 10 ? isbn : null,
    format: f.format,
    publisher: text(f.publisher),
    publishDate: f.publishDate || null,
    publishDatePrecision: f.publishDate ? f.publishDatePrecision : null,
    pageCount:
      f.pageCount === null || Number.isNaN(f.pageCount) ? null : f.pageCount,
    language: text(f.language),
    ownership: f.ownership,
    acquiredAt: f.acquiredAt || null,
    note: text(f.note),
  };
}

async function submit() {
  if (!canSave.value) return;
  saving.value = true;
  error.value = null;
  try {
    const book = props.edition
      ? await updateEdition(props.bookId, props.edition.id, toInput())
      : await addEdition(props.bookId, toInput());
    emit("saved", book);
  } catch (err) {
    error.value =
      err instanceof Error ? err.message : "Could not save the edition.";
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <UiModal
    :title="edition ? 'Edit edition' : 'Add an edition'"
    :dirty="dirty"
    @close="emit('closed')"
  >
    <form class="editor-grid" @submit.prevent="submit">
      <label class="field">
        <span>ISBN</span>
        <input
          v-model="fields.isbn"
          type="text"
          inputmode="numeric"
          maxlength="20"
          placeholder="ISBN-13 or ISBN-10"
        />
      </label>
      <label class="field">
        <span>Format</span>
        <select v-model="fields.format">
          <option
            v-for="option in BOOK_FORMAT_OPTIONS"
            :key="option.value"
            :value="option.value"
          >
            {{ option.label }}
          </option>
        </select>
      </label>
      <label class="field">
        <span>Publisher</span>
        <input v-model="fields.publisher" type="text" maxlength="200" />
      </label>
      <div class="field">
        <span>Published</span>
        <PartialDateInput
          v-model:date="fields.publishDate"
          v-model:precision="fields.publishDatePrecision"
          :years="BOOK_DATE_YEARS"
          @update:valid="datesValid.publish = $event"
        />
      </div>
      <label class="field">
        <span>Pages</span>
        <input
          v-model.number="fields.pageCount"
          type="number"
          min="1"
          :max="MAX_PAGES"
        />
      </label>
      <label class="field">
        <span>Language</span>
        <input
          v-model="fields.language"
          type="text"
          maxlength="16"
          placeholder="en"
        />
      </label>
      <label class="field">
        <span>Ownership</span>
        <select v-model="fields.ownership">
          <option
            v-for="option in BOOK_OWNERSHIP_OPTIONS"
            :key="option.value"
            :value="option.value"
          >
            {{ option.label }}
          </option>
        </select>
      </label>
      <div class="field">
        <span>Acquired on</span>
        <PartialDateInput
          v-model:date="fields.acquiredAt"
          :partial="false"
          :years="BOOK_DATE_YEARS"
          @update:valid="datesValid.acquired = $event"
        />
      </div>
      <label class="field full">
        <span>Note</span>
        <textarea v-model="fields.note" rows="2"></textarea>
      </label>
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
        {{ saving ? "Saving…" : edition ? "Save" : "Add edition" }}
      </button>
    </template>
  </UiModal>
</template>

<style scoped src="../styles/shared/editorForm.css"></style>
