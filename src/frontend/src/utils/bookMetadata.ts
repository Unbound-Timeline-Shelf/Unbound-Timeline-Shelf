// What a book provider's result fills in (Plugin API 1.1.20 `metadata.book`),
// and how one of its editions becomes an edition of a work in the library
// ("Add this edition", C15 §6.2). Dates stay partial: a provider's year or
// month is stored as the first day of that period with its precision, and the
// page shows it through the shared formatter.
import type {
  MetadataBookDate,
  MetadataBookEdition,
  MetadataCandidate,
} from "../services/metadata";
import type { Book, BookEditionInput, BookFormat } from "../types/book";
import { bookFormatLabel } from "./bookLabels";
import type { DatePrecision } from "./dates";
import { normalizeIsbn13 } from "./isbn";

export interface BookMetadataResult {
  candidateId: string;
  provider: string;
  providerId: string;
  title: string;
  subtitle: string | null;
  authors: string[];
  description: string | null;
  firstPublished: string | null;
  firstPublishedPrecision: DatePrecision | null;
  language: string | null;
  seriesName: string | null;
  seriesPosition: string | null;
  genres: string[];
  coverUrl: string | null;
  providerIds: Record<string, string>;
  // the ISBN-13 the lookup matched, when it was an ISBN search
  isbn13: string | null;
  editions: MetadataBookEdition[];
}

const FORMATS: readonly BookFormat[] = [
  "paperback",
  "hardcover",
  "ebook",
  "audiobook",
  "other",
];

/** "1965" (year) -> ["1965-01-01", "year"]; a day keeps a null precision. */
export function bookDate(
  value: MetadataBookDate | null | undefined,
): [string | null, DatePrecision | null] {
  if (!value) return [null, null];
  const [year, month = "01", day = "01"] = value.date.split("-");
  return [
    `${year}-${month}-${day}`,
    value.precision === "day" ? null : value.precision,
  ];
}

export function bookMetadataResult(
  candidate: MetadataCandidate,
): BookMetadataResult {
  const metadata = candidate.metadata;
  const book = metadata.book ?? {};
  const year = metadata.year ?? candidate.year;
  const [firstPublished, firstPublishedPrecision] = book.first_published
    ? bookDate(book.first_published)
    : year
      ? bookDate({ date: String(year).padStart(4, "0"), precision: "year" })
      : [null, null];
  const authors = (book.authors ?? []).map((author) => author.name);
  return {
    candidateId: candidate.id,
    provider: candidate.provider_name,
    providerId: candidate.external_id,
    title: metadata.title || candidate.title,
    subtitle: book.subtitle ?? null,
    authors: authors.length ? authors : (candidate.authors ?? []),
    description: metadata.description ?? null,
    firstPublished,
    firstPublishedPrecision,
    language: book.original_language ?? null,
    seriesName: book.series_name ?? null,
    seriesPosition: book.series_position ?? null,
    genres: metadata.genres ?? [],
    coverUrl:
      candidate.assets.find((asset) => asset.kind === "poster")?.url ?? null,
    providerIds: candidate.provider_ids,
    isbn13: book.isbn_13 ?? candidate.isbn_13 ?? null,
    editions: book.editions ?? [],
  };
}

/**
 * The edition the person's ISBN search named, to add with the work. Only an
 * ISBN query names an edition: a provider may also report an ISBN (its primary
 * edition, say) for a title or author search, and that is not one the person
 * asked for, so nothing is added then.
 */
export function matchedEdition(
  result: BookMetadataResult,
  query: string,
): MetadataBookEdition | null {
  if (!result.isbn13 || normalizeIsbn13(query) !== result.isbn13) return null;
  return (
    result.editions.find((edition) => edition.isbn_13 === result.isbn13) ?? {
      isbn_13: result.isbn13,
    }
  );
}

function editionFormat(edition: MetadataBookEdition): BookFormat {
  return FORMATS.includes(edition.format as BookFormat)
    ? (edition.format as BookFormat)
    : "other";
}

export function editionInput(edition: MetadataBookEdition): BookEditionInput {
  const [publishDate, publishDatePrecision] = bookDate(edition.publish_date);
  return {
    isbn13: edition.isbn_13 ?? null,
    isbn10: edition.isbn_13 ? null : (edition.isbn_10 ?? null),
    format: editionFormat(edition),
    publisher: edition.publisher ?? null,
    publishDate,
    publishDatePrecision: publishDate ? publishDatePrecision : null,
    pageCount: edition.page_count ?? null,
    language: edition.language ?? null,
  };
}

function editionIsbn13(edition: MetadataBookEdition): string | null {
  return (
    edition.isbn_13 ??
    (edition.isbn_10 ? normalizeIsbn13(edition.isbn_10) : null)
  );
}

/** Whether the work already holds this provider edition (same ISBN-13). */
export function editionInLibrary(
  book: Book,
  edition: MetadataBookEdition,
): boolean {
  const isbn = editionIsbn13(edition);
  return isbn !== null && book.editions.some((own) => own.isbn13 === isbn);
}

/** A provider edition's label: its own name, else format and publisher. */
export function providerEditionTitle(edition: MetadataBookEdition): string {
  if (edition.name) return edition.name;
  return [bookFormatLabel(editionFormat(edition)), edition.publisher]
    .filter(Boolean)
    .join(" · ");
}
