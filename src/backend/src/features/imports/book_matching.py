"""Identifying imported book rows through the registered book providers (C15 6.2).

Rows are matched by ISBN first, then by title and author. A provider that declares the batched
``resolve`` operation (Plugin API 1.1.20; ``metadata-atlas`` maps it to one Atlas ``/resolve``)
gets the rows in batches of at most ``MAX_RESOLVE_ITEMS`` (200): one call for up to 200 ISBN rows,
then one call for up to 200 of the rows still unmatched, by title and authors. A provider without
it is searched row by row (the ISBN as the reserved ``isbn`` option, then "title author"), and
those single lookups are bounded per import, because they cost one provider request each.

The host never takes a provider's word for a title match: the titles must agree once case,
accents, punctuation and a leading article are set aside, and so must one author's surname when
both sides name authors. A subtitle (after ``:``) is set aside only when one side has none:
"Ember Cycle: The First Spark" agrees with "Ember Cycle" but never with "Ember Cycle: The Last
Coal". An identifier answer that names a different ISBN is ignored. A provider that fails is
skipped for the rest of the import; a failure never blocks it, and every row a provider
cannot identify is still imported, by hand (nothing is dropped).

Matching is bounded per import: at most ``MAX_RESOLVE_CALLS`` batched calls, ``MAX_ROW_LOOKUPS``
single lookups and ``MATCH_SECONDS`` in all, so one request never runs for hours or sends a burst
of thousands of provider requests. Rows left when a bound is reached are imported by hand and
counted as not looked up.
"""

from __future__ import annotations

import time
import unicodedata
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import ValidationError

from src.features.imports.books import ImportedBook
from src.features.metadata.deadlines import bounded
from src.plugin_api.metadata_contracts import (
    ISBN_OPTION,
    MAX_RESOLVE_ITEMS,
    MediaType,
    MetadataCandidate,
    MetadataProviderRequest,
    MetadataResolveItem,
    MetadataResolveRequest,
    ProviderResponse,
)

# Single lookups (one provider request each) for providers without ``resolve``, per import.
MAX_ROW_LOOKUPS = 100
# Batched calls per import (each up to 200 rows, so 4,000 rows a pass) and the time one import may
# spend matching: the request is synchronous, so it must end well inside a proxy's timeout.
MAX_RESOLVE_CALLS = 20
MATCH_SECONDS = 120.0
RESOLVE_SECONDS = 60.0
SEARCH_SECONDS = 15.0

MatchedBy = Literal["identifier", "title"]


@dataclass(frozen=True)
class BookMatch:
    candidate: MetadataCandidate
    matched_by: MatchedBy
    provider: str
    # the identifier namespaces the provider owns: the only provider ids taken from its answer
    namespaces: frozenset[str] = frozenset()


@dataclass
class MatchReport:
    # row index -> how it was identified
    matches: dict[int, BookMatch] = field(default_factory=dict)
    resolve_calls: int = 0
    row_lookups: int = 0
    # rows left unmatched because the single-lookup bound was reached
    not_looked_up: int = 0
    failed_providers: list[str] = field(default_factory=list)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    letters = "".join(
        character if character.isalnum() else " "
        for character in decomposed
        if not unicodedata.combining(character)
    )
    return " ".join(letters.split())


def _without_article(text: str) -> str:
    for article in ("the ", "a ", "an "):
        if text.startswith(article) and len(text) > len(article):
            return text[len(article) :]
    return text


def title_parts(title: str) -> tuple[str, str | None]:
    """The folded title before ``:`` without a leading article, and the folded subtitle."""
    main, _, subtitle = title.partition(":")
    return _without_article(_fold(main)), _without_article(_fold(subtitle)) or None


def titles_agree(first: str, second: str) -> bool:
    """The same title, setting a subtitle aside only when one side has none: different
    subtitles are different books ("Ember Cycle: The First Spark" is not "Ember Cycle: The
    Second Flame")."""
    first_main, first_subtitle = title_parts(first)
    second_main, second_subtitle = title_parts(second)
    if not first_main or first_main != second_main:
        return False
    return first_subtitle is None or second_subtitle is None or first_subtitle == second_subtitle


def surnames(names: Sequence[str]) -> set[str]:
    return {_fold(name).split()[-1] for name in names if _fold(name)}


def authors_agree(first: Sequence[str], second: Sequence[str]) -> bool:
    """A shared author surname, when both sides name authors."""
    first_names, second_names = surnames(first), surnames(second)
    return not first_names or not second_names or bool(first_names & second_names)


def title_agrees(row: ImportedBook, candidate: MetadataCandidate) -> bool:
    """A title match the host accepts: the same title (``titles_agree``) and a shared author
    surname when both sides name authors."""
    if not row.title or not titles_agree(row.title, candidate.title):
        return False
    return authors_agree(row.authors, candidate.authors)


def namespaces(provider: Any) -> frozenset[str]:
    """The identifier namespaces ``provider`` owns: its provider id and declared namespace."""
    declaration = provider.declaration
    owned = (
        getattr(declaration, "provider_id", None),
        getattr(declaration, "identifier_namespace", None),
    )
    return frozenset(name for name in owned if name)


def _identifier_agrees(row: ImportedBook, candidate: MetadataCandidate) -> bool:
    return candidate.media_type == MediaType.BOOK and candidate.isbn_13 in {None, row.isbn_13}


def _chunks(indexes: list[int]) -> Iterator[list[int]]:
    for start in range(0, len(indexes), MAX_RESOLVE_ITEMS):
        yield indexes[start : start + MAX_RESOLVE_ITEMS]


def _key(index: int) -> str:
    return f"r{index}"


class _Matcher:  # pylint: disable=too-few-public-methods,too-many-instance-attributes
    """One import's matching state: the rows still pending and what was found so far."""

    def __init__(self, rows: Sequence[ImportedBook], user_id: UUID) -> None:
        self.rows = rows
        self.user_id = user_id
        self.report = MatchReport()
        self.pending = [index for index, row in enumerate(rows) if row.isbn_13 or row.title]
        self.looked_up: set[int] = set()
        self.deadline = time.monotonic() + MATCH_SECONDS
        # a per-import bound was reached, so some rows may not have been asked about
        self.bounded = False
        # the last call's allowance was cut short by the deadline
        self.clipped = False

    def _remaining(self) -> float:
        return self.deadline - time.monotonic()

    def _out_of_time(self) -> bool:
        if self._remaining() <= 0:
            self.bounded = True
        return self._remaining() <= 0

    def _may_call(self, counted: int, limit: int) -> bool:
        if counted >= limit:
            self.bounded = True
            return False
        return not self._out_of_time()

    def _seconds(self, per_call: float) -> float:
        remaining = self._remaining()
        self.clipped = remaining < per_call
        return max(0.0, min(per_call, remaining))

    def _timed_out(self) -> None:
        """A call timed out; when the deadline cut it, the import's time is over.

        Timers may fire a few milliseconds before the monotonic clock reaches the
        deadline (Windows), so this does not wait for the clock to agree.
        """
        if self.clipped:
            self.deadline = min(self.deadline, time.monotonic())

    def _take(self, index: int, candidate: MetadataCandidate, by: MatchedBy, provider: Any) -> None:
        self.report.matches[index] = BookMatch(candidate, by, provider.name, namespaces(provider))

    def _failed(self, provider: Any) -> None:
        self.report.failed_providers.append(provider.name)

    async def _call_resolve(
        self, provider: Any, items: list[MetadataResolveItem]
    ) -> ProviderResponse | None:
        self.report.resolve_calls += 1
        try:
            request = MetadataResolveRequest(
                request_id=uuid4(), user_id=self.user_id, media_type=MediaType.BOOK, items=items
            )
            response = await bounded(provider.resolve(request), self._seconds(RESOLVE_SECONDS))
        except TimeoutError:
            self._timed_out()
            return None
        except ValidationError:
            return None
        return None if response.failure is not None else response

    async def _resolve_pass(self, provider: Any, by: MatchedBy) -> bool:
        """One pass over the pending rows; False when the provider failed."""
        if by == "identifier":
            indexes = [index for index in self.pending if self.rows[index].isbn_13]
        else:
            indexes = [index for index in self.pending if self.rows[index].title]
        for chunk in _chunks(indexes):
            if not self._may_call(self.report.resolve_calls, MAX_RESOLVE_CALLS):
                return True
            items = [self._item(index, by) for index in chunk]
            response = await self._call_resolve(provider, items)
            if response is None:
                # cut off by the import's own time bound: not the provider's failure
                return self._out_of_time()
            self.looked_up.update(chunk)
            asked = {_key(index): index for index in chunk}
            for resolution in response.resolved:
                index = asked.get(resolution.key)
                if index is None or resolution.matched_by != by:
                    continue
                row, candidate = self.rows[index], resolution.candidate
                if candidate.media_type != MediaType.BOOK:
                    continue
                agrees = (
                    _identifier_agrees(row, candidate)
                    if by == "identifier"
                    else title_agrees(row, candidate)
                )
                if agrees:
                    self._take(index, candidate, by, provider)
            self.pending = [index for index in self.pending if index not in self.report.matches]
        return True

    def _item(self, index: int, by: MatchedBy) -> MetadataResolveItem:
        row = self.rows[index]
        if by == "identifier":
            return MetadataResolveItem(key=_key(index), isbn_13=row.isbn_13)
        return MetadataResolveItem(
            key=_key(index),
            title=(row.title or "")[:512],
            authors=tuple(row.authors[:30]),
            year=row.first_published_year,
        )

    async def _search(self, provider: Any, query: str, isbn: str | None) -> ProviderResponse | None:
        self.report.row_lookups += 1
        try:
            request = MetadataProviderRequest(
                request_id=uuid4(),
                user_id=self.user_id,
                query=query[:512],
                media_type=MediaType.BOOK,
                policy="background",
                limit=5,
                options={ISBN_OPTION: isbn} if isbn else {},
            )
            response = await bounded(
                provider.invoke("search", request), self._seconds(SEARCH_SECONDS)
            )
        except TimeoutError:
            self._timed_out()
            return None
        except ValidationError:
            return None
        return None if response.failure is not None else response

    async def _search_row(self, provider: Any, index: int) -> bool:
        """Look one row up; False when the provider failed."""
        row = self.rows[index]
        self.looked_up.add(index)
        if row.isbn_13:
            response = await self._search(provider, row.isbn_13, row.isbn_13)
            if response is None:
                return False
            for candidate in response.candidates:
                if candidate.media_type == MediaType.BOOK and candidate.isbn_13 == row.isbn_13:
                    self._take(index, candidate, "identifier", provider)
                    return True
        if row.title:
            query = " ".join([row.title, *row.authors[:1]])
            response = await self._search(provider, query, None)
            if response is None:
                return False
            for candidate in response.candidates:
                if candidate.media_type == MediaType.BOOK and title_agrees(row, candidate):
                    self._take(index, candidate, "title", provider)
                    break
        return True

    async def _search_pass(self, provider: Any) -> None:
        for index in list(self.pending):
            if not self._may_call(self.report.row_lookups, MAX_ROW_LOOKUPS):
                break
            if not await self._search_row(provider, index):
                if not self._out_of_time():
                    self._failed(provider)
                break
        self.pending = [index for index in self.pending if index not in self.report.matches]

    async def run(self, providers: Sequence[Any]) -> MatchReport:
        for provider in providers:
            if not self.pending or self._out_of_time():
                break
            operations = provider.declaration.operations
            if getattr(operations, "resolve", None) and hasattr(provider, "resolve"):
                if not (
                    await self._resolve_pass(provider, "identifier")
                    and await self._resolve_pass(provider, "title")
                ):
                    self._failed(provider)
            elif getattr(operations, "search", None):
                await self._search_pass(provider)
        if self.bounded:
            self.report.not_looked_up = len(
                [index for index in self.pending if index not in self.looked_up]
            )
        return self.report


async def match_books(
    rows: Sequence[ImportedBook], providers: Sequence[Any], user_id: UUID
) -> MatchReport:
    """Identify ``rows`` with ``providers`` (already in the account's order)."""
    return await _Matcher(rows, user_id).run(providers)
