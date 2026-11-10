"""Identifying imported book rows through the registered book providers (C15 6.2).

Rows are matched by ISBN first, then by title and author. A provider that declares the batched
``resolve`` operation (Plugin API 1.1.20; ``metadata-atlas`` maps it to one Atlas ``/resolve``)
gets the rows in batches of at most ``MAX_RESOLVE_ITEMS`` (200): one call for up to 200 ISBN rows,
then one call for up to 200 of the rows still unmatched, by title and authors. A provider without
it is searched row by row (the ISBN as the reserved ``isbn`` option, then "title author"), and
those single lookups are bounded per import, because they cost one provider request each.

The host never takes a provider's word for a title match: the titles must agree once case,
accents, punctuation and a subtitle are set aside, and so must one author's surname when both
sides name authors. An identifier answer that names a different ISBN is ignored. A provider that
fails is skipped for the rest of the import; a failure never blocks it, and every row a provider
cannot identify is still imported, by hand (nothing is dropped).
"""

from __future__ import annotations

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
RESOLVE_SECONDS = 60.0
SEARCH_SECONDS = 15.0

MatchedBy = Literal["identifier", "title"]


@dataclass(frozen=True)
class BookMatch:
    candidate: MetadataCandidate
    matched_by: MatchedBy
    provider: str


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


def title_forms(title: str) -> set[str]:
    """The title, and the title without a subtitle (after ``:``) or a leading article."""
    full = _fold(title)
    main = _fold(title.split(":", 1)[0])
    forms = {full, main}
    for form in list(forms):
        for article in ("the ", "a ", "an "):
            if form.startswith(article):
                forms.add(form[len(article) :])
    return {form for form in forms if form}


def surnames(names: Sequence[str]) -> set[str]:
    return {_fold(name).split()[-1] for name in names if _fold(name)}


def title_agrees(row: ImportedBook, candidate: MetadataCandidate) -> bool:
    """A title match the host accepts: same title, and a shared author surname when both name
    authors."""
    if not row.title or not title_forms(row.title) & title_forms(candidate.title):
        return False
    if row.authors and candidate.authors:
        return bool(surnames(row.authors) & surnames(candidate.authors))
    return True


def _identifier_agrees(row: ImportedBook, candidate: MetadataCandidate) -> bool:
    return candidate.media_type == MediaType.BOOK and candidate.isbn_13 in {None, row.isbn_13}


def _chunks(indexes: list[int]) -> Iterator[list[int]]:
    for start in range(0, len(indexes), MAX_RESOLVE_ITEMS):
        yield indexes[start : start + MAX_RESOLVE_ITEMS]


def _key(index: int) -> str:
    return f"r{index}"


class _Matcher:  # pylint: disable=too-few-public-methods
    """One import's matching state: the rows still pending and what was found so far."""

    def __init__(self, rows: Sequence[ImportedBook], user_id: UUID) -> None:
        self.rows = rows
        self.user_id = user_id
        self.report = MatchReport()
        self.pending = [index for index, row in enumerate(rows) if row.isbn_13 or row.title]
        self.looked_up: set[int] = set()

    def _take(self, index: int, candidate: MetadataCandidate, by: MatchedBy, name: str) -> None:
        self.report.matches[index] = BookMatch(candidate, by, name)

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
            response = await bounded(provider.resolve(request), RESOLVE_SECONDS)
        except (TimeoutError, ValidationError):
            return None
        return None if response.failure is not None else response

    async def _resolve_pass(self, provider: Any, by: MatchedBy) -> bool:
        """One pass over the pending rows; False when the provider failed."""
        if by == "identifier":
            indexes = [index for index in self.pending if self.rows[index].isbn_13]
        else:
            indexes = [index for index in self.pending if self.rows[index].title]
        for chunk in _chunks(indexes):
            items = [self._item(index, by) for index in chunk]
            response = await self._call_resolve(provider, items)
            if response is None:
                return False
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
                    self._take(index, candidate, by, provider.name)
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
            response = await bounded(provider.invoke("search", request), SEARCH_SECONDS)
        except (TimeoutError, ValidationError):
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
                    self._take(index, candidate, "identifier", provider.name)
                    return True
        if row.title:
            query = " ".join([row.title, *row.authors[:1]])
            response = await self._search(provider, query, None)
            if response is None:
                return False
            for candidate in response.candidates:
                if candidate.media_type == MediaType.BOOK and title_agrees(row, candidate):
                    self._take(index, candidate, "title", provider.name)
                    break
        return True

    async def _search_pass(self, provider: Any) -> None:
        for index in list(self.pending):
            if self.report.row_lookups >= MAX_ROW_LOOKUPS:
                return
            if not await self._search_row(provider, index):
                self._failed(provider)
                break
        self.pending = [index for index in self.pending if index not in self.report.matches]

    async def run(self, providers: Sequence[Any]) -> MatchReport:
        for provider in providers:
            if not self.pending:
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
        if self.report.row_lookups >= MAX_ROW_LOOKUPS:
            self.report.not_looked_up = len(
                [index for index in self.pending if index not in self.looked_up]
            )
        return self.report


async def match_books(
    rows: Sequence[ImportedBook], providers: Sequence[Any], user_id: UUID
) -> MatchReport:
    """Identify ``rows`` with ``providers`` (already in the account's order)."""
    return await _Matcher(rows, user_id).run(providers)
