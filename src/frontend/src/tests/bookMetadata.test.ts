// Book provider results (Plugin API 1.1.20, C15 §6.2): ISBN reading, how a
// result is found by author or ISBN, what it fills in, and how a provider
// edition becomes one of the work's editions. All books here are invented.
import { describe, expect, it } from "vitest";
import { normalizeIsbn13 } from "../utils/isbn";
import { matchesMetadataQuery } from "../composables/useMetadataSearch";
import {
  bookDate,
  bookMetadataResult,
  editionInLibrary,
  editionInput,
  matchedEdition,
  providerEditionTitle,
} from "../utils/bookMetadata";
import type {
  MetadataBookEdition,
  MetadataCandidate,
} from "../services/metadata";
import type { Book } from "../types/book";

function candidate(over: Partial<MetadataCandidate> = {}): MetadataCandidate {
  return {
    id: "c1",
    rank: 0,
    external_id: "W1",
    title: "The Lantern Archive",
    year: 1813,
    provider: "official.metadata-atlas.atlas",
    provider_name: "Atlas",
    providers: ["official.metadata-atlas.atlas"],
    provider_ids: { atlas: "atlas:01BOOK" },
    media_type: "book",
    alternate_titles: [],
    authors: ["Ada Quill"],
    isbn_13: null,
    metadata: {},
    assets: [],
    ...over,
  };
}

describe("ISBN text", () => {
  it("reads either form, with hyphens and spaces, as an ISBN-13", () => {
    expect(normalizeIsbn13("978-0-00-000000-2")).toBe("9780000000002");
    expect(normalizeIsbn13("0 00 000001 9")).toBe("9780000000019");
    expect(normalizeIsbn13("080442957x")).toBe("9780804429573");
  });
  it("never guesses a wrong check digit or a title", () => {
    expect(normalizeIsbn13("9780000000003")).toBeNull();
    expect(normalizeIsbn13("000000001X")).toBeNull();
    expect(normalizeIsbn13("The Lantern Archive")).toBeNull();
  });
});

describe("finding a book result", () => {
  it("matches the title, an author, or the ISBN the lookup matched", () => {
    expect(matchesMetadataQuery(candidate(), "lantern")).toBe(true);
    expect(matchesMetadataQuery(candidate(), "quill")).toBe(true);
    expect(matchesMetadataQuery(candidate(), "marsh")).toBe(false);
    const looked = candidate({ isbn_13: "9780000000002" });
    expect(matchesMetadataQuery(looked, "978-0-00-000000-2")).toBe(true);
    expect(matchesMetadataQuery(looked, "9780000000019")).toBe(false);
    // an ISBN lookup's answer stands even when it does not repeat the ISBN
    expect(matchesMetadataQuery(candidate(), "9780000000002")).toBe(true);
  });
  it("keeps the title rule for other media", () => {
    const movie = candidate({ media_type: "movie", authors: undefined });
    expect(matchesMetadataQuery(movie, "9780000000002")).toBe(false);
  });
});

describe("a book result", () => {
  const result = bookMetadataResult(
    candidate({
      metadata: {
        title: "The Lantern Archive",
        description: "An invented novel.",
        genres: ["Fantasy"],
        book: {
          authors: [
            { name: "Ada Quill", role: "author" },
            { name: "Ben Marsh", role: "translator" },
          ],
          subtitle: "A Novel",
          isbn_13: "9780000000002",
          series_name: "Lantern Archive",
          series_position: "1",
          original_language: "en",
          first_published: { date: "1813", precision: "year" },
          editions: [
            {
              key: "ED1",
              isbn_13: "9780000000002",
              format: "paperback",
              publisher: "Harbor Books",
              publish_date: { date: "2005-08", precision: "month" },
              page_count: 528,
              language: "en",
            },
            { key: "ED2", isbn_10: "0000000019", format: "audiobook" },
          ],
        },
      },
      assets: [
        {
          kind: "poster",
          url: "https://covers.test/1.jpg",
          width: null,
          height: null,
        },
      ],
    }),
  );

  it("fills the work's fields with the provider's values", () => {
    expect(result).toMatchObject({
      title: "The Lantern Archive",
      subtitle: "A Novel",
      authors: ["Ada Quill", "Ben Marsh"],
      firstPublished: "1813-01-01",
      firstPublishedPrecision: "year",
      language: "en",
      seriesName: "Lantern Archive",
      seriesPosition: "1",
      genres: ["Fantasy"],
      coverUrl: "https://covers.test/1.jpg",
      providerIds: { atlas: "atlas:01BOOK" },
      isbn13: "9780000000002",
    });
  });

  it("falls back to the candidate's authors and year", () => {
    const bare = bookMetadataResult(candidate());
    expect(bare.authors).toEqual(["Ada Quill"]);
    expect([bare.firstPublished, bare.firstPublishedPrecision]).toEqual([
      "1813-01-01",
      "year",
    ]);
    expect(matchedEdition(bare, "978-0-00-000000-2")).toBeNull();
  });

  it("adds the edition the ISBN search named", () => {
    expect(matchedEdition(result, "978-0-00-000000-2")?.key).toBe("ED1");
    const unlisted = { ...result, isbn13: "9790000000001" };
    expect(matchedEdition(unlisted, "9790000000001")).toEqual({
      isbn_13: "9790000000001",
    });
  });

  it("adds no edition after a title or author search", () => {
    // the provider reports an ISBN, but the person searched by title
    expect(result.isbn13).toBe("9780000000002");
    expect(matchedEdition(result, "Lantern Archive")).toBeNull();
    expect(matchedEdition(result, "Ada Quill")).toBeNull();
    // an ISBN naming another edition does not pick this one either
    expect(matchedEdition(result, "9780000000019")).toBeNull();
  });

  it("turns a provider edition into the edition form", () => {
    expect(editionInput(result.editions[0]!)).toEqual({
      isbn13: "9780000000002",
      isbn10: null,
      format: "paperback",
      publisher: "Harbor Books",
      publishDate: "2005-08-01",
      publishDatePrecision: "month",
      pageCount: 528,
      language: "en",
    });
    expect(
      editionInput({ isbn_10: "0000000019", format: "scroll" as never }),
    ).toMatchObject({ isbn13: null, isbn10: "0000000019", format: "other" });
    expect(bookDate({ date: "2005-08-01", precision: "day" })).toEqual([
      "2005-08-01",
      null,
    ]);
  });

  it("names an edition and knows when the work already holds it", () => {
    expect(providerEditionTitle(result.editions[0]!)).toBe(
      "Paperback · Harbor Books",
    );
    expect(providerEditionTitle({ name: "First printing" })).toBe(
      "First printing",
    );
    const owned = {
      editions: [{ isbn13: "9780000000019" }],
    } as unknown as Book;
    expect(editionInLibrary(owned, result.editions[1]!)).toBe(true);
    expect(editionInLibrary(owned, result.editions[0]!)).toBe(false);
    expect(editionInLibrary(owned, { key: "ED3" })).toBe(false);
  });

  it("treats an ISBN-less edition already added from the provider as held", () => {
    const edition: MetadataBookEdition = {
      key: "ED9",
      format: "paperback",
      publisher: "Synth Press",
      page_count: 210,
    };
    const input = editionInput(edition);
    const none = { editions: [] } as unknown as Book;
    expect(editionInLibrary(none, edition)).toBe(false);
    const added = {
      editions: [{ ...input, isbn13: null, isbn10: null }],
    } as unknown as Book;
    expect(editionInLibrary(added, edition)).toBe(true);
    const other = {
      editions: [
        { ...input, isbn13: null, isbn10: null, publisher: "Other House" },
      ],
    } as unknown as Book;
    expect(editionInLibrary(other, edition)).toBe(false);
  });
});
