"""Restoring movies, TV shows, anime and books from the app's own JSON export
(GET /api/export/library, or a scheduled backup file).

Rows are rebuilt from the mapped columns of each model rather than a field
list kept by hand, so a column added later is restored without touching this
file. What is never carried over: ids, the owner, timestamps, and links to
other rows (a linked movie or show would point at a stranger's row on
another server). A title already in the library, same name and same year, is
skipped and never overwritten, so restoring twice or onto a live library is
safe. One bad entry is reported and skipped without stopping the rest.

A book (export format 3) brings its editions and reads; the export's edition ids only tie a
read and the active edition to the right new edition, and are never kept."""

from __future__ import annotations

import datetime as dt
import enum
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, TypeAlias

from pydantic import Field, ValidationError, model_validator
from sqlalchemy import Date, Numeric, select
from sqlalchemy import Enum as SAEnum
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import class_mapper

from src.api.schemas.book import BookBase, BookEditionCreate, reading_problem
from src.core.titles import derive_sort_title
from src.database.models.anime import Anime, AnimeEpisode, AnimeSeason
from src.database.models.book import Book, BookEdition, BookFormat, BookReading, ProgressUnit
from src.database.models.movies import Movie
from src.database.models.tv_show import TVEpisode, TVSeason, TVShow
from src.features.books.isbn import InvalidIsbn, normalise_pair

_NEVER = {
    "id",
    "user_id",
    "created_at",
    "updated_at",
    "deleted_at",
    "show_id",
    "season_id",
    "linked_tv_show_id",
    "linked_movie_id",
    "relations_cache",
    "relations_cached_at",
    "book_id",
    "edition_id",
    "active_edition_id",
}
MAX_ENTRIES = 20000

MediaModel: TypeAlias = type[Movie] | type[TVShow] | type[Anime] | type[Book]
MAX_BOOK_EDITIONS = 50
MAX_BOOK_READS = 200


def _convert(column: Any, value: Any) -> Any:
    if value is None:
        return None
    kind = column.type
    if isinstance(kind, Date):
        return value if isinstance(value, dt.date) else dt.date.fromisoformat(str(value)[:10])
    if isinstance(kind, Numeric):
        try:
            number = Decimal(str(value))
        except InvalidOperation as exc:
            raise ValueError(f"{column.key}: not a number") from exc
        if not number.is_finite():
            raise ValueError(f"{column.key}: must be a finite number")
        return number
    if isinstance(kind, SAEnum) and kind.enum_class is not None:
        enum_class: type[enum.Enum] = kind.enum_class
        try:
            return enum_class(value)
        except ValueError as exc:
            raise ValueError(f"{column.key}: unknown value {value!r}") from exc
    return value


def _fields(model: type, data: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for column in class_mapper(model).columns:
        if column.key in _NEVER or column.key not in data:
            continue
        out[column.key] = _convert(column, data[column.key])
    return out


def _year(value: Any) -> int | None:
    return value.year if isinstance(value, dt.date) else None


async def _existing(
    db: AsyncSession, model: MediaModel, user_id: Any, date_field: str
) -> set[tuple[str, int | None]]:
    rows = (
        await db.execute(
            select(model.title, getattr(model, date_field)).where(  # type: ignore[attr-defined]
                model.user_id == user_id,
                model.deleted_at.is_(None),
            )
        )
    ).all()
    return {(t.lower(), _year(d)) for t, d in rows}


def _add_children(
    db: AsyncSession,
    show_id: Any,
    seasons: list[dict[str, Any]],
    season_model: type,
    episode_model: type,
) -> None:
    seen_seasons: set[int] = set()
    for raw in seasons:
        number = raw.get("season_number")
        if not isinstance(number, int) or number in seen_seasons:
            continue
        seen_seasons.add(number)
        season = season_model(show_id=show_id, **_fields(season_model, raw))
        db.add(season)
        episodes = raw.get("episodes") or []
        if episodes:
            season.episodes = []
        seen_episodes: set[int] = set()
        for ep in episodes:
            n = ep.get("episode_number")
            if not isinstance(n, int) or n in seen_episodes:
                continue
            seen_episodes.add(n)
            season.episodes.append(episode_model(**_fields(episode_model, ep)))


@dataclass(frozen=True)
class _RestorePlan:
    key: str
    model: MediaModel
    date_field: str
    season_model: type | None = None
    episode_model: type | None = None


async def _restore_entries(
    db: AsyncSession,
    user_id: Any,
    entries: list[dict[str, Any]],
    plan: _RestorePlan,
    errors: list[str],
) -> tuple[int, int]:
    seen = await _existing(db, plan.model, user_id, plan.date_field)
    created = skipped = 0
    for raw in entries[:MAX_ENTRIES]:
        title = raw.get("title") if isinstance(raw, dict) else None
        if not isinstance(title, str) or not title.strip():
            errors.append(f"{plan.key}: an entry has no title")
            continue
        try:
            fields = _fields(plan.model, raw)
            identity = (title.lower(), _year(fields.get(plan.date_field)))
            if identity in seen:
                skipped += 1
                continue
            async with db.begin_nested():
                item = plan.model(user_id=user_id, **fields)
                db.add(item)
                await db.flush()
                if plan.season_model is not None and plan.episode_model is not None:
                    _add_children(
                        db, item.id, raw.get("seasons") or [], plan.season_model, plan.episode_model
                    )
                    await db.flush()
            seen.add(identity)
            created += 1
        except (SQLAlchemyError, ValueError, TypeError, KeyError, AttributeError) as exc:
            skipped += 1
            errors.append(f"{title}: {exc}")
    return created, skipped


class _RestoredBook(BookBase):
    """A restored work must fit the same limits the book responses (and so the library, the
    export and the scheduled backup) validate; otherwise one hand-edited entry would break all
    three for its owner."""

    sort_title: str = Field(min_length=1, max_length=500)
    locked_fields: list[str] = Field(default_factory=list)


class _RestoredEdition(BookEditionCreate):
    """An edition's limits as its response checks them; ISBNs and precision are already
    normalised by the restore itself."""

    @model_validator(mode="after")
    def _normalise(self) -> "_RestoredEdition":
        return self


def _limits_problem(
    model: type[BookBase] | type[BookEditionCreate], fields: dict[str, Any]
) -> str | None:
    """The first field that breaks the response model's limits, or None."""
    try:
        model.model_validate({k: v for k, v in fields.items() if k in model.model_fields})
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "entry"
        return f"{where}: {first['msg']}"
    return None


def _partial_precision(day: Any, precision: Any) -> str | None:
    """A stored year or month precision is kept only when the date is that period's first day."""
    if not isinstance(day, dt.date) or precision not in ("year", "month"):
        return None
    if day.day != 1 or (precision == "year" and day.month != 1):
        return None
    return str(precision)


def _edition_fields(
    raw: dict[str, Any], seen_isbns: set[str], problems: list[str]
) -> dict[str, Any] | None:
    """The edition's columns, or None when it cannot be kept; every loss is told in ``problems``."""
    data = dict(raw)
    # a format this build does not know is "other", never a reason to lose the edition
    if data.get("format") not in {member.value for member in BookFormat}:
        data["format"] = BookFormat.OTHER.value
    fields = _fields(BookEdition, data)
    fields["publish_date_precision"] = _partial_precision(
        fields.get("publish_date"), fields.get("publish_date_precision")
    )
    try:
        pair = normalise_pair(fields.get("isbn_13"), fields.get("isbn_10"))
    except InvalidIsbn:
        pair = None
        problems.append("an edition's ISBN was not valid and was left out")
    fields["isbn_13"] = pair.isbn_13 if pair else None
    fields["isbn_10"] = pair.isbn_10 if pair else None
    if pair is not None and pair.isbn_13 in seen_isbns:
        # only the duplicate is dropped; the work and its other editions stay
        problems.append(f"an edition was skipped (another edition has ISBN {pair.isbn_13})")
        return None
    limit = _limits_problem(_RestoredEdition, fields)
    if limit is not None:
        problems.append(f"an edition was skipped ({limit})")
        return None
    if pair is not None:
        seen_isbns.add(pair.isbn_13)
    return fields


def _capped(raw: dict[str, Any], key: str, cap: int, label: str, problems: list[str]) -> list[Any]:
    entries = raw.get(key) or []
    if not isinstance(entries, list):
        problems.append(f"{key} was not a list and was left out")
        return []
    if len(entries) > cap:
        problems.append(f"only the first {cap} {label} were kept ({len(entries) - cap} left out)")
    return entries[:cap]


async def _add_book_children(
    db: AsyncSession, book: Book, raw: dict[str, Any], errors: list[str]
) -> None:
    problems: list[str] = []
    editions: dict[str, BookEdition] = {}
    seen_isbns: set[str] = set()
    for entry in _capped(raw, "editions", MAX_BOOK_EDITIONS, "editions", problems):
        if not isinstance(entry, dict):
            continue
        fields = _edition_fields(entry, seen_isbns, problems)
        if fields is None:
            continue
        edition = BookEdition(book_id=book.id, **fields)
        db.add(edition)
        if entry.get("id") is not None:
            editions[str(entry["id"])] = edition
    await db.flush()
    active = editions.get(str(raw.get("active_edition_id")))
    book.active_edition_id = active.id if active is not None else None
    for entry in _capped(raw, "reads", MAX_BOOK_READS, "reads", problems):
        if not isinstance(entry, dict):
            continue
        read_edition = editions.get(str(entry.get("edition_id")))
        fields = _fields(BookReading, entry)
        if fields.get("progress_unit") is None:
            fields["progress_unit"] = ProgressUnit.PAGE
        reading = BookReading(
            book_id=book.id,
            edition_id=read_edition.id if read_edition is not None else None,
            **fields,
        )
        problem = reading_problem(
            reading, read_edition.page_count if read_edition is not None else None
        )
        if problem is not None:
            # one bad read is dropped; the work and its other editions and reads stay
            problems.append(f"a read was skipped ({problem.rstrip('.')})")
            continue
        db.add(reading)
    await db.flush()
    errors.extend(f"{book.title}: {problem}" for problem in problems)


def _author_key(authors: Any) -> tuple[str, ...]:
    """A stable, order-independent key for a book's author list."""
    return tuple(
        sorted({a.strip().lower() for a in (authors or []) if isinstance(a, str) and a.strip()})
    )


def _entry_isbn13(raw: dict[str, Any]) -> str | None:
    """The lowest normalised ISBN-13 among an entry's editions.

    Title and first-published year alone are not a book's identity: two distinct works can
    share both (a reprint under the same title, a common title, or simply two unrelated books
    published the same year), and both can have an unknown year. Author and ISBN are the
    available signals that tell such entries apart (#533 review); this is read the same way on
    export (``_entry_isbn13``) and from already-restored books (``_existing_books``), so
    restoring one export twice still matches its own books and skips them, not just entries that
    also happen to share every one of these fields by coincidence."""
    isbns = set()
    for entry in raw.get("editions") or []:
        if not isinstance(entry, dict):
            continue
        try:
            pair = normalise_pair(entry.get("isbn_13"), entry.get("isbn_10"))
        except InvalidIsbn:
            continue
        if pair and pair.isbn_13:
            isbns.add(pair.isbn_13)
    return min(isbns) if isbns else None


_BookIdentity: TypeAlias = tuple[str, int | None, tuple[str, ...], str | None]


def _book_identity(title: str, fields: dict[str, Any], raw: dict[str, Any]) -> _BookIdentity:
    return (
        title.lower(),
        _year(fields.get("first_published")),
        _author_key(fields.get("authors")),
        _entry_isbn13(raw),
    )


async def _existing_books(db: AsyncSession, user_id: Any) -> set[_BookIdentity]:
    rows = (
        await db.execute(
            select(Book.id, Book.title, Book.first_published, Book.authors).where(
                Book.user_id == user_id, Book.deleted_at.is_(None)
            )
        )
    ).all()
    if not rows:
        return set()
    isbns = (
        await db.execute(
            select(BookEdition.book_id, BookEdition.isbn_13).where(
                BookEdition.book_id.in_([row.id for row in rows]), BookEdition.isbn_13.is_not(None)
            )
        )
    ).all()
    isbn_by_book: dict[Any, str] = {}
    for book_id, isbn_13 in isbns:
        if isbn_13 is None:
            continue
        if book_id not in isbn_by_book or isbn_13 < isbn_by_book[book_id]:
            isbn_by_book[book_id] = isbn_13
    return {
        (title.lower(), _year(published), _author_key(authors), isbn_by_book.get(book_id))
        for book_id, title, published, authors in rows
    }


async def _restore_books(
    db: AsyncSession, user_id: Any, entries: list[Any], errors: list[str]
) -> tuple[int, int]:
    seen = await _existing_books(db, user_id)
    created = skipped = 0
    for raw in entries[:MAX_ENTRIES]:
        title = raw.get("title") if isinstance(raw, dict) else None
        if not isinstance(title, str) or not title.strip():
            errors.append("books: an entry has no title")
            continue
        try:
            fields = _fields(Book, raw)
            fields["first_published_precision"] = _partial_precision(
                fields.get("first_published"), fields.get("first_published_precision")
            )
            # a hand-written or trimmed export may leave the derived sort title out
            if not fields.get("sort_title"):
                fields["sort_title"] = derive_sort_title(title)
            limit = _limits_problem(_RestoredBook, fields)
            if limit is not None:
                raise ValueError(f"skipped ({limit})")
            identity = _book_identity(title, fields, raw)
            if identity in seen:
                skipped += 1
                continue
            async with db.begin_nested():
                book = Book(user_id=user_id, **fields)
                db.add(book)
                await db.flush()
                await _add_book_children(db, book, raw, errors)
            seen.add(identity)
            created += 1
        except (SQLAlchemyError, ValueError, TypeError, KeyError, AttributeError) as exc:
            skipped += 1
            errors.append(f"{title}: {exc}")
    return created, skipped


async def restore_media(db: AsyncSession, user_id: Any, payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"created": {}, "skipped": {}, "errors": []}
    plans = (
        _RestorePlan("movies", Movie, "release_date"),
        _RestorePlan("tv_shows", TVShow, "first_air_date", TVSeason, TVEpisode),
        _RestorePlan("anime", Anime, "first_air_date", AnimeSeason, AnimeEpisode),
    )
    for plan in plans:
        entries = payload.get(plan.key) or []
        if not isinstance(entries, list):
            result["errors"].append(f"{plan.key}: expected a list")
            continue
        created, skipped = await _restore_entries(db, user_id, entries, plan, result["errors"])
        result["created"][plan.key] = created
        result["skipped"][plan.key] = skipped
    # an export from before format 3 has no books section and reports none
    if "books" in payload:
        books = payload.get("books") or []
        if isinstance(books, list):
            created, skipped = await _restore_books(db, user_id, books, result["errors"])
            result["created"]["books"] = created
            result["skipped"]["books"] = skipped
        else:
            result["errors"].append("books: expected a list")
    await db.commit()
    result["errors"] = result["errors"][:30]
    return result
