"""API routes for books, their editions and read-throughs (#265 phase 1).

The routes follow the movie, TV show and anime ones: ``/api/book/list``, ``/create``,
``/get/{id}``, ``/update/{id}``, ``/delete/{id}`` and the shared trash life cycle, with the
editions and reads of a work nested under it. Every handler resolves the work through its owner
first, so another user's work, edition or read is a 404, the same as one that does not exist.
No metadata provider is involved in this phase: a book is entered by hand.
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.functions import count as sql_count

from src.api.routes.media_common import (
    LibraryFilters,
    library_page,
    owned_row,
    purge_row,
    related_row,
    restore_row,
    soft_delete,
    title_search,
    trash_listing,
)
from src.api.schemas.book import (
    MAX_EDITIONS,
    MAX_READS,
    BookCreate,
    BookEditionCreate,
    BookEditionUpdate,
    BookRead,
    BookReadingCreate,
    BookReadingUpdate,
    BookUpdate,
    reading_problem,
)
from src.api.schemas.media_trash import MediaTrashEmptyRequest, MediaTrashEmptyResult
from src.api.schemas.pagination import LibraryQuery, PaginatedResponse
from src.core.auth import AuthenticatedActor, get_current_actor, get_current_user
from src.core.titles import derive_sort_title
from src.database.models.book import (
    Book,
    BookEdition,
    BookReading,
    BookStatus,
    ProgressUnit,
    ReadOutcome,
)
from src.database.models.user import User
from src.database.session import get_db
from src.features.metadata.locked_fields import (
    apply_updates_with_locking,
    drop_nulls_for_required_fields,
)
from src.features.trash.media_titles import empty_trash

_DB = Depends(get_db)
_USER = Depends(get_current_user)

router = APIRouter(prefix="/api/book", tags=["book"], dependencies=[Depends(get_current_user)])

# Descriptive fields a later metadata refresh must not overwrite once the person changed them
# (#265 3.3); personal fields such as status or notes are never refreshed, so never locked.
LOCKABLE_FIELDS = frozenset(
    {
        "title",
        "subtitle",
        "authors",
        "description",
        "first_published",
        "language",
        "genres",
        "series_name",
        "series_position",
        "cover_url",
    }
)

# The five library tabs, read for books (#265 3.1): Wishlist and Backlog are to read.
BOOK_STATUS_BUCKETS: dict[str, set[str]] = {
    "plan": {"WISHLIST", "BACKLOG"},
    "hold": {"ON_HOLD"},
    "watching": {"IN_PROGRESS"},
    "completed": {"READ"},
    "dropped": {"DROPPED"},
}
_NOT_STARTED = {BookStatus.WISHLIST, BookStatus.BACKLOG}


async def _book(
    db: AsyncSession,
    book_id: UUID,
    user_id: UUID,
    *,
    include_deleted: bool = False,
    for_update: bool = False,
) -> Book:
    return await owned_row(
        db,
        Book,
        book_id,
        user_id,
        "Book",
        include_deleted=include_deleted,
        for_update=for_update,
        populate_existing=True,
    )


async def _edition(db: AsyncSession, book: Book, edition_id: UUID) -> BookEdition:
    return await related_row(
        db,
        BookEdition,
        edition_id,
        parent_column=BookEdition.book_id,
        parent_id=book.id,
        label="Edition",
    )


async def _reading(db: AsyncSession, book: Book, read_id: UUID) -> BookReading:
    return await related_row(
        db,
        BookReading,
        read_id,
        parent_column=BookReading.book_id,
        parent_id=book.id,
        label="Read",
    )


def _invalid(message: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, message)


async def _commit(db: AsyncSession, conflict: str) -> None:
    """Commit, turning the database's own refusal (a duplicate ISBN raced past the check, or a
    value outside a constraint) into the same validation answer the check gives."""
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _invalid(conflict) from exc


async def _count(db: AsyncSession, model: Any, book_id: UUID) -> int:
    return int(
        await db.scalar(select(sql_count()).select_from(model).where(model.book_id == book_id)) or 0
    )


async def _refuse_duplicate_isbn(
    db: AsyncSession, book_id: UUID, isbn_13: str | None, edition_id: UUID | None = None
) -> None:
    """#265 3.2: one work never holds two editions with the same ISBN-13; nothing is merged."""
    if isbn_13 is None:
        return
    stmt = select(BookEdition.id).where(
        BookEdition.book_id == book_id, BookEdition.isbn_13 == isbn_13
    )
    if edition_id is not None:
        stmt = stmt.where(BookEdition.id != edition_id)
    if await db.scalar(stmt) is not None:
        raise _invalid(f"This book already has an edition with ISBN {isbn_13}.")


def _sync_status(book: Book, reading: BookReading) -> None:
    """Keep the work's status telling the same story as its reads: finishing a read makes the
    work Read, and reading something not started moves it to In progress on that edition."""
    if reading.outcome == ReadOutcome.FINISHED:
        book.status = BookStatus.READ
        return
    if reading.outcome == ReadOutcome.READING:
        if book.status in _NOT_STARTED:
            book.status = BookStatus.IN_PROGRESS
        if reading.edition_id is not None:
            book.active_edition_id = reading.edition_id


async def _checked_reading(db: AsyncSession, book: Book, reading: BookReading) -> None:
    """A read's edition is one of this work's own; pages stop at that edition's page count."""
    page_count = None
    if reading.edition_id is not None:
        # the pending read is checked before the database sees it
        with db.no_autoflush:
            edition = await db.scalar(
                select(BookEdition).where(
                    BookEdition.id == reading.edition_id, BookEdition.book_id == book.id
                )
            )
        if edition is None:
            raise _invalid("The edition of a read must be one of this book's editions.")
        page_count = edition.page_count
    problem = reading_problem(reading, page_count)
    if problem is not None:
        raise _invalid(problem)


@router.post("/create", response_model=BookRead, status_code=status.HTTP_201_CREATED)
async def create_book(
    payload: BookCreate, db: AsyncSession = _DB, current_user: User = _USER
) -> Book:
    """Create a book by hand. A work needs only a title; editions and reads come later."""
    data = payload.model_dump()
    if not data.get("sort_title"):
        data["sort_title"] = derive_sort_title(data["title"])
    book = Book(**data, user_id=current_user.id)
    db.add(book)
    await db.commit()
    return await _book(db, book.id, current_user.id)


# Typed route adapters retain their own HTTP schemas and ownership checks; behavior is shared.
# pylint: disable=duplicate-code
@router.get("/list", response_model=PaginatedResponse[BookRead])
async def list_books(
    query: Annotated[LibraryQuery[BookStatus], Query()],
    db: AsyncSession = _DB,
    current_user: User = _USER,
) -> PaginatedResponse[BookRead]:
    """One page of the current user's books (title, subtitle or author search) and the total."""
    authors = func.array_to_string(Book.authors, " ")
    return await library_page(
        db,
        Book,
        current_user.id,
        skip=query.skip,
        limit=query.limit,
        include_facets=query.include_facets,
        filters=LibraryFilters.from_query(
            query,
            BookStatus,
            search_clause=title_search([Book.title, Book.subtitle, authors], query.search),
            search_columns=(Book.title,),
            year_column=Book.first_published,
            progress_column=(
                case((Book.status == BookStatus.READ, 100), else_=0)
                if query.sort == "progress"
                else None
            ),
            status_buckets=BOOK_STATUS_BUCKETS,
        ),
    )


# pylint: enable=duplicate-code


@router.get("/get/{book_id}", response_model=BookRead)
async def get_book(book_id: UUID, db: AsyncSession = _DB, current_user: User = _USER) -> Book:
    return await _book(db, book_id, current_user.id)


@router.patch("/update/{book_id}", response_model=BookRead)
async def update_book(
    book_id: UUID,
    payload: BookUpdate,
    db: AsyncSession = _DB,
    current_user: User = _USER,
    actor: AuthenticatedActor = Depends(get_current_actor),
) -> Book:
    """Update a work. A changed descriptive field is locked against later metadata refreshes."""
    book = await _book(db, book_id, current_user.id, for_update=True)
    updates = drop_nulls_for_required_fields(
        payload.model_dump(exclude_unset=True),
        {column.key for column in Book.__table__.columns if not column.nullable},
    )
    if "first_published" in updates and "first_published_precision" not in updates:
        updates["first_published_precision"] = None
    edition_id = updates.get("active_edition_id")
    if edition_id is not None:
        await _edition(db, book, edition_id)
    apply_updates_with_locking(book, updates, LOCKABLE_FIELDS, actor=actor)
    if "title" in updates and "sort_title" not in updates:
        book.sort_title = derive_sort_title(book.title)
    await db.commit()
    return await _book(db, book.id, current_user.id)


@router.delete("/delete/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_book(book_id: UUID, db: AsyncSession = _DB, current_user: User = _USER) -> None:
    """Move a book to the trash, with its editions and reads."""
    await soft_delete(db, await _book(db, book_id, current_user.id))


@router.get("/trash")
async def list_book_trash(db: AsyncSession = _DB, current_user: User = _USER) -> list[dict]:
    """Deleted books, most recently deleted first."""
    return await trash_listing(db, Book, current_user.id)


@router.post("/trash/empty", response_model=MediaTrashEmptyResult)
async def empty_book_trash(
    payload: MediaTrashEmptyRequest, db: AsyncSession = _DB, current_user: User = _USER
) -> dict:
    """Permanently delete the books in the caller's trash, in bounded batches."""
    return await empty_trash(db, Book, current_user.id, deleted_through=payload.deleted_through)


@router.post("/{book_id}/restore", response_model=BookRead)
async def restore_book(book_id: UUID, db: AsyncSession = _DB, current_user: User = _USER) -> Book:
    book = await _book(db, book_id, current_user.id, include_deleted=True)
    await restore_row(db, book, "Book")
    return await _book(db, book.id, current_user.id)


@router.delete("/{book_id}/purge", status_code=status.HTTP_204_NO_CONTENT)
async def purge_book(book_id: UUID, db: AsyncSession = _DB, current_user: User = _USER) -> None:
    """Permanently remove a book already in the trash, with its editions and reads."""
    book = await _book(db, book_id, current_user.id, include_deleted=True)
    await purge_row(db, book, "Book")


# ---------------------------------------------------------------------------- editions


@router.post("/{book_id}/editions", response_model=BookRead, status_code=status.HTTP_201_CREATED)
async def add_edition(
    book_id: UUID,
    payload: BookEditionCreate,
    db: AsyncSession = _DB,
    current_user: User = _USER,
) -> Book:
    """Add an edition to a work; the response is the whole work."""
    book = await _book(db, book_id, current_user.id, for_update=True)
    if await _count(db, BookEdition, book.id) >= MAX_EDITIONS:
        raise _invalid(f"A book can have at most {MAX_EDITIONS} editions.")
    await _refuse_duplicate_isbn(db, book.id, payload.isbn_13)
    db.add(BookEdition(book_id=book.id, **payload.model_dump()))
    await _commit(db, "This edition conflicts with another edition of this book.")
    return await _book(db, book.id, current_user.id)


@router.patch("/{book_id}/editions/{edition_id}", response_model=BookRead)
async def update_edition(
    book_id: UUID,
    edition_id: UUID,
    payload: BookEditionUpdate,
    db: AsyncSession = _DB,
    current_user: User = _USER,
) -> Book:
    book = await _book(db, book_id, current_user.id, for_update=True)
    edition = await _edition(db, book, edition_id)
    updates = drop_nulls_for_required_fields(
        payload.model_dump(exclude_unset=True),
        {column.key for column in BookEdition.__table__.columns if not column.nullable},
    )
    if "publish_date" in updates and "publish_date_precision" not in updates:
        updates["publish_date_precision"] = None
    if "isbn_13" in updates:
        await _refuse_duplicate_isbn(db, book.id, updates["isbn_13"], edition.id)
    for field, value in updates.items():
        setattr(edition, field, value)
    if edition.page_count is not None:
        with db.no_autoflush:
            over = await db.scalar(
                select(BookReading.id).where(
                    BookReading.edition_id == edition.id,
                    BookReading.progress_unit == ProgressUnit.PAGE,
                    BookReading.progress_value > edition.page_count,
                )
            )
        if over is not None:
            raise _invalid("A read of this edition is already past that page count.")
    await _commit(db, "This edition conflicts with another edition of this book.")
    return await _book(db, book.id, current_user.id)


@router.delete("/{book_id}/editions/{edition_id}", response_model=BookRead)
async def delete_edition(
    book_id: UUID, edition_id: UUID, db: AsyncSession = _DB, current_user: User = _USER
) -> Book:
    """Remove an edition. Reads of it are kept, with no edition; it stops being the active one."""
    book = await _book(db, book_id, current_user.id, for_update=True)
    await db.delete(await _edition(db, book, edition_id))
    await db.commit()
    return await _book(db, book.id, current_user.id)


# ---------------------------------------------------------------------------- reads


@router.post("/{book_id}/reads", response_model=BookRead, status_code=status.HTTP_201_CREATED)
async def add_reading(
    book_id: UUID,
    payload: BookReadingCreate,
    db: AsyncSession = _DB,
    current_user: User = _USER,
) -> Book:
    """Record a read-through (a reread is another one); the response is the whole work."""
    book = await _book(db, book_id, current_user.id, for_update=True)
    if await _count(db, BookReading, book.id) >= MAX_READS:
        raise _invalid(f"A book can have at most {MAX_READS} reads.")
    reading = BookReading(book_id=book.id, **payload.model_dump())
    await _checked_reading(db, book, reading)
    db.add(reading)
    _sync_status(book, reading)
    await _commit(db, "This read is not valid.")
    return await _book(db, book.id, current_user.id)


@router.patch("/{book_id}/reads/{read_id}", response_model=BookRead)
async def update_reading(
    book_id: UUID,
    read_id: UUID,
    payload: BookReadingUpdate,
    db: AsyncSession = _DB,
    current_user: User = _USER,
) -> Book:
    book = await _book(db, book_id, current_user.id, for_update=True)
    reading = await _reading(db, book, read_id)
    updates = drop_nulls_for_required_fields(
        payload.model_dump(exclude_unset=True),
        {column.key for column in BookReading.__table__.columns if not column.nullable},
    )
    outcome_before = reading.outcome
    for field, value in updates.items():
        setattr(reading, field, value)
    await _checked_reading(db, book, reading)
    # only a changed outcome speaks for the work: editing an older read's dates or progress
    # must not overwrite a status the person set (say, In progress during a reread)
    if reading.outcome != outcome_before:
        _sync_status(book, reading)
    elif "edition_id" in updates and reading.outcome == ReadOutcome.READING:
        if reading.edition_id is not None:
            book.active_edition_id = reading.edition_id
    await _commit(db, "This read is not valid.")
    return await _book(db, book.id, current_user.id)


@router.delete("/{book_id}/reads/{read_id}", response_model=BookRead)
async def delete_reading(
    book_id: UUID, read_id: UUID, db: AsyncSession = _DB, current_user: User = _USER
) -> Book:
    book = await _book(db, book_id, current_user.id, for_update=True)
    await db.delete(await _reading(db, book, read_id))
    await db.commit()
    return await _book(db, book.id, current_user.id)
