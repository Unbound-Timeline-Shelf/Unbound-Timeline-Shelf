"""Book list imports through the API (C15 6.2, #265 phase 4): preview, import, owner scoping,
nothing changed twice, and the batched provider match (T-H3) against a stand-in provider.

Synthetic books only: invented titles and authors, ISBNs computed to be valid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select, update

from src.api.routes import book_imports
from src.core.auth import create_api_key
from src.database.models.auth import UserApiKey
from src.database.models.book import Book, BookStatus
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.imports.book_matching import BookMatch
from src.features.imports.books import ImportedBook
from src.main import app
from src.plugin_api.metadata_contracts import (
    MediaType,
    MetadataCandidate,
    MetadataResolveRequest,
    ProviderResponse,
)

GLASS_13 = "9781111111113"
LANTERN_13 = "9782222222224"
NINTH_13 = "9783333333335"

GOODREADS = (
    "Book Id,Title,Author,Author l-f,Additional Authors,ISBN,ISBN13,My Rating,Average Rating,"
    "Publisher,Binding,Number of Pages,Year Published,Original Publication Year,Date Read,"
    "Date Added,Bookshelves,Bookshelves with positions,Exclusive Shelf,My Review,Spoiler,"
    "Private Notes,Read Count,Owned Copies\n"
    f'101,"The Glass Orchard (Lantern Archive, #1)",Ada Quill,"Quill, Ada",,"=""""",'
    f'"=""{GLASS_13}""",5,4,Harbor Books,Paperback,412,2005,1998,2021/03/14,2020/12/01,'
    "read,,read,,,,1,1\n"
    '102,Salt and Ember,Cora Vale,"Vale, Cora",,"=""""","=""""",0,4,,Hardcover,,,2019,,'
    "2022/01/05,to-read,,to-read,,,,0,0\n"
)


async def _person() -> SimpleNamespace:
    raw, prefix, digest = create_api_key()
    async with SessionLocal() as db:
        user = User(
            username=f"bookimp_{uuid4().hex[:10]}",
            email=f"{uuid4()}@example.test",
            password_hash="x",
            is_active=True,
        )
        db.add(user)
        await db.flush()
        db.add(
            UserApiKey(
                user_id=user.id,
                name="k",
                key_prefix=prefix,
                key_hash=digest,
                scopes=["read", "write"],
            )
        )
        await db.commit()
        return SimpleNamespace(id=user.id, headers={"Authorization": "Bearer " + raw})


@pytest.fixture(name="http")
async def http_client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


@pytest.fixture(name="people")
async def two_people():
    made = [await _person(), await _person()]
    yield made
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id.in_([p.id for p in made])))
        await db.commit()


@dataclass
class _Catalogue:
    """A stand-in book provider with the batched ``resolve`` operation."""

    by_isbn: dict[str, MetadataCandidate]
    calls: list[MetadataResolveRequest] = field(default_factory=list)
    name: str = "Stand-in books"
    priority: int = 1

    @property
    def declaration(self) -> Any:
        return SimpleNamespace(
            operations=SimpleNamespace(resolve="resolve", search="search"),
            provider_id="stand-in.books",
            identifier_namespace="stand-in",
        )

    async def resolve(self, request: MetadataResolveRequest) -> ProviderResponse:
        self.calls.append(request)
        return ProviderResponse.model_validate(
            {
                "resolved": [
                    {
                        "key": item.key,
                        "matched_by": "identifier",
                        "candidate": self.by_isbn[item.isbn_13],
                    }
                    for item in request.items
                    if item.isbn_13 in self.by_isbn
                ]
            }
        )


def _found(title: str, isbn: str, key: str) -> MetadataCandidate:
    return MetadataCandidate(
        external_id=key,
        title=title,
        provider="stand-in.books",
        media_type=MediaType.BOOK,
        provider_ids={"stand-in": key},
        authors=("Eli Ford",),
        isbn_13=isbn,
        year=2011,
    )


def _use(monkeypatch, *providers: Any) -> None:
    async def chosen(_db, _user_id):
        return list(providers)

    monkeypatch.setattr(book_imports, "_providers", chosen)


async def _post(http, who, path: str, body: str, **form: str) -> httpx.Response:
    return await http.post(
        path,
        files={"file": ("list.csv", body.encode(), "text/csv")},
        data=form,
        headers=who.headers,
    )


async def _books(user_id) -> list[Book]:
    async with SessionLocal() as db:
        return list(
            (await db.scalars(select(Book).where(Book.user_id == user_id).order_by(Book.title)))
            .unique()
            .all()
        )


async def test_goodreads_import_creates_books_and_a_second_import_changes_nothing(
    http, people, monkeypatch
):
    reader, _ = people
    _use(monkeypatch)
    response = await _post(http, reader, "/api/import/books", GOODREADS, source="goodreads")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["created"] == 2 and result["kept"] == 0 and result["manual"] == 2

    books = await _books(reader.id)
    glass = next(book for book in books if book.title == "The Glass Orchard")
    assert glass.series_name == "Lantern Archive" and glass.series_position == "1"
    assert glass.status is BookStatus.READ
    assert str(glass.rating_overall) == "10.00"
    assert glass.first_published.year == 1998 and glass.first_published_precision == "year"
    (edition,) = glass.editions
    assert edition.isbn_13 == GLASS_13 and edition.page_count == 412
    assert glass.active_edition_id == edition.id
    (read,) = glass.reads
    assert read.finished_on.isoformat() == "2021-03-14" and read.edition_id == edition.id

    again = (await _post(http, reader, "/api/import/books", GOODREADS)).json()
    assert again["source"] == "goodreads"  # detected from the header
    assert again["created"] == 0 and again["kept"] == 2
    assert len(await _books(reader.id)) == 2


async def test_preview_counts_without_writing_and_is_scoped_to_the_reader(
    http, people, monkeypatch
):
    reader, other = people
    _use(monkeypatch)
    await _post(http, other, "/api/import/books", GOODREADS)
    preview = await _post(http, reader, "/api/import/books/preview", GOODREADS)
    assert preview.status_code == 200, preview.text
    body = preview.json()
    # another reader's books are not "already here"
    assert body["total"] == 2 and body["new"] == 2 and body["already_here"] == 0
    assert body["with_isbn"] == 1
    assert body["rows"][0]["last_read"] == "2021-03-14"
    assert await _books(reader.id) == []


async def test_isbn_list_is_matched_in_one_batch_and_unmatched_rows_still_import(
    http, people, monkeypatch
):
    reader, _ = people
    catalogue = _Catalogue(
        by_isbn={
            LANTERN_13: _found("Ninth Lantern", LANTERN_13, "W2"),
            GLASS_13: _found("The Glass Orchard", GLASS_13, "W1"),
        }
    )
    _use(monkeypatch, catalogue)
    body = f"{GLASS_13}\n{LANTERN_13}\n{NINTH_13}\nnot-an-isbn\n"
    result = (await _post(http, reader, "/api/import/books", body, source="isbn_list")).json()

    (call,) = catalogue.calls
    assert [item.isbn_13 for item in call.items] == [GLASS_13, LANTERN_13, NINTH_13]
    assert result["resolve_calls"] == 1
    assert result["matched_by_isbn"] == 2 and result["manual"] == 1
    assert result["created"] == 3
    assert result["invalid_entries"] == ["not-an-isbn"]

    books = {book.title: book for book in await _books(reader.id)}
    lantern = books["Ninth Lantern"]
    assert lantern.provider_ids == {"stand-in": "W2"}
    assert lantern.authors == ["Eli Ford"]
    assert lantern.first_published.year == 2011
    assert lantern.status is BookStatus.WISHLIST
    # nothing is dropped: the row no provider knows keeps its ISBN on an edition
    manual = books[f"ISBN {NINTH_13}"]
    assert [edition.isbn_13 for edition in manual.editions] == [NINTH_13]
    assert manual.provider_ids == {}


async def test_matching_can_be_switched_off(http, people, monkeypatch):
    reader, _ = people
    catalogue = _Catalogue(by_isbn={GLASS_13: _found("The Glass Orchard", GLASS_13, "W1")})
    _use(monkeypatch, catalogue)
    result = (
        await _post(http, reader, "/api/import/books", GLASS_13, source="isbn_list", match="false")
    ).json()
    assert catalogue.calls == []
    assert result["created"] == 1 and result["manual"] == 1


@pytest.mark.parametrize(
    ("body", "source"),
    [("name,score\nA,1\n", "auto"), ("title,author\nA,B\n", "goodreads")],
)
async def test_a_file_in_another_shape_is_refused(http, people, monkeypatch, body, source):
    reader, _ = people
    _use(monkeypatch)
    response = await _post(http, reader, "/api/import/books/preview", body, source=source)
    assert response.status_code == 400


# ------------------------------------------------------- already here, and provider ids (pure)


def _library_book(title: str, authors: list[str], isbns: tuple[str, ...] = ()) -> Any:
    return SimpleNamespace(
        title=title,
        authors=authors,
        editions=[SimpleNamespace(isbn_13=isbn) for isbn in isbns],
    )


def _row(title: str | None, authors=("Ann Sand",), isbn: str | None = None) -> ImportedBook:
    return ImportedBook(line=1, title=title, authors=list(authors), isbn_13=isbn)


def test_another_volume_of_a_series_is_not_already_here():
    library = book_imports._Library(
        [_library_book("Ember Cycle: The First Spark", ["Ann Sand"], (GLASS_13,))]
    )
    assert not library.has(_row("Ember Cycle: The Second Flame", isbn=LANTERN_13))
    assert not library.has(_row("Ember Cycle: The Second Flame"))
    # the same book still is, by ISBN or by title (a subtitle missing on one side)
    assert library.has(_row("Something Else", isbn=GLASS_13))
    assert library.has(_row("Ember Cycle: The First Spark"))
    assert library.has(_row("Ember Cycle"))


def test_a_row_whose_isbn_differs_from_a_same_titled_book_is_another_book():
    library = book_imports._Library([_library_book("Ninth Lantern", ["Eli Ford"], (GLASS_13,))])
    assert not library.has(_row("Ninth Lantern", ["Eli Ford"], isbn=LANTERN_13))
    # a book entered by hand, with no ISBN, is still found by title and author
    by_hand = book_imports._Library([_library_book("Ninth Lantern", ["Eli Ford"])])
    assert by_hand.has(_row("Ninth Lantern", ["Eli Ford"], isbn=LANTERN_13))


def test_volumes_of_a_series_in_one_file_are_not_repeats():
    seen = book_imports._Library()
    seen.add(_row("Ember Cycle: The First Spark", isbn=GLASS_13))
    assert not seen.has(_row("Ember Cycle: The Second Flame", isbn=LANTERN_13))
    assert not seen.has(_row("Ember Cycle: The Second Flame"))
    assert seen.has(_row("Ember Cycle: The First Spark"))


def _match(candidate: MetadataCandidate, owned: frozenset[str]) -> BookMatch:
    return BookMatch(candidate, "identifier", "Stand-in books", owned)


def test_provider_ids_stay_in_the_providers_own_namespaces():
    owned = frozenset({"stand-in.books", "stand-in"})
    spoofed = MetadataCandidate(
        external_id="1942", title="T", provider="igdb", media_type=MediaType.BOOK, provider_ids={}
    )
    # a plugin naming another provider gets no ids, even through the provider/external_id fallback
    assert book_imports._provider_ids(_match(spoofed, owned)) == {}
    mixed = spoofed.model_copy(update={"provider_ids": {"igdb": "1942", "stand-in": "W9"}})
    assert book_imports._provider_ids(_match(mixed, owned)) == {"stand-in": "W9"}
    own = spoofed.model_copy(update={"provider": "stand-in.books", "external_id": "W1"})
    assert book_imports._provider_ids(_match(own, owned)) == {"stand-in.books": "W1"}


SERIES = (
    "Book Id,Title,Author,Author l-f,Additional Authors,ISBN,ISBN13,My Rating,Average Rating,"
    "Publisher,Binding,Number of Pages,Year Published,Original Publication Year,Date Read,"
    "Date Added,Bookshelves,Bookshelves with positions,Exclusive Shelf,My Review,Spoiler,"
    "Private Notes,Read Count,Owned Copies\n"
    f'201,"Lantern Archive: The Glass Orchard",Ada Quill,"Quill, Ada",,"=""""",'
    f'"=""{GLASS_13}""",0,4,,Paperback,,,,,2022/01/05,to-read,,to-read,,,,0,0\n'
    f'202,"Lantern Archive: Salt and Ember",Ada Quill,"Quill, Ada",,"=""""",'
    f'"=""{LANTERN_13}""",0,4,,Paperback,,,,,2022/01/05,to-read,,to-read,,,,0,0\n'
    '203,"Lantern Archive: Salt and Ember",Ada Quill,"Quill, Ada",,"=""""",'
    '"=""""",0,4,,Paperback,,,,,2022/01/05,to-read,,to-read,,,,0,0\n'
)


async def test_volumes_of_a_series_all_import_and_a_true_repeat_is_shown(http, people, monkeypatch):
    reader, _ = people
    _use(monkeypatch)
    preview = (await _post(http, reader, "/api/import/books/preview", SERIES)).json()
    assert [row["repeated"] for row in preview["rows"]] == [False, False, True]
    assert preview["new"] == 2 and preview["repeated"] == 1
    result = (await _post(http, reader, "/api/import/books", SERIES)).json()
    assert result["created"] == 2 and result["repeated"] == 1
    assert [book.title for book in await _books(reader.id)] == [
        "Lantern Archive: Salt and Ember",
        "Lantern Archive: The Glass Orchard",
    ]
    # with the first volume already here (the second removed), the second still imports
    second = next(b for b in await _books(reader.id) if "Salt" in b.title)
    async with SessionLocal() as db:
        await db.execute(update(Book).where(Book.id == second.id).values(deleted_at=1))
        await db.commit()
    again = (await _post(http, reader, "/api/import/books", SERIES)).json()
    assert again["created"] == 1 and again["kept"] == 1
