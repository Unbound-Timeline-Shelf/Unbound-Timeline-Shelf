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

from sqlalchemy import Date, Numeric, select
from sqlalchemy import Enum as SAEnum
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import class_mapper

from src.core.titles import derive_sort_title
from src.database.models.anime import Anime, AnimeEpisode, AnimeSeason
from src.database.models.book import Book, BookEdition, BookFormat, BookReading
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


def _partial_precision(day: Any, precision: Any) -> str | None:
    """A stored year or month precision is kept only when the date is that period's first day."""
    if not isinstance(day, dt.date) or precision not in ("year", "month"):
        return None
    if day.day != 1 or (precision == "year" and day.month != 1):
        return None
    return str(precision)


def _edition_fields(raw: dict[str, Any], seen_isbns: set[str]) -> dict[str, Any]:
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
    fields["isbn_13"] = pair.isbn_13 if pair else None
    fields["isbn_10"] = pair.isbn_10 if pair else None
    if pair is not None:
        if pair.isbn_13 in seen_isbns:
            raise ValueError(f"two editions share ISBN {pair.isbn_13}")
        seen_isbns.add(pair.isbn_13)
    return fields


async def _add_book_children(db: AsyncSession, book: Book, raw: dict[str, Any]) -> None:
    editions: dict[str, BookEdition] = {}
    seen_isbns: set[str] = set()
    for entry in (raw.get("editions") or [])[:MAX_BOOK_EDITIONS]:
        if not isinstance(entry, dict):
            continue
        edition = BookEdition(book_id=book.id, **_edition_fields(entry, seen_isbns))
        db.add(edition)
        if entry.get("id") is not None:
            editions[str(entry["id"])] = edition
    await db.flush()
    active = editions.get(str(raw.get("active_edition_id")))
    book.active_edition_id = active.id if active is not None else None
    for entry in (raw.get("reads") or [])[:MAX_BOOK_READS]:
        if not isinstance(entry, dict):
            continue
        read_edition = editions.get(str(entry.get("edition_id")))
        db.add(
            BookReading(
                book_id=book.id,
                edition_id=read_edition.id if read_edition is not None else None,
                **_fields(BookReading, entry),
            )
        )
    await db.flush()


async def _restore_books(
    db: AsyncSession, user_id: Any, entries: list[Any], errors: list[str]
) -> tuple[int, int]:
    seen = await _existing(db, Book, user_id, "first_published")
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
            identity = (title.lower(), _year(fields.get("first_published")))
            if identity in seen:
                skipped += 1
                continue
            async with db.begin_nested():
                book = Book(user_id=user_id, **fields)
                db.add(book)
                await db.flush()
                await _add_book_children(db, book, raw)
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
