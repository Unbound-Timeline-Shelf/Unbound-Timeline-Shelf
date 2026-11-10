import type { DatePrecision } from "../utils/dates";

// A book's own statuses (#265 3.1), lowercase with spaces like the other kinds.
export type BookStatus =
  "wishlist" | "backlog" | "in progress" | "on hold" | "read" | "dropped";

// The Atlas `book_format` vocabulary (C15 §2.3).
export type BookFormat =
  "paperback" | "hardcover" | "ebook" | "audiobook" | "other";
export type BookOwnership =
  "owned" | "borrowed" | "library" | "wishlist" | "none";
export type ProgressUnit = "page" | "percent";
export type ReadOutcome = "reading" | "finished" | "abandoned";

export interface BookEdition {
  id: string;
  bookId: string;
  isbn13: string | null;
  isbn10: string | null;
  otherIdentifiers: Record<string, string>;
  format: BookFormat;
  publisher: string | null;
  // ISO first day of the period, with how much of it is known (null = a day)
  publishDate: string | null;
  publishDatePrecision: DatePrecision | null;
  pageCount: number | null;
  language: string | null;
  coverUrl: string | null;
  ownership: BookOwnership;
  acquiredAt: string | null;
  note: string | null;
}

export interface BookReading {
  id: string;
  bookId: string;
  editionId: string | null;
  startedOn: string | null;
  finishedOn: string | null;
  progressUnit: ProgressUnit;
  progressValue: number | null;
  outcome: ReadOutcome;
}

export interface Book {
  id: string;
  userId: string;
  title: string;
  sortTitle: string;
  subtitle: string | null;
  authors: string[];
  description: string | null;
  firstPublished: string | null;
  firstPublishedPrecision: DatePrecision | null;
  language: string | null;
  genres: string[];
  tags: string[];
  seriesName: string | null;
  seriesPosition: string | null;
  coverUrl: string | null;
  providerIds: Record<string, string>;
  status: BookStatus;
  priority: string | null;
  favorite: boolean;
  note: string | null;
  ratingOverall: number | null;
  personalRank: number | null;
  activeEditionId: string | null;
  lockedFields: string[];
  editions: BookEdition[];
  reads: BookReading[];
  createdAt: string;
  updatedAt: string;
}

// What the work form edits. Fields left out of an update stay as they are.
export interface BookInput {
  title: string;
  subtitle?: string | null;
  authors?: string[];
  description?: string | null;
  firstPublished?: string | null;
  firstPublishedPrecision?: DatePrecision | null;
  language?: string | null;
  genres?: string[];
  tags?: string[];
  seriesName?: string | null;
  seriesPosition?: string | null;
  coverUrl?: string | null;
  status?: BookStatus;
  priority?: string | null;
  favorite?: boolean;
  note?: string | null;
  ratingOverall?: number | null;
  personalRank?: number | null;
  activeEditionId?: string | null;
}

export interface BookEditionInput {
  isbn13?: string | null;
  isbn10?: string | null;
  format?: BookFormat;
  publisher?: string | null;
  publishDate?: string | null;
  publishDatePrecision?: DatePrecision | null;
  pageCount?: number | null;
  language?: string | null;
  ownership?: BookOwnership;
  acquiredAt?: string | null;
  note?: string | null;
}

export interface BookReadingInput {
  editionId?: string | null;
  startedOn?: string | null;
  finishedOn?: string | null;
  progressUnit?: ProgressUnit;
  progressValue?: number | null;
  outcome?: ReadOutcome;
}
