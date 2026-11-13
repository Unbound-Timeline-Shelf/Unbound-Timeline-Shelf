"""Books: a tracked work, its optional editions and its read-throughs (#265 phase 1).

A book is its own feature, like movies, TV shows and anime (#265 section 5, option A): one
``books`` row per work the user tracks, zero or more ``book_editions`` (a specific publication,
identified by a normalised ISBN-13 when one is known) and one ``book_reads`` row per read-through,
so rereads are kept. Every row belongs to the owner of its work; a work is soft-deleted into the
media trash and its editions and reads go with it when it is removed for good.
"""

from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.database.base import Base, unix_timestamp


class BookStatus(str, Enum):
    """Where the work stands for its reader (#265 section 3.1)."""

    WISHLIST = "WISHLIST"
    BACKLOG = "BACKLOG"
    IN_PROGRESS = "IN_PROGRESS"
    ON_HOLD = "ON_HOLD"
    READ = "READ"
    DROPPED = "DROPPED"


class BookFormat(str, Enum):
    """An edition's format. The values are the Atlas ``book_format`` vocabulary (C15 2.3)."""

    PAPERBACK = "paperback"
    HARDCOVER = "hardcover"
    EBOOK = "ebook"
    AUDIOBOOK = "audiobook"
    OTHER = "other"


class BookOwnership(str, Enum):
    OWNED = "owned"
    BORROWED = "borrowed"
    LIBRARY = "library"
    WISHLIST = "wishlist"
    NONE = "none"


class ProgressUnit(str, Enum):
    PAGE = "page"
    PERCENT = "percent"


class ReadOutcome(str, Enum):
    READING = "reading"
    FINISHED = "finished"
    ABANDONED = "abandoned"


def _values(enum_class: type[Enum]) -> list[str]:
    return [member.value for member in enum_class]


def _value_enum(enum_class: type[Enum], length: int) -> SAEnum:
    # The lowercase vocabulary values are what the database and the export store.
    return SAEnum(enum_class, native_enum=False, length=length, values_callable=_values)


# Repeated column declarations preserve this table's explicit schema contract.
class Book(Base):
    # pylint: disable=duplicate-code
    __tablename__ = "books"
    __table_args__ = (
        CheckConstraint(
            "first_published_precision IS NULL "
            "OR first_published_precision IN ('year', 'month', 'day')",
            name="ck_books_first_published_precision",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # NULL means active; set when the work is moved to the trash.
    deleted_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    provider_ids: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    sort_title: Mapped[str] = mapped_column(String(500), nullable=False)
    subtitle: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # ordered display names only in this phase (#265 3.1; C15 D15.3: no author records)
    authors: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A C5 partial date: the first day of the known period plus how much of it is known
    # (NULL = a day), exactly like a game's release date.
    first_published: Mapped[date | None] = mapped_column(Date, nullable=True)
    first_published_precision: Mapped[str | None] = mapped_column(String(5), nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    genres: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )
    series_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    series_position: Mapped[str | None] = mapped_column(String(16), nullable=True)
    cover_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    locked_fields: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )

    status: Mapped[BookStatus] = mapped_column(
        SAEnum(BookStatus, native_enum=False, length=30),
        nullable=False,
        default=BookStatus.WISHLIST,
    )
    priority: Mapped[str | None] = mapped_column(String(20), nullable=True)
    favorite: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "rating" in #265; named like the other kinds so the shared library ranking applies
    rating_overall: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    personal_rank: Mapped[int | None] = mapped_column(nullable=True)
    # the edition being read now; it always belongs to this work (checked by the API)
    active_edition_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "book_editions.id",
            ondelete="SET NULL",
            use_alter=True,
            name="fk_books_active_edition",
        ),
        nullable=True,
    )

    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=unix_timestamp, onupdate=unix_timestamp
    )

    editions: Mapped[list["BookEdition"]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="BookEdition.created_at, BookEdition.id",
        foreign_keys="BookEdition.book_id",
        lazy="selectin",
    )
    reads: Mapped[list["BookReading"]] = relationship(
        back_populates="book",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="BookReading.created_at, BookReading.id",
        lazy="selectin",
    )


class BookEdition(Base):
    """One publication of a work. Its identity is the normalised ISBN-13 when known."""

    __tablename__ = "book_editions"
    __table_args__ = (
        # #265 3.2: two editions of one work with the same ISBN-13 are refused, never merged.
        Index(
            "uq_book_editions_book_isbn13",
            "book_id",
            "isbn_13",
            unique=True,
            postgresql_where=text("isbn_13 IS NOT NULL"),
        ),
        CheckConstraint(
            "publish_date_precision IS NULL OR publish_date_precision IN ('year', 'month', 'day')",
            name="ck_book_editions_publish_date_precision",
        ),
        CheckConstraint(
            "page_count IS NULL OR page_count BETWEEN 1 AND 100000",
            name="ck_book_editions_page_count",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    book_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    isbn_10: Mapped[str | None] = mapped_column(String(10), nullable=True)
    isbn_13: Mapped[str | None] = mapped_column(String(13), nullable=True)
    other_identifiers: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    format: Mapped[BookFormat] = mapped_column(
        _value_enum(BookFormat, 20), nullable=False, default=BookFormat.OTHER
    )
    publisher: Mapped[str | None] = mapped_column(String(200), nullable=True)
    publish_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    publish_date_precision: Mapped[str | None] = mapped_column(String(5), nullable=True)
    page_count: Mapped[int | None] = mapped_column(nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    cover_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    ownership: Mapped[BookOwnership] = mapped_column(
        _value_enum(BookOwnership, 20), nullable=False, default=BookOwnership.NONE
    )
    acquired_at: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=unix_timestamp, onupdate=unix_timestamp
    )

    book: Mapped[Book] = relationship(back_populates="editions", foreign_keys=[book_id])


class BookReading(Base):
    """One read-through of a work (table ``book_reads``); a reread is another row."""

    __tablename__ = "book_reads"
    __table_args__ = (
        CheckConstraint(
            "progress_value IS NULL OR progress_value >= 0", name="ck_book_reads_progress_min"
        ),
        CheckConstraint(
            "progress_value IS NULL OR progress_unit <> 'percent' OR progress_value <= 100",
            name="ck_book_reads_percent_max",
        ),
        CheckConstraint(
            "started_on IS NULL OR finished_on IS NULL OR finished_on >= started_on",
            name="ck_book_reads_dates",
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    book_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("books.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    edition_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("book_editions.id", ondelete="SET NULL"),
        nullable=True,
    )
    started_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    finished_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    progress_unit: Mapped[ProgressUnit] = mapped_column(
        _value_enum(ProgressUnit, 10), nullable=False, default=ProgressUnit.PAGE
    )
    progress_value: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    outcome: Mapped[ReadOutcome] = mapped_column(
        _value_enum(ReadOutcome, 10), nullable=False, default=ReadOutcome.READING
    )

    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=unix_timestamp, onupdate=unix_timestamp
    )

    book: Mapped[Book] = relationship(back_populates="reads")
