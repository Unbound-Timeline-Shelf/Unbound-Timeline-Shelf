"""Bringing a book list in: Goodreads CSV, StoryGraph CSV or a list of ISBNs (C15 6.2, #265 4).

- POST /api/import/books/preview  what the file holds and what is already in the library
- POST /api/import/books          add the new books

Like the other imports, nothing already in the library is changed: a row whose ISBN is on one of
the reader's editions, or whose title and author match a book already there, is kept as it is.
With ``match`` on, the new rows are identified through the account's book providers (ISBN first,
in batches of at most 200 per ``resolve`` call, then title and author; see
``features.imports.book_matching``). A matched row keeps the file's own data and gains the
provider's ids (and its title, authors and first-publication year where the file has none). Every
row a provider cannot identify is imported by hand: nothing is dropped.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
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
    match_books,
    surnames,
    title_forms,
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
from src.plugin_api.metadata_contracts import MediaType, MetadataCandidate, ProviderHealth

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


class _Library:
    """The reader's books, to tell which rows are already here (and which repeat in the file).

    Indexed by the title forms ``title_agrees`` compares, so a large file against a large library
    stays linear."""

    def __init__(self, books: Sequence[Book] = ()) -> None:
        self.isbns: set[str] = set()
        self.titles: dict[str, list[set[str]]] = {}
        for book in books:
            self.isbns.update(edition.isbn_13 for edition in book.editions if edition.isbn_13)
            self._index(book.title, list(book.authors))

    def _index(self, title: str, authors: list[str]) -> None:
        for form in title_forms(title):
            self.titles.setdefault(form, []).append(surnames(authors))

    def has(self, row: ImportedBook) -> bool:
        if row.isbn_13 and row.isbn_13 in self.isbns:
            return True
        if not row.title:
            return False
        names = surnames(row.authors)
        return any(
            not names or not known or bool(names & known)
            for form in title_forms(row.title)
            for known in self.titles.get(form, ())
        )

    def add(self, row: ImportedBook) -> None:
        if row.isbn_13:
            self.isbns.add(row.isbn_13)
        if row.title:
            self._index(row.title, row.authors)


async def _split(
    db: AsyncSession, user_id: UUID, rows: list[ImportedBook]
) -> tuple[list[ImportedBook], list[bool], int]:
    """The rows to create, whether each row is already here, and how many repeat in the file."""
    books = (
        await db.scalars(select(Book).where(Book.user_id == user_id, Book.deleted_at.is_(None)))
    ).all()
    library = _Library(books)
    seen = _Library()
    new: list[ImportedBook] = []
    here: list[bool] = []
    repeated = 0
    for row in rows:
        if library.has(row):
            here.append(True)
            continue
        here.append(False)
        if seen.has(row):
            repeated += 1
            continue
        seen.add(row)
        new.append(row)
    return new, here, repeated


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
    new, here, repeated = await _split(db, current_user.id, parsed.rows)
    providers = await _providers(db, current_user.id)
    return BookImportPreview(
        source=chosen,
        total=len(parsed.rows),
        new=len(new),
        already_here=sum(here),
        repeated=repeated + parsed.repeated,
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
            )
            for row, is_here in list(zip(parsed.rows, here, strict=True))[:PREVIEW_ROWS]
        ],
    )


def _year(year: int | None) -> dt.date | None:
    low, high = _YEARS
    return dt.date(year, 1, 1) if year is not None and low <= year <= high else None


def _provider_ids(candidate: MetadataCandidate) -> dict[str, str]:
    ids = dict(candidate.provider_ids) or {candidate.provider: candidate.external_id}
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
        provider_ids=_provider_ids(candidate) if candidate else {},
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
    chosen, parsed = await _parse(file, source)
    new, here, repeated = await _split(db, current_user.id, parsed.rows)
    report = MatchReport()
    if match and new:
        report = await match_books(new, await _providers(db, current_user.id), current_user.id)
    created, errors = await _create_all(db, new, report, current_user.id)
    await db.commit()
    by = [found.matched_by for found in report.matches.values()]
    return BookImportResult(
        source=chosen,
        total=len(parsed.rows),
        created=created,
        kept=sum(here),
        repeated=repeated + parsed.repeated,
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
