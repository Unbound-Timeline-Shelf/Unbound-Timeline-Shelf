"""Bringing a book list in: Goodreads CSV, StoryGraph CSV or a list of ISBNs (C15 6.2, #265 4).

- POST /api/import/books/preview  what the file holds and what is already in the library
- POST /api/import/books          add the new books

Like the other imports, nothing already in the library is changed: a row whose ISBN is on one of
the reader's editions, or whose title and author match a book already there, is kept as it is.
Titles with different subtitles are different books, and a row whose ISBN differs from every ISBN
of a same-titled book is another book (a reader keeps one book per work, with its editions).
With ``match`` on, the new rows are identified through the account's book providers (ISBN first,
in batches of at most 200 per ``resolve`` call, then title and author; see
``features.imports.book_matching``). A matched row keeps the file's own data and gains the
provider's ids (and its title, authors and first-publication year where the file has none). Every
row a provider cannot identify is imported by hand: nothing is dropped.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas.book import reading_problem
from src.core.auth import get_current_user
from src.core.titles import derive_sort_title
from src.database.models.book import (
    Book,
    BookEdition,
    BookFormat,
    BookOwnership,
    BookReading,
)
from src.database.models.user import User
from src.database.session import get_db
from src.features.imports.book_matching import (
    BookMatch,
    MatchReport,
    authors_agree,
    match_books,
    title_parts,
)
from src.features.imports.books import (
    FORMATS,
    MAX_BYTES,
    BookImportError,
    ImportedBook,
    ImportFormat,
    IsbnListResult,
    detect_format,
    parse_book_import,
)
from src.features.metadata.providers import discover_providers
from src.plugin_api.metadata_contracts import MediaType, ProviderHealth

router = APIRouter(
    prefix="/api/import/books", tags=["import"], dependencies=[Depends(get_current_user)]
)

PREVIEW_ROWS = 50
MAX_ERRORS = 30
_PROVIDER_ID_KEY = 50
_PROVIDER_ID_VALUE = 256
_YEARS = (1000, 2100)
SourceField = Literal["auto", "goodreads", "storygraph", "isbn_list"]


class BookImportPreviewRow(BaseModel):
    line: int
    title: str | None
    authors: list[str]
    isbn_13: str | None
    invalid_isbn: str | None
    status: str
    last_read: dt.date | None
    already_here: bool
    # the same book as an earlier row of the file, so it is imported once
    repeated: bool = False


class BookImportProvider(BaseModel):
    name: str
    batched: bool


class BookImportPreview(BaseModel):
    source: ImportFormat
    total: int
    new: int
    already_here: int
    repeated: int
    with_isbn: int
    invalid_isbn: int
    invalid_entries: list[str]
    providers: list[BookImportProvider]
    rows: list[BookImportPreviewRow]


class BookImportResult(BaseModel):
    source: ImportFormat
    total: int
    created: int
    kept: int
    repeated: int
    matched_by_isbn: int
    matched_by_title: int
    manual: int
    resolve_calls: int
    row_lookups: int
    not_looked_up: int
    failed_providers: list[str]
    invalid_isbn: int
    invalid_entries: list[str]
    errors: list[str]


async def _parse(file: UploadFile, source: SourceField) -> tuple[ImportFormat, IsbnListResult]:
    raw = await file.read(MAX_BYTES + 1)
    try:
        chosen: ImportFormat = detect_format(raw) if source == "auto" else source
        if chosen not in FORMATS:
            raise BookImportError("Unknown book list format.")
        return chosen, parse_book_import(raw, chosen)
    except BookImportError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@dataclass(frozen=True)
class _Known:
    """A book the reader has (or a row already taken from the file)."""

    subtitle: str | None
    authors: tuple[str, ...]
    isbns: frozenset[str]


class _Library:
    """The reader's books, to tell which rows are already here (and which repeat in the file).

    Indexed by the main title ``title_parts`` folds, so a large file against a large library stays
    linear. A row is already here when one of its ISBNs is on the reader's editions, or when a
    book has the same title (subtitles agreeing, see ``titles_agree``) and a shared author
    surname, unless both have ISBNs and none is shared: that is another book."""

    def __init__(self, books: Sequence[Book] = ()) -> None:
        self.isbns: set[str] = set()
        self.titles: dict[str, list[_Known]] = {}
        for book in books:
            isbns = frozenset(edition.isbn_13 for edition in book.editions if edition.isbn_13)
            self.isbns.update(isbns)
            self._index(book.title, list(book.authors), isbns)

    def _index(self, title: str, authors: list[str], isbns: frozenset[str]) -> None:
        main, subtitle = title_parts(title)
        if main:
            self.titles.setdefault(main, []).append(_Known(subtitle, tuple(authors), isbns))

    def has(self, row: ImportedBook) -> bool:
        if row.isbn_13 and row.isbn_13 in self.isbns:
            return True
        if not row.title:
            return False
        main, subtitle = title_parts(row.title)
        return any(
            (subtitle is None or known.subtitle is None or subtitle == known.subtitle)
            and authors_agree(row.authors, known.authors)
            and not (row.isbn_13 and known.isbns)
            for known in self.titles.get(main, ())
        )

    def add(self, row: ImportedBook) -> None:
        isbns = frozenset({row.isbn_13} if row.isbn_13 else ())
        self.isbns.update(isbns)
        if row.title:
            self._index(row.title, row.authors, isbns)


async def _split(
    db: AsyncSession, user_id: UUID, rows: list[ImportedBook]
) -> tuple[list[ImportedBook], list[bool], list[bool]]:
    """The rows to create, whether each row is already here, and whether each repeats an
    earlier row of the file."""
    books = (
        await db.scalars(select(Book).where(Book.user_id == user_id, Book.deleted_at.is_(None)))
    ).all()
    library = _Library(books)
    seen = _Library()
    new: list[ImportedBook] = []
    here: list[bool] = []
    repeats: list[bool] = []
    for row in rows:
        is_here = library.has(row)
        is_repeat = not is_here and seen.has(row)
        here.append(is_here)
        repeats.append(is_repeat)
        if not (is_here or is_repeat):
            seen.add(row)
            new.append(row)
    return new, here, repeats


async def _providers(db: AsyncSession, user_id: UUID) -> list[Any]:
    providers = [
        provider
        for provider in await discover_providers(db, user_id, MediaType.BOOK)
        if provider.state not in {ProviderHealth.DISABLED, ProviderHealth.NOT_CONFIGURED}
        and (provider.declaration.operations.search or provider.declaration.operations.resolve)
    ]
    return sorted(providers, key=lambda provider: (provider.priority, provider.name))


@router.post("/preview", response_model=BookImportPreview)
async def preview_book_import(
    file: UploadFile,
    source: Annotated[SourceField, Form()] = "auto",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BookImportPreview:
    chosen, parsed = await _parse(file, source)
    new, here, repeats = await _split(db, current_user.id, parsed.rows)
    providers = await _providers(db, current_user.id)
    return BookImportPreview(
        source=chosen,
        total=len(parsed.rows),
        new=len(new),
        already_here=sum(here),
        repeated=sum(repeats) + parsed.repeated,
        with_isbn=sum(1 for row in parsed.rows if row.isbn_13),
        invalid_isbn=sum(1 for row in parsed.rows if row.invalid_isbn),
        invalid_entries=parsed.invalid,
        providers=[
            BookImportProvider(
                name=provider.name, batched=bool(provider.declaration.operations.resolve)
            )
            for provider in providers
        ],
        rows=[
            BookImportPreviewRow(
                line=row.line,
                title=row.title,
                authors=row.authors[:3],
                isbn_13=row.isbn_13,
                invalid_isbn=row.invalid_isbn,
                status=row.status.value,
                last_read=next(
                    (read.finished_on for read in reversed(row.reads) if read.finished_on), None
                ),
                already_here=is_here,
                repeated=is_repeat,
            )
            for row, is_here, is_repeat in list(zip(parsed.rows, here, repeats, strict=True))[
                :PREVIEW_ROWS
            ]
        ],
    )


def _year(year: int | None) -> dt.date | None:
    low, high = _YEARS
    return dt.date(year, 1, 1) if year is not None and low <= year <= high else None


def _provider_ids(match: BookMatch) -> dict[str, str]:
    """The provider's ids for the book, only in the namespaces that provider owns.

    ``candidate.provider`` is the plugin's own free text, so the ``provider: external_id``
    fallback is only taken when it names one of those namespaces."""
    candidate, owned = match.candidate, match.namespaces
    ids = {key: value for key, value in candidate.provider_ids.items() if key in owned}
    if not ids and candidate.provider in owned:
        ids = {candidate.provider: candidate.external_id}
    return {
        key: value
        for key, value in ids.items()
        if 0 < len(key) <= _PROVIDER_ID_KEY
        and all(
            character.islower() or character.isdigit() or character in "._-" for character in key
        )
        and len(value) <= _PROVIDER_ID_VALUE
    }


def _book(row: ImportedBook, match: BookMatch | None, user_id: UUID) -> Book:
    candidate = match.candidate if match else None
    title = row.title or (candidate.title[:500] if candidate else f"ISBN {row.isbn_13}")
    first_published = _year(row.first_published_year) or _year(
        candidate.year if candidate else None
    )
    return Book(
        id=uuid4(),
        user_id=user_id,
        title=title,
        sort_title=derive_sort_title(title),
        authors=row.authors or (list(candidate.authors) if candidate else []),
        first_published=first_published,
        first_published_precision="year" if first_published else None,
        series_name=row.series_name,
        series_position=row.series_position,
        tags=row.tags,
        status=row.status,
        rating_overall=row.rating,
        note=row.note,
        provider_ids=_provider_ids(match) if match else {},
    )


def _edition(row: ImportedBook, book: Book) -> BookEdition | None:
    if not (row.isbn_13 or row.format or row.publisher or row.page_count):
        return None
    published = _year(row.edition_year)
    return BookEdition(
        id=uuid4(),
        book_id=book.id,
        isbn_13=row.isbn_13,
        isbn_10=row.isbn_10,
        format=row.format or BookFormat.OTHER,
        publisher=row.publisher,
        publish_date=published,
        publish_date_precision="year" if published else None,
        page_count=row.page_count,
        ownership=BookOwnership.OWNED if row.owned else BookOwnership.NONE,
    )


async def _create(
    db: AsyncSession, row: ImportedBook, match: BookMatch | None, user_id: UUID, errors: list[str]
) -> Book:
    book = _book(row, match, user_id)
    db.add(book)
    await db.flush()
    edition = _edition(row, book)
    if edition is not None:
        db.add(edition)
        await db.flush()
        book.active_edition_id = edition.id
    for read in row.reads:
        reading = BookReading(
            id=uuid4(),
            book_id=book.id,
            edition_id=edition.id if edition else None,
            started_on=read.started_on,
            finished_on=read.finished_on,
            outcome=read.outcome,
        )
        problem = reading_problem(reading, edition.page_count if edition else None)
        if problem is not None:
            if len(errors) < MAX_ERRORS:
                errors.append(f"{book.title} (line {row.line}): a read was left out: {problem}")
            continue
        db.add(reading)
    await db.flush()
    return book


def _label(row: ImportedBook) -> str:
    return f"{row.title or row.isbn_13} (line {row.line})"


async def _create_all(
    db: AsyncSession, rows: list[ImportedBook], report: MatchReport, user_id: UUID
) -> tuple[int, list[str]]:
    """Each row in its own savepoint, so one bad row never costs the others."""
    errors: list[str] = []
    created = 0
    for index, row in enumerate(rows):
        try:
            async with db.begin_nested():
                await _create(db, row, report.matches.get(index), user_id, errors)
            created += 1
        except Exception as exc:  # pylint: disable=broad-exception-caught
            if len(errors) < MAX_ERRORS:
                errors.append(f"{_label(row)}: {type(exc).__name__}")
    return created, errors


@router.post("", response_model=BookImportResult)
async def import_books(
    file: UploadFile,
    source: Annotated[SourceField, Form()] = "auto",
    match: Annotated[bool, Form()] = True,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BookImportResult:
    user_id = current_user.id
    # One import per account at a time: each one may fan out to the book providers, and two
    # copies of one file sent together would both see the books as new and create them twice.
    # The app runs as one process, so an in-process set is enough.
    if user_id in _IMPORTING:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A book import is already running for this account. Try again when it finishes.",
        )
    _IMPORTING.add(user_id)
    try:
        return await _import(db, user_id, file, source, match)
    finally:
        _IMPORTING.discard(user_id)


_IMPORTING: set[UUID] = set()


async def _import(
    db: AsyncSession, user_id: UUID, file: UploadFile, source: SourceField, match: bool
) -> BookImportResult:
    chosen, parsed = await _parse(file, source)
    new, here, repeats = await _split(db, user_id, parsed.rows)
    providers = await _providers(db, user_id) if match and new else []
    # End the read transaction before matching: matching can take MATCH_SECONDS of provider
    # calls (which open their own sessions), and must not pin this pooled connection idle in
    # a transaction meanwhile. Nothing has been written yet, so this commit changes nothing.
    await db.commit()
    report = MatchReport()
    if match and new:
        report = await match_books(new, providers, user_id)
    created, errors = await _create_all(db, new, report, user_id)
    await db.commit()
    by = [found.matched_by for found in report.matches.values()]
    return BookImportResult(
        source=chosen,
        total=len(parsed.rows),
        created=created,
        kept=sum(here),
        repeated=sum(repeats) + parsed.repeated,
        matched_by_isbn=by.count("identifier"),
        matched_by_title=by.count("title"),
        manual=len(new) - len(report.matches),
        resolve_calls=report.resolve_calls,
        row_lookups=report.row_lookups,
        not_looked_up=report.not_looked_up,
        failed_providers=report.failed_providers,
        invalid_isbn=sum(1 for row in parsed.rows if row.invalid_isbn),
        invalid_entries=parsed.invalid,
        errors=errors,
    )
