<script setup lang="ts">
// The Books library (#265 phase 1): the same List / Shelf / Board view as
// Movies, TV and Anime, with reading statuses. A book is added by hand with
// its own form, or found through a metadata provider plugin (#265 phase 2).
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { useKeptAlive } from "../utils/useKeptAlive";
import { useMediaLibraryPage } from "../composables/useMediaLibraryPage";
import {
  fetchBooksPage,
  bookLibraryRevision,
  updateBook,
  deleteBook,
} from "../services/books";
import type { Book, BookStatus } from "../types/book";
import { resolveStatus } from "../utils/mediaStatus";
import { localBookCover } from "../utils/mediaImages";
import { bookProgress, bookProgressLabel } from "../utils/bookProgress";
import MediaLibraryView from "../components/library/MediaLibraryView.vue";
import type {
  LibraryCardVM,
  EditForm,
} from "../components/library/MediaLibraryView.vue";
import BookFormModal from "../components/BookFormModal.vue";

const router = useRouter();
const {
  entries: books,
  loading,
  refreshing,
  error,
  total,
  statusCounts,
  scoreRanks,
  currentFilters,
  hasMore,
  load,
  loadMore,
  refreshLibrary,
  removeEntries,
} = useMediaLibraryPage(fetchBooksPage, "book", "books", bookLibraryRevision);

function toVM(b: Book): LibraryCardVM {
  const progress = bookProgress(b);
  return {
    id: b.id,
    title: b.title,
    poster: localBookCover(b.id, b.coverUrl),
    status: b.status,
    favorite: b.favorite,
    score: b.ratingOverall,
    personalRank: b.personalRank,
    note: b.note,
    genres: b.genres,
    altTitles: [b.subtitle, ...b.authors].filter((v): v is string => !!v),
    isEpisodic: false,
    watched: b.status === "read" || progress >= 100 ? 1 : 0,
    total: 1,
    progressLabel: bookProgressLabel(b),
    canAdvance: false,
    releaseYear: b.firstPublished ? b.firstPublished.slice(0, 4) : null,
    addedAt: Date.parse(b.createdAt) || null,
  };
}

const items = computed(() => books.value.map(toVM));

onMounted(() => load(currentFilters.value));
useKeptAlive(refreshLibrary, {
  isLoading: () => refreshing.value || loading.value,
  hasError: () => error.value !== null,
});

function findBook(id: string): Book {
  const book = books.value.find((b) => b.id === id);
  if (!book) throw new Error(`Book ${id} not in the loaded list`);
  return book;
}
function replaceBook(updated: Book) {
  const idx = books.value.findIndex((b) => b.id === updated.id);
  if (idx !== -1) books.value[idx] = updated;
}

async function onToggleFavorite(id: string) {
  const book = findBook(id);
  const next = !book.favorite;
  book.favorite = next;
  try {
    replaceBook(await updateBook(id, { favorite: next }));
  } catch {
    book.favorite = !next;
  }
}

async function onSaveNote(id: string, note: string | null) {
  replaceBook(await updateBook(id, { note }));
}

async function onSaveEdit(id: string, form: EditForm) {
  const book = findBook(id);
  replaceBook(
    await updateBook(id, {
      status: resolveStatus(form.status, book.status, "book") as BookStatus,
      ratingOverall: form.score,
    }),
  );
}

async function onBulkSetStatus(ids: string[], status: string) {
  for (const id of ids) {
    replaceBook(
      await updateBook(id, {
        status: resolveStatus(status, undefined, "book") as BookStatus,
      }),
    );
  }
}
async function onBulkFavorite(ids: string[]) {
  for (const id of ids) {
    replaceBook(await updateBook(id, { favorite: true }));
  }
}
async function onBulkDelete(ids: string[]) {
  return removeEntries(ids, deleteBook);
}

function detailRoute(id: string): string {
  return `/books/${id}`;
}

const adding = ref(false);
function onCreated(book: Book) {
  adding.value = false;
  void router.push(detailRoute(book.id));
}
</script>

<template>
  <MediaLibraryView
    kind="book"
    add-label="+ Add book"
    :items="items"
    :total="total"
    :status-counts="statusCounts"
    :score-ranks="scoreRanks"
    :has-more="hasMore"
    :loading="loading"
    :error="error"
    :detail-route="detailRoute"
    @add="adding = true"
    @filters-change="load"
    @load-more="loadMore"
    @toggle-favorite="onToggleFavorite"
    @save-note="onSaveNote"
    @save-edit="onSaveEdit"
    @bulk-set-status="onBulkSetStatus"
    @bulk-favorite="onBulkFavorite"
    @bulk-delete="onBulkDelete"
  />
  <BookFormModal v-if="adding" @closed="adding = false" @saved="onCreated" />
</template>
