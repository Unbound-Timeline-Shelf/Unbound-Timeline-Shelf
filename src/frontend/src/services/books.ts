// Books (#265 phase 1): the library calls every media kind makes, plus a
// work's editions and read-throughs. Nothing outside this file sees the raw
// snake_case backend shape.
import {
  createMediaApi,
  handle,
  toNumberOrNull,
  unixSecondsToIso,
} from "./mediaApi";
import type { TrashedMedia } from "./mediaApi";
import {
  atEpoch,
  createEntityCache,
  entityCacheEpoch,
} from "../utils/entityCache";
import type { DatePrecision } from "../utils/dates";
import type {
  Book,
  BookEdition,
  BookEditionInput,
  BookFormat,
  BookInput,
  BookOwnership,
  BookReading,
  BookReadingInput,
  BookStatus,
  ProgressUnit,
  ReadOutcome,
} from "../types/book";

interface BackendEdition {
  id: string;
  book_id: string;
  isbn_13: string | null;
  isbn_10: string | null;
  other_identifiers?: Record<string, string>;
  format: string;
  publisher: string | null;
  publish_date: string | null;
  publish_date_precision: string | null;
  page_count: number | null;
  language: string | null;
  cover_url: string | null;
  ownership: string;
  acquired_at: string | null;
  note: string | null;
}

interface BackendReading {
  id: string;
  book_id: string;
  edition_id: string | null;
  started_on: string | null;
  finished_on: string | null;
  progress_unit: string;
  progress_value: number | string | null;
  outcome: string;
}

export interface BackendBook {
  id: string;
  user_id: string;
  title: string;
  sort_title: string;
  subtitle: string | null;
  authors: string[];
  description: string | null;
  first_published: string | null;
  first_published_precision: string | null;
  language: string | null;
  genres: string[];
  tags: string[];
  series_name: string | null;
  series_position: string | null;
  cover_url: string | null;
  provider_ids?: Record<string, string>;
  status: string;
  priority: string | null;
  favorite: boolean;
  note: string | null;
  rating_overall: number | string | null;
  personal_rank: number | null;
  active_edition_id: string | null;
  locked_fields: string[];
  editions?: BackendEdition[];
  reads?: BackendReading[];
  created_at: number;
  updated_at: number;
}

const bookCache = createEntityCache<Book>();
export const peekBook = bookCache.peek;

function precision(value: string | null): DatePrecision | null {
  return value === "year" || value === "month" || value === "day"
    ? value
    : null;
}

// "IN_PROGRESS" -> "in progress", and back.
function normalizeStatus(raw: string): BookStatus {
  return raw.toLowerCase().replace(/_/g, " ") as BookStatus;
}
function denormalizeStatus(status: BookStatus): string {
  return status.toUpperCase().replace(/ /g, "_");
}

export function mapBackendEdition(raw: BackendEdition): BookEdition {
  return {
    id: raw.id,
    bookId: raw.book_id,
    isbn13: raw.isbn_13,
    isbn10: raw.isbn_10,
    otherIdentifiers: raw.other_identifiers ?? {},
    format: raw.format as BookFormat,
    publisher: raw.publisher,
    publishDate: raw.publish_date,
    publishDatePrecision: precision(raw.publish_date_precision),
    pageCount: raw.page_count,
    language: raw.language,
    coverUrl: raw.cover_url,
    ownership: raw.ownership as BookOwnership,
    acquiredAt: raw.acquired_at,
    note: raw.note,
  };
}

export function mapBackendReading(raw: BackendReading): BookReading {
  return {
    id: raw.id,
    bookId: raw.book_id,
    editionId: raw.edition_id,
    startedOn: raw.started_on,
    finishedOn: raw.finished_on,
    progressUnit: raw.progress_unit as ProgressUnit,
    progressValue: toNumberOrNull(raw.progress_value),
    outcome: raw.outcome as ReadOutcome,
  };
}

export function mapBackendBookRaw(raw: BackendBook): Book {
  return {
    id: raw.id,
    userId: raw.user_id,
    title: raw.title,
    sortTitle: raw.sort_title,
    subtitle: raw.subtitle,
    authors: raw.authors ?? [],
    description: raw.description,
    firstPublished: raw.first_published,
    firstPublishedPrecision: precision(raw.first_published_precision),
    language: raw.language,
    genres: raw.genres ?? [],
    tags: raw.tags ?? [],
    seriesName: raw.series_name,
    seriesPosition: raw.series_position,
    coverUrl: raw.cover_url,
    providerIds: raw.provider_ids ?? {},
    status: normalizeStatus(raw.status),
    priority: raw.priority,
    favorite: raw.favorite,
    note: raw.note,
    ratingOverall: toNumberOrNull(raw.rating_overall),
    personalRank: raw.personal_rank,
    activeEditionId: raw.active_edition_id,
    lockedFields: raw.locked_fields ?? [],
    editions: (raw.editions ?? []).map(mapBackendEdition),
    reads: (raw.reads ?? []).map(mapBackendReading),
    createdAt: unixSecondsToIso(raw.created_at),
    updatedAt: unixSecondsToIso(raw.updated_at),
  };
}

function mapBackendBook(raw: BackendBook): Book {
  return bookCache.put(mapBackendBookRaw(raw));
}

// Only the fields given are sent, so an update changes (and locks) nothing
// the person did not touch.
export function bookInputToBody(
  input: Partial<BookInput>,
): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  const fields: [keyof BookInput, string][] = [
    ["title", "title"],
    ["subtitle", "subtitle"],
    ["authors", "authors"],
    ["description", "description"],
    ["firstPublished", "first_published"],
    ["firstPublishedPrecision", "first_published_precision"],
    ["language", "language"],
    ["genres", "genres"],
    ["tags", "tags"],
    ["seriesName", "series_name"],
    ["seriesPosition", "series_position"],
    ["coverUrl", "cover_url"],
    ["priority", "priority"],
    ["favorite", "favorite"],
    ["note", "note"],
    ["ratingOverall", "rating_overall"],
    ["personalRank", "personal_rank"],
    ["activeEditionId", "active_edition_id"],
  ];
  for (const [key, wire] of fields) {
    if (input[key] !== undefined) body[wire] = input[key];
  }
  if (input.status !== undefined) body.status = denormalizeStatus(input.status);
  return body;
}

export function editionInputToBody(
  input: BookEditionInput,
): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  const fields: [keyof BookEditionInput, string][] = [
    ["isbn13", "isbn_13"],
    ["isbn10", "isbn_10"],
    ["format", "format"],
    ["publisher", "publisher"],
    ["publishDate", "publish_date"],
    ["publishDatePrecision", "publish_date_precision"],
    ["pageCount", "page_count"],
    ["language", "language"],
    ["ownership", "ownership"],
    ["acquiredAt", "acquired_at"],
    ["note", "note"],
  ];
  for (const [key, wire] of fields) {
    if (input[key] !== undefined) body[wire] = input[key];
  }
  return body;
}

export function readingInputToBody(
  input: BookReadingInput,
): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  const fields: [keyof BookReadingInput, string][] = [
    ["editionId", "edition_id"],
    ["startedOn", "started_on"],
    ["finishedOn", "finished_on"],
    ["progressUnit", "progress_unit"],
    ["progressValue", "progress_value"],
    ["outcome", "outcome"],
  ];
  for (const [key, wire] of fields) {
    if (input[key] !== undefined) body[wire] = input[key];
  }
  return body;
}

const api = createMediaApi<BackendBook, Book, Partial<BookInput>>({
  base: "/api/book",
  noun: "book",
  plural: "books",
  cache: bookCache,
  map: mapBackendBook,
  toBody: bookInputToBody,
});
export const fetchBooksPage = api.fetchPage;
export const bookLibraryRevision = api.mutationRevision;
export const getBook = api.get;
export const createBook = (input: BookInput) => api.create(input);
export const updateBook = api.update;
export const deleteBook = api.remove;
export type TrashedBook = TrashedMedia;
export const fetchBookTrash = api.fetchTrash;
export const restoreBook = api.restore;
export const purgeBook = api.purge;
export const emptyBookTrash = api.emptyTrash;

// The edition and read calls answer with the whole work.
async function sendForBook(
  path: string,
  method: "POST" | "PATCH" | "DELETE",
  action: string,
  body?: Record<string, unknown>,
): Promise<Book> {
  const started = entityCacheEpoch();
  const response = await fetch(`/api/book/${path}`, {
    method,
    credentials: "include",
    ...(body
      ? {
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : {}),
  });
  const raw = await handle<BackendBook>(response, action);
  const book = atEpoch(started, () => mapBackendBook(raw));
  api.markMutation();
  return book;
}

export function addEdition(
  bookId: string,
  input: BookEditionInput,
): Promise<Book> {
  return sendForBook(
    `${bookId}/editions`,
    "POST",
    "add an edition",
    editionInputToBody(input),
  );
}

export function updateEdition(
  bookId: string,
  editionId: string,
  input: BookEditionInput,
): Promise<Book> {
  return sendForBook(
    `${bookId}/editions/${editionId}`,
    "PATCH",
    "update an edition",
    editionInputToBody(input),
  );
}

export function removeEdition(
  bookId: string,
  editionId: string,
): Promise<Book> {
  return sendForBook(
    `${bookId}/editions/${editionId}`,
    "DELETE",
    "remove an edition",
  );
}

export function addReading(
  bookId: string,
  input: BookReadingInput,
): Promise<Book> {
  return sendForBook(
    `${bookId}/reads`,
    "POST",
    "add a read",
    readingInputToBody(input),
  );
}

export function updateReading(
  bookId: string,
  readId: string,
  input: BookReadingInput,
): Promise<Book> {
  return sendForBook(
    `${bookId}/reads/${readId}`,
    "PATCH",
    "update a read",
    readingInputToBody(input),
  );
}

export function removeReading(bookId: string, readId: string): Promise<Book> {
  return sendForBook(`${bookId}/reads/${readId}`, "DELETE", "remove a read");
}
