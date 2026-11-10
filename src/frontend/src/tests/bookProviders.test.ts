// @vitest-environment happy-dom
// Books through providers (C15 §6.2): the add form finds a book and saves its
// identities with it (plus the edition an ISBN search named), and the book
// page lists a provider's editions with "Add this edition". The provider
// search itself is replaced by a stub; all books here are invented.
import { flushPromises, mount } from "@vue/test-utils";
import { computed, ref } from "vue";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Book, BookEdition } from "../types/book";
import type { MetadataCandidate } from "../services/metadata";
import { bookMetadataResult } from "../utils/bookMetadata";
import type { BookMetadataResult } from "../utils/bookMetadata";

const getBook = vi.fn();
const createBook = vi.fn();
const addEdition = vi.fn();
vi.mock("../services/books", async (importOriginal) => {
  const real = await importOriginal<typeof import("../services/books")>();
  return {
    ...real,
    getBook: (id: string) => getBook(id),
    peekBook: () => undefined,
    createBook: (input: unknown) => createBook(input),
    addEdition: (id: string, input: unknown) => addEdition(id, input),
  };
});
vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { id: "b1" } }),
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
}));
vi.mock("../composables/useProviderAvailability", () => ({
  useProviderAvailability: () => ({ block: computed(() => null) }),
}));

// the page's editions lookup
const lookupSelected = ref<MetadataCandidate | null>(null);
const lookupEnriching = ref(false);
const focus = vi.fn();
vi.mock("../composables/useMetadataSearch", () => ({
  useMetadataSearch: () => ({
    selected: lookupSelected,
    enrichingMedia: lookupEnriching,
    focus: (...args: unknown[]) => focus(...args),
    stop: vi.fn(),
  }),
}));

// the add form's search box
const formResults = ref<BookMetadataResult[]>([]);
const formSelected = ref<BookMetadataResult | null>(null);
const select = vi.fn();
vi.mock("../utils/useMetadataSearch", () => ({
  useMetadataSearch: () => ({
    query: ref(""),
    results: formResults,
    selected: formSelected,
    searching: ref(false),
    message: ref(null),
    warnings: ref([]),
    enrichingMedia: ref(false),
    run: vi.fn(),
    select: (result: BookMetadataResult) => select(result),
    applied: vi.fn(),
    stop: () => {
      formSelected.value = null;
    },
  }),
}));

import BookDetail from "../views/BookDetail.vue";
import BookFormModal from "../components/BookFormModal.vue";

function edition(over: Partial<BookEdition> = {}): BookEdition {
  return {
    id: "e1",
    bookId: "b1",
    isbn13: null,
    isbn10: null,
    otherIdentifiers: {},
    format: "paperback",
    publisher: null,
    publishDate: null,
    publishDatePrecision: null,
    pageCount: null,
    language: null,
    coverUrl: null,
    ownership: "none",
    acquiredAt: null,
    note: null,
    ...over,
  };
}

function book(over: Partial<Book> = {}): Book {
  return {
    id: "b1",
    userId: "u1",
    title: "The Lantern Archive",
    sortTitle: "Lantern Archive",
    subtitle: null,
    authors: ["Ada Quill"],
    description: null,
    firstPublished: null,
    firstPublishedPrecision: null,
    language: null,
    genres: [],
    tags: [],
    seriesName: null,
    seriesPosition: null,
    coverUrl: null,
    providerIds: { atlas: "atlas:01BOOK" },
    status: "wishlist",
    priority: null,
    favorite: false,
    note: null,
    ratingOverall: null,
    personalRank: null,
    activeEditionId: null,
    lockedFields: [],
    editions: [],
    reads: [],
    createdAt: "2026-01-01T00:00:00.000Z",
    updatedAt: "2026-01-01T00:00:00.000Z",
    ...over,
  };
}

const PROVIDER_BOOK: MetadataCandidate = {
  id: "c1",
  rank: 0,
  external_id: "W1",
  title: "The Lantern Archive",
  year: 1813,
  provider: "official.metadata-atlas.atlas",
  provider_name: "Atlas",
  providers: [],
  provider_ids: { atlas: "atlas:01BOOK" },
  media_type: "book",
  alternate_titles: [],
  authors: ["Ada Quill"],
  isbn_13: "9780000000002",
  metadata: {
    title: "The Lantern Archive",
    description: "An invented novel.",
    book: {
      authors: [{ name: "Ada Quill", role: "author" }],
      isbn_13: "9780000000002",
      first_published: { date: "1813", precision: "year" },
      editions: [
        {
          key: "ED1",
          isbn_13: "9780000000002",
          format: "paperback",
          publisher: "Harbor Books",
          publish_date: { date: "2005-08", precision: "month" },
          page_count: 528,
        },
        { key: "ED2", isbn_13: "9780000000019", format: "audiobook" },
      ],
    },
  },
  assets: [],
};

beforeEach(() => {
  getBook.mockReset();
  createBook.mockReset();
  addEdition.mockReset();
  focus.mockReset();
  select.mockReset();
  lookupSelected.value = null;
  lookupEnriching.value = false;
  formResults.value = [];
  formSelected.value = null;
});

describe("provider editions on the book page", () => {
  it("lists a provider's editions and adds one to the work", async () => {
    const owned = edition({ id: "e2", isbn13: "9780000000019" });
    getBook.mockResolvedValue(book({ editions: [owned] }));
    focus.mockImplementation(async () => {
      lookupSelected.value = PROVIDER_BOOK;
    });
    addEdition.mockResolvedValue(
      book({ editions: [owned, edition({ isbn13: "9780000000002" })] }),
    );
    const page = mount(BookDetail);
    await flushPromises();
    const find = page
      .findAll("button")
      .find((button) => button.text() === "Find editions");
    expect(find).toBeTruthy();
    await find!.trigger("click");
    await flushPromises();
    expect(focus).toHaveBeenCalledWith("The Lantern Archive", {
      atlas: "atlas:01BOOK",
    });
    const rows = page.find(".provider-editions").findAll(".book-row");
    expect(rows).toHaveLength(2);
    // dates through the shared formatter, at their precision
    expect(rows[0]!.text()).toContain("August 2005");
    expect(rows[0]!.text()).toContain("ISBN 9780000000002");
    // the audiobook is already one of the work's editions
    expect(rows[1]!.text()).toContain("In your editions");
    await rows[0]!
      .findAll("button")
      .find((button) => button.text() === "Add this edition")!
      .trigger("click");
    await flushPromises();
    expect(addEdition).toHaveBeenCalledWith("b1", {
      isbn13: "9780000000002",
      isbn10: null,
      format: "paperback",
      publisher: "Harbor Books",
      publishDate: "2005-08-01",
      publishDatePrecision: "month",
      pageCount: 528,
      language: null,
    });
    expect(page.find(".provider-editions").text()).not.toContain(
      "Add this edition",
    );
  });

  it("offers no lookup for a book typed by hand", async () => {
    getBook.mockResolvedValue(book({ providerIds: {} }));
    const page = mount(BookDetail);
    await flushPromises();
    expect(page.find(".provider-editions").exists()).toBe(false);
  });

  it("says when a provider lists no editions", async () => {
    getBook.mockResolvedValue(book());
    focus.mockImplementation(async () => {
      lookupSelected.value = {
        ...PROVIDER_BOOK,
        metadata: { title: "The Lantern Archive" },
      };
    });
    const page = mount(BookDetail);
    await flushPromises();
    await page
      .findAll("button")
      .find((button) => button.text() === "Find editions")!
      .trigger("click");
    await flushPromises();
    expect(page.text()).toContain("No provider listed editions for this book.");
  });
});

describe("adding a book found through a provider", () => {
  it("fills the form, saves the identities and adds the searched edition", async () => {
    const result = bookMetadataResult(PROVIDER_BOOK);
    formResults.value = [result];
    select.mockImplementation(async () => {
      formSelected.value = result;
    });
    createBook.mockResolvedValue(book());
    addEdition.mockResolvedValue(
      book({ editions: [edition({ isbn13: "9780000000002" })] }),
    );
    const form = mount(BookFormModal, {
      global: { stubs: { teleport: true } },
    });
    await flushPromises();
    await form.find(".metadata-result").trigger("click");
    await flushPromises();
    expect(form.find(".found-book").text()).toContain("1813");
    expect(form.find(".found-book").text()).toContain(
      "Also adds the edition you searched for",
    );
    await form
      .findAll("button")
      .find((button) => button.text() === "Use these details")!
      .trigger("click");
    await flushPromises();
    await form
      .findAll("button")
      .find((button) => button.text() === "Add book")!
      .trigger("click");
    await flushPromises();
    expect(createBook).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "The Lantern Archive",
        authors: ["Ada Quill"],
        firstPublished: "1813-01-01",
        firstPublishedPrecision: "year",
        description: "An invented novel.",
        providerIds: { atlas: "atlas:01BOOK" },
      }),
    );
    expect(addEdition).toHaveBeenCalledWith(
      "b1",
      expect.objectContaining({ isbn13: "9780000000002", format: "paperback" }),
    );
    const saved = form.emitted("saved")?.[0]?.[0] as Book;
    expect(saved.editions).toHaveLength(1);
  });

  it("keeps the work when the edition cannot be added", async () => {
    const result = bookMetadataResult(PROVIDER_BOOK);
    formResults.value = [result];
    select.mockImplementation(async () => {
      formSelected.value = result;
    });
    createBook.mockResolvedValue(book());
    addEdition.mockRejectedValue(new Error("duplicate"));
    const form = mount(BookFormModal, {
      global: { stubs: { teleport: true } },
    });
    await form.find(".metadata-result").trigger("click");
    await flushPromises();
    await form
      .findAll("button")
      .find((button) => button.text() === "Use these details")!
      .trigger("click");
    await form
      .findAll("button")
      .find((button) => button.text() === "Add book")!
      .trigger("click");
    await flushPromises();
    expect(form.emitted("saved")).toHaveLength(1);
  });

  it("has no provider search when editing", async () => {
    const form = mount(BookFormModal, {
      props: { book: book() },
      global: { stubs: { teleport: true } },
    });
    expect(form.find(".metadata-results").exists()).toBe(false);
    expect(form.text()).not.toContain("Find the book");
  });
});
