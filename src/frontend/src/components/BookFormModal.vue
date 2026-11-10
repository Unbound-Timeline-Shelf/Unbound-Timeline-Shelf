<script setup lang="ts">
// Adds or edits a book work (#265 phase 1). There is no book metadata
// provider yet, so every field is typed by hand; editions and reads are
// managed on the book's own page.
import { computed, ref } from "vue";
import UiModal from "./UiModal.vue";
import PartialDateInput from "./PartialDateInput.vue";
import { createBook, updateBook } from "../services/books";
import type { Book, BookInput, BookStatus } from "../types/book";
import type { DatePrecision } from "../utils/dates";
import { splitList } from "../utils/formLists";
import { BOOK_DATE_YEARS, BOOK_STATUS_OPTIONS } from "../utils/bookLabels";

const props = defineProps<{ book?: Book | null }>();
const emit = defineEmits<{ saved: [book: Book]; closed: [] }>();

function blank() {
  const b = props.book;
  return {
    title: b?.title ?? "",
    subtitle: b?.subtitle ?? "",
    authors: b ? b.authors.join(", ") : "",
    firstPublished: b?.firstPublished ?? "",
    firstPublishedPrecision: (b?.firstPublishedPrecision ??
      null) as DatePrecision | null,
    language: b?.language ?? "",
    seriesName: b?.seriesName ?? "",
    seriesPosition: b?.seriesPosition ?? "",
    genres: b ? b.genres.join(", ") : "",
    tags: b ? b.tags.join(", ") : "",
    status: (b?.status ?? "wishlist") as BookStatus,
    coverUrl: b?.coverUrl ?? "",
    description: b?.description ?? "",
    rating: b?.ratingOverall ?? (null as number | null),
  };
}

const fields = ref(blank());
const initial = JSON.stringify(fields.value);
const dateValid = ref(true);
const saving = ref(false);
const error = ref<string | null>(null);
const dirty = computed(() => JSON.stringify(fields.value) !== initial);
const canSave = computed(
  () => !!fields.value.title.trim() && dateValid.value && !saving.value,
);

const text = (v: string) => v.trim() || null;

function toInput(): BookInput {
  const f = fields.value;
  return {
    title: f.title.trim(),
    subtitle: text(f.subtitle),
    authors: splitList(f.authors),
    firstPublished: f.firstPublished || null,
    firstPublishedPrecision: f.firstPublished
      ? f.firstPublishedPrecision
      : null,
    language: text(f.language),
    seriesName: text(f.seriesName),
    seriesPosition: text(f.seriesPosition),
    genres: splitList(f.genres),
    tags: splitList(f.tags),
    status: f.status,
    coverUrl: text(f.coverUrl),
    description: text(f.description),
    ratingOverall:
      f.rating === null || Number.isNaN(f.rating) ? null : f.rating,
  };
}

async function submit() {
  if (!canSave.value) return;
  saving.value = true;
  error.value = null;
  try {
    const input = toInput();
    const saved = props.book
      ? await updateBook(props.book.id, input)
      : await createBook(input);
    emit("saved", saved);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "Could not save the book.";
  } finally {
    saving.value = false;
  }
}
</script>

<template>
  <UiModal
    :title="book ? 'Edit book' : 'Add a book'"
    size="wide"
    :dirty="dirty"
    @close="emit('closed')"
  >
    <form class="book-form" @submit.prevent="submit">
      <div class="editor-grid">
        <label class="field full">
          <span>Title</span>
          <input
            v-model="fields.title"
            type="text"
            maxlength="500"
            required
            placeholder="The Lantern Archive"
          />
        </label>
        <label class="field full">
          <span>Subtitle</span>
          <input v-model="fields.subtitle" type="text" maxlength="300" />
        </label>
        <label class="field full">
          <span>Authors (comma-separated)</span>
          <input v-model="fields.authors" type="text" />
        </label>
        <div class="field">
          <span>First published</span>
          <PartialDateInput
            v-model:date="fields.firstPublished"
            v-model:precision="fields.firstPublishedPrecision"
            :years="BOOK_DATE_YEARS"
            @update:valid="dateValid = $event"
          />
        </div>
        <label class="field">
          <span>Status</span>
          <select v-model="fields.status">
            <option
              v-for="option in BOOK_STATUS_OPTIONS"
              :key="option.value"
              :value="option.value"
            >
              {{ option.label }}
            </option>
          </select>
        </label>
        <label class="field">
          <span>Series</span>
          <input v-model="fields.seriesName" type="text" maxlength="200" />
        </label>
        <label class="field">
          <span>Number in series</span>
          <input
            v-model="fields.seriesPosition"
            type="text"
            maxlength="16"
            placeholder="2"
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
          <span>Rating (0-10)</span>
          <input
            v-model.number="fields.rating"
            type="number"
            min="0"
            max="10"
            step="0.5"
          />
        </label>
        <label class="field">
          <span>Genres (comma-separated)</span>
          <input v-model="fields.genres" type="text" />
        </label>
        <label class="field">
          <span>Tags (comma-separated)</span>
          <input v-model="fields.tags" type="text" />
        </label>
        <label class="field full">
          <span>Cover image URL</span>
          <input
            v-model="fields.coverUrl"
            type="url"
            maxlength="2048"
            placeholder="https://"
          />
        </label>
        <label class="field full">
          <span>Description</span>
          <textarea v-model="fields.description" rows="4"></textarea>
        </label>
      </div>
      <p v-if="error" class="ui-error" role="alert">{{ error }}</p>
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
        {{ saving ? "Saving…" : book ? "Save" : "Add book" }}
      </button>
    </template>
  </UiModal>
</template>

<style scoped src="../styles/shared/editorForm.css"></style>
<style scoped>
.book-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
</style>
