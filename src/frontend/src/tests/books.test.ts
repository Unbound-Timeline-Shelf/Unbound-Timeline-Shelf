// @vitest-environment happy-dom
// Books (#265 phase 1): the service's mapping, reading statuses in the
// library tabs, progress in pages or percent, and the book page showing
// several editions, an empty work and every date through the shared
// formatter. All books here are invented.
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Book, BookEdition, BookReading } from "../types/book";
import type { BackendBook } from "../services/books";
import { preferences } from "../state/preferences";
import { DEFAULT_PREFERENCES } from "../services/preferences";

const getBook = vi.fn();
vi.mock("../services/books", async (importOriginal) => {
  const real = await importOriginal<typeof import("../services/books")>();
  return {
    ...real,
    getBook: (id: string) => getBook(id),
    peekBook: () => undefined,
  };
});
vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { id: "b1" } }),
  useRouter: () => ({ push: vi.fn(), back: vi.fn() }),
}));

import {
  bookInputToBody,
  editionInputToBody,
  mapBackendBookRaw,
  readingInputToBody,
} from "../services/books";
import {
  bucketToReal,
  mediaBucketCounts,
  resolveStatus,
  statusBucket,
  statusBucketLabel,
  statusBuckets,
} from "../utils/mediaStatus";
import {
  bookProgress,
  bookProgressLabel,
  convertProgress,
  currentRead,
  progressMax,
  readProgressText,
} from "../utils/bookProgress";
import BookDetail from "../views/BookDetail.vue";

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

function reading(over: Partial<BookReading> = {}): BookReading {
  return {
    id: "r1",
    bookId: "b1",
    editionId: null,
    startedOn: null,
    finishedOn: null,
    progressUnit: "page",
    progressValue: null,
    outcome: "reading",
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
    authors: [],
    description: null,
    firstPublished: null,
    firstPublishedPrecision: null,
    language: null,
    genres: [],
    tags: [],
    seriesName: null,
    seriesPosition: null,
    coverUrl: null,
    providerIds: {},
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

describe("book service", () => {
  const raw: BackendBook = {
    id: "b1",
    user_id: "u1",
    title: "The Lantern Archive",
    sort_title: "Lantern Archive",
    subtitle: "A Tale of Tides",
    authors: ["Mira Holt", "Tobias Fenn"],
    description: null,
    first_published: "1987-01-01",
    first_published_precision: "year",
    language: "en",
    genres: ["Fantasy"],
    tags: [],
    series_name: "Tidewater",
    series_position: "2",
    cover_url: null,
    status: "IN_PROGRESS",
    priority: null,
    favorite: false,
    note: null,
    rating_overall: "8.50",
    personal_rank: null,
    active_edition_id: "e1",
    locked_fields: [],
    editions: [
      {
        id: "e1",
        book_id: "b1",
        isbn_13: "9780000000002",
        isbn_10: "0000000000",
        format: "hardcover",
        publisher: "Gullwing Press",
        publish_date: "1987-03-01",
        publish_date_precision: "month",
        page_count: 320,
        language: "en",
        cover_url: null,
        ownership: "owned",
        acquired_at: "2020-05-17",
        note: null,
      },
    ],
    reads: [
      {
        id: "r1",
        book_id: "b1",
        edition_id: "e1",
        started_on: "2026-02-01",
        finished_on: null,
        progress_unit: "page",
        progress_value: "120.00",
        outcome: "reading",
      },
    ],
    created_at: 1767225600,
    updated_at: 1767225600,
  };

  it("maps the backend shape, with numbers from decimal strings", () => {
    const b = mapBackendBookRaw(raw);
    expect(b.status).toBe("in progress");
    expect(b.ratingOverall).toBe(8.5);
    expect(b.firstPublishedPrecision).toBe("year");
    expect(b.editions[0]).toMatchObject({
      isbn13: "9780000000002",
      format: "hardcover",
      publishDatePrecision: "month",
      pageCount: 320,
      ownership: "owned",
    });
    expect(b.reads[0]).toMatchObject({
      editionId: "e1",
      progressUnit: "page",
      progressValue: 120,
    });
  });

  it("maps a work with no editions or reads to empty lists", () => {
    const bare: BackendBook = { ...raw, authors: [] };
    delete bare.editions;
    delete bare.reads;
    const b = mapBackendBookRaw(bare);
    expect(b.editions).toEqual([]);
    expect(b.reads).toEqual([]);
    expect(b.authors).toEqual([]);
  });

  it("sends only the fields given, in the backend's names", () => {
    expect(bookInputToBody({ status: "on hold", favorite: true })).toEqual({
      status: "ON_HOLD",
      favorite: true,
    });
    expect(
      bookInputToBody({ firstPublished: "1987-01-01", activeEditionId: null }),
    ).toEqual({ first_published: "1987-01-01", active_edition_id: null });
    expect(editionInputToBody({ isbn13: "978-0-00-000000-2" })).toEqual({
      isbn_13: "978-0-00-000000-2",
    });
    expect(
      readingInputToBody({ progressUnit: "percent", progressValue: 40 }),
    ).toEqual({ progress_unit: "percent", progress_value: 40 });
  });
});

describe("book statuses in the library", () => {
  it("names the tabs for reading", () => {
    expect(statusBuckets("book").map((b) => b.label)).toEqual([
      "Reading",
      "Read",
      "On Hold",
      "Dropped",
      "Plan to Read",
    ]);
    expect(statusBuckets().map((b) => b.label)).toContain("Plan to Watch");
  });

  it("files each book status under one tab", () => {
    expect(statusBucket("wishlist", "book")).toBe("plan");
    expect(statusBucket("backlog", "book")).toBe("plan");
    expect(statusBucket("in progress", "book")).toBe("watching");
    expect(statusBucket("on hold", "book")).toBe("hold");
    expect(statusBucket("read", "book")).toBe("completed");
    expect(statusBucket("dropped", "book")).toBe("dropped");
    expect(statusBucketLabel("read", "book")).toBe("Read");
  });

  it("keeps a stored status that is already in the chosen tab", () => {
    expect(resolveStatus("plan", "backlog", "book")).toBe("backlog");
    expect(resolveStatus("completed", "backlog", "book")).toBe("read");
    expect(bucketToReal("hold", "book")).toBe("on hold");
  });

  it("counts the backend's status counts into tabs", () => {
    expect(
      mediaBucketCounts(
        { WISHLIST: 2, BACKLOG: 1, IN_PROGRESS: 1, READ: 3 },
        "book",
      ),
    ).toMatchObject({ all: 7, plan: 3, watching: 1, completed: 3 });
  });
});

describe("reading progress", () => {
  const e = edition({ id: "e1", pageCount: 400 });

  it("reads pages against the edition's page count", () => {
    const b = book({
      activeEditionId: "e1",
      editions: [e],
      reads: [reading({ editionId: "e1", progressValue: 100 })],
    });
    expect(bookProgress(b)).toBe(25);
    expect(bookProgressLabel(b)).toBe("p. 100 of 400");
  });

  it("cannot place pages without a page count", () => {
    const b = book({ reads: [reading({ progressValue: 100 })] });
    expect(bookProgress(b)).toBe(0);
    expect(bookProgressLabel(b)).toBe("p. 100");
  });

  it("reads percent directly, and a read work is complete", () => {
    const r = reading({ progressUnit: "percent", progressValue: 62.5 });
    expect(readProgressText(r, null)).toBe("62.5%");
    expect(bookProgress(book({ reads: [r] }))).toBe(62.5);
    expect(bookProgress(book({ status: "read" }))).toBe(100);
    expect(bookProgressLabel(book())).toBe("–");
  });

  it("follows the open read on the active edition during a reread", () => {
    const b = book({
      activeEditionId: "e2",
      editions: [e, edition({ id: "e2", pageCount: 200 })],
      reads: [
        reading({ id: "old", outcome: "finished", editionId: "e1" }),
        reading({ id: "a", editionId: "e1", progressValue: 10 }),
        reading({ id: "b", editionId: "e2", progressValue: 50 }),
      ],
    });
    expect(currentRead(b)?.id).toBe("b");
    expect(bookProgress(b)).toBe(25);
  });

  it("carries progress across a unit switch through the page count", () => {
    expect(convertProgress(100, "page", "percent", 400)).toBe(25);
    expect(convertProgress(25, "percent", "page", 400)).toBe(100);
    expect(convertProgress(1, "page", "percent", 3)).toBe(33.3);
    expect(convertProgress(100, "page", "percent", null)).toBeNull();
    expect(convertProgress(7, "page", "page", null)).toBe(7);
    expect(convertProgress(null, "page", "percent", 400)).toBeNull();
  });

  it("bounds the value by the unit", () => {
    expect(progressMax("percent", e)).toBe(100);
    expect(progressMax("page", e)).toBe(400);
    expect(progressMax("page", null)).toBe(100_000);
  });
});

describe("the book page", () => {
  beforeEach(() => {
    getBook.mockReset();
    preferences.value = {
      ...DEFAULT_PREFERENCES,
      date_format: "iso",
      date_format_chosen: true,
    };
  });
  afterEach(() => {
    preferences.value = { ...DEFAULT_PREFERENCES };
  });

  async function render(b: Book) {
    getBook.mockResolvedValue(b);
    const wrapper = mount(BookDetail, {
      global: {
        stubs: {
          MediaTopBar: true,
          BackButton: true,
          MyNote: true,
          RetryMessage: true,
        },
      },
    });
    await flushPromises();
    return wrapper;
  }

  it("lists every edition, marking the one being read", async () => {
    const wrapper = await render(
      book({
        authors: ["Mira Holt"],
        firstPublished: "1987-01-01",
        firstPublishedPrecision: "year",
        activeEditionId: "e2",
        editions: [
          edition({
            id: "e1",
            format: "hardcover",
            publisher: "Gullwing Press",
            isbn13: "9780000000002",
            pageCount: 320,
            publishDate: "1987-03-01",
            publishDatePrecision: "month",
          }),
          edition({
            id: "e2",
            format: "ebook",
            acquiredAt: "2024-11-05",
          }),
        ],
        reads: [
          reading({
            editionId: "e2",
            startedOn: "2025-01-02",
            progressUnit: "percent",
            progressValue: 40,
          }),
        ],
      }),
    );
    const rows = wrapper.findAll(".book-row");
    // one read and two editions
    expect(rows).toHaveLength(3);
    const editions = wrapper.findAll("[aria-labelledby=editions-heading] li");
    expect(editions[0]!.text()).toContain("Hardcover · Gullwing Press");
    expect(editions[0]!.text()).toContain("ISBN 9780000000002");
    expect(editions[0]!.text()).toContain("320 pages");
    expect(editions[0]!.text()).not.toContain("Reading this edition");
    expect(editions[1]!.classes()).toContain("active");
    expect(editions[1]!.text()).toContain("Reading this edition");
    expect(wrapper.text()).toContain("40%");
  });

  it("shows every date through the shared formatter at its precision", async () => {
    const wrapper = await render(
      book({
        firstPublished: "1987-01-01",
        firstPublishedPrecision: "year",
        editions: [
          edition({
            publishDate: "1987-03-01",
            publishDatePrecision: "month",
            acquiredAt: "2024-11-05",
          }),
        ],
        reads: [
          reading({
            outcome: "finished",
            startedOn: "2025-01-02",
            finishedOn: "2025-02-03",
          }),
        ],
      }),
    );
    const text = wrapper.text();
    // a year is only a year, never "1 January 1987"
    expect(text).toContain("First published 1987");
    expect(text).not.toContain("1987-01-01");
    expect(text).toContain("acquired 2024-11-05");
    expect(text).toContain("2025-01-02 – 2025-02-03");

    preferences.value = {
      ...DEFAULT_PREFERENCES,
      date_format: "dmy",
      date_format_chosen: true,
    };
    await flushPromises();
    expect(wrapper.text()).toContain("05/11/2024");
  });

  it("renders a work with no metadata, editions or reads", async () => {
    const wrapper = await render(book());
    const text = wrapper.text();
    expect(wrapper.get("h1").text()).toBe("The Lantern Archive");
    expect(text).not.toContain("First published");
    expect(text).toContain("No reads yet");
    expect(text).toContain("No editions yet");
    expect(text).toContain("Start reading");
    expect(wrapper.find(".book-cover.empty").exists()).toBe(true);
  });
});
