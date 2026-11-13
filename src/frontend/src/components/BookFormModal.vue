<script setup lang="ts">
// Adds or edits a book work (#265). A new book can be found through the
// installed book providers (by title, author or ISBN, C15 6.2) to fill the
// form in; every field stays editable and is saved only when the person adds
// the book. Editions and reads are managed on the book's own page.
import { computed, onBeforeUnmount, ref, watch } from "vue";
import UiModal from "./UiModal.vue";
import PartialDateInput from "./PartialDateInput.vue";
import MediaMetadataSearch from "./MediaMetadataSearch.vue";
import { addEdition, createBook, updateBook } from "../services/books";
import type { MetadataBookEdition } from "../services/metadata";
import type { Book, BookInput, BookStatus } from "../types/book";
import { formatPartialDate } from "../utils/dates";
import type { DatePrecision } from "../utils/dates";
import { splitList } from "../utils/formLists";
import {
  BOOK_DATE_YEARS,
  BOOK_STATUS_OPTIONS,
  authorLine,
} from "../utils/bookLabels";
import {
  bookMetadataResult,
  editionInput,
  matchedEdition,
  providerEditionTitle,
} from "../utils/bookMetadata";
import type { BookMetadataResult } from "../utils/bookMetadata";
import { useMetadataSearch } from "../utils/useMetadataSearch";

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

// Provider search (new books only). The identities of the result applied last
// are saved with the book; the edition an ISBN search named is added with it.
const search = useMetadataSearch<BookMetadataResult>({
  mediaType: "book",
  convert: bookMetadataResult,
  noun: "book",
});
const providerIds = ref<Record<string, string>>({});
const pendingEdition = ref<MetadataBookEdition | null>(null);
// The query the chosen result answered: only an ISBN query names an edition.
const pickedQuery = ref("");
// The chosen result's cover is shown through the host's artwork preview, which
// fetches only the selected result's own asset URLs, so the browser contacts no
// provider host before the person commits anything.
const coverPreview = ref<string | null>(null);
function clearCoverPreview() {
  if (coverPreview.value) URL.revokeObjectURL(coverPreview.value);
  coverPreview.value = null;
}
onBeforeUnmount(() => {
  search.stop();
  clearCoverPreview();
});
const searchResults = computed(() =>
  search.results.value.map((result) => ({
    key: result.candidateId,
    title: result.title,
    provider: result.provider,
    detail: [
      authorLine(result.authors.slice(0, 3)),
      result.firstPublished
        ? formatPartialDate(
            result.firstPublished,
            result.firstPublishedPrecision,
          )
        : "",
    ]
      .filter(Boolean)
      .join(" · "),
  })),
);
const found = computed(() => search.selected.value);
const foundPublished = computed(() =>
  found.value?.firstPublished
    ? formatPartialDate(
        found.value.firstPublished,
        found.value.firstPublishedPrecision,
      )
    : "",
);
const foundEdition = computed(() =>
  found.value ? matchedEdition(found.value, pickedQuery.value) : null,
);
watch(
  () => found.value?.coverUrl ?? null,
  async (url) => {
    clearCoverPreview();
    if (!url) return;
    try {
      const blob = await search.previewArtwork(url);
      if (found.value?.coverUrl === url) {
        clearCoverPreview();
        coverPreview.value = URL.createObjectURL(blob);
      }
    } catch {
      // no preview: the cover URL is still filled in when the details are used
    }
  },
);
function pickResult(key: string) {
  const result = search.results.value.find((r) => r.candidateId === key);
  if (!result) return;
  pickedQuery.value = search.query.value;
  void search.select(result).catch(() => {});
}
function applyResult() {
  const result = found.value;
  if (!result) return;
  const f = fields.value;
  const keep = (incoming: string | null, current: string) =>
    incoming ?? current;
  f.title = result.title || f.title;
  f.subtitle = keep(result.subtitle, f.subtitle);
  if (result.authors.length) f.authors = result.authors.join(", ");
  if (result.firstPublished) {
    f.firstPublished = result.firstPublished;
    f.firstPublishedPrecision = result.firstPublishedPrecision;
  }
  f.language = keep(result.language, f.language);
  f.seriesName = keep(result.seriesName, f.seriesName);
  f.seriesPosition = keep(result.seriesPosition, f.seriesPosition);
  if (result.genres.length) f.genres = result.genres.join(", ");
  f.coverUrl = keep(result.coverUrl, f.coverUrl);
  f.description = keep(result.description, f.description);
  // replaced, not merged: one book carries one work's identities
  providerIds.value = { ...result.providerIds };
  pendingEdition.value = matchedEdition(result, pickedQuery.value);
  search.applied(result, []);
  search.stop();
}
function cancelResult() {
  search.stop();
}

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
    ...(props.book ? {} : { providerIds: providerIds.value }),
  };
}

async function submit() {
  if (!canSave.value) return;
  saving.value = true;
  error.value = null;
  try {
    const input = toInput();
    let saved = props.book
      ? await updateBook(props.book.id, input)
      : await createBook(input);
    if (!props.book && pendingEdition.value) {
      try {
        saved = await addEdition(saved.id, editionInput(pendingEdition.value));
      } catch {
        // the work is saved; the edition can still be added from its page
      }
    }
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
      <MediaMetadataSearch
        v-if="!book"
        v-model:query="search.query.value"
        media-type="book"
        label="Find the book"
        noun="book"
        placeholder="Search by title, author or ISBN"
        :results="searchResults"
        :searching="search.searching.value"
        :enriching-media="search.enrichingMedia.value"
        :message="search.message.value"
        :warnings="search.warnings.value"
        @search="search.run"
        @pick="pickResult"
      />
      <section v-if="found" class="found-book" aria-label="Book found">
        <img v-if="coverPreview" :src="coverPreview" alt="" />
        <div class="found-text">
          <strong>{{ found.title }}</strong>
          <span v-if="found.subtitle">{{ found.subtitle }}</span>
          <span v-if="found.authors.length" class="dim">{{
            authorLine(found.authors)
          }}</span>
          <span class="dim"
            >{{ found.provider
            }}<template v-if="foundPublished">
              · first published {{ foundPublished }}</template
            ><template v-if="found.editions.length">
              · {{ found.editions.length }}
              {{
                found.editions.length === 1 ? "edition" : "editions"
              }}</template
            ></span
          >
          <span v-if="foundEdition" class="dim"
            >Also adds the edition you searched for:
            {{ providerEditionTitle(foundEdition) }} (ISBN
            {{ foundEdition.isbn_13 }})</span
          >
        </div>
        <div class="found-actions">
          <button
            type="button"
            class="ui-btn ui-btn-primary"
            @click="applyResult"
          >
            Use these details
          </button>
          <button
            type="button"
            class="ui-btn ui-btn-ghost"
            @click="cancelResult"
          >
            Back to results
          </button>
        </div>
      </section>
      <p v-if="pendingEdition && !found" class="hint" role="status">
        The edition with ISBN {{ pendingEdition.isbn_13 }} is added with the
        book.
      </p>
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
.found-book {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-start;
  padding: 12px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  background: var(--ui-surface);
}
.found-book img {
  width: 56px;
  height: 84px;
  object-fit: cover;
  border-radius: var(--ui-radius-control);
}
.found-text {
  display: flex;
  flex: 1 1 220px;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  overflow-wrap: anywhere;
}
.found-text .dim,
.hint {
  color: var(--ui-dim);
  font-size: var(--ui-font-small);
}
.found-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
</style>
