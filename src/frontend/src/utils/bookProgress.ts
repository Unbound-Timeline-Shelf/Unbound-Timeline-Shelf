// Where a reader is in a book (#265 3.1): the current read-through's
// progress, in pages or percent, on the edition it is read in. A finished
// work is complete whatever its reads say.
import type { Book, BookEdition, BookReading } from "../types/book";

// The read being read now: the newest one still "reading", preferring one
// on the active edition.
export function currentRead(book: Book): BookReading | null {
  const open = book.reads.filter((r) => r.outcome === "reading");
  if (!open.length) return null;
  const onActive = open.filter((r) => r.editionId === book.activeEditionId);
  const pool = onActive.length ? onActive : open;
  return pool[pool.length - 1] ?? null;
}

export function readEdition(
  book: Book,
  read: BookReading | null,
): BookEdition | null {
  if (!read?.editionId) return null;
  return book.editions.find((e) => e.id === read.editionId) ?? null;
}

// 0 to 100, or 0 when it cannot be known (pages of an unknown length).
export function bookProgress(book: Book): number {
  if (book.status === "read") return 100;
  const read = currentRead(book);
  if (!read || read.progressValue === null) return 0;
  if (read.progressUnit === "percent")
    return Math.min(100, Math.max(0, read.progressValue));
  const pages = readEdition(book, read)?.pageCount;
  return pages ? Math.min(100, (read.progressValue / pages) * 100) : 0;
}

function number(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

// "63%", "p. 120 of 300", "p. 120", "Read" or "–".
export function readProgressText(
  read: BookReading,
  edition: BookEdition | null,
): string {
  if (read.progressValue === null) return "–";
  if (read.progressUnit === "percent") return `${number(read.progressValue)}%`;
  return edition?.pageCount
    ? `p. ${number(read.progressValue)} of ${edition.pageCount}`
    : `p. ${number(read.progressValue)}`;
}

export function bookProgressLabel(book: Book): string {
  if (book.status === "read") return "Read";
  const read = currentRead(book);
  return read ? readProgressText(read, readEdition(book, read)) : "–";
}

// The upper bound a progress field accepts for its unit (the API enforces
// the same: percent 0-100, pages up to the edition's page count, otherwise
// a fixed sane bound).
export const MAX_PAGES = 100_000;
export function progressMax(
  unit: BookReading["progressUnit"],
  edition: BookEdition | null,
): number {
  if (unit === "percent") return 100;
  return edition?.pageCount ?? MAX_PAGES;
}

// A progress value carried across a unit switch: pages and percent convert
// through the edition's page count; without one the value cannot be
// carried and is cleared.
export function convertProgress(
  value: number | null,
  from: BookReading["progressUnit"],
  to: BookReading["progressUnit"],
  pageCount: number | null | undefined,
): number | null {
  if (value === null || from === to) return value;
  if (!pageCount) return null;
  if (to === "percent")
    return Math.min(100, Math.round((value / pageCount) * 1000) / 10);
  return Math.min(pageCount, Math.round((value / 100) * pageCount));
}
