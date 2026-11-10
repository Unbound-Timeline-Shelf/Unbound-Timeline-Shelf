"""Pydantic schemas for books, their editions and read-throughs (#265 phase 1).

Every free-text field is length-bounded (#265 section 6). Dates are ISO on the wire; a work's
first publication and an edition's publish date are C5 partial dates (a date plus ``year`` /
``month`` precision, NULL meaning a day), shown by the frontend's shared formatter.
"""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.api.schemas.provider_identity import ProviderIDs
from src.api.schemas.title_protection import TitleProtectionUpdate
from src.database.models.book import (
    BookFormat,
    BookOwnership,
    BookReading,
    BookStatus,
    ProgressUnit,
    ReadOutcome,
)
from src.features.books.isbn import normalise_pair

# A first publication can be centuries old; the frontend's entry allows the same years.
PARTIAL_DATE_YEARS = (1000, 2100)
# The fixed sane page bound when the edition's page count is unknown (#265 3.1).
MAX_PAGES = 100_000
MAX_AUTHORS = 30
MAX_EDITIONS = 50
MAX_READS = 200

Precision = Literal["year", "month", "day"]
_Author = Annotated[str, Field(min_length=1, max_length=200)]
_Label = Annotated[str, Field(min_length=1, max_length=128)]
_Labels = Annotated[list[_Label], Field(max_length=50)]
_Url = Annotated[str, Field(max_length=2048)]
_Note = Annotated[str, Field(max_length=20_000)]


def checked_precision(
    day: date | None, precision: Precision | None, *, field: str, given: bool
) -> Literal["year", "month"] | None:
    """The precision to store: None for a day; refuses a period that does not match the date."""
    if precision is None or precision == "day":
        return None
    if not given or day is None:
        raise ValueError(f"{field}_precision needs {field} in the same request")
    low, high = PARTIAL_DATE_YEARS
    if not low <= day.year <= high:
        raise ValueError(f"A year or month {field} must be from {low} to {high}")
    if day.day != 1 or (precision == "year" and day.month != 1):
        raise ValueError(f"A {precision} {field} must be the first day of that {precision}")
    return precision


# Preserve this media contract's field defaults and compatibility model names.
# pylint: disable=duplicate-code
class BookBase(BaseModel):
    """Fields shared by create payloads and responses."""

    title: str = Field(min_length=1, max_length=500)
    subtitle: str | None = Field(default=None, max_length=300)
    authors: list[_Author] = Field(default_factory=list, max_length=MAX_AUTHORS)
    description: str | None = Field(default=None, max_length=50_000)
    first_published: date | None = None
    first_published_precision: Precision | None = None
    language: str | None = Field(default=None, max_length=16)
    genres: _Labels = Field(default_factory=list)
    tags: _Labels = Field(default_factory=list)
    series_name: str | None = Field(default=None, max_length=200)
    series_position: str | None = Field(default=None, max_length=16)
    cover_url: _Url | None = None
    provider_ids: ProviderIDs = Field(default_factory=dict)

    status: BookStatus = BookStatus.WISHLIST
    priority: str | None = Field(default=None, max_length=20)
    favorite: bool = False
    note: _Note | None = None
    rating_overall: Decimal | None = Field(default=None, ge=0, le=10)
    personal_rank: int | None = Field(default=None, ge=1, le=2_147_483_647)


class BookCreate(BookBase):
    """Payload for creating a book. sort_title is derived if not given."""

    sort_title: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _precision(self) -> "BookCreate":
        self.first_published_precision = checked_precision(
            self.first_published,
            self.first_published_precision,
            field="first_published",
            given="first_published" in self.model_fields_set,
        )
        return self


class BookUpdate(TitleProtectionUpdate):
    """Partial update: only fields the caller sends are applied. A new ``first_published``
    without a precision is a day."""

    provider_ids: ProviderIDs | None = None
    title: str | None = Field(default=None, min_length=1, max_length=500)
    sort_title: str | None = Field(default=None, max_length=500)
    subtitle: str | None = Field(default=None, max_length=300)
    authors: list[_Author] | None = Field(default=None, max_length=MAX_AUTHORS)
    description: str | None = Field(default=None, max_length=50_000)
    first_published: date | None = None
    first_published_precision: Precision | None = None
    language: str | None = Field(default=None, max_length=16)
    genres: _Labels | None = None
    tags: _Labels | None = None
    series_name: str | None = Field(default=None, max_length=200)
    series_position: str | None = Field(default=None, max_length=16)
    cover_url: _Url | None = None

    status: BookStatus | None = None
    priority: str | None = Field(default=None, max_length=20)
    favorite: bool | None = None
    note: _Note | None = None
    rating_overall: Decimal | None = Field(default=None, ge=0, le=10)
    personal_rank: int | None = Field(default=None, ge=1, le=2_147_483_647)
    # one of this work's own editions, or null for none
    active_edition_id: UUID | None = None

    @model_validator(mode="after")
    def _precision(self) -> "BookUpdate":
        if "first_published_precision" in self.model_fields_set:
            self.first_published_precision = checked_precision(
                self.first_published,
                self.first_published_precision,
                field="first_published",
                given="first_published" in self.model_fields_set,
            )
        return self


# pylint: enable=duplicate-code


class _IsbnFields(BaseModel):
    """ISBNs as typed; normalised (and checked) together into the stored pair."""

    isbn_13: str | None = Field(default=None, max_length=20)
    isbn_10: str | None = Field(default=None, max_length=20)

    def normalise_isbns(self) -> None:
        pair = normalise_pair(self.isbn_13, self.isbn_10)
        self.isbn_13 = pair.isbn_13 if pair else None
        self.isbn_10 = pair.isbn_10 if pair else None


_Identifiers = Annotated[
    dict[
        Annotated[str, Field(pattern=r"^[a-z0-9._-]{1,50}$")],
        Annotated[str, Field(max_length=256)],
    ],
    Field(max_length=32),
]


class BookEditionCreate(_IsbnFields):
    other_identifiers: _Identifiers = Field(default_factory=dict)
    format: BookFormat = BookFormat.OTHER
    publisher: str | None = Field(default=None, max_length=200)
    publish_date: date | None = None
    publish_date_precision: Precision | None = None
    page_count: int | None = Field(default=None, ge=1, le=MAX_PAGES)
    language: str | None = Field(default=None, max_length=16)
    cover_url: _Url | None = None
    ownership: BookOwnership = BookOwnership.NONE
    acquired_at: date | None = None
    note: _Note | None = None

    @model_validator(mode="after")
    def _normalise(self) -> "BookEditionCreate":
        self.normalise_isbns()
        self.publish_date_precision = checked_precision(
            self.publish_date,
            self.publish_date_precision,
            field="publish_date",
            given="publish_date" in self.model_fields_set,
        )
        return self


class BookEditionUpdate(_IsbnFields):
    """Partial update. The two ISBN fields are one value: sending either replaces both."""

    other_identifiers: _Identifiers | None = None
    format: BookFormat | None = None
    publisher: str | None = Field(default=None, max_length=200)
    publish_date: date | None = None
    publish_date_precision: Precision | None = None
    page_count: int | None = Field(default=None, ge=1, le=MAX_PAGES)
    language: str | None = Field(default=None, max_length=16)
    cover_url: _Url | None = None
    ownership: BookOwnership | None = None
    acquired_at: date | None = None
    note: _Note | None = None

    @model_validator(mode="after")
    def _normalise(self) -> "BookEditionUpdate":
        if self.model_fields_set & {"isbn_13", "isbn_10"}:
            self.normalise_isbns()
            self.model_fields_set.update({"isbn_13", "isbn_10"})
        if "publish_date_precision" in self.model_fields_set:
            self.publish_date_precision = checked_precision(
                self.publish_date,
                self.publish_date_precision,
                field="publish_date",
                given="publish_date" in self.model_fields_set,
            )
        return self


def _check_progress(unit: ProgressUnit | None, value: Decimal | None) -> None:
    if value is None:
        return
    if unit == ProgressUnit.PERCENT and value > 100:
        raise ValueError("Percent progress is from 0 to 100")
    if unit == ProgressUnit.PAGE and value > MAX_PAGES:
        raise ValueError(f"Page progress is at most {MAX_PAGES}")


def _check_dates(started: date | None, finished: date | None) -> None:
    if started is not None and finished is not None and finished < started:
        raise ValueError("A read cannot finish before it started")


class BookReadingCreate(BaseModel):
    edition_id: UUID | None = None
    started_on: date | None = None
    finished_on: date | None = None
    progress_unit: ProgressUnit = ProgressUnit.PAGE
    progress_value: Decimal | None = Field(default=None, ge=0, max_digits=8, decimal_places=2)
    outcome: ReadOutcome = ReadOutcome.READING

    @model_validator(mode="after")
    def _bounds(self) -> "BookReadingCreate":
        _check_progress(self.progress_unit, self.progress_value)
        _check_dates(self.started_on, self.finished_on)
        return self


class BookReadingUpdate(BaseModel):
    """Partial update; the route checks the merged values against the bounds again."""

    edition_id: UUID | None = None
    started_on: date | None = None
    finished_on: date | None = None
    progress_unit: ProgressUnit | None = None
    progress_value: Decimal | None = Field(default=None, ge=0, max_digits=8, decimal_places=2)
    outcome: ReadOutcome | None = None


def check_reading(
    unit: ProgressUnit, value: Decimal | None, started: date | None, finished: date | None
) -> None:
    """The merged values of a read, as the database will hold them."""
    _check_progress(unit, value)
    _check_dates(started, finished)


def reading_problem(reading: BookReading, page_count: int | None) -> str | None:
    """Why a read breaks the rules, or None. Page progress stops at the edition's page count.

    The read routes and the library restore share this, so a restored read stays editable."""
    unit, value = reading.progress_unit, reading.progress_value
    try:
        check_reading(unit, value, reading.started_on, reading.finished_on)
    except ValueError as exc:
        return str(exc)
    if value is not None and value < 0:
        return "Progress cannot be negative"
    limit = page_count or MAX_PAGES
    if unit == ProgressUnit.PAGE and value is not None and value > limit:
        return f"Page progress cannot pass the edition's {limit} pages."
    return None


class BookEditionRead(BookEditionCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    book_id: UUID
    created_at: int
    updated_at: int

    @model_validator(mode="after")
    def _normalise(self) -> "BookEditionRead":
        # stored values are already normalised; a response never re-validates them
        return self


class BookReadingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    book_id: UUID
    edition_id: UUID | None
    started_on: date | None
    finished_on: date | None
    progress_unit: ProgressUnit
    progress_value: Decimal | None
    outcome: ReadOutcome
    created_at: int
    updated_at: int


class BookRead(BookBase):
    """Full representation returned to clients, with its editions and reads."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    sort_title: str
    active_edition_id: UUID | None = None
    locked_fields: list[str] = Field(default_factory=list)
    editions: list[BookEditionRead] = Field(default_factory=list)
    reads: list[BookReadingRead] = Field(default_factory=list)
    created_at: int = Field(description="Unix timestamp in seconds when the book was created.")
    updated_at: int = Field(description="Unix timestamp in seconds when the book was last updated.")


# The export's own book schema (#265 4.4): a work with its editions and reads nested, so one
# round trip is self-contained. It is the response shape, kept as a separate name so the export
# format can diverge from the API without changing either by accident.
class BookExport(BookRead):
    pass


def restorable(raw: Any) -> bool:
    """A tolerant reader's minimum: an object with a title."""
    return (
        isinstance(raw, dict) and isinstance(raw.get("title"), str) and bool(raw["title"].strip())
    )
