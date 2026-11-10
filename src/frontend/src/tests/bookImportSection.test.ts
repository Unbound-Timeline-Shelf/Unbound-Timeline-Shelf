// @vitest-environment happy-dom
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import BookImportSection from "../components/settings/BookImportSection.vue";
import { importBooks, previewBookImport } from "../services/bookImports";
import type {
  BookImportPreview,
  BookImportResult,
} from "../services/bookImports";
import { preferences } from "../state/preferences";
import { DEFAULT_PREFERENCES } from "../services/preferences";

vi.mock("../services/bookImports", async (original) => ({
  ...(await original<typeof import("../services/bookImports")>()),
  previewBookImport: vi.fn(),
  importBooks: vi.fn(),
}));

// Synthetic books only.
const PREVIEW: BookImportPreview = {
  source: "goodreads",
  total: 3,
  new: 2,
  already_here: 1,
  repeated: 0,
  with_isbn: 2,
  invalid_isbn: 1,
  invalid_entries: [],
  providers: [{ name: "Atlas", batched: true }],
  rows: [
    {
      line: 2,
      title: "The Glass Orchard",
      authors: ["Ada Quill"],
      isbn_13: "9781111111113",
      invalid_isbn: null,
      status: "READ",
      last_read: "2021-03-14",
      already_here: false,
    },
    {
      line: 3,
      title: "Salt and Ember",
      authors: ["Cora Vale"],
      isbn_13: null,
      invalid_isbn: "0000000018",
      status: "IN_PROGRESS",
      last_read: null,
      already_here: false,
    },
    {
      line: 4,
      title: "Ninth Lantern",
      authors: ["Eli Ford"],
      isbn_13: "9782222222224",
      invalid_isbn: null,
      status: "WISHLIST",
      last_read: null,
      already_here: true,
    },
  ],
};

const RESULT: BookImportResult = {
  source: "goodreads",
  total: 3,
  created: 2,
  kept: 1,
  repeated: 0,
  matched_by_isbn: 1,
  matched_by_title: 0,
  manual: 1,
  resolve_calls: 1,
  row_lookups: 0,
  not_looked_up: 0,
  failed_providers: [],
  invalid_isbn: 1,
  invalid_entries: [],
  errors: [],
};

async function choose(wrapper: ReturnType<typeof mount>, name = "list.csv") {
  const input = wrapper.get('input[type="file"]');
  Object.defineProperty(input.element, "files", {
    value: [new File(["x"], name, { type: "text/csv" })],
    configurable: true,
  });
  await input.trigger("change");
  await flushPromises();
}

function dateStyle(date_format: string) {
  preferences.value = {
    ...DEFAULT_PREFERENCES,
    date_format,
    date_format_chosen: true,
  } as typeof preferences.value;
}

beforeEach(() => {
  dateStyle("dmy");
  vi.mocked(previewBookImport).mockResolvedValue(PREVIEW);
  vi.mocked(importBooks).mockResolvedValue(RESULT);
});
afterEach(() => {
  vi.clearAllMocks();
  preferences.value = { ...DEFAULT_PREFERENCES };
});

describe("book list import", () => {
  it("previews the file before anything is added, dates through the shared formatter", async () => {
    const wrapper = mount(BookImportSection);
    await choose(wrapper);
    expect(previewBookImport).toHaveBeenCalledWith(
      expect.any(File),
      "goodreads",
    );
    expect(importBooks).not.toHaveBeenCalled();
    const text = wrapper.text();
    expect(text).toContain("3 books in the file");
    expect(text).toContain("2 new");
    expect(text).toContain("1 already in your library");
    expect(text).toContain("read 14/03/2021");
    expect(text).not.toContain("2021-03-14");
    expect(text).toContain("Reading");
    expect(text).toContain("In library");
    expect(text).toContain("up to 200 in one request");
  });

  it("follows the viewer's date style", async () => {
    dateStyle("iso");
    const wrapper = mount(BookImportSection);
    await choose(wrapper);
    expect(wrapper.text()).toContain("read 2021-03-14");
  });

  it("imports with provider matching and reports what happened", async () => {
    const wrapper = mount(BookImportSection);
    await choose(wrapper);
    await wrapper.get(".primary-button").trigger("click");
    await flushPromises();
    expect(importBooks).toHaveBeenCalledWith(
      expect.any(File),
      "goodreads",
      true,
    );
    const result = wrapper.get('[data-testid="book-import-result"]').text();
    expect(result).toContain("Added 2 books");
    expect(result).toContain("Identified 1 by ISBN");
    expect(result).toContain("1 book was added from the file's data alone");
    expect(wrapper.find('[data-testid="book-import-preview"]').exists()).toBe(
      false,
    );
  });

  it("can import without asking any provider", async () => {
    const wrapper = mount(BookImportSection);
    await choose(wrapper);
    await wrapper.get('.book-match input[type="checkbox"]').setValue(false);
    await wrapper.get(".primary-button").trigger("click");
    await flushPromises();
    expect(importBooks).toHaveBeenCalledWith(
      expect.any(File),
      "goodreads",
      false,
    );
  });

  it("says plainly when no book provider is set up", async () => {
    vi.mocked(previewBookImport).mockResolvedValue({
      ...PREVIEW,
      providers: [],
    });
    const wrapper = mount(BookImportSection);
    await choose(wrapper);
    expect(wrapper.text()).toContain("No book metadata provider is set up");
    expect(wrapper.find(".book-match").exists()).toBe(false);
    await wrapper.get(".primary-button").trigger("click");
    await flushPromises();
    expect(importBooks).toHaveBeenCalledWith(
      expect.any(File),
      "goodreads",
      false,
    );
  });

  it("previews a pasted ISBN list as a text file", async () => {
    const wrapper = mount(BookImportSection);
    const isbnTab = wrapper
      .findAll("button")
      .find((button) => button.text() === "ISBN list");
    await isbnTab!.trigger("click");
    await wrapper.get("#book-isbn-list").setValue("9781111111113\n");
    await wrapper
      .findAll("button")
      .find((button) => button.text() === "Preview list")!
      .trigger("click");
    await flushPromises();
    const [file, source] = vi.mocked(previewBookImport).mock.calls[0];
    expect(source).toBe("isbn_list");
    expect(await (file as File).text()).toBe("9781111111113\n");
  });

  it("shows a file the server refuses as an error", async () => {
    vi.mocked(previewBookImport).mockRejectedValue(
      new Error("This does not look like a Goodreads export"),
    );
    const wrapper = mount(BookImportSection);
    await choose(wrapper);
    expect(wrapper.get('[role="alert"]').text()).toContain("Goodreads export");
  });
});
