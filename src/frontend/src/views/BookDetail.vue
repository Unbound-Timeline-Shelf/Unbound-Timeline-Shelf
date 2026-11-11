<script setup lang="ts">
// One book (#265): the work, its editions and its read-throughs. A book
// found through a provider can list that provider's editions, and "Add this
// edition" copies one into the work's own editions (C15 6.2). Every date on
// the page goes through the shared formatter, at the precision that is known.
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { latestRequest } from "../utils/latestRequest";
import { usePageTitle } from "../state/pageTitle";
import { useConfirm } from "../state/dialog";
import RetryMessage from "../components/RetryMessage.vue";
import MediaTopBar from "../components/MediaTopBar.vue";
import BackButton from "../components/BackButton.vue";
import MyNote from "../components/MyNote.vue";
import BookFormModal from "../components/BookFormModal.vue";
import { localBookCover } from "../utils/mediaImages";
import BookEditionDialog from "../components/BookEditionDialog.vue";
import BookReadingDialog from "../components/BookReadingDialog.vue";
import {
  addEdition,
  getBook,
  peekBook,
  updateBook,
  deleteBook,
  removeEdition,
  removeReading,
} from "../services/books";
import type { MetadataBookEdition } from "../services/metadata";
import { useMetadataSearch } from "../composables/useMetadataSearch";
import {
  bookDate,
  editionInLibrary,
  editionInput,
  providerEditionTitle,
} from "../utils/bookMetadata";
import type { Book, BookEdition, BookReading, BookStatus } from "../types/book";
import { formatDay, formatPartialDate } from "../utils/dates";
import {
  BOOK_STATUS_OPTIONS,
  authorLine,
  bookFormatLabel,
  ownershipLabel,
  readOutcomeLabel,
} from "../utils/bookLabels";
import {
  bookProgress,
  bookProgressLabel,
  readProgressText,
} from "../utils/bookProgress";

const route = useRoute();
const router = useRouter();
const confirm = useConfirm();
const bookId = computed(() => route.params.id as string);

const book = ref<Book | null>(null);
usePageTitle(() => book.value?.title);
const loading = ref(true);
const error = ref<string | null>(null);
const actionError = ref<string | null>(null);
const staleNotice = ref(false);
const editing = ref(false);
const editionDialog = ref<{ edition: BookEdition | null } | null>(null);
const readingDialog = ref<{ reading: BookReading | null } | null>(null);

const loadGuard = latestRequest();
async function load() {
  const token = loadGuard.start();
  const id = bookId.value;
  error.value = null;
  staleNotice.value = false;
  const cached = peekBook(id);
  if (cached) {
    book.value = cached;
    loading.value = false;
  } else loading.value = true;
  try {
    const fresh = await getBook(id);
    if (!loadGuard.isLatest(token)) return;
    book.value = fresh;
  } catch (e) {
    if (!loadGuard.isLatest(token)) return;
    if (cached) staleNotice.value = true;
    else error.value = e instanceof Error ? e.message : "Failed to load book.";
  } finally {
    if (loadGuard.isLatest(token)) loading.value = false;
  }
}
watch(bookId, load, { immediate: true });

// The provider editions of this work, looked up on request from the identities
// it was added with. Nothing is saved until the person adds one.
const lookup = useMetadataSearch(ref(""), "book");
const lookupStarted = ref(false);
const lookupError = ref<string | null>(null);
const hasProviderIds = computed(
  () => !!book.value && Object.keys(book.value.providerIds).length > 0,
);
const providerEditions = computed(
  () => lookup.selected.value?.metadata.book?.editions ?? [],
);
const lookupBusy = computed(
  () => lookupStarted.value && lookup.enrichingMedia.value,
);
const lookupProvider = computed(
  () => lookup.selected.value?.provider_name ?? "",
);
async function findEditions() {
  const b = book.value;
  if (!b) return;
  lookupStarted.value = true;
  lookupError.value = null;
  try {
    await lookup.focus(b.title, b.providerIds);
  } catch (e) {
    lookupError.value =
      e instanceof Error ? e.message : "Could not look up editions.";
  }
}
watch(bookId, () => {
  lookup.stop();
  lookupStarted.value = false;
  lookupError.value = null;
});
function providerEditionFacts(edition: MetadataBookEdition): string[] {
  const [date, precision] = bookDate(edition.publish_date);
  return [
    edition.isbn_13
      ? `ISBN ${edition.isbn_13}`
      : edition.isbn_10
        ? `ISBN ${edition.isbn_10}`
        : "",
    edition.page_count ? `${edition.page_count} pages` : "",
    date ? formatPartialDate(date, precision) : "",
    edition.language ?? "",
  ].filter(Boolean);
}
async function addProviderEdition(edition: MetadataBookEdition) {
  const b = book.value;
  if (!b) return;
  await apply(
    () => addEdition(b.id, editionInput(edition)),
    "Could not add the edition.",
  );
}

function goBack() {
  if (window.history.length > 1) router.back();
  else void router.push("/books");
}

// Runs a change that answers with the whole work, keeping the page as it
// was and saying why when it fails.
async function apply(change: () => Promise<Book>, failed: string) {
  actionError.value = null;
  try {
    book.value = await change();
  } catch (e) {
    actionError.value = e instanceof Error ? e.message : failed;
  }
}

const published = computed(() =>
  book.value
    ? formatPartialDate(
        book.value.firstPublished,
        book.value.firstPublishedPrecision,
      )
    : "",
);
const series = computed(() => {
  const b = book.value;
  if (!b?.seriesName) return "";
  return b.seriesPosition
    ? `${b.seriesName} #${b.seriesPosition}`
    : b.seriesName;
});
const progress = computed(() => (book.value ? bookProgress(book.value) : 0));
const progressText = computed(() =>
  book.value ? bookProgressLabel(book.value) : "",
);
// newest first: a reread sits above the first read
const reads = computed(() =>
  book.value
    ? [...book.value.reads].sort((a, b) =>
        (b.startedOn ?? "").localeCompare(a.startedOn ?? ""),
      )
    : [],
);

function editionOf(read: BookReading): BookEdition | null {
  return book.value?.editions.find((e) => e.id === read.editionId) ?? null;
}
function editionTitle(e: BookEdition): string {
  return [bookFormatLabel(e.format), e.publisher].filter(Boolean).join(" · ");
}
function editionFacts(e: BookEdition): string[] {
  return [
    e.isbn13 ? `ISBN ${e.isbn13}` : e.isbn10 ? `ISBN ${e.isbn10}` : "",
    e.pageCount ? `${e.pageCount} pages` : "",
    e.publishDate
      ? formatPartialDate(e.publishDate, e.publishDatePrecision)
      : "",
    e.language ?? "",
    ownershipLabel(e.ownership),
    e.acquiredAt ? `acquired ${formatDay(e.acquiredAt)}` : "",
  ].filter(Boolean);
}
function readDates(read: BookReading): string {
  const start = read.startedOn ? formatDay(read.startedOn) : "";
  const end = read.finishedOn ? formatDay(read.finishedOn) : "";
  if (start && end) return `${start} – ${end}`;
  if (start) return `Started ${start}`;
  if (end) return `Finished ${end}`;
  return "No dates";
}

async function setStatus(event: Event) {
  const b = book.value;
  if (!b) return;
  const status = (event.target as HTMLSelectElement).value as BookStatus;
  await apply(() => updateBook(b.id, { status }), "Could not change status.");
}
async function toggleFavorite() {
  const b = book.value;
  if (!b) return;
  await apply(
    () => updateBook(b.id, { favorite: !b.favorite }),
    "Could not change favourite.",
  );
}
async function saveNote(note: string | null) {
  const b = book.value;
  if (!b) return;
  await apply(() => updateBook(b.id, { note }), "Could not save the note.");
}
async function makeActive(edition: BookEdition) {
  const b = book.value;
  if (!b) return;
  await apply(
    () => updateBook(b.id, { activeEditionId: edition.id }),
    "Could not change the edition being read.",
  );
}
async function dropEdition(edition: BookEdition) {
  const b = book.value;
  if (!b) return;
  const ok = await confirm({
    title: "Remove edition?",
    message: `Remove the ${editionTitle(edition)} edition? Reads of it stay, without an edition.`,
    confirmLabel: "Remove",
    danger: true,
  });
  if (!ok) return;
  await apply(
    () => removeEdition(b.id, edition.id),
    "Could not remove the edition.",
  );
}
async function dropReading(read: BookReading) {
  const b = book.value;
  if (!b) return;
  const ok = await confirm({
    title: "Remove read?",
    message: `Remove the read ${readDates(read)}?`,
    confirmLabel: "Remove",
    danger: true,
  });
  if (!ok) return;
  await apply(() => removeReading(b.id, read.id), "Could not remove the read.");
}
async function trashBook() {
  const b = book.value;
  if (!b) return;
  const ok = await confirm({
    title: "Move to trash?",
    message: `Move “${b.title}” to the trash? It can be restored from Settings.`,
    confirmLabel: "Move to trash",
    danger: true,
  });
  if (!ok) return;
  try {
    await deleteBook(b.id);
    void router.push("/books");
  } catch (e) {
    actionError.value =
      e instanceof Error ? e.message : "Could not delete the book.";
  }
}
function saved(next: Book) {
  book.value = next;
  editing.value = false;
  editionDialog.value = null;
  readingDialog.value = null;
}
</script>

<template>
  <main v-if="loading" class="detail loading-state">
    <MediaTopBar active="book" />
    <p class="loading-text">Loading…</p>
  </main>

  <main v-else-if="error" class="detail error-state">
    <MediaTopBar active="book" />
    <p class="loading-text">
      <RetryMessage :message="error" @retry="load" />
    </p>
  </main>

  <main v-else-if="book" class="detail book-detail">
    <MediaTopBar active="book" />
    <p v-if="staleNotice" class="empty-state error-text">
      <RetryMessage
        message="Showing a saved copy: this book could not be refreshed."
        @retry="load"
      />
    </p>
    <BackButton class="back-spot" @click="goBack" />

    <section class="book-hero">
      <div class="book-cover" :class="{ empty: !book.coverUrl }">
        <img
          v-if="localBookCover(book.id, book.coverUrl)"
          :src="localBookCover(book.id, book.coverUrl) ?? undefined"
          :alt="`Cover of ${book.title}`"
          referrerpolicy="no-referrer"
        />
        <span v-else aria-hidden="true">{{ book.title.slice(0, 1) }}</span>
      </div>
      <div class="book-head">
        <h1>{{ book.title }}</h1>
        <p v-if="book.subtitle" class="book-subtitle">{{ book.subtitle }}</p>
        <p v-if="book.authors.length" class="book-authors">
          {{ authorLine(book.authors) }}
        </p>
        <p class="book-facts">
          <span v-if="published">First published {{ published }}</span>
          <span v-if="series">{{ series }}</span>
          <span v-if="book.language">{{ book.language }}</span>
          <span v-if="book.ratingOverall !== null"
            >Rated {{ book.ratingOverall }}/10</span
          >
        </p>
        <div class="book-actions">
          <label class="status-pick">
            <select
              class="ui-field"
              :value="book.status"
              aria-label="Status"
              @change="setStatus"
            >
              <option
                v-for="option in BOOK_STATUS_OPTIONS"
                :key="option.value"
                :value="option.value"
              >
                {{ option.label }}
              </option>
            </select>
          </label>
          <button
            type="button"
            class="ui-btn ui-btn-secondary"
            :class="{ on: book.favorite }"
            :aria-pressed="book.favorite"
            @click="toggleFavorite"
          >
            {{ book.favorite ? "★ Favourite" : "☆ Favourite" }}
          </button>
          <button
            type="button"
            class="ui-btn ui-btn-secondary"
            @click="editing = true"
          >
            Edit
          </button>
          <button
            type="button"
            class="ui-btn ui-btn-danger-soft"
            @click="trashBook"
          >
            Delete
          </button>
        </div>
        <div
          class="book-progress"
          role="progressbar"
          aria-label="Reading progress"
          aria-valuemin="0"
          aria-valuemax="100"
          :aria-valuenow="Math.round(progress)"
          :aria-valuetext="progressText"
        >
          <div class="bar">
            <span :style="{ width: `${progress}%` }"></span>
          </div>
          <small>{{ progressText }}</small>
        </div>
        <p v-if="book.genres.length || book.tags.length" class="chip-row">
          <span v-for="g in book.genres" :key="`g-${g}`" class="chip">{{
            g
          }}</span>
          <span v-for="t in book.tags" :key="`t-${t}`" class="chip"
            >#{{ t }}</span
          >
        </p>
      </div>
    </section>

    <p v-if="actionError" class="ui-error action-error" role="alert">
      {{ actionError }}
    </p>

    <div class="body">
      <p v-if="book.description" class="book-description">
        {{ book.description }}
      </p>

      <section class="book-section" aria-labelledby="reads-heading">
        <div class="section-heading">
          <h2 id="reads-heading">Reads</h2>
          <button
            type="button"
            class="ui-btn ui-btn-sm ui-btn-primary"
            @click="readingDialog = { reading: null }"
          >
            {{ book.reads.length ? "Start a reread" : "Start reading" }}
          </button>
        </div>
        <p v-if="!reads.length" class="empty-state">
          No reads yet. Start one to track your progress.
        </p>
        <ul v-else class="book-rows">
          <li v-for="read in reads" :key="read.id" class="book-row">
            <div class="row-main">
              <strong>{{ readOutcomeLabel(read.outcome) }}</strong>
              <span>{{ readDates(read) }}</span>
              <span>{{ readProgressText(read, editionOf(read)) }}</span>
              <span v-if="editionOf(read)" class="dim">{{
                editionTitle(editionOf(read)!)
              }}</span>
            </div>
            <div class="row-actions">
              <button
                type="button"
                class="ui-btn ui-btn-sm ui-btn-ghost"
                @click="readingDialog = { reading: read }"
              >
                Edit
              </button>
              <button
                type="button"
                class="ui-btn ui-btn-sm ui-btn-ghost"
                @click="dropReading(read)"
              >
                Remove
              </button>
            </div>
          </li>
        </ul>
      </section>

      <section class="book-section" aria-labelledby="editions-heading">
        <div class="section-heading">
          <h2 id="editions-heading">Editions</h2>
          <button
            type="button"
            class="ui-btn ui-btn-sm ui-btn-secondary"
            @click="editionDialog = { edition: null }"
          >
            Add edition
          </button>
        </div>
        <p v-if="!book.editions.length" class="empty-state">
          No editions yet. Add the copy you own or read to track pages.
        </p>
        <ul v-else class="book-rows">
          <li
            v-for="edition in book.editions"
            :key="edition.id"
            class="book-row"
            :class="{ active: edition.id === book.activeEditionId }"
          >
            <div class="row-main">
              <strong>{{ editionTitle(edition) }}</strong>
              <span
                v-if="edition.id === book.activeEditionId"
                class="ui-chip on"
                >Reading this edition</span
              >
              <span
                v-for="fact in editionFacts(edition)"
                :key="fact"
                class="dim"
                >{{ fact }}</span
              >
            </div>
            <div class="row-actions">
              <button
                v-if="edition.id !== book.activeEditionId"
                type="button"
                class="ui-btn ui-btn-sm ui-btn-ghost"
                @click="makeActive(edition)"
              >
                Read this one
              </button>
              <button
                type="button"
                class="ui-btn ui-btn-sm ui-btn-ghost"
                @click="editionDialog = { edition }"
              >
                Edit
              </button>
              <button
                type="button"
                class="ui-btn ui-btn-sm ui-btn-ghost"
                @click="dropEdition(edition)"
              >
                Remove
              </button>
            </div>
          </li>
        </ul>
        <div v-if="hasProviderIds" class="provider-editions">
          <div class="section-heading">
            <h3 id="provider-editions-heading">Editions from providers</h3>
            <button
              type="button"
              class="ui-btn ui-btn-sm ui-btn-ghost"
              :disabled="lookupBusy"
              @click="findEditions"
            >
              {{
                lookupBusy
                  ? "Looking up…"
                  : lookupStarted
                    ? "Look up again"
                    : "Find editions"
              }}
            </button>
          </div>
          <p v-if="lookupError" class="action-error" role="alert">
            {{ lookupError }}
          </p>
          <p v-else-if="lookupBusy" class="dim" role="status">
            Asking the book providers for this work's editions…
          </p>
          <p
            v-else-if="lookupStarted && !providerEditions.length"
            class="empty-state"
          >
            No provider listed editions for this book.
          </p>
          <ul
            v-if="providerEditions.length"
            class="book-rows"
            aria-labelledby="provider-editions-heading"
          >
            <li
              v-for="(edition, index) in providerEditions"
              :key="edition.key ?? edition.isbn_13 ?? index"
              class="book-row"
            >
              <div class="row-main">
                <strong>{{ providerEditionTitle(edition) }}</strong>
                <span
                  v-for="fact in providerEditionFacts(edition)"
                  :key="fact"
                  class="dim"
                  >{{ fact }}</span
                >
                <span v-if="lookupProvider" class="dim"
                  >from {{ lookupProvider }}</span
                >
              </div>
              <div class="row-actions">
                <span v-if="editionInLibrary(book, edition)" class="ui-chip"
                  >In your editions</span
                >
                <button
                  v-else
                  type="button"
                  class="ui-btn ui-btn-sm ui-btn-secondary"
                  @click="addProviderEdition(edition)"
                >
                  Add this edition
                </button>
              </div>
            </li>
          </ul>
        </div>
      </section>

      <MyNote :note="book.note" @save="saveNote" />
    </div>

    <BookFormModal
      v-if="editing"
      :book="book"
      @closed="editing = false"
      @saved="saved"
    />
    <BookEditionDialog
      v-if="editionDialog"
      :book-id="book.id"
      :edition="editionDialog.edition"
      @closed="editionDialog = null"
      @saved="saved"
    />
    <BookReadingDialog
      v-if="readingDialog"
      :book="book"
      :reading="readingDialog.reading"
      @closed="readingDialog = null"
      @saved="saved"
    />
  </main>
</template>

<style scoped src="../styles/shared/mediaDetail.css"></style>
<style scoped>
.book-hero {
  display: grid;
  grid-template-columns: 160px minmax(0, 1fr);
  gap: 24px;
  padding: 8px 24px 16px;
}
.book-cover {
  aspect-ratio: 2 / 3;
  border-radius: var(--ui-radius-control);
  overflow: hidden;
  background: var(--ui-surface-2);
  border: 1px solid var(--ui-border);
  display: grid;
  place-items: center;
}
.book-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.book-cover.empty span {
  font-size: 3rem;
  color: var(--ui-dim);
}
.book-head {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
}
.book-head h1 {
  margin: 0;
  font-size: 1.8rem;
  line-height: 1.2;
  overflow-wrap: anywhere;
}
.book-subtitle,
.book-authors,
.book-facts {
  margin: 0;
}
.book-subtitle {
  font-size: 1.05rem;
}
.book-authors {
  color: var(--ui-text);
  font-weight: 600;
}
.book-facts {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  color: var(--ui-dim);
  font-size: 0.875rem;
}
.book-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.status-pick select {
  min-width: 140px;
}
.book-progress {
  display: flex;
  align-items: center;
  gap: 10px;
  max-width: 420px;
}
.book-progress .bar {
  flex: 1;
  height: 6px;
  border-radius: 3px;
  background: var(--ui-surface-2);
  overflow: hidden;
}
.book-progress .bar span {
  display: block;
  height: 100%;
  background: var(--ui-accent);
}
.book-progress small {
  color: var(--ui-dim);
  white-space: nowrap;
}
.action-error {
  padding: 0 24px;
}
.book-description {
  white-space: pre-line;
  margin: 0 0 20px;
  line-height: 1.6;
}
.book-section {
  margin-bottom: 24px;
}
.book-rows {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.book-row {
  display: flex;
  flex-wrap: wrap;
  justify-content: space-between;
  gap: 8px 16px;
  padding: 10px 12px;
  border: 1px solid var(--ui-border);
  border-radius: var(--ui-radius-control);
  background: var(--ui-surface);
}
.book-row.active {
  border-color: var(--ui-accent-line);
}
.row-main {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 12px;
  min-width: 0;
}
.row-actions {
  display: flex;
  gap: 4px;
}
.provider-editions {
  margin-top: 16px;
}
.provider-editions h3 {
  margin: 0;
  font-size: var(--ui-font-body, 1rem);
}
.dim {
  color: var(--ui-dim);
  font-size: 0.875rem;
}
@media (max-width: 640px) {
  .book-hero {
    grid-template-columns: 96px minmax(0, 1fr);
    gap: 14px;
    padding: 8px 16px 12px;
  }
  .book-head h1 {
    font-size: 1.35rem;
  }
  .action-error {
    padding: 0 16px;
  }
}
</style>
