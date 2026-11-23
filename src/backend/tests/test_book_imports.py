"""Book list imports (C15 6.2, #265 phase 4): Goodreads CSV, StoryGraph CSV and an ISBN list, and
the provider matching that identifies their rows, ISBN first through one batched ``resolve`` call
of at most 200 rows (T-H3), then title and author.

No database: the parsers and the matcher are pure. Every book here is synthetic: invented titles
and authors, ISBNs computed to have valid check digits (Open Library data is not redistributable).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.database.models.book import BookFormat, BookStatus, ReadOutcome
from src.features.imports import book_matching
from src.features.imports.book_matching import (
    MAX_ROW_LOOKUPS,
    match_books,
    title_agrees,
    titles_agree,
)
from src.features.imports.books import (
    BookImportError,
    ImportedBook,
    detect_format,
    parse_book_import,
    parse_goodreads,
    parse_isbn_list,
    parse_storygraph,
)
from src.plugin_api.metadata_contracts import (
    MAX_RESOLVE_ITEMS,
    MediaType,
    MetadataCandidate,
    MetadataProviderRequest,
    MetadataResolveRequest,
    ProviderFailure,
    ProviderResponse,
)

# Valid synthetic ISBNs (the ISBN-10 beside each names the same book).
GLASS_13, GLASS_10 = "9781111111113", "1111111111"
LANTERN_13 = "9782222222224"
HARBOUR_13, HARBOUR_10 = "9780000000019", "0000000019"
NINTH_13 = "9783333333335"
ONLY_979 = "9791000000008"

GOODREADS_HEADER = (
    "Book Id,Title,Author,Author l-f,Additional Authors,ISBN,ISBN13,My Rating,Average Rating,"
    "Publisher,Binding,Number of Pages,Year Published,Original Publication Year,Date Read,"
    "Date Added,Bookshelves,Bookshelves with positions,Exclusive Shelf,My Review,Spoiler,"
    "Private Notes,Read Count,Owned Copies\n"
)
GOODREADS = (
    GOODREADS_HEADER
    + f'101,"The Glass Orchard (Lantern Archive, #1)",Ada Quill,"Quill, Ada",Ben Marsh,'
    f'"=""{GLASS_10}""","=""{GLASS_13}""",4,3.91,Harbor Books,Paperback,412,2005,1998,'
    '2021/03/14,2020/12/01,"favourites, read","favourites (#2), read (#7)",read,Lovely.,,'
    "Lent by Mia.,2,1\n"
    '102,Salt and Ember,Cora Vale,"Vale, Cora",,"=""""","=""""",0,4.10,,Hardcover,,,2019,,'
    "2022/01/05,to-read,to-read (#3),to-read,,,,0,0\n"
    '103,Tidewater Letters,Dov Pike,"Pike, Dov",,"=""0000000018""","=""""",0,3.5,,'
    "Kindle Edition,220,2020,2020,,2022/02/01,currently-reading,,currently-reading,,,,0,0\n"
    '104,Quiet Harbour,Fen Lo,"Lo, Fen",,"=""""","=""""",2,3.0,,Audible Audio,,,,,'
    "2022/03/01,did-not-finish,,did-not-finish,,,,0,0\n"
)

STORYGRAPH_HEADER = (
    "Title,Authors,Contributors,ISBN/UID,Format,Read Status,Date Added,Last Date Read,"
    "Dates Read,Read Count,Moods,Pace,Character- or Plot-Driven?,"
    "Strong Character Development?,Loveable Characters?,Diverse Characters?,"
    "Flawed Characters?,Star Rating,Review,Content Warnings,Content Warning Description,Tags,"
    "Owned?\n"
)
STORYGRAPH = (
    STORYGRAPH_HEADER
    + f'The Glass Orchard,"Ada Quill, Ben Marsh",Ben Marsh (Translator),{GLASS_13},paperback,'
    'read,2023/01/02,2023/02/10,"2022/05/01-2022/05/20, 2023/01/20-2023/02/10",2,'
    'reflective,slow,Character,Yes,Yes,No,Yes,4.25,Great.,,,"cosy, rainy-day",Yes\n'
    "Salt and Ember,Cora Vale,,a1b2c3d4-uid,digital,to-read,2023/03/01,,,0,,,,,,,,,,,,,No\n"
    f"Ninth Lantern,Eli Ford,,{NINTH_13},audio,currently-reading,2023/04/01,,,0,"
    ",,,,,,,,,,,,No\n"
    "Wren's Ledger,Gil Moss,,,hardcover,did-not-finish,2023/04/05,2023/04/09,,0,"
    ",,,,,,,1.5,,,,,No\n"
    "Paper Lanterns,Hal Reed,,,paperback,paused,2023/04/07,,,0,,,,,,,,,,,,,No\n"
)


# ------------------------------------------------------------------------------- Goodreads


def test_goodreads_rows_map_onto_books_editions_and_reads():
    rows = parse_goodreads(GOODREADS.encode())
    assert [row.title for row in rows] == [
        "The Glass Orchard",
        "Salt and Ember",
        "Tidewater Letters",
        "Quiet Harbour",
    ]
    glass = rows[0]
    assert glass.authors == ["Ada Quill", "Ben Marsh"]
    assert (glass.isbn_13, glass.isbn_10) == (GLASS_13, GLASS_10)
    assert glass.series_name == "Lantern Archive"
    assert glass.series_position == "1"
    assert glass.status is BookStatus.READ
    # five stars onto the app's ten-point scale
    assert glass.rating == Decimal("8.00")
    assert glass.first_published_year == 1998
    assert glass.edition_year == 2005
    assert glass.publisher == "Harbor Books"
    assert glass.page_count == 412
    assert glass.format is BookFormat.PAPERBACK
    assert glass.owned is True
    assert glass.tags == ["favourites"]
    assert glass.note == "Lovely.\n\nLent by Mia."
    # Read Count 2 with one dated read: one dated finish and one undated finish
    assert [(r.started_on, r.finished_on, r.outcome) for r in glass.reads] == [
        (None, dt.date(2021, 3, 14), ReadOutcome.FINISHED),
        (None, None, ReadOutcome.FINISHED),
    ]

    salt = rows[1]
    assert salt.status is BookStatus.WISHLIST
    assert salt.rating is None  # Goodreads writes 0 for "not rated"
    assert salt.isbn_13 is None and salt.invalid_isbn is None
    assert salt.reads == []

    tidewater = rows[2]
    # a wrong check digit is reported and never repaired; the book still imports
    assert tidewater.isbn_13 is None
    assert tidewater.invalid_isbn == "0000000018"
    assert tidewater.status is BookStatus.IN_PROGRESS
    assert tidewater.format is BookFormat.EBOOK
    assert [r.outcome for r in tidewater.reads] == [ReadOutcome.READING]

    quiet = rows[3]
    assert quiet.status is BookStatus.DROPPED
    assert quiet.format is BookFormat.AUDIOBOOK
    assert [r.outcome for r in quiet.reads] == [ReadOutcome.ABANDONED]


def test_goodreads_owned_unread_book_is_backlog_not_wishlist():
    raw = GOODREADS_HEADER + (
        '201,Owned Shelf Book,Ina Roe,"Roe, Ina",,"=""""","=""""",0,0,,Paperback,,,,,'
        "2022/01/01,to-read,,to-read,,,,0,1\n"
    )
    (row,) = parse_goodreads(raw.encode())
    assert row.status is BookStatus.BACKLOG
    assert row.owned is True


def test_goodreads_refuses_a_file_that_is_not_a_goodreads_export():
    with pytest.raises(BookImportError, match="Goodreads"):
        parse_goodreads(b"title,author\nA,B\n")


# ------------------------------------------------------------------------------- StoryGraph


def test_storygraph_rows_map_onto_books_editions_and_reads():
    rows = parse_storygraph(STORYGRAPH.encode())
    assert [row.title for row in rows] == [
        "The Glass Orchard",
        "Salt and Ember",
        "Ninth Lantern",
        "Wren's Ledger",
        "Paper Lanterns",
    ]
    glass = rows[0]
    assert glass.authors == ["Ada Quill", "Ben Marsh"]
    assert glass.isbn_13 == GLASS_13
    assert glass.status is BookStatus.READ
    # StoryGraph's quarter stars (0-5) onto the ten-point scale
    assert glass.rating == Decimal("8.50")
    assert glass.format is BookFormat.PAPERBACK
    assert glass.owned is True
    assert glass.tags == ["cosy", "rainy-day"]
    assert glass.note == "Great."
    assert [(r.started_on, r.finished_on, r.outcome) for r in glass.reads] == [
        (dt.date(2022, 5, 1), dt.date(2022, 5, 20), ReadOutcome.FINISHED),
        (dt.date(2023, 1, 20), dt.date(2023, 2, 10), ReadOutcome.FINISHED),
    ]

    salt = rows[1]
    # a StoryGraph UID is not an ISBN and is not reported as a bad one either
    assert salt.isbn_13 is None and salt.invalid_isbn is None
    assert salt.status is BookStatus.WISHLIST
    assert salt.format is BookFormat.EBOOK

    ninth = rows[2]
    assert ninth.status is BookStatus.IN_PROGRESS
    assert ninth.format is BookFormat.AUDIOBOOK

    wren = rows[3]
    assert wren.status is BookStatus.DROPPED
    assert wren.rating == Decimal("3.00")
    assert [(r.finished_on, r.outcome) for r in wren.reads] == [
        (dt.date(2023, 4, 9), ReadOutcome.ABANDONED)
    ]
    assert rows[4].status is BookStatus.ON_HOLD


def test_storygraph_read_range_that_ends_before_it_starts_keeps_only_the_finish():
    raw = STORYGRAPH_HEADER + (
        "Backwards,Ida Lane,,,paperback,read,2023/01/01,2023/01/02,2023/01/09-2023/01/02,1,"
        ",,,,,,,,,,,,No\n"
    )
    (row,) = parse_storygraph(raw.encode())
    assert [(r.started_on, r.finished_on) for r in row.reads] == [(None, dt.date(2023, 1, 2))]


# ------------------------------------------------------------------------------- ISBN list


def test_isbn_list_normalises_reports_bad_entries_and_drops_repeats():
    raw = (
        f"# my shelf\n{GLASS_13}\n0-000-00001-9, 978-2-222-22222-4\n\nnot-an-isbn\n"
        f"{GLASS_10}\n{ONLY_979}\n"
    ).encode()
    result = parse_isbn_list(raw)
    assert [row.isbn_13 for row in result.rows] == [GLASS_13, HARBOUR_13, LANTERN_13, ONLY_979]
    assert result.rows[1].isbn_10 == HARBOUR_10
    assert all(row.title is None for row in result.rows)
    assert all(row.status is BookStatus.WISHLIST for row in result.rows)
    assert result.invalid == ["not-an-isbn"]
    assert result.repeated == 1


def test_formats_are_detected_from_the_header():
    assert detect_format(GOODREADS.encode()) == "goodreads"
    assert detect_format(STORYGRAPH.encode()) == "storygraph"
    assert detect_format(f"{GLASS_13}\n{LANTERN_13}\n".encode()) == "isbn_list"
    with pytest.raises(BookImportError):
        detect_format(b"name,score\nA,1\n")


def test_a_large_file_cut_inside_a_character_is_still_detected():
    """Detection reads the first 64 KB; a cut inside a multi-byte character is not a bad file."""
    row = '1,"Café Ledger été",Ada Quill,"Quill, Ada",,,,0,0,,,,,,,,,,to-read,,,,0,0\n'
    cut_mid_character = 0
    for padding in range(4):
        raw = (GOODREADS_HEADER + "x" * padding + "\n" + row * 2000).encode()
        assert len(raw) > 65_536
        cut_mid_character += raw[65_536] & 0xC0 == 0x80
        assert detect_format(raw) == "goodreads"
    assert cut_mid_character  # at least one padding puts the 64 KB cut inside "é"
    for padding in range(2):  # a first line longer than 64 KB, cut mid-character once
        raw = ("x" * padding + "é" * 40_000 + "\n").encode()
        with pytest.raises(BookImportError, match="not a Goodreads"):
            detect_format(raw)
    with pytest.raises(BookImportError, match="UTF-8"):
        detect_format(b"Title\xff,Author\n")


def test_parse_book_import_bounds_size_and_rows(monkeypatch):
    from src.features.imports import books

    monkeypatch.setattr(books, "MAX_ROWS", 2)
    with pytest.raises(BookImportError, match="more than 2"):
        parse_book_import(GOODREADS.encode(), "goodreads")
    monkeypatch.setattr(books, "MAX_BYTES", 10)
    with pytest.raises(BookImportError, match="too large"):
        parse_book_import(GOODREADS.encode(), "goodreads")
    with pytest.raises(BookImportError, match="UTF-8"):
        parse_book_import(b"\xff\xfe\x00", "isbn_list")


# ------------------------------------------------------------------------------- matching


def _candidate(title: str, *, isbn: str | None = None, authors=(), year=None, key="W1"):
    return MetadataCandidate(
        external_id=key,
        title=title,
        provider="books.example",
        media_type=MediaType.BOOK,
        provider_ids={"books.example": key},
        authors=tuple(authors),
        isbn_13=isbn,
        year=year,
    )


@dataclass
class ResolvingProvider:
    """A provider with a ``resolve`` operation: a catalogue keyed by ISBN and by title."""

    by_isbn: dict[str, MetadataCandidate] = field(default_factory=dict)
    by_title: dict[str, MetadataCandidate] = field(default_factory=dict)
    calls: list[MetadataResolveRequest] = field(default_factory=list)
    searches: list[MetadataProviderRequest] = field(default_factory=list)
    failure: str | None = None
    name: str = "Example books"
    priority: int = 1

    @property
    def declaration(self):
        return SimpleNamespace(
            operations=SimpleNamespace(resolve="resolve", search="search"),
            provider_id="books.example",
        )

    async def resolve(self, request: MetadataResolveRequest) -> ProviderResponse:
        assert request.media_type is MediaType.BOOK
        assert len(request.items) <= MAX_RESOLVE_ITEMS
        self.calls.append(request)
        if self.failure:
            return ProviderResponse(failure=ProviderFailure(code=self.failure))
        resolved = []
        for item in request.items:
            if item.isbn_13 and item.isbn_13 in self.by_isbn:
                resolved.append(
                    {
                        "key": item.key,
                        "matched_by": "identifier",
                        "candidate": self.by_isbn[item.isbn_13],
                    }
                )
            elif item.isbn_13 is None and item.title in self.by_title:
                resolved.append(
                    {"key": item.key, "matched_by": "title", "candidate": self.by_title[item.title]}
                )
        return ProviderResponse.model_validate({"resolved": resolved})

    async def invoke(self, operation, request, candidate=None):  # pragma: no cover - unused
        self.searches.append(request)
        return ProviderResponse()


@dataclass
class SearchOnlyProvider:
    """A provider without ``resolve``: each row costs one search."""

    results: dict[str, tuple[MetadataCandidate, ...]] = field(default_factory=dict)
    searches: list[MetadataProviderRequest] = field(default_factory=list)
    name: str = "Search books"
    priority: int = 2

    @property
    def declaration(self):
        return SimpleNamespace(
            operations=SimpleNamespace(resolve=None, search="search"),
            provider_id="search.example",
        )

    async def invoke(self, operation, request, candidate=None):
        assert operation == "search"
        self.searches.append(request)
        return ProviderResponse(candidates=self.results.get(request.query, ()))


def _isbn_row(isbn: str, title: str | None = None, authors=()) -> ImportedBook:
    return ImportedBook(line=1, title=title, authors=list(authors), isbn_13=isbn)


def _title_row(title: str, authors=(), year=None) -> ImportedBook:
    return ImportedBook(line=1, title=title, authors=list(authors), first_published_year=year)


async def test_isbn_rows_are_resolved_in_batches_of_at_most_200():
    """T-H3: 450 ISBN rows are three resolve calls (200, 200, 50), never one per row."""
    body = "978000"
    rows = []
    catalogue = {}
    from src.features.books.isbn import _isbn13_check

    for number in range(450):
        prefix = f"{body}{number:06d}"
        isbn = prefix + _isbn13_check(prefix)
        rows.append(_isbn_row(isbn))
        catalogue[isbn] = _candidate(f"Book {number}", isbn=isbn, key=f"W{number}")
    provider = ResolvingProvider(by_isbn=catalogue)

    report = await match_books(rows, [provider], uuid4())

    assert [len(call.items) for call in provider.calls] == [200, 200, 50]
    assert report.resolve_calls == 3
    assert report.row_lookups == 0
    assert len(report.matches) == 450
    assert {match.matched_by for match in report.matches.values()} == {"identifier"}
    assert report.matches[7].candidate.title == "Book 7"


async def test_isbn_first_then_title_and_author_for_the_rest():
    rows = [
        _isbn_row(GLASS_13, "The Glass Orchard", ["Ada Quill"]),
        _title_row("Salt and Ember", ["Cora Vale"]),
        _isbn_row(LANTERN_13, "Ninth Lantern", ["Eli Ford"]),
        _title_row("Nobody Knows This One", ["Jo Port"]),
    ]
    provider = ResolvingProvider(
        by_isbn={GLASS_13: _candidate("The Glass Orchard", isbn=GLASS_13, authors=["Ada Quill"])},
        by_title={
            "Salt and Ember": _candidate("Salt and Ember", authors=["Cora Vale"], key="W2"),
            "Ninth Lantern": _candidate("Ninth Lantern", authors=["Eli Ford"], key="W3"),
        },
    )

    report = await match_books(rows, [provider], uuid4())

    # the identifier pass carries only the ISBN rows; the title pass carries the rest
    assert [[item.isbn_13 for item in call.items] for call in provider.calls] == [
        [GLASS_13, LANTERN_13],
        [None, None, None],
    ]
    assert [item.title for item in provider.calls[1].items] == [
        "Salt and Ember",
        "Ninth Lantern",
        "Nobody Knows This One",
    ]
    assert provider.calls[1].items[0].authors == ("Cora Vale",)
    assert {index: match.matched_by for index, match in report.matches.items()} == {
        0: "identifier",
        1: "title",
        2: "title",
    }
    assert 3 not in report.matches


async def test_a_title_answer_that_does_not_agree_is_not_taken():
    """A provider's title match is a candidate, not an identity: the host checks it again."""
    rows = [_title_row("The Glass Orchard", ["Ada Quill"])]
    provider = ResolvingProvider(
        by_title={"The Glass Orchard": _candidate("The Glass Orchard", authors=["Someone Else"])}
    )
    report = await match_books(rows, [provider], uuid4())
    assert report.matches == {}


async def test_an_identifier_answer_naming_another_isbn_is_not_taken():
    rows = [_isbn_row(GLASS_13)]
    provider = ResolvingProvider(
        by_isbn={GLASS_13: _candidate("The Glass Orchard", isbn=LANTERN_13)}
    )
    report = await match_books(rows, [provider], uuid4())
    assert report.matches == {}


async def test_a_failed_batch_moves_on_and_never_blocks_the_import():
    rows = [_isbn_row(GLASS_13), _title_row("Salt and Ember", ["Cora Vale"])]
    broken = ResolvingProvider(failure="unavailable", name="Broken")
    fallback = SearchOnlyProvider(
        results={
            GLASS_13: (_candidate("The Glass Orchard", isbn=GLASS_13),),
            "Salt and Ember Cora Vale": (_candidate("Salt and Ember", authors=["Cora Vale"]),),
        }
    )
    report = await match_books(rows, [broken, fallback], uuid4())
    # the broken provider is asked once and then skipped
    assert len(broken.calls) == 1
    assert report.failed_providers == ["Broken"]
    assert {index: match.provider for index, match in report.matches.items()} == {
        0: "Search books",
        1: "Search books",
    }


async def test_providers_without_resolve_are_searched_row_by_row_with_the_isbn_option():
    rows = [_isbn_row(GLASS_13, "The Glass Orchard", ["Ada Quill"])]
    provider = SearchOnlyProvider(
        results={GLASS_13: (_candidate("The Glass Orchard", isbn=GLASS_13),)}
    )
    report = await match_books(rows, [provider], uuid4())
    (request,) = provider.searches
    assert request.media_type is MediaType.BOOK
    assert request.options["isbn"] == GLASS_13
    assert request.policy == "background"
    assert report.matches[0].matched_by == "identifier"
    assert report.row_lookups == 1


async def test_row_by_row_lookups_are_bounded(monkeypatch):
    monkeypatch.setattr(book_matching, "MAX_ROW_LOOKUPS", 3)
    rows = [_title_row(f"Book {n}", ["Kai Moe"]) for n in range(10)]
    provider = SearchOnlyProvider()
    report = await match_books(rows, [provider], uuid4())
    assert len(provider.searches) == 3
    assert report.not_looked_up == 7
    assert MAX_ROW_LOOKUPS >= 50  # the real bound is not tiny


async def test_the_lookup_bound_counts_both_searches_of_a_row(monkeypatch):
    """A row with an ISBN and a title can cost two searches; the bound is never passed by one."""
    monkeypatch.setattr(book_matching, "MAX_ROW_LOOKUPS", 3)
    rows = [
        _isbn_row(GLASS_13, "The Glass Orchard", ["Ada Quill"]),
        _isbn_row(LANTERN_13, "Lantern Hours", ["Bo Reed"]),
        _isbn_row(HARBOUR_13, "Harbour Lights", ["Cy Lund"]),
    ]
    provider = SearchOnlyProvider()
    report = await match_books(rows, [provider], uuid4())
    assert len(provider.searches) == 3
    assert report.row_lookups == 3


async def test_no_provider_means_every_row_stays_manual():
    report = await match_books([_isbn_row(GLASS_13)], [], uuid4())
    assert report.matches == {}
    assert report.resolve_calls == 0


@pytest.mark.parametrize(
    ("row_title", "row_authors", "candidate_title", "candidate_authors", "agrees"),
    [
        ("The Glass Orchard", ["Ada Quill"], "the glass orchard", ["Ada Quill"], True),
        ("Glass Orchard: A Novel", ["Ada Quill"], "Glass Orchard", ["A. Quill"], True),
        ("Café Ledger", [], "Cafe Ledger", [], True),
        ("The Glass Orchard", ["Ada Quill"], "The Glass Orchard", ["Cora Vale"], False),
        ("The Glass Orchard", ["Ada Quill"], "The Glass Orchards", ["Ada Quill"], False),
        ("The Glass Orchard", ["Ada Quill"], "The Glass Orchard", [], True),
        # different subtitles are different books, even with the same series title and author
        (
            "Ember Cycle: The First Spark",
            ["Ann Sand"],
            "Ember Cycle: The Last Coal",
            ["Ann Sand"],
            False,
        ),
        (
            "Ember Cycle: The First Spark",
            ["Ann Sand"],
            "Ember Cycle: First Spark",
            ["Ann Sand"],
            True,
        ),
        ("Ember Cycle: The First Spark", ["Ann Sand"], "Ember Cycle", ["Ann Sand"], True),
    ],
)
def test_title_agrees(row_title, row_authors, candidate_title, candidate_authors, agrees):
    row = _title_row(row_title, row_authors)
    assert title_agrees(row, _candidate(candidate_title, authors=candidate_authors)) is agrees


@pytest.mark.parametrize(
    ("first", "second", "agrees"),
    [
        ("Lantern Archive: The Glass Orchard", "Lantern Archive: Salt and Ember", False),
        ("Lantern Archive: The Glass Orchard", "lantern archive : glass orchard", True),
        ("Lantern Archive", "Lantern Archive: The Glass Orchard", True),
        ("The Lantern", "Lantern", True),
        ("Lantern", "Lanterns", False),
        (":", ":", False),
    ],
)
def test_titles_agree(first, second, agrees):
    assert titles_agree(first, second) is agrees
    assert titles_agree(second, first) is agrees


async def test_a_title_answer_for_another_volume_is_not_taken():
    """A provider answering with the next volume of a series is refused, in both the batched and
    the row-by-row lookups."""
    row = _title_row("Lantern Archive: The Glass Orchard", ["Ada Quill"])
    other = _candidate("Lantern Archive: Salt and Ember", authors=["Ada Quill"])
    batched = ResolvingProvider(by_title={row.title: other})
    assert (await match_books([row], [batched], uuid4())).matches == {}
    searched = SearchOnlyProvider(results={f"{row.title} Ada Quill": (other,)})
    assert (await match_books([row], [searched], uuid4())).matches == {}


async def test_a_match_carries_the_namespaces_its_provider_owns():
    row = _isbn_row(GLASS_13)
    provider = ResolvingProvider(by_isbn={GLASS_13: _candidate("The Glass Orchard", isbn=GLASS_13)})
    (match,) = (await match_books([row], [provider], uuid4())).matches.values()
    assert match.namespaces == frozenset({"books.example"})


@dataclass
class SlowProvider(ResolvingProvider):
    """A resolve provider that takes ``seconds`` per call."""

    seconds: float = 0.0

    async def resolve(self, request: MetadataResolveRequest) -> ProviderResponse:
        import asyncio

        await asyncio.sleep(self.seconds)
        return await super().resolve(request)


def _isbn_rows(count: int) -> list[ImportedBook]:
    from src.features.books.isbn import _isbn13_check

    rows = []
    for number in range(count):
        prefix = f"979100{number:06d}"
        rows.append(_isbn_row(prefix + _isbn13_check(prefix)))
    return rows


async def test_batched_calls_are_bounded_per_import(monkeypatch):
    assert book_matching.MAX_RESOLVE_CALLS >= 10  # the real bound covers a large library
    monkeypatch.setattr(book_matching, "MAX_RESOLVE_CALLS", 2)
    provider = ResolvingProvider()
    report = await match_books(_isbn_rows(1000), [provider], uuid4())
    # two calls of 200, then no more: the rest are added by hand, not reported as a failure
    assert [len(call.items) for call in provider.calls] == [200, 200]
    assert report.resolve_calls == 2
    assert report.not_looked_up == 600
    assert report.failed_providers == []


async def test_matching_stops_at_the_import_deadline(monkeypatch):
    assert book_matching.MATCH_SECONDS <= 300  # well inside a reverse proxy's timeout
    monkeypatch.setattr(book_matching, "MATCH_SECONDS", 0.2)
    slow = SlowProvider(seconds=0.15)
    later = SearchOnlyProvider()
    report = await match_books(_isbn_rows(600), [slow, later], uuid4())
    # the first call ends in time, the second is cut off at the deadline, the third never starts
    assert len(slow.calls) == 1
    assert report.resolve_calls == 2
    assert later.searches == []
    assert report.not_looked_up == 400
    # running out of the import's time is not the provider's failure
    assert report.failed_providers == []


async def test_a_call_cut_at_the_deadline_ends_matching_even_if_the_clock_lags(monkeypatch):
    # Windows timers can fire a few milliseconds before time.monotonic() reaches the deadline;
    # the call cut by the import's time bound must still end matching, without blaming anyone.
    monkeypatch.setattr(book_matching, "MATCH_SECONDS", 100)
    monkeypatch.setattr(book_matching, "RESOLVE_SECONDS", 1000)  # every call is clipped
    real_bounded = book_matching.bounded
    calls = 0

    async def early_timer(awaitable, seconds):
        nonlocal calls
        calls += 1
        if calls == 1:
            return await real_bounded(awaitable, seconds)
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(book_matching, "bounded", early_timer)
    later = SearchOnlyProvider()
    report = await match_books(_isbn_rows(600), [ResolvingProvider(), later], uuid4())
    assert report.resolve_calls == 2
    assert later.searches == []
    assert report.not_looked_up == 400
    assert report.failed_providers == []
