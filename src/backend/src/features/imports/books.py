"""Reading book lists from elsewhere: a Goodreads CSV, a StoryGraph CSV, or a list of ISBNs.

C15 6.2 / #265 phase 4. Every format becomes the same ``ImportedBook`` rows; nothing here touches
the database or a provider. The files are bounded (size and rows) like the other imports.

- An ISBN is normalised with the edition rules (``features.books.isbn``): a wrong check digit is
  reported on the row (``invalid_isbn``) and never repaired; the book itself still imports.
- Ratings move onto the app's ten-point scale (Goodreads stars and StoryGraph's quarter stars
  are out of five; Goodreads writes 0 for "not rated").
- Reads: Goodreads gives one "Date Read" and a "Read Count"; StoryGraph gives every
  ``start-finish`` range. A dated read is kept with its dates; extra reads the count names are
  kept without dates. A read whose finish is before its start keeps only the finish.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from src.database.models.book import BookFormat, BookStatus, ReadOutcome
from src.features.books.isbn import InvalidIsbn, parse_isbn
from src.features.imports.ratings import parse_rating

MAX_BYTES = 30 * 1024 * 1024
MAX_ROWS = 20_000
# Extra undated reads kept for one row's read count (the work's limit is 200).
MAX_UNDATED_READS = 20
MAX_AUTHORS = 30
MAX_TAGS = 50

ImportFormat = Literal["goodreads", "storygraph", "isbn_list"]
FORMATS: tuple[ImportFormat, ...] = ("goodreads", "storygraph", "isbn_list")

_GOODREADS_COLUMNS = {"Title", "Author", "ISBN13", "Exclusive Shelf"}
_STORYGRAPH_COLUMNS = {"Title", "Authors", "ISBN/UID", "Read Status"}
# Goodreads puts the series after the title: "The Glass Orchard (Lantern Archive, #1)".
_SERIES = re.compile(
    r"^(?P<title>.+?)\s*\((?P<series>[^()]+?),?\s*#(?P<position>[\w.]{1,16})\)\s*$"
)
_ISBN_TOKEN = re.compile(r"[^\s,;]+(?:[\s-][^\s,;]+)*")
_ISBNISH = re.compile(r"^[\d\s-]{9,17}[\dXx]$")


class BookImportError(ValueError):
    """The file cannot be read as the format asked for."""


@dataclass
class ImportedRead:
    started_on: dt.date | None
    finished_on: dt.date | None
    outcome: ReadOutcome


@dataclass
class ImportedBook:  # pylint: disable=too-many-instance-attributes
    """One row of any book list, in the host's terms (a plain record of the row's fields)."""

    line: int
    title: str | None
    authors: list[str] = field(default_factory=list)
    isbn_13: str | None = None
    isbn_10: str | None = None
    # the text of an identifier that looked like an ISBN but was not a valid one
    invalid_isbn: str | None = None
    status: BookStatus = BookStatus.WISHLIST
    rating: Decimal | None = None
    first_published_year: int | None = None
    edition_year: int | None = None
    publisher: str | None = None
    page_count: int | None = None
    format: BookFormat | None = None
    owned: bool = False
    series_name: str | None = None
    series_position: str | None = None
    tags: list[str] = field(default_factory=list)
    note: str | None = None
    reads: list[ImportedRead] = field(default_factory=list)


@dataclass
class IsbnListResult:
    rows: list[ImportedBook]
    invalid: list[str]
    repeated: int


def _text(raw: bytes) -> str:
    if len(raw) > MAX_BYTES:
        raise BookImportError(f"The file is too large (at most {MAX_BYTES // (1024 * 1024)} MB).")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BookImportError("The file must be UTF-8 text.") from exc


def _reader(raw: bytes, required: set[str], source: str) -> csv.DictReader:
    reader = csv.DictReader(io.StringIO(_text(raw)))
    if not required.issubset(set(reader.fieldnames or [])):
        raise BookImportError(
            f"This does not look like a {source} export; expected the columns "
            + ", ".join(sorted(required))
            + "."
        )
    return reader


def _rows(reader: csv.DictReader) -> list[tuple[int, dict[str, str]]]:
    rows: list[tuple[int, dict[str, str]]] = []
    for row in reader:
        if len(rows) >= MAX_ROWS:
            raise BookImportError(f"The file contains more than {MAX_ROWS} rows.")
        rows.append((reader.line_num, {key: (value or "") for key, value in row.items() if key}))
    return rows


def _clean(value: str | None, limit: int) -> str | None:
    text = " ".join((value or "").split())
    return text[:limit] if text else None


def _int(value: str | None, low: int, high: int) -> int | None:
    text = (value or "").strip()
    if not text.isdecimal():
        return None
    number = int(text)
    return number if low <= number <= high else None


def _date(value: str | None) -> dt.date | None:
    text = (value or "").strip()
    for pattern in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _names(value: str | None) -> list[str]:
    names = [_clean(part, 200) for part in (value or "").split(",")]
    return [name for name in names if name][:MAX_AUTHORS]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _isbn(row: ImportedBook, *values: str | None) -> None:
    """The first value that is an ISBN; a value that only looks like one is reported."""
    for value in values:
        text = (value or "").strip().removeprefix("=").strip('"').strip()
        if not text:
            continue
        try:
            parsed = parse_isbn(text)
        except InvalidIsbn:
            if _ISBNISH.match(text) and row.invalid_isbn is None:
                row.invalid_isbn = text[:32]
            continue
        row.isbn_13, row.isbn_10 = parsed.isbn_13, parsed.isbn_10
        row.invalid_isbn = None
        return


def _note(*parts: str | None) -> str | None:
    text = "\n\n".join(part.strip() for part in parts if part and part.strip())
    return text[:20_000] or None


_FORMATS = {
    "paperback": BookFormat.PAPERBACK,
    "mass market paperback": BookFormat.PAPERBACK,
    "trade paperback": BookFormat.PAPERBACK,
    "hardcover": BookFormat.HARDCOVER,
    "library binding": BookFormat.HARDCOVER,
    "kindle edition": BookFormat.EBOOK,
    "ebook": BookFormat.EBOOK,
    "nook": BookFormat.EBOOK,
    "digital": BookFormat.EBOOK,
    "audiobook": BookFormat.AUDIOBOOK,
    "audible audio": BookFormat.AUDIOBOOK,
    "audio cd": BookFormat.AUDIOBOOK,
    "audio": BookFormat.AUDIOBOOK,
}


def _format(value: str | None) -> BookFormat | None:
    text = (value or "").strip().lower()
    if not text:
        return None
    return _FORMATS.get(text, BookFormat.OTHER)


_SHELF_STATUS = {
    "read": BookStatus.READ,
    "currently-reading": BookStatus.IN_PROGRESS,
    "to-read": BookStatus.WISHLIST,
    "did-not-finish": BookStatus.DROPPED,
    "dnf": BookStatus.DROPPED,
    "abandoned": BookStatus.DROPPED,
    "paused": BookStatus.ON_HOLD,
    "on-hold": BookStatus.ON_HOLD,
}
_OUTCOME = {
    BookStatus.IN_PROGRESS: ReadOutcome.READING,
    BookStatus.DROPPED: ReadOutcome.ABANDONED,
}


def _status(shelf: str, owned: bool) -> BookStatus:
    status = _SHELF_STATUS.get(shelf.strip().lower(), BookStatus.WISHLIST)
    # a book on the reader's own shelf that is not started yet is backlog, not a wish
    return BookStatus.BACKLOG if status is BookStatus.WISHLIST and owned else status


def _finished_reads(dated: list[ImportedRead], count: int | None) -> list[ImportedRead]:
    extra = min(max((count or 0) - len(dated), 0), MAX_UNDATED_READS)
    return dated + [ImportedRead(None, None, ReadOutcome.FINISHED) for _ in range(extra)]


def _reads(status: BookStatus, dated: list[ImportedRead], count: int | None) -> list[ImportedRead]:
    if status is BookStatus.READ:
        reads = _finished_reads(dated, count)
        return reads or [ImportedRead(None, None, ReadOutcome.FINISHED)]
    outcome = _OUTCOME.get(status)
    if outcome is None:
        return _finished_reads(dated, count) if dated else []
    if outcome is ReadOutcome.READING or not dated:
        return [*dated, ImportedRead(None, None, outcome)]
    # the last dated read of an abandoned book is where the reader stopped
    return [*dated[:-1], ImportedRead(dated[-1].started_on, dated[-1].finished_on, outcome)]


def _series(row: ImportedBook) -> None:
    match = _SERIES.match(row.title or "")
    if match is None:
        return
    row.title = match["title"].strip()[:500]
    row.series_name = match["series"].strip()[:200]
    row.series_position = match["position"]


# ------------------------------------------------------------------------------- Goodreads


def parse_goodreads(raw: bytes) -> list[ImportedBook]:
    """Goodreads "Export Library" CSV."""
    result: list[ImportedBook] = []
    for line, data in _rows(_reader(raw, _GOODREADS_COLUMNS, "Goodreads")):
        title = _clean(data.get("Title"), 500)
        if title is None:
            continue
        owned = (_int(data.get("Owned Copies"), 0, 10_000) or 0) > 0
        shelf = data.get("Exclusive Shelf", "")
        status = _status(shelf, owned)
        row = ImportedBook(
            line=line,
            title=title,
            authors=_unique(_names(data.get("Author")) + _names(data.get("Additional Authors")))[
                :MAX_AUTHORS
            ],
            status=status,
            rating=parse_rating(data.get("My Rating"), scale=Decimal(2)) or None,
            first_published_year=_int(data.get("Original Publication Year"), 1000, 2100)
            or _int(data.get("Year Published"), 1000, 2100),
            edition_year=_int(data.get("Year Published"), 1000, 2100),
            publisher=_clean(data.get("Publisher"), 200),
            page_count=_int(data.get("Number of Pages"), 1, 100_000),
            format=_format(data.get("Binding")),
            owned=owned,
            note=_note(data.get("My Review"), data.get("Private Notes")),
        )
        _isbn(row, data.get("ISBN13"), data.get("ISBN"))
        _series(row)
        exclusive = shelf.strip().lower()
        shelves = [_clean(part, 128) for part in data.get("Bookshelves", "").split(",")]
        row.tags = _unique(
            [name for name in shelves if name and name.lower() not in {exclusive, *_SHELF_STATUS}]
        )[:MAX_TAGS]
        finished = _date(data.get("Date Read"))
        dated = [ImportedRead(None, finished, ReadOutcome.FINISHED)] if finished else []
        row.reads = _reads(status, dated, _int(data.get("Read Count"), 0, 10_000))
        result.append(row)
    return result


# ------------------------------------------------------------------------------- StoryGraph


def _ranges(value: str | None) -> list[ImportedRead]:
    reads: list[ImportedRead] = []
    for part in (value or "").split(","):
        start_text, _, finish_text = part.strip().partition("-")
        started, finished = _date(start_text), _date(finish_text)
        if started is None and finished is None:
            continue
        if started is not None and finished is not None and finished < started:
            started = None
        reads.append(ImportedRead(started, finished, ReadOutcome.FINISHED))
    return reads


def parse_storygraph(raw: bytes) -> list[ImportedBook]:
    """The StoryGraph "Export your data" CSV."""
    result: list[ImportedBook] = []
    for line, data in _rows(_reader(raw, _STORYGRAPH_COLUMNS, "StoryGraph")):
        title = _clean(data.get("Title"), 500)
        if title is None:
            continue
        owned = data.get("Owned?", "").strip().lower() in {"yes", "true", "owned"}
        status = _status(data.get("Read Status", ""), owned)
        dated = _ranges(data.get("Dates Read"))
        last = _date(data.get("Last Date Read"))
        if not dated and last is not None:
            dated = [ImportedRead(None, last, ReadOutcome.FINISHED)]
        row = ImportedBook(
            line=line,
            title=title,
            authors=_names(data.get("Authors")),
            status=status,
            rating=parse_rating(data.get("Star Rating"), scale=Decimal(2)) or None,
            format=_format(data.get("Format")),
            owned=owned,
            note=_note(data.get("Review")),
            tags=_unique([name for name in _names(data.get("Tags")) if len(name) <= 128])[
                :MAX_TAGS
            ],
        )
        _isbn(row, data.get("ISBN/UID"))
        row.reads = _reads(status, dated, _int(data.get("Read Count"), 0, 10_000))
        result.append(row)
    return result


# ------------------------------------------------------------------------------- ISBN list


def _tokens(content: str) -> list[str]:
    """ISBN-shaped pieces of a line. A spaced ISBN ("978 0 306 40615 7") is one token; two ISBNs
    with only a space between them are two."""
    tokens: list[str] = []
    for token in _ISBN_TOKEN.findall(content):
        parts = token.split()
        if len(parts) > 1 and all(_valid(part) for part in parts):
            tokens.extend(parts)
        else:
            tokens.append(token)
    return tokens


def _valid(text: str) -> bool:
    try:
        parse_isbn(text)
    except InvalidIsbn:
        return False
    return True


def parse_isbn_list(raw: bytes) -> IsbnListResult:
    """One or more ISBNs per line (commas, semicolons or spaces between them); ``#`` starts a
    comment. Each work to read later is a wish-list book, identified by its ISBN."""
    rows: list[ImportedBook] = []
    invalid: list[str] = []
    seen: set[str] = set()
    repeated = 0
    for number, text in enumerate(_text(raw).splitlines(), start=1):
        content = text.split("#", 1)[0]
        for token in _tokens(content):
            try:
                parsed = parse_isbn(token)
            except InvalidIsbn:
                if len(invalid) < 100:
                    invalid.append(token[:32])
                continue
            if parsed.isbn_13 in seen:
                repeated += 1
                continue
            if len(rows) >= MAX_ROWS:
                raise BookImportError(f"The file contains more than {MAX_ROWS} ISBNs.")
            seen.add(parsed.isbn_13)
            rows.append(
                ImportedBook(
                    line=number, title=None, isbn_13=parsed.isbn_13, isbn_10=parsed.isbn_10
                )
            )
    return IsbnListResult(rows=rows, invalid=invalid, repeated=repeated)


# ------------------------------------------------------------------------------- dispatch


def detect_format(raw: bytes) -> ImportFormat:
    """Which of the three formats a file is, from its first line."""
    first = _text(raw[:65_536]).splitlines()[0] if raw.strip() else ""
    columns = {column.strip() for column in next(csv.reader([first]), [])}
    if _GOODREADS_COLUMNS <= columns:
        return "goodreads"
    if _STORYGRAPH_COLUMNS <= columns:
        return "storygraph"
    tokens = _ISBN_TOKEN.findall(first.split("#", 1)[0])
    if (
        not first.strip()
        or first.lstrip().startswith("#")
        or any(_ISBNISH.match(token) for token in tokens)
    ):
        return "isbn_list"
    raise BookImportError("This is not a Goodreads or StoryGraph export or a list of ISBNs.")


def parse_book_import(raw: bytes, source: ImportFormat) -> IsbnListResult:
    """Any format as rows (CSV formats never report invalid list entries or repeats)."""
    if source == "isbn_list":
        return parse_isbn_list(raw)
    parser = parse_goodreads if source == "goodreads" else parse_storygraph
    return IsbnListResult(rows=parser(raw), invalid=[], repeated=0)
