"""Provider-neutral, bounded metadata extension contracts for Plugin API v1.1.1.

Plugin API 1.1.20 adds partial and regional release dates (``first_release``, ``releases``),
the identity verdict of a lookup (``identity``), achievement ``aliases``, the ``platinum``
tier, and books: the ``book`` media type, ``MetadataPatch.book``, a candidate's ``authors`` and
``isbn_13``, and the reserved request option ``isbn``. Every one of them is gated on the single
``UNIFIED_CONTRACT`` (C15 6.4). A plugin whose manifest declares an older contract is held to the
older shape: the host
validates its responses with ``api_contract`` in the validation context (see
``contract_context``), and a 1.1.20 field or value from it is an ``invalid_response``, exactly as
an unknown field was before. Host code that builds these models without a context (core
providers, merging pages) is not limited.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    ValidationInfo,
    model_validator,
)

from .base_contracts import UNIFIED_CONTRACT, parse_semver
from .vocab import BOOK_CREATOR_ROLES, BOOK_FORMATS

Identifier = Annotated[str, Field(min_length=1, max_length=512)]
ActionId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")]

# The Plugin API release that introduced the release, identity, alias and book fields and
# ``platinum``. It is ``base_contracts.UNIFIED_CONTRACT`` itself (the manifest's
# ``api_contract_max`` is gated on it too); ``RELEASES_CONTRACT`` is M7's name for it.
RELEASES_CONTRACT = UNIFIED_CONTRACT
UNIFIED_CONTRACT_VERSION = ".".join(map(str, UNIFIED_CONTRACT))
CONTRACT_CONTEXT_KEY = "api_contract"
# The reserved request option for an identifier lookup: a normalised ISBN-13 (C15 6.2).
ISBN_OPTION = "isbn"


def contract_context(api_contract_version: str) -> dict[str, tuple[int, int, int]]:
    """The validation context for a response from a plugin that declares this contract."""
    return {CONTRACT_CONTEXT_KEY: parse_semver(api_contract_version)}


def _declared_contract(info: ValidationInfo) -> tuple[int, int, int] | None:
    context = info.context
    if not isinstance(context, dict):
        return None
    declared = context.get(CONTRACT_CONTEXT_KEY)
    return declared if isinstance(declared, tuple) else None


def _require_unified_contract(info: ValidationInfo, used: list[str]) -> None:
    """Refuse fields of the unified release from a plugin that declared an older contract."""
    declared = _declared_contract(info)
    if used and declared is not None and declared < UNIFIED_CONTRACT:
        raise ValueError(f"{', '.join(used)} require Plugin API {UNIFIED_CONTRACT_VERSION}")


# M7's name, kept for its callers.
_require_releases_contract = _require_unified_contract


def _isbn10_check(first_nine: str) -> str:
    total = sum((10 - index) * int(digit) for index, digit in enumerate(first_nine))
    check = (11 - total % 11) % 11
    return "X" if check == 10 else str(check)


def _isbn13_check(first_twelve: str) -> str:
    total = sum(int(digit) * (3 if index % 2 else 1) for index, digit in enumerate(first_twelve))
    return str((10 - total % 10) % 10)


def valid_isbn13(value: str) -> bool:
    """A normalised ISBN-13: 978 or 979, thirteen digits, right check digit."""
    return (
        len(value) == 13
        and value.isascii()
        and value.isdigit()
        and value[:3] in {"978", "979"}
        and _isbn13_check(value[:12]) == value[12]
    )


def valid_isbn10(value: str) -> bool:
    """A normalised ISBN-10: nine digits and a digit or ``X``, right check digit."""
    return (
        len(value) == 10
        and value.isascii()
        and value[:9].isdigit()
        and (value[9].isdigit() or value[9] == "X")
        and _isbn10_check(value[:9]) == value[9]
    )


def isbn10_as_13(value: str) -> str:
    body = "978" + value[:9]
    return body + _isbn13_check(body)


def _checked_isbn13(value: str | None) -> None:
    if value is not None and not valid_isbn13(value):
        raise ValueError("isbn_13 is not a normalised ISBN-13 with a valid check digit")


def _checked_isbn10(value: str | None) -> None:
    if value is not None and not valid_isbn10(value):
        raise ValueError("isbn_10 is not a normalised ISBN-10 with a valid check digit")


# Normalised identifiers only: no spaces or hyphens, an upper-case X. The host never repairs one.
Isbn13 = Annotated[str, Field(min_length=13, max_length=13, pattern=r"^97[89]\d{10}$")]
Isbn10 = Annotated[str, Field(min_length=10, max_length=10, pattern=r"^\d{9}[\dX]$")]


CustomValue = (
    StrictBool
    | StrictInt
    | Annotated[StrictFloat, Field(allow_inf_nan=False)]
    | Annotated[str, Field(max_length=500)]
    | Annotated[tuple[Annotated[str, Field(max_length=200)], ...], Field(max_length=50)]
)


class MetadataModel(BaseModel):
    """Reject unknown fields at the public boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class MediaType(StrEnum):
    """Canonical library domains supported by provider capabilities."""

    GAME = "game"
    MOVIE = "movie"
    TV_SHOW = "tv_show"
    ANIME = "anime"
    # Plugin API 1.1.20 (C15 6.3).
    BOOK = "book"


class ProviderHealth(StrEnum):
    """Availability, distinguished from plugin process health."""

    DISABLED = "disabled"
    NOT_CONFIGURED = "not_configured"
    UNVALIDATED = "unvalidated"
    HEALTHY = "healthy"
    UNAVAILABLE = "temporarily_unavailable"
    RATE_LIMITED = "rate_limited"
    PLUGIN_UNAVAILABLE = "plugin_unavailable"
    INVALID_CONFIGURATION = "invalid_configuration"


def _option(options: Mapping[str, object], key: str) -> tuple[bool, object]:
    return key in options, options.get(key)


class MetadataProviderRequest(MetadataModel):
    """Authenticated work issued by the host, including its execution policy."""

    request_id: UUID
    user_id: UUID
    query: str = Field(min_length=1, max_length=512)
    media_type: MediaType = MediaType.GAME
    policy: Literal["interactive", "background"] = "interactive"
    limit: int = Field(default=20, ge=1, le=50)
    cursor: Identifier | None = None
    resource: Literal[
        "entity", "episodes", "airing", "relations", "recommendations", "achievements"
    ] = "entity"
    season_number: int | None = Field(default=None, ge=0, le=10_000)
    # ``isbn`` is reserved (Plugin API 1.1.20): a normalised ISBN-13 the person typed, sent with a
    # ``book`` request as an identifier lookup rather than a title.
    options: dict[ActionId, Annotated[str, Field(max_length=512)] | bool | int] = Field(
        default_factory=dict, max_length=32
    )

    @model_validator(mode="after")
    def book_request(self, info: ValidationInfo) -> "MetadataProviderRequest":
        has_isbn, isbn = _option(self.options, ISBN_OPTION)
        if has_isbn and (
            self.media_type != MediaType.BOOK or not isinstance(isbn, str) or not valid_isbn13(isbn)
        ):
            raise ValueError("the isbn option is a normalised ISBN-13 on a book request")
        used = ["media_type book"] if self.media_type == MediaType.BOOK else []
        if has_isbn:
            used.append("option isbn")
        _require_unified_contract(info, used)
        return self


class MetadataCandidate(MetadataModel):
    """Lightweight identity; metadata and artwork are separate operations."""

    external_id: Identifier
    title: str = Field(min_length=1, max_length=512)
    year: int | None = Field(default=None, ge=1800, le=3000)
    provider: str = Field(min_length=1, max_length=128)
    media_type: MediaType = MediaType.GAME
    alternate_titles: tuple[Annotated[str, Field(max_length=512)], ...] = Field(
        default=(), max_length=20
    )
    provider_ids: dict[ActionId, Identifier] = Field(default_factory=dict, max_length=32)
    platforms: tuple[Annotated[str, Field(max_length=128)], ...] = Field(default=(), max_length=32)
    # Plugin API 1.1.20, books: creator names to show beside a result (display only; the
    # ordered list with roles is ``MetadataPatch.book.authors``) and the ISBN-13 it matched.
    authors: tuple[Annotated[str, Field(min_length=1, max_length=200)], ...] = Field(
        default=(), max_length=30
    )
    isbn_13: Isbn13 | None = None

    @model_validator(mode="after")
    def declared_contract(self, info: ValidationInfo) -> "MetadataCandidate":
        _checked_isbn13(self.isbn_13)
        used = [name for name in ("authors", "isbn_13") if name in self.model_fields_set]
        if self.media_type == MediaType.BOOK:
            used.insert(0, "media_type book")
        _require_unified_contract(info, used)
        return self


class MetadataLink(MetadataModel):
    """A provider reference rendered by the existing application links UI."""

    label: str = Field(min_length=1, max_length=128)
    url: AnyHttpUrl


class MetadataEpisode(MetadataModel):
    """Provider-neutral episode data; watched state remains entirely host-owned."""

    episode_number: int = Field(ge=0, le=100_000)
    season_number: int | None = Field(default=None, ge=0, le=10_000)
    title: str | None = Field(default=None, max_length=512)
    description: str | None = Field(default=None, max_length=100_000)
    air_date: date | None = None
    air_at: int | None = Field(default=None, ge=0)
    runtime_minutes: int | None = Field(default=None, ge=0, le=100_000)
    still_url: AnyHttpUrl | None = None


class MetadataSeason(MetadataModel):
    season_number: int = Field(ge=0, le=10_000)
    external_id: Identifier | None = None
    title: str | None = Field(default=None, max_length=512)
    episode_count: int | None = Field(default=None, ge=0, le=100_000)
    air_date: date | None = None


class MetadataRelation(MetadataModel):
    """An identity and its relation, with no application database references."""

    candidate: MetadataCandidate
    relation: str = Field(min_length=1, max_length=64)
    group: Literal["related", "chain", "branch", "recommendation"] = "related"
    parent_external_id: Identifier | None = None
    parent_group: Literal["chain", "branch"] | None = None
    is_current: bool = False
    format: str | None = Field(default=None, max_length=64)
    episode_count: int | None = Field(default=None, ge=0, le=100_000)
    poster_url: AnyHttpUrl | None = None


class MetadataAiring(MetadataModel):
    status: Literal["ongoing", "completed", "paused", "cancelled", "unknown"] = "unknown"
    is_airing: bool | None = None
    aired_episodes: int | None = Field(default=None, ge=0, le=100_000)
    total_episodes: int | None = Field(default=None, ge=0, le=100_000)
    next_episode_number: int | None = Field(default=None, ge=0, le=100_000)
    next_episode_at: int | None = Field(default=None, ge=0)


# The most achievement definitions one catalogue may hold, across all of its pages.
MAX_ACHIEVEMENTS = 2000


class MetadataAchievement(MetadataModel):
    """An achievement definition from a provider's catalogue. Progress is never provider-owned."""

    external_id: Identifier
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2_000)
    # A hidden entry whose real text the provider did not reveal arrives as a placeholder.
    hidden: bool = False
    kind: Literal["progression", "missable", "win_condition"] | None = None
    # ``platinum`` (a whole-catalogue completion trophy) needs Plugin API 1.1.20 (C7.1).
    tier: Literal["bronze", "silver", "gold", "platinum"] | None = None
    points: int | None = Field(default=None, ge=0, le=100_000)
    global_percent: float | None = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    icon_url: AnyHttpUrl | None = None
    locked_icon_url: AnyHttpUrl | None = None
    # Ids the same achievement has elsewhere (e.g. {"steam": "ACH_X"}), used to match a user's
    # synced progress to this entry.
    source_ids: dict[ActionId, Identifier] = Field(default_factory=dict, max_length=8)
    # Plugin API 1.1.20: other stable ids of this same entry (Atlas sends every merged row's key).
    # A stored definition listed here keeps its id instead of being replaced (C2.5 step 3).
    aliases: tuple[Identifier, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def declared_contract(self, info: ValidationInfo) -> "MetadataAchievement":
        used = ["aliases"] if "aliases" in self.model_fields_set else []
        if self.tier == "platinum":
            used.append("tier platinum")
        _require_releases_contract(info, used)
        return self


# C5.1: YYYY, YYYY-MM or YYYY-MM-DD; a day must also be a real calendar date.
PARTIAL_DATE_RE = r"^\d{4}(-(0[1-9]|1[0-2])(-(0[1-9]|[12]\d|3[01]))?)?$"
_PRECISION_LENGTH = {"year": 4, "month": 7, "day": 10}
# Year and month values follow C5.1 (1950-2100). A day keeps the host's general date range, as a
# day ``release_date`` always did: Atlas carries a pre-1950 day into ``first_release``.
_PARTIAL_YEARS = (1950, 2100)
_DAY_YEARS = (1800, 3000)
PartialDate = Annotated[str, Field(pattern=PARTIAL_DATE_RE, max_length=10)]


def partial_date_start(value: str) -> date:
    """The first day of the period a PartialDate names ("2011" is 1 January 2011)."""
    year, month, day = (value.split("-") + ["1", "1"])[:3]
    return date(int(year), int(month), int(day))


class MetadataRelease(MetadataModel):
    """One release of a game, at year, month or day precision (Plugin API 1.1.20, C5.3)."""

    date: PartialDate
    precision: Literal["year", "month", "day"]
    platform: str | None = Field(default=None, min_length=1, max_length=128)
    region: str | None = Field(default=None, pattern=r"^(ww|eu|na|asia|oce|latam|[a-z]{2})$")
    edition: str | None = Field(default=None, min_length=1, max_length=100)
    category: Literal["full", "early_access", "beta", "rerelease", "port", "demo"] = "full"

    @model_validator(mode="after")
    def precision_matches_date(self) -> "MetadataRelease":
        if len(self.date) != _PRECISION_LENGTH[self.precision]:
            raise ValueError(f"a {self.precision} date is written {self.precision_shape()}")
        try:
            start = partial_date_start(self.date)
        except ValueError as exc:
            raise ValueError("date is not a calendar date") from exc
        low, high = _DAY_YEARS if self.precision == "day" else _PARTIAL_YEARS
        if not low <= start.year <= high:
            raise ValueError(f"a {self.precision} date must fall between {low} and {high}")
        return self

    def precision_shape(self) -> str:
        return {"year": "YYYY", "month": "YYYY-MM", "day": "YYYY-MM-DD"}[self.precision]

    @property
    def key(self) -> tuple[str | None, str | None, str | None, str]:
        """Entries are unique on (platform, region, edition, category) (C5.1)."""
        return (self.platform, self.region, self.edition, self.category)


# The years a book date may name (the host's ``api.schemas.book.PARTIAL_DATE_YEARS``).
BOOK_YEARS = (1000, 2100)
MAX_BOOK_AUTHORS = 30
MAX_BOOK_EDITIONS = 50
MAX_BOOK_PAGES = 100_000


class MetadataBookDate(MetadataModel):
    """A book date at year, month or day precision (the C5.1 shape, in book years 1000-2100).

    A book can be centuries older than C5.1's 1950 floor for release years, so a work's first
    publication and an edition's date use this shape instead of ``MetadataRelease``."""

    date: PartialDate
    precision: Literal["year", "month", "day"]

    @model_validator(mode="after")
    def precision_matches_date(self) -> "MetadataBookDate":
        if len(self.date) != _PRECISION_LENGTH[self.precision]:
            raise ValueError(f"a {self.precision} date does not have that shape")
        try:
            start = partial_date_start(self.date)
        except ValueError as exc:
            raise ValueError("date is not a calendar date") from exc
        low, high = BOOK_YEARS
        if not low <= start.year <= high:
            raise ValueError(f"a book date must fall between {low} and {high}")
        return self


BookCreatorRole = Literal[BOOK_CREATOR_ROLES]  # type: ignore[valid-type]
BookFormat = Literal[BOOK_FORMATS]  # type: ignore[valid-type]


class MetadataBookAuthor(MetadataModel):
    """One creator of a work, in the provider's order (C15 2.3 ``authors[]``)."""

    name: str = Field(min_length=1, max_length=200)
    role: BookCreatorRole = "author"


def _isbn_pair(isbn_13: str | None, isbn_10: str | None) -> None:
    _checked_isbn13(isbn_13)
    _checked_isbn10(isbn_10)
    if isbn_13 is not None and isbn_10 is not None and isbn10_as_13(isbn_10) != isbn_13:
        raise ValueError("isbn_10 and isbn_13 name different books")


class MetadataBookEdition(MetadataModel):
    """One edition of the work (C15 2.3 ``editions[]``, with the host's field names).

    ``key`` is the source's own edition id. An edition never becomes a library record by
    itself: the host copies one into the work's editions only when the person adds it."""

    key: Identifier | None = None
    name: str | None = Field(default=None, min_length=1, max_length=200)
    isbn_13: Isbn13 | None = None
    isbn_10: Isbn10 | None = None
    format: BookFormat | None = None
    publisher: str | None = Field(default=None, min_length=1, max_length=200)
    publish_date: MetadataBookDate | None = None
    page_count: int | None = Field(default=None, ge=1, le=MAX_BOOK_PAGES)
    language: str | None = Field(default=None, min_length=1, max_length=16)

    @model_validator(mode="after")
    def consistent_isbns(self) -> "MetadataBookEdition":
        _isbn_pair(self.isbn_13, self.isbn_10)
        return self


class MetadataBook(MetadataModel):
    """What a book provider knows about a work (Plugin API 1.1.20, C15 6.3).

    ``isbn_13``/``isbn_10`` name the edition the lookup matched (an ISBN search), if any. The
    host keeps ``authors`` as its ordered author names; ``series_*`` stay plain fields (#265 9).
    """

    authors: tuple[MetadataBookAuthor, ...] = Field(default=(), max_length=MAX_BOOK_AUTHORS)
    subtitle: str | None = Field(default=None, min_length=1, max_length=300)
    isbn_13: Isbn13 | None = None
    isbn_10: Isbn10 | None = None
    page_count: int | None = Field(default=None, ge=1, le=MAX_BOOK_PAGES)
    series_name: str | None = Field(default=None, min_length=1, max_length=200)
    series_position: str | None = Field(default=None, min_length=1, max_length=16)
    original_language: str | None = Field(default=None, min_length=1, max_length=16)
    # The work's first publication. ``MetadataPatch.first_release`` keeps the C5.1 release years
    # (1950-2100); a book's may be centuries older.
    first_published: MetadataBookDate | None = None
    editions: tuple[MetadataBookEdition, ...] = Field(default=(), max_length=MAX_BOOK_EDITIONS)

    @model_validator(mode="after")
    def consistent_editions(self) -> "MetadataBook":
        _isbn_pair(self.isbn_13, self.isbn_10)
        isbns = [edition.isbn_13 for edition in self.editions if edition.isbn_13]
        if len(isbns) != len(set(isbns)):
            # The host refuses a duplicate ISBN within one work (#265 3.2); a provider collapses
            # its duplicates before it answers (Atlas does, C15 2.5).
            raise ValueError("editions repeat an ISBN-13")
        return self


class MetadataIdentity(MetadataModel):
    """How the provider matched the lookup to its record (Plugin API 1.1.20, C4.4).

    ``id``, ``redirect`` and ``crosswalk`` are identity evidence; ``previous_ids`` names ids the
    provider no longer uses for this record (a merge or redirect), so the host can follow them.
    ``conflict`` and ``ambiguous`` are never assigned automatically."""

    match: Literal["id", "redirect", "crosswalk", "exact", "conflict", "ambiguous"]
    previous_ids: dict[ActionId, Identifier] = Field(default_factory=dict, max_length=8)


class MetadataPatch(MetadataModel):
    """Partial canonical fields; missing values never erase existing information."""

    title: str | None = Field(default=None, min_length=1, max_length=512)
    year: int | None = Field(default=None, ge=1800, le=3000)
    release_date: date | None = None
    description: str | None = Field(default=None, max_length=100_000)
    developer: str | None = Field(default=None, max_length=200)
    publisher: str | None = Field(default=None, max_length=200)
    series: str | None = Field(default=None, max_length=200)
    age_rating: str | None = Field(default=None, max_length=64)
    tags: tuple[Annotated[str, Field(max_length=128)], ...] = Field(default=(), max_length=128)
    features: tuple[Annotated[str, Field(max_length=128)], ...] = Field(default=(), max_length=128)
    genres: tuple[Annotated[str, Field(max_length=128)], ...] = Field(default=(), max_length=128)
    platforms: tuple[Annotated[str, Field(max_length=128)], ...] = Field(default=(), max_length=64)
    time_to_beat_hours: float | None = Field(default=None, ge=0, le=100_000, allow_inf_nan=False)
    time_to_beat_extras_hours: float | None = Field(
        default=None, ge=0, le=99_999.9, allow_inf_nan=False
    )
    time_to_beat_completionist_hours: float | None = Field(
        default=None, ge=0, le=99_999.9, allow_inf_nan=False
    )
    time_to_beat_all_hours: float | None = Field(
        default=None, ge=0, le=99_999.9, allow_inf_nan=False
    )
    runtime_minutes: int | None = Field(default=None, ge=0, le=100_000)
    director: str | None = Field(default=None, max_length=512)
    writer: str | None = Field(default=None, max_length=512)
    creators: tuple[Annotated[str, Field(max_length=512)], ...] = Field(default=(), max_length=64)
    studios: tuple[Annotated[str, Field(max_length=256)], ...] = Field(default=(), max_length=64)
    countries: tuple[Annotated[str, Field(max_length=128)], ...] = Field(default=(), max_length=64)
    languages: tuple[Annotated[str, Field(max_length=128)], ...] = Field(default=(), max_length=64)
    provider_ids: dict[ActionId, Identifier] = Field(default_factory=dict, max_length=32)
    alternate_titles: tuple[Annotated[str, Field(max_length=512)], ...] = Field(
        default=(), max_length=20
    )
    links: tuple[MetadataLink, ...] = Field(default=(), max_length=32)
    episode_runtime_minutes: int | None = Field(default=None, ge=0, le=100_000)
    episode_count: int | None = Field(default=None, ge=0, le=100_000)
    format: str | None = Field(default=None, max_length=64)
    scores: dict[ActionId, Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]] = Field(
        default_factory=dict, max_length=32
    )
    titles: dict[ActionId, Annotated[str, Field(max_length=512)]] = Field(
        default_factory=dict, max_length=20
    )
    # Bounded aggregate across pages, including long-running anime and daily TV shows.
    episodes: tuple[MetadataEpisode, ...] = Field(default=(), max_length=10_000)
    seasons: tuple[MetadataSeason, ...] = Field(default=(), max_length=100)
    airing: MetadataAiring | None = None
    relations: tuple[MetadataRelation, ...] = Field(default=(), max_length=500)
    relation_group: str | None = Field(default=None, max_length=512)
    # Group play as reported by the provider. Unknown stays unset: absence never means "no".
    players_min: int | None = Field(default=None, ge=1, le=32)
    players_max: int | None = Field(default=None, ge=1, le=32)
    crossplay: Literal["yes", "no", "partial"] | None = None
    # Achievement definitions, only requested through the "achievements" resource.
    achievements: tuple[MetadataAchievement, ...] = Field(default=(), max_length=MAX_ACHIEVEMENTS)
    # Provider-defined fields the host has no column for, kept as read-only provider details.
    custom: dict[ActionId, CustomValue] = Field(default_factory=dict, max_length=32)
    # Plugin API 1.1.20. The first release at its real precision ("2011", "2011-04"); its
    # platform, region and edition are ignored. ``release_date`` keeps its day-only meaning.
    first_release: MetadataRelease | None = None
    # Plugin API 1.1.20. Up to five releases, unique on (platform, region, edition, category).
    releases: tuple[MetadataRelease, ...] = Field(default=(), max_length=5)
    # Plugin API 1.1.20. How the provider matched this lookup (C4.4 rules 2 and 4).
    identity: MetadataIdentity | None = None
    # Plugin API 1.1.20. Book details (C15 6.3); refused from an older plugin even when null.
    book: MetadataBook | None = None

    @model_validator(mode="after")
    def ordered_players(self) -> "MetadataPatch":
        if (
            self.players_min is not None
            and self.players_max is not None
            and self.players_min > self.players_max
        ):
            raise ValueError("players_max must be at least players_min")
        return self

    @model_validator(mode="after")
    def consistent_releases(self, info: ValidationInfo) -> "MetadataPatch":
        _require_releases_contract(
            info,
            [
                name
                for name in ("first_release", "releases", "identity", "book")
                if name in self.model_fields_set
            ],
        )
        keys = [release.key for release in self.releases]
        if len(keys) != len(set(keys)):
            raise ValueError("releases repeat a (platform, region, edition, category) entry")
        first = self.first_release
        if (
            first is not None
            and first.precision == "day"
            and self.release_date is not None
            and self.release_date.isoformat() != first.date
        ):
            raise ValueError("release_date and a day first_release must name the same day")
        return self


class MediaAsset(MetadataModel):
    """An asset reference; providers never write the host filesystem."""

    kind: Literal["key_art", "banner", "logo", "icon", "screenshot", "hero", "poster"]
    url: AnyHttpUrl
    priority: int = Field(default=100, ge=0, le=10_000)
    width: int | None = Field(default=None, ge=1, le=50_000)
    height: int | None = Field(default=None, ge=1, le=50_000)


class ProviderFailure(MetadataModel):
    """Safe failure data without remote response bodies or credential values."""

    code: Literal[
        "not_configured",
        "invalid_configuration",
        "unavailable",
        "rate_limited",
        "timeout",
        "plugin_unavailable",
        "invalid_response",
    ]
    retry_after_seconds: int | None = Field(default=None, ge=0, le=86_400)


class ProviderResponse(MetadataModel):
    """The same response envelope for every provider operation."""

    candidates: tuple[MetadataCandidate, ...] = Field(default=(), max_length=50)
    metadata: MetadataPatch | None = None
    assets: tuple[MediaAsset, ...] = Field(default=(), max_length=200)
    health: ProviderHealth | None = None
    failure: ProviderFailure | None = None
    next_cursor: Identifier | None = None


class ProviderOperations(MetadataModel):
    """Independent optional capabilities, implemented by existing runtime actions."""

    search: ActionId | None = None
    metadata: ActionId | None = None
    media: ActionId | None = None
    health: ActionId


class ProviderConfigurationField(MetadataModel):
    """Author-declared credential ownership rendered by the host configuration UI."""

    key: ActionId
    label: str = Field(min_length=1, max_length=128)
    scope: Literal["system", "user", "both"] = "both"
    required: bool = True
    secret: bool = True


class MetadataProviderRegistration(MetadataModel):
    """Namespaced registration bound by the host to an authenticated installation."""

    provider_id: ActionId
    name: str = Field(min_length=1, max_length=128)
    # One of each media type at most; ``book`` needs Plugin API 1.1.20 (C15 6.3).
    media_types: tuple[MediaType, ...] = Field(min_length=1, max_length=len(MediaType))
    identifier_namespace: ActionId | None = None
    metadata_resources: tuple[
        Literal["entity", "episodes", "airing", "relations", "recommendations", "achievements"],
        ...,
    ] = Field(default=("entity",), min_length=1, max_length=6)
    episode_source: Literal["primary", "fallback"] = "fallback"
    operations: ProviderOperations
    configuration: tuple[ProviderConfigurationField, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def declared_contract(self, info: ValidationInfo) -> "MetadataProviderRegistration":
        """A registration from an older plugin may not declare ``book``. The host validates a
        declaration with the plugin's contract where it enters the host (registration); a stored
        row is read without one, so it never becomes unreadable."""
        _require_unified_contract(
            info, ["media_types book"] if MediaType.BOOK in self.media_types else []
        )
        return self

    @model_validator(mode="after")
    def unique_declarations(self) -> "MetadataProviderRegistration":
        """Reject ambiguous media and configuration declarations."""
        keys = [item.key for item in self.configuration]
        if len(keys) != len(set(keys)) or len(self.media_types) != len(set(self.media_types)):
            raise ValueError("provider contains duplicate declarations")
        if len(self.metadata_resources) != len(set(self.metadata_resources)):
            raise ValueError("provider contains duplicate metadata resources")
        if not any((self.operations.search, self.operations.metadata, self.operations.media)):
            raise ValueError("provider must expose an application capability")
        return self
