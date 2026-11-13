// Book list imports (C15 6.2): a Goodreads or StoryGraph CSV, or a list of ISBNs.
// The preview reads the file and says what it holds; the import adds the new
// books, identifying them through the account's book providers first.

export type BookImportSource =
  "auto" | "goodreads" | "storygraph" | "isbn_list";

export interface BookImportPreviewRow {
  line: number;
  title: string | null;
  authors: string[];
  isbn_13: string | null;
  invalid_isbn: string | null;
  // the API's status value, e.g. "IN_PROGRESS"
  status: string;
  // ISO date of the last finished read, if the file gives one
  last_read: string | null;
  already_here: boolean;
  // the same book as an earlier row of the file: imported once
  repeated: boolean;
}

export interface BookImportPreview {
  source: Exclude<BookImportSource, "auto">;
  total: number;
  new: number;
  already_here: number;
  repeated: number;
  with_isbn: number;
  invalid_isbn: number;
  invalid_entries: string[];
  providers: { name: string; batched: boolean }[];
  rows: BookImportPreviewRow[];
}

export interface BookImportResult {
  source: Exclude<BookImportSource, "auto">;
  total: number;
  created: number;
  kept: number;
  repeated: number;
  matched_by_isbn: number;
  matched_by_title: number;
  manual: number;
  resolve_calls: number;
  row_lookups: number;
  not_looked_up: number;
  failed_providers: string[];
  invalid_isbn: number;
  invalid_entries: string[];
  errors: string[];
}

async function post(
  path: string,
  file: File,
  fields: Record<string, string>,
): Promise<Response> {
  const body = new FormData();
  body.append("file", file);
  for (const [key, value] of Object.entries(fields)) body.append(key, value);
  const response = await fetch(path, {
    method: "POST",
    credentials: "include",
    body,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      /* keep the status line */
    }
    throw new Error(detail);
  }
  return response;
}

export async function previewBookImport(
  file: File,
  source: BookImportSource,
): Promise<BookImportPreview> {
  return await (
    await post("/api/import/books/preview", file, { source })
  ).json();
}

export async function importBooks(
  file: File,
  source: BookImportSource,
  match: boolean,
): Promise<BookImportResult> {
  return await (
    await post("/api/import/books", file, { source, match: String(match) })
  ).json();
}

// A pasted list of ISBNs travels as a small text file, like an uploaded one.
export function isbnListFile(text: string): File {
  return new File([text], "isbn-list.txt", { type: "text/plain" });
}
