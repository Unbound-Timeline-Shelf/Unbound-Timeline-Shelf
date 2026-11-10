// What a person reads for each of a book's stored values (#265 phase 1).
import type {
  BookFormat,
  BookOwnership,
  BookStatus,
  ProgressUnit,
  ReadOutcome,
} from "../types/book";

export const BOOK_STATUS_OPTIONS: readonly {
  value: BookStatus;
  label: string;
}[] = [
  { value: "wishlist", label: "Wishlist" },
  { value: "backlog", label: "Backlog" },
  { value: "in progress", label: "Reading" },
  { value: "on hold", label: "On hold" },
  { value: "read", label: "Read" },
  { value: "dropped", label: "Dropped" },
];

export const BOOK_FORMAT_OPTIONS: readonly {
  value: BookFormat;
  label: string;
}[] = [
  { value: "paperback", label: "Paperback" },
  { value: "hardcover", label: "Hardcover" },
  { value: "ebook", label: "E-book" },
  { value: "audiobook", label: "Audiobook" },
  { value: "other", label: "Other" },
];

export const BOOK_OWNERSHIP_OPTIONS: readonly {
  value: BookOwnership;
  label: string;
}[] = [
  { value: "none", label: "Not owned" },
  { value: "owned", label: "Owned" },
  { value: "borrowed", label: "Borrowed" },
  { value: "library", label: "Library loan" },
  { value: "wishlist", label: "Want to own" },
];

export const READ_OUTCOME_OPTIONS: readonly {
  value: ReadOutcome;
  label: string;
}[] = [
  { value: "reading", label: "Reading" },
  { value: "finished", label: "Finished" },
  { value: "abandoned", label: "Abandoned" },
];

export const PROGRESS_UNIT_OPTIONS: readonly {
  value: ProgressUnit;
  label: string;
}[] = [
  { value: "page", label: "Pages" },
  { value: "percent", label: "Percent" },
];

function labelOf<T extends string>(
  options: readonly { value: T; label: string }[],
  value: T,
): string {
  return options.find((o) => o.value === value)?.label ?? value;
}

export const bookStatusLabel = (v: BookStatus) =>
  labelOf(BOOK_STATUS_OPTIONS, v);
export const bookFormatLabel = (v: BookFormat) =>
  labelOf(BOOK_FORMAT_OPTIONS, v);
export const ownershipLabel = (v: BookOwnership) =>
  labelOf(BOOK_OWNERSHIP_OPTIONS, v);
export const readOutcomeLabel = (v: ReadOutcome) =>
  labelOf(READ_OUTCOME_OPTIONS, v);

// The years a book date may be typed in; the API accepts the same.
export const BOOK_DATE_YEARS: [number, number] = [1000, 2100];

// "Ursula K. Le Guin, Jane Roe" for a list of authors; "" for none.
export function authorLine(authors: readonly string[]): string {
  return authors.join(", ");
}
