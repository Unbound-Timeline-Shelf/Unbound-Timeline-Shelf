<script setup lang="ts">
// Book list imports (C15 6.2, #265 phase 4): Goodreads, StoryGraph or a list of
// ISBNs. The file is read first and shown; nothing is added until Import.
import { computed, ref } from "vue";
import SegmentedControl from "./SegmentedControl.vue";
import {
  importBooks,
  isbnListFile,
  previewBookImport,
} from "../../services/bookImports";
import type {
  BookImportPreview,
  BookImportResult,
  BookImportSource,
} from "../../services/bookImports";
import { formatDisplayDate } from "../../utils/dates";
import { bookStatusLabel } from "../../utils/bookLabels";
import type { BookStatus } from "../../types/book";

type Source = Exclude<BookImportSource, "auto">;

const SOURCES: { value: Source; label: string; how: string }[] = [
  {
    value: "goodreads",
    label: "Goodreads",
    how: "Use the CSV from Goodreads' Import and export page (Export Library). Shelves, ratings (doubled onto the 10-point scale), read dates and counts, your review and private notes, and the edition's ISBN, format, publisher and pages come across.",
  },
  {
    value: "storygraph",
    label: "StoryGraph",
    how: "Use the CSV from StoryGraph's Manage account page (Export StoryGraph library). Read status, every start and finish date, star rating, review, tags, format and ISBN come across.",
  },
  {
    value: "isbn_list",
    label: "ISBN list",
    how: "Paste ISBNs or choose a text file with them, one or more per line (ISBN-10 or ISBN-13, hyphens allowed, # starts a comment). Each becomes a book to read.",
  },
];

const source = ref<Source>("goodreads");
const info = computed(
  () => SOURCES.find((s) => s.value === source.value) ?? SOURCES[0],
);
const pasted = ref("");
const busy = ref(false);
const error = ref<string | null>(null);
const file = ref<File | null>(null);
const preview = ref<BookImportPreview | null>(null);
const result = ref<BookImportResult | null>(null);
const matchProviders = ref(true);

function reset() {
  file.value = null;
  preview.value = null;
  error.value = null;
}

function changeSource(value: string) {
  source.value = value as Source;
  reset();
  result.value = null;
}

async function read(chosen: File) {
  busy.value = true;
  error.value = null;
  result.value = null;
  preview.value = null;
  try {
    preview.value = await previewBookImport(chosen, source.value);
    file.value = chosen;
  } catch (err) {
    error.value =
      err instanceof Error ? err.message : "Failed to read the file";
  } finally {
    busy.value = false;
  }
}

async function onFileSelected(event: Event) {
  const input = event.target as HTMLInputElement;
  const chosen = input.files?.[0];
  input.value = "";
  if (chosen) await read(chosen);
}

async function previewPasted() {
  if (pasted.value.trim()) await read(isbnListFile(pasted.value));
}

async function runImport() {
  if (!file.value) return;
  busy.value = true;
  error.value = null;
  try {
    result.value = await importBooks(
      file.value,
      source.value,
      matchProviders.value && hasProviders.value,
    );
    reset();
    pasted.value = "";
  } catch (err) {
    error.value =
      err instanceof Error ? err.message : "Failed to import the books";
  } finally {
    busy.value = false;
  }
}

const hasProviders = computed(() => Boolean(preview.value?.providers.length));
const providerNames = computed(() =>
  (preview.value?.providers ?? []).map((p) => p.name).join(", "),
);
const batched = computed(() =>
  (preview.value?.providers ?? []).some((p) => p.batched),
);
const hiddenRows = computed(() =>
  preview.value ? preview.value.total - preview.value.rows.length : 0,
);

function statusLabel(status: string): string {
  return bookStatusLabel(status.toLowerCase().replace(/_/g, " ") as BookStatus);
}
function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}
</script>

<template>
  <div class="tile book-import">
    <h3>Import books</h3>
    <div class="book-source">
      <SegmentedControl
        :model-value="source"
        :options="SOURCES.map((s) => ({ value: s.value, label: s.label }))"
        @update:model-value="changeSource"
      />
    </div>
    <p class="tile-desc">
      {{ info.how }} You see what the file holds before anything is added. Books
      already in your library (same ISBN, or same title and author) are kept
      exactly as they are.
    </p>

    <div v-if="error" class="form-error" role="alert">{{ error }}</div>

    <div v-if="result" class="form-success" data-testid="book-import-result">
      Added {{ plural(result.created, "book") }}, kept {{ result.kept }} already
      in your library.
      <template v-if="result.matched_by_isbn || result.matched_by_title">
        Identified {{ result.matched_by_isbn }} by ISBN and
        {{ result.matched_by_title }} by title and author.
      </template>
      <template v-if="result.manual">
        {{ plural(result.manual, "book was", "books were") }} added from the
        file's data alone.
      </template>
      <template v-if="result.not_looked_up">
        {{ result.not_looked_up }} were not looked up (the lookup limit for one
        import was reached); import the file again to try them.
      </template>
      <template v-if="result.failed_providers.length">
        {{ result.failed_providers.join(", ") }} could not be reached; those
        books were added from the file.
      </template>
      <template v-if="result.invalid_isbn">
        {{ plural(result.invalid_isbn, "row") }} had an ISBN with a wrong check
        digit, which was left out.
      </template>
      <ul v-if="result.errors.length" class="import-errors">
        <li v-for="(err, i) in result.errors" :key="i">{{ err }}</li>
      </ul>
    </div>

    <div v-if="preview" class="book-review" data-testid="book-import-preview">
      <p class="tile-desc">
        {{ plural(preview.total, "book") }} in the file:
        <strong>{{ preview.new }} new</strong>,
        {{ preview.already_here }} already in your library<template
          v-if="preview.repeated"
          >, {{ preview.repeated }} repeated</template
        >. {{ preview.with_isbn }} with an ISBN.
        <template v-if="preview.invalid_isbn">
          {{ plural(preview.invalid_isbn, "ISBN") }} with a wrong check digit
          will be left out.
        </template>
        <template v-if="preview.invalid_entries.length">
          Not ISBNs: {{ preview.invalid_entries.join(", ") }}.
        </template>
      </p>
      <ul class="book-rows">
        <li
          v-for="row in preview.rows"
          :key="`${row.line}-${row.isbn_13}-${row.title}`"
          :class="{ here: row.already_here }"
        >
          <span class="book-title">{{
            row.title ?? `ISBN ${row.isbn_13}`
          }}</span>
          <span class="book-meta">
            <template v-if="row.authors.length">{{
              row.authors.join(", ")
            }}</template>
            <template v-if="row.isbn_13"> · {{ row.isbn_13 }}</template>
            · {{ statusLabel(row.status) }}
            <template v-if="row.last_read">
              · read {{ formatDisplayDate(row.last_read) }}</template
            >
          </span>
          <span v-if="row.already_here" class="book-badge">In library</span>
        </li>
      </ul>
      <p v-if="hiddenRows > 0" class="book-more">
        and {{ plural(hiddenRows, "more book") }}.
      </p>
      <label v-if="hasProviders" class="book-match">
        <input v-model="matchProviders" type="checkbox" />
        <span>
          Identify the new books through {{ providerNames }}: ISBN
          first<template v-if="batched">, up to 200 in one request</template>,
          then title and author. Your file's data is kept; a book no provider
          knows is still added.
        </span>
      </label>
      <p v-else class="tile-desc">
        No book metadata provider is set up, so the new books are added from the
        file's data alone. Add one under Metadata Sources to identify them.
      </p>
      <div class="book-actions">
        <button
          type="button"
          class="primary-button"
          :disabled="busy || preview.new === 0"
          @click="runImport"
        >
          {{ busy ? "Importing…" : `Import ${plural(preview.new, "book")}` }}
        </button>
        <button
          type="button"
          class="secondary-button"
          :disabled="busy"
          @click="reset"
        >
          Cancel
        </button>
      </div>
    </div>

    <template v-else>
      <div v-if="source === 'isbn_list'" class="book-paste">
        <label for="book-isbn-list" class="book-paste-label">ISBNs</label>
        <textarea
          id="book-isbn-list"
          v-model="pasted"
          rows="4"
          maxlength="200000"
          placeholder="9780000000019&#10;0-000-00001-9"
        />
        <button
          type="button"
          class="secondary-button"
          :disabled="busy || !pasted.trim()"
          @click="previewPasted"
        >
          {{ busy ? "Reading…" : "Preview list" }}
        </button>
      </div>
      <label class="secondary-button upload-label">
        {{ busy ? "Reading…" : "Choose file…" }}
        <input
          type="file"
          :accept="
            source === 'isbn_list' ? '.txt,.csv,text/plain' : '.csv,text/csv'
          "
          class="hidden-input"
          :disabled="busy"
          @change="onFileSelected"
        />
      </label>
    </template>
  </div>
</template>

<style scoped>
.tile {
  background: var(--ui-bg);
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  padding: 18px 20px;
  margin-bottom: 16px;
}
.tile h3 {
  margin: 0 0 6px;
  font-size: 0.9rem;
  color: var(--ui-text);
}
.tile-desc {
  color: var(--ui-dim);
  font-size: 0.8rem;
  line-height: 1.5;
  margin: 0 0 14px;
}
.book-source {
  margin-bottom: 12px;
}
.book-review {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.book-rows {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 360px;
  overflow-y: auto;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
}
.book-rows > li {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 2px 10px;
  padding: 9px 12px;
  border-bottom: 1px solid var(--ui-surface-2);
}
.book-rows > li:last-child {
  border-bottom: none;
}
.book-rows > li.here {
  opacity: 0.7;
}
.book-title {
  color: var(--ui-text);
  font-size: 0.85rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.book-meta {
  grid-column: 1;
  color: var(--ui-dim);
  font-size: 0.75rem;
  overflow-wrap: anywhere;
}
.book-badge {
  grid-column: 2;
  grid-row: 1 / span 2;
  align-self: center;
  color: var(--ui-accent-text);
  font-size: 0.72rem;
  font-weight: 600;
}
.book-more {
  margin: 0;
  color: var(--ui-dim);
  font-size: 0.75rem;
}
.book-match {
  display: flex;
  gap: 8px;
  color: var(--ui-dim);
  font-size: 0.8rem;
  line-height: 1.5;
}
.book-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.book-paste {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 10px;
}
.book-paste-label {
  color: var(--ui-text);
  font-size: 0.8rem;
  font-weight: 600;
}
.book-paste textarea {
  width: 100%;
  box-sizing: border-box;
  font: inherit;
  font-size: 0.85rem;
  background: var(--ui-surface);
  color: var(--ui-text);
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  padding: 8px 10px;
  resize: vertical;
}
.book-paste .secondary-button {
  align-self: flex-start;
}
.primary-button {
  background: var(--ui-accent);
  color: var(--ui-on-accent);
  border: none;
  border-radius: var(--ui-radius-control);
  padding: 11px 20px;
  font-weight: 600;
  cursor: pointer;
}
.primary-button:disabled,
.secondary-button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.secondary-button {
  background: color-mix(in srgb, var(--ui-text) 8%, transparent);
  color: var(--ui-text);
  border: none;
  border-radius: var(--ui-radius-control);
  padding: 10px 16px;
  font-size: 0.82rem;
  font-weight: 600;
  cursor: pointer;
}
.upload-label {
  display: inline-block;
  cursor: pointer;
}
.hidden-input {
  display: none;
}
.form-error {
  color: var(--ui-error);
  font-size: 13px;
  background: rgba(220, 38, 38, 0.1);
  border: 1px solid rgba(220, 38, 38, 0.3);
  border-radius: var(--ui-radius-control);
  padding: 8px 10px;
  margin-bottom: 12px;
}
.form-success {
  color: var(--ui-good);
  font-size: 13px;
  background: rgba(34, 197, 94, 0.1);
  border: 1px solid rgba(34, 197, 94, 0.3);
  border-radius: var(--ui-radius-control);
  padding: 8px 10px;
  margin-bottom: 12px;
}
.import-errors {
  margin: 8px 0 0;
  padding-left: 18px;
  color: var(--ui-error);
  font-size: 12px;
}
</style>
