"""Books (#265 phase 1): CRUD, editions with normalised ISBNs, read-throughs with bounded
progress, the trash, owner scoping, and the library export (format 3) round trip.

The book data here is synthetic: invented titles and authors, ISBNs computed to be valid.
"""

import io
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select
from sqlalchemy.sql.functions import count as sql_count

from src.core.auth import create_api_key
from src.database.models.auth import UserApiKey
from src.database.models.book import Book, BookEdition, BookReading
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.backup import scheduler
from src.features.books.isbn import InvalidIsbn, isbn10_to_13, normalise_pair, parse_isbn
from src.main import app

# Valid synthetic ISBNs: the same book as ISBN-10 and ISBN-13, a 979 ISBN, and one more.
ISBN10 = "0306406152"
ISBN13 = "9780306406157"
ISBN13_979 = "9791234567896"
OTHER13 = "9781234567897"


async def _person() -> SimpleNamespace:
    raw, prefix, digest = create_api_key()
    async with SessionLocal() as db:
        user = User(
            username=f"books_{uuid4().hex[:10]}",
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


async def _create(http, who, **fields) -> dict:
    response = await http.post(
        "/api/book/create", json={"title": "The Glass Orchard", **fields}, headers=who.headers
    )
    assert response.status_code == 201, response.text
    return response.json()


# ------------------------------------------------------------------------------- ISBN


def test_isbn10_becomes_isbn13_with_a_recomputed_check_digit():
    assert isbn10_to_13(ISBN10) == ISBN13
    assert parse_isbn("0-306-40615-2") == parse_isbn(ISBN13)
    assert parse_isbn(" 978 0 306 40615 7 ").isbn_10 == ISBN10


def test_isbn_with_x_check_digit_and_979_without_isbn10():
    assert parse_isbn("080442957x").isbn_10 == "080442957X"
    assert parse_isbn(ISBN13_979).isbn_10 is None


@pytest.mark.parametrize("bad", ["0306406153", "9780306406158", "12345", "97803064061X7", "abc"])
def test_isbn_with_a_wrong_check_digit_or_shape_is_refused_never_guessed(bad):
    with pytest.raises(InvalidIsbn):
        parse_isbn(bad)


def test_isbn_pair_must_name_one_book():
    assert normalise_pair(None, " ") is None
    assert normalise_pair(ISBN13, ISBN10).isbn_13 == ISBN13
    with pytest.raises(InvalidIsbn):
        normalise_pair(OTHER13, ISBN10)
    with pytest.raises(InvalidIsbn):
        normalise_pair(ISBN10, None)  # an ISBN-10 typed into the ISBN-13 field


# ------------------------------------------------------------------------------- works


async def test_manual_book_with_only_a_title_is_a_valid_work(http, people):
    me, _ = people
    book = await _create(http, me, title="The thing my aunt gave me")
    assert book["authors"] == [] and book["editions"] == [] and book["reads"] == []
    assert book["status"] == "WISHLIST" and book["sort_title"] == "thing my aunt gave me"
    fetched = await http.get(f"/api/book/get/{book['id']}", headers=me.headers)
    assert fetched.status_code == 200 and fetched.json()["title"] == "The thing my aunt gave me"


async def test_authors_keep_their_order_and_dates_keep_their_precision(http, people):
    me, _ = people
    book = await _create(
        http,
        me,
        authors=["Ida Marsh", "Ottoline Reyes"],
        first_published="1965-01-01",
        first_published_precision="year",
        series_name="Orchard Cycle",
        series_position="2.5",
    )
    assert book["authors"] == ["Ida Marsh", "Ottoline Reyes"]
    assert (book["first_published"], book["first_published_precision"]) == ("1965-01-01", "year")
    # a new date without a precision is a day
    changed = await http.patch(
        f"/api/book/update/{book['id']}",
        json={"first_published": "1966-03-04"},
        headers=me.headers,
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["first_published_precision"] is None
    assert "first_published" in changed.json()["locked_fields"]


@pytest.mark.parametrize(
    "fields",
    [
        {"first_published": "1965-03-04", "first_published_precision": "year"},
        {"first_published_precision": "month"},
        {"first_published": "0900-01-01", "first_published_precision": "year"},
        {"authors": ["x"] * 31},
        {"rating_overall": 11},
        {"title": ""},
    ],
)
async def test_invalid_work_fields_are_refused(http, people, fields):
    me, _ = people
    response = await http.post(
        "/api/book/create", json={"title": "Bounded", **fields}, headers=me.headers
    )
    assert response.status_code == 422, response.text


async def test_library_tabs_search_authors_and_read_books_as_completed(http, people):
    me, _ = people
    await _create(http, me, title="Salt Roads", authors=["Wen Adeyemi"], status="BACKLOG")
    await _create(http, me, title="Low Tide", status="WISHLIST")
    await _create(http, me, title="Paper Lanterns", status="READ", rating_overall=8)
    await _create(http, me, title="Quiet Engine", status="ON_HOLD")

    page = (await http.get("/api/book/list", headers=me.headers)).json()
    assert page["total"] == 4
    assert page["status_counts"] == {"BACKLOG": 1, "WISHLIST": 1, "READ": 1, "ON_HOLD": 1}
    plan = (
        await http.get("/api/book/list", params={"status_bucket": "plan"}, headers=me.headers)
    ).json()
    assert sorted(b["title"] for b in plan["items"]) == ["Low Tide", "Salt Roads"]
    for bucket, title in (("completed", "Paper Lanterns"), ("hold", "Quiet Engine")):
        tab = await http.get("/api/book/list", params={"status_bucket": bucket}, headers=me.headers)
        assert [b["title"] for b in tab.json()["items"]] == [title]
    by_author = await http.get("/api/book/list", params={"search": "adeyemi"}, headers=me.headers)
    assert [b["title"] for b in by_author.json()["items"]] == ["Salt Roads"]


# ---------------------------------------------------------------------------- editions


async def test_editions_normalise_isbns_and_refuse_a_duplicate_within_one_work(http, people):
    me, _ = people
    book = await _create(http, me)
    added = await http.post(
        f"/api/book/{book['id']}/editions",
        json={"isbn_10": "0-306-40615-2", "format": "paperback", "page_count": 535},
        headers=me.headers,
    )
    assert added.status_code == 201, added.text
    edition = added.json()["editions"][0]
    assert (edition["isbn_13"], edition["isbn_10"]) == (ISBN13, ISBN10)
    assert edition["ownership"] == "none" and edition["format"] == "paperback"

    duplicate = await http.post(
        f"/api/book/{book['id']}/editions",
        json={"isbn_13": "978-0-306-40615-7", "format": "hardcover"},
        headers=me.headers,
    )
    assert duplicate.status_code == 422 and ISBN13 in duplicate.text
    bad = await http.post(
        f"/api/book/{book['id']}/editions", json={"isbn_13": "9780306406158"}, headers=me.headers
    )
    assert bad.status_code == 422
    # editing another edition onto the same ISBN is refused too, and nothing is merged
    second = await http.post(
        f"/api/book/{book['id']}/editions",
        json={"isbn_13": OTHER13, "format": "audiobook"},
        headers=me.headers,
    )
    other_id = next(e["id"] for e in second.json()["editions"] if e["isbn_13"] == OTHER13)
    clash = await http.patch(
        f"/api/book/{book['id']}/editions/{other_id}", json={"isbn_13": ISBN13}, headers=me.headers
    )
    assert clash.status_code == 422
    assert (
        len((await http.get(f"/api/book/get/{book['id']}", headers=me.headers)).json()["editions"])
        == 2
    )

    # the same ISBN in another work is a different work's edition, which is allowed
    another = await _create(http, me, title="Another Work")
    allowed = await http.post(
        f"/api/book/{another['id']}/editions", json={"isbn_13": ISBN13}, headers=me.headers
    )
    assert allowed.status_code == 201


async def test_removing_the_active_edition_keeps_its_reads_without_an_edition(http, people):
    me, _ = people
    book = await _create(http, me)
    work = (
        await http.post(
            f"/api/book/{book['id']}/editions", json={"format": "ebook"}, headers=me.headers
        )
    ).json()
    edition_id = work["editions"][0]["id"]
    work = (
        await http.post(
            f"/api/book/{book['id']}/reads",
            json={"edition_id": edition_id, "progress_unit": "percent", "progress_value": 40},
            headers=me.headers,
        )
    ).json()
    assert work["active_edition_id"] == edition_id and work["status"] == "IN_PROGRESS"
    removed = await http.delete(f"/api/book/{book['id']}/editions/{edition_id}", headers=me.headers)
    assert removed.status_code == 200, removed.text
    assert removed.json()["active_edition_id"] is None and removed.json()["editions"] == []
    assert removed.json()["reads"][0]["edition_id"] is None


# ------------------------------------------------------------------------------- reads


async def test_progress_is_bounded_by_its_unit_and_the_editions_page_count(http, people):
    me, _ = people
    book = await _create(http, me)
    work = (
        await http.post(
            f"/api/book/{book['id']}/editions",
            json={"format": "paperback", "page_count": 300},
            headers=me.headers,
        )
    ).json()
    edition_id = work["editions"][0]["id"]
    reads = f"/api/book/{book['id']}/reads"
    for body in (
        {"progress_unit": "percent", "progress_value": 100.5},
        {"progress_unit": "page", "progress_value": 100_001},
        {"progress_unit": "page", "progress_value": 301, "edition_id": edition_id},
        {"progress_value": -1},
        {"started_on": "2024-05-02", "finished_on": "2024-05-01"},
    ):
        response = await http.post(reads, json=body, headers=me.headers)
        assert response.status_code == 422, (body, response.text)
    ok = await http.post(
        reads,
        json={"progress_unit": "page", "progress_value": 300, "edition_id": edition_id},
        headers=me.headers,
    )
    assert ok.status_code == 201, ok.text
    read_id = ok.json()["reads"][0]["id"]
    # switching the same read to percent keeps the bound of the new unit
    over = await http.patch(
        f"{reads}/{read_id}", json={"progress_unit": "percent"}, headers=me.headers
    )
    assert over.status_code == 422
    switched = await http.patch(
        f"{reads}/{read_id}",
        json={"progress_unit": "percent", "progress_value": 63},
        headers=me.headers,
    )
    assert switched.status_code == 200, switched.text
    # an edition cannot shrink below a read already past it
    shrink = await http.patch(
        f"/api/book/{book['id']}/editions/{edition_id}",
        json={"page_count": 10},
        headers=me.headers,
    )
    assert shrink.status_code == 200
    page_read = await http.post(
        reads,
        json={"progress_unit": "page", "progress_value": 9, "edition_id": edition_id},
        headers=me.headers,
    )
    assert page_read.status_code == 201
    smaller = await http.patch(
        f"/api/book/{book['id']}/editions/{edition_id}", json={"page_count": 5}, headers=me.headers
    )
    assert smaller.status_code == 422


async def test_finishing_a_read_marks_the_work_read_and_rereads_are_kept(http, people):
    me, _ = people
    book = await _create(http, me, status="BACKLOG")
    reads = f"/api/book/{book['id']}/reads"
    first = await http.post(
        reads,
        json={"started_on": "2023-01-02", "finished_on": "2023-02-03", "outcome": "finished"},
        headers=me.headers,
    )
    assert first.json()["status"] == "READ"
    second = await http.post(
        reads, json={"started_on": "2024-06-01", "outcome": "reading"}, headers=me.headers
    )
    work = second.json()
    assert len(work["reads"]) == 2
    # a reread of a book already read keeps it Read until the person changes it
    assert work["status"] == "READ"
    read_id = next(r["id"] for r in work["reads"] if r["started_on"] == "2024-06-01")
    gone = await http.delete(f"{reads}/{read_id}", headers=me.headers)
    assert gone.status_code == 200 and len(gone.json()["reads"]) == 1


# ----------------------------------------------------------------------- trash and owners


async def _children(book_id) -> tuple[int, int]:
    async with SessionLocal() as db:
        editions = await db.scalar(
            select(sql_count()).select_from(BookEdition).where(BookEdition.book_id == book_id)
        )
        reads = await db.scalar(
            select(sql_count()).select_from(BookReading).where(BookReading.book_id == book_id)
        )
        return int(editions or 0), int(reads or 0)


async def test_trash_restore_and_purge_take_editions_and_reads_along(http, people):
    me, _ = people
    book = await _create(http, me)
    await http.post(f"/api/book/{book['id']}/editions", json={}, headers=me.headers)
    await http.post(f"/api/book/{book['id']}/reads", json={}, headers=me.headers)

    assert (
        await http.delete(f"/api/book/delete/{book['id']}", headers=me.headers)
    ).status_code == 204
    assert (await http.get(f"/api/book/get/{book['id']}", headers=me.headers)).status_code == 404
    assert (await http.get("/api/book/list", headers=me.headers)).json()["total"] == 0
    trash = (await http.get("/api/book/trash", headers=me.headers)).json()
    assert [t["id"] for t in trash] == [book["id"]]
    restored = await http.post(f"/api/book/{book['id']}/restore", headers=me.headers)
    assert restored.status_code == 200 and len(restored.json()["editions"]) == 1

    # purging is only for something in the trash
    early = await http.delete(f"/api/book/{book['id']}/purge", headers=me.headers)
    assert early.status_code == 400
    await http.delete(f"/api/book/delete/{book['id']}", headers=me.headers)
    purged = await http.delete(f"/api/book/{book['id']}/purge", headers=me.headers)
    assert purged.status_code == 204
    assert await _children(book["id"]) == (0, 0)

    emptied_book = await _create(http, me, title="Emptied")
    await http.post(f"/api/book/{emptied_book['id']}/editions", json={}, headers=me.headers)
    await http.delete(f"/api/book/delete/{emptied_book['id']}", headers=me.headers)
    emptied = await http.post("/api/book/trash/empty", json={"confirm": True}, headers=me.headers)
    assert emptied.status_code == 200 and emptied.json()["deleted"] == 1
    assert await _children(emptied_book["id"]) == (0, 0)


async def _book_with_active_edition(http, who, title: str) -> str:
    book = await _create(http, who, title=title)
    work = (
        await http.post(
            f"/api/book/{book['id']}/editions", json={"page_count": 300}, headers=who.headers
        )
    ).json()
    edition_id = work["editions"][0]["id"]
    work = (
        await http.post(
            f"/api/book/{book['id']}/reads",
            json={"edition_id": edition_id, "progress_value": 12},
            headers=who.headers,
        )
    ).json()
    # the work points at its edition and the edition at its work: the foreign-key cycle
    assert work["active_edition_id"] == edition_id
    return book["id"]


async def test_purge_empty_trash_and_account_removal_clear_a_book_with_an_active_edition(
    http, people
):
    me, _ = people
    purged = await _book_with_active_edition(http, me, "Purged")
    await http.delete(f"/api/book/delete/{purged}", headers=me.headers)
    response = await http.delete(f"/api/book/{purged}/purge", headers=me.headers)
    assert response.status_code == 204, response.text
    assert await _children(purged) == (0, 0)

    emptied = await _book_with_active_edition(http, me, "Emptied")
    await http.delete(f"/api/book/delete/{emptied}", headers=me.headers)
    response = await http.post("/api/book/trash/empty", json={"confirm": True}, headers=me.headers)
    assert response.status_code == 200 and response.json()["deleted"] == 1, response.text
    assert await _children(emptied) == (0, 0)

    # removing the account cascades through the same cycle
    ghost = await _person()
    kept = await _book_with_active_edition(http, ghost, "Kept until the account goes")
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id == ghost.id))
        await db.commit()
    async with SessionLocal() as db:
        assert await db.scalar(select(Book).where(Book.id == kept)) is None
    assert await _children(kept) == (0, 0)


async def test_book_tags_join_the_tag_catalogue(http, people):
    me, _ = people
    await _create(http, me, tags=["Gothic Fiction"])
    found = await http.post("/api/tags/discover", headers=me.headers)
    assert found.json() == {"created": 1}, found.text
    labels = [t["label"] for t in (await http.get("/api/tags", headers=me.headers)).json()["tags"]]
    assert labels == ["Gothic Fiction"]


async def test_another_user_cannot_read_change_or_list_a_book_its_editions_or_reads(http, people):
    owner, stranger = people
    book = await _create(http, owner)
    work = (
        await http.post(
            f"/api/book/{book['id']}/editions", json={"isbn_13": ISBN13}, headers=owner.headers
        )
    ).json()
    edition_id = work["editions"][0]["id"]
    work = (
        await http.post(
            f"/api/book/{book['id']}/reads", json={"edition_id": edition_id}, headers=owner.headers
        )
    ).json()
    read_id = work["reads"][0]["id"]
    base = f"/api/book/{book['id']}"
    attempts = [
        ("GET", f"/api/book/get/{book['id']}", None),
        ("PATCH", f"/api/book/update/{book['id']}", {"title": "Taken"}),
        ("DELETE", f"/api/book/delete/{book['id']}", None),
        ("POST", f"{base}/restore", None),
        ("DELETE", f"{base}/purge", None),
        ("POST", f"{base}/editions", {"format": "ebook"}),
        ("PATCH", f"{base}/editions/{edition_id}", {"publisher": "Taken"}),
        ("DELETE", f"{base}/editions/{edition_id}", None),
        ("POST", f"{base}/reads", {}),
        ("PATCH", f"{base}/reads/{read_id}", {"outcome": "abandoned"}),
        ("DELETE", f"{base}/reads/{read_id}", None),
    ]
    for method, path, body in attempts:
        kwargs = {"json": body} if body is not None else {}
        response = await http.request(method, path, headers=stranger.headers, **kwargs)
        assert response.status_code == 404, (method, path, response.status_code)
    assert (await http.get("/api/book/list", headers=stranger.headers)).json()["total"] == 0
    assert (await http.get("/api/book/trash", headers=stranger.headers)).json() == []

    # the stranger's own work cannot reach the owner's edition or read through its own id
    mine = await _create(http, stranger, title="Mine")
    crossed = await http.patch(
        f"/api/book/{mine['id']}/editions/{edition_id}",
        json={"note": "x"},
        headers=stranger.headers,
    )
    assert crossed.status_code == 404
    borrowed = await http.post(
        f"/api/book/{mine['id']}/reads", json={"edition_id": edition_id}, headers=stranger.headers
    )
    assert borrowed.status_code == 422
    pointed = await http.patch(
        f"/api/book/update/{mine['id']}",
        json={"active_edition_id": edition_id},
        headers=stranger.headers,
    )
    assert pointed.status_code == 404

    untouched = (await http.get(f"/api/book/get/{book['id']}", headers=owner.headers)).json()
    assert untouched["title"] == "The Glass Orchard"
    assert (
        untouched["editions"][0]["publisher"] is None
        and untouched["reads"][0]["outcome"] == "reading"
    )


# --------------------------------------------------------------------------- export v3


def _upload(payload: dict) -> dict:
    return {"file": ("library.json", io.BytesIO(json.dumps(payload).encode()), "application/json")}


async def test_export_v3_round_trips_books_editions_reads_and_locks(http, people):
    me, other = people
    book = await _create(
        http,
        me,
        authors=["Ida Marsh"],
        first_published="1965-08-01",
        first_published_precision="month",
        status="IN_PROGRESS",
        rating_overall=7.5,
    )
    await http.patch(
        f"/api/book/update/{book['id']}", json={"subtitle": "A Novel"}, headers=me.headers
    )
    work = (
        await http.post(
            f"/api/book/{book['id']}/editions",
            json={
                "isbn_13": ISBN13,
                "format": "paperback",
                "page_count": 535,
                "ownership": "owned",
            },
            headers=me.headers,
        )
    ).json()
    work = (
        await http.post(
            f"/api/book/{book['id']}/editions",
            json={
                "format": "audiobook",
                "ownership": "borrowed",
                "publish_date": "2005-01-01",
                "publish_date_precision": "year",
            },
            headers=me.headers,
        )
    ).json()
    paperback = next(e for e in work["editions"] if e["format"] == "paperback")
    audiobook = next(e for e in work["editions"] if e["format"] == "audiobook")
    await http.post(
        f"/api/book/{book['id']}/reads",
        json={
            "edition_id": paperback["id"],
            "started_on": "2020-01-01",
            "finished_on": "2020-02-01",
            "outcome": "finished",
        },
        headers=me.headers,
    )
    await http.post(
        f"/api/book/{book['id']}/reads",
        json={"edition_id": audiobook["id"], "progress_unit": "percent", "progress_value": 63},
        headers=me.headers,
    )
    exported = (await http.get("/api/export/library", headers=me.headers)).json()
    assert exported["format_version"] == 3
    [entry] = exported["books"]
    assert len(entry["editions"]) == 2 and len(entry["reads"]) == 2
    assert entry["active_edition_id"] == audiobook["id"]
    assert "subtitle" in entry["locked_fields"]

    restored = await http.post("/api/import/media", files=_upload(exported), headers=other.headers)
    assert restored.status_code == 200, restored.text
    assert restored.json()["created"]["books"] == 1, restored.json()
    [copy] = (await http.get("/api/book/list", headers=other.headers)).json()["items"]

    def essence(item: dict) -> dict:
        editions = {e["id"]: e["format"] for e in item["editions"]}
        drop = {
            "id",
            "user_id",
            "created_at",
            "updated_at",
            "editions",
            "reads",
            "active_edition_id",
        }
        return {
            **{k: v for k, v in item.items() if k not in drop},
            "active": editions.get(item["active_edition_id"]),
            "editions": sorted(
                (
                    e["format"],
                    e["isbn_13"],
                    e["page_count"],
                    e["ownership"],
                    e["publish_date"],
                    e["publish_date_precision"],
                )
                for e in item["editions"]
            ),
            "reads": sorted(
                (
                    editions.get(r["edition_id"]),
                    r["started_on"],
                    r["finished_on"],
                    r["outcome"],
                    r["progress_unit"],
                    str(r["progress_value"]),
                )
                for r in item["reads"]
            ),
        }

    assert essence(copy) == essence(entry)
    assert copy["id"] != entry["id"]

    again = await http.post("/api/import/media", files=_upload(exported), headers=other.headers)
    assert again.json()["created"]["books"] == 0 and again.json()["skipped"]["books"] == 1


async def test_restore_keeps_two_distinct_books_sharing_a_title_and_year(http, people):
    """Title and first-published year alone are not a book's identity: two unrelated works can
    share both (#533 review). Author and ISBN tell them apart; restoring the same export twice
    must still be idempotent, not fall back to losing the second work again."""
    _, other = people
    export = {
        "format_version": 3,
        "exported_at": 1,
        "books": [
            {
                "title": "Echoes",
                "sort_title": "Echoes",
                "authors": ["Dana Price"],
                "first_published": "1999-01-01",
                "first_published_precision": "year",
                "editions": [{"isbn_13": ISBN13, "format": "paperback"}],
            },
            {
                "title": "Echoes",
                "sort_title": "Echoes",
                "authors": ["Marcus Webb"],
                "first_published": "1999-01-01",
                "first_published_precision": "year",
                "editions": [{"isbn_13": ISBN13_979, "format": "hardcover"}],
            },
        ],
    }
    first = await http.post("/api/import/media", files=_upload(export), headers=other.headers)
    assert first.status_code == 200, first.text
    assert first.json()["created"]["books"] == 2, first.json()
    assert first.json()["skipped"]["books"] == 0

    items = (await http.get("/api/book/list", headers=other.headers)).json()["items"]
    assert sorted(item["authors"][0] for item in items) == ["Dana Price", "Marcus Webb"]
    for item in items:
        assert len(item["editions"]) == 1

    # restoring the same export again is still idempotent: nothing is duplicated
    again = await http.post("/api/import/media", files=_upload(export), headers=other.headers)
    assert again.json() == {
        "created": {"movies": 0, "tv_shows": 0, "anime": 0, "books": 0},
        "skipped": {"movies": 0, "tv_shows": 0, "anime": 0, "books": 2},
        "errors": [],
    }
    items_after = (await http.get("/api/book/list", headers=other.headers)).json()["items"]
    assert len(items_after) == 2


async def test_an_export_from_before_books_still_imports_and_reports_no_books(http, people):
    _, other = people
    older = {
        "format_version": 2,
        "exported_at": 1,
        "movies": [{"title": "Old Film", "sort_title": "Old Film"}],
    }
    result = await http.post("/api/import/media", files=_upload(older), headers=other.headers)
    assert result.status_code == 200, result.text
    assert result.json()["created"] == {"movies": 1, "tv_shows": 0, "anime": 0}
    library = await http.post(
        "/api/import/library", json={"format_version": 2, "games": []}, headers=other.headers
    )
    assert library.status_code == 200


async def test_a_newer_export_format_is_refused_clearly_by_both_imports(http, people):
    _, other = people
    future = {"format_version": 4, "exported_at": 1, "books": [{"title": "Later"}], "games": []}
    media = await http.post("/api/import/media", files=_upload(future), headers=other.headers)
    assert media.status_code == 400 and "format 4" in media.json()["detail"]
    library = await http.post("/api/import/library", json=future, headers=other.headers)
    assert library.status_code == 400 and "format 4" in library.json()["detail"]
    async with SessionLocal() as db:
        assert await db.scalar(select(Book.id).where(Book.user_id == other.id)) is None


async def test_restore_keeps_a_book_whose_edition_has_an_unknown_format_or_bad_isbn(http, people):
    _, other = people
    payload = {
        "format_version": 3,
        "exported_at": 1,
        "books": [
            {
                "title": "Tolerant",
                "future_field": True,
                "editions": [
                    {"id": "e1", "format": "scroll", "isbn_13": "9780306406158"},
                    {"id": "e2", "format": "ebook", "isbn_13": OTHER13},
                ],
                "reads": [{"edition_id": "e2", "outcome": "finished"}],
                "active_edition_id": "e2",
            }
        ],
    }
    result = await http.post("/api/import/media", files=_upload(payload), headers=other.headers)
    assert result.json()["created"]["books"] == 1, result.json()
    [book] = (await http.get("/api/book/list", headers=other.headers)).json()["items"]
    formats = {e["format"]: e["isbn_13"] for e in book["editions"]}
    assert formats == {"other": None, "ebook": OTHER13}
    ebook = next(e["id"] for e in book["editions"] if e["format"] == "ebook")
    assert book["active_edition_id"] == ebook and book["reads"][0]["edition_id"] == ebook


async def test_restore_drops_a_read_that_breaks_the_progress_rules_and_keeps_the_work(http, people):
    _, other = people
    payload = {
        "format_version": 3,
        "exported_at": 1,
        "books": [
            {
                "title": "Hand Edited",
                "editions": [{"id": "e1", "format": "paperback", "page_count": 200}],
                "reads": [
                    {"edition_id": "e1", "progress_unit": "page", "progress_value": 250},
                    {"edition_id": "e1", "progress_unit": "percent", "progress_value": 140},
                    {
                        "edition_id": "e1",
                        "started_on": "2024-05-02",
                        "finished_on": "2024-05-01",
                    },
                    {"edition_id": "e1", "progress_unit": "page", "progress_value": 80},
                ],
                "active_edition_id": "e1",
            }
        ],
    }
    result = await http.post("/api/import/media", files=_upload(payload), headers=other.headers)
    body = result.json()
    assert body["created"]["books"] == 1 and body["skipped"]["books"] == 0, body
    assert sum("a read was skipped" in e for e in body["errors"]) == 3, body
    [book] = (await http.get("/api/book/list", headers=other.headers)).json()["items"]
    assert len(book["editions"]) == 1
    assert [float(r["progress_value"]) for r in book["reads"]] == [80.0]
    # the kept read can still be edited, because it obeys the API's rules
    read_id = book["reads"][0]["id"]
    edited = await http.patch(
        f"/api/book/{book['id']}/reads/{read_id}",
        json={"progress_value": 90},
        headers=other.headers,
    )
    assert edited.status_code == 200, edited.text


async def test_restore_skips_a_work_over_the_response_limits_so_library_and_export_still_load(
    http, people
):
    _, other = people
    payload = {
        "format_version": 3,
        "exported_at": 1,
        "books": [
            {"title": "Too Many Hands", "authors": [f"Author {n}" for n in range(31)]},
            {"title": "Blank Genre", "genres": [""]},
            {"title": "Off The Scale", "rating_overall": 15},
            {"title": "Fits", "rating_overall": 8.5, "genres": ["Mystery"]},
        ],
    }
    result = await http.post("/api/import/media", files=_upload(payload), headers=other.headers)
    body = result.json()
    assert body["created"]["books"] == 1 and body["skipped"]["books"] == 3, body
    for title in ("Too Many Hands", "Blank Genre", "Off The Scale"):
        assert any(e.startswith(f"{title}: skipped (") for e in body["errors"]), body
    listed = await http.get("/api/book/list", headers=other.headers)
    assert listed.status_code == 200, listed.text
    assert [b["title"] for b in listed.json()["items"]] == ["Fits"]
    exported = await http.get("/api/export/library", headers=other.headers)
    assert exported.status_code == 200, exported.text
    assert [b["title"] for b in exported.json()["books"]] == ["Fits"]


async def test_restore_reports_every_edition_or_read_it_leaves_out_and_keeps_the_rest(http, people):
    _, other = people
    editions = [
        {"id": "a", "format": "paperback", "isbn_13": ISBN13},
        {"id": "b", "format": "ebook", "isbn_10": ISBN10},
        {"id": "c", "format": "hardcover", "isbn_13": "9780306406158"},
        {"id": "d", "format": "ebook", "other_identifiers": {"Bad Key": "x"}},
    ] + [{"id": f"x{n}", "format": "other"} for n in range(48)]
    payload = {
        "format_version": 3,
        "exported_at": 1,
        "books": [
            {
                "title": "Lossy",
                "editions": editions,
                "reads": [{"outcome": "finished"} for _ in range(201)],
                "active_edition_id": "a",
            }
        ],
    }
    result = await http.post("/api/import/media", files=_upload(payload), headers=other.headers)
    body = result.json()
    assert body["created"]["books"] == 1 and body["skipped"]["books"] == 0, body
    errors = [e for e in body["errors"] if e.startswith("Lossy: ")]
    assert any("another edition has ISBN" in e for e in errors), errors
    assert any("ISBN was not valid" in e for e in errors), errors
    assert any("other_identifiers" in e for e in errors), errors
    assert any("first 50 editions" in e for e in errors), errors
    assert any("first 200 reads" in e for e in errors), errors
    [book] = (await http.get("/api/book/list", headers=other.headers)).json()["items"]
    # 50 kept by the cap, less the duplicate ISBN and the bad identifier key
    assert len(book["editions"]) == 48
    assert len(book["reads"]) == 200
    [paperback] = [e for e in book["editions"] if e["format"] == "paperback"]
    assert paperback["isbn_13"] == ISBN13
    assert book["active_edition_id"] == paperback["id"]


async def test_editing_an_older_read_does_not_overwrite_the_works_status(http, people):
    me, _ = people
    book = await _create(http, me, status="BACKLOG")
    reads = f"/api/book/{book['id']}/reads"
    first = await http.post(
        reads,
        json={"started_on": "2023-01-02", "finished_on": "2023-02-03", "outcome": "finished"},
        headers=me.headers,
    )
    old_read = first.json()["reads"][0]["id"]
    await http.post(reads, json={"started_on": "2024-06-01"}, headers=me.headers)
    # the person marks the reread In progress themselves
    marked = await http.patch(
        f"/api/book/update/{book['id']}", json={"status": "IN_PROGRESS"}, headers=me.headers
    )
    assert marked.status_code == 200, marked.text
    edited = await http.patch(
        f"{reads}/{old_read}", json={"finished_on": "2023-02-04"}, headers=me.headers
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["status"] == "IN_PROGRESS"
    # changing the outcome still speaks for the work
    open_read = next(r["id"] for r in edited.json()["reads"] if r["id"] != old_read)
    done = await http.patch(
        f"{reads}/{open_read}", json={"outcome": "finished"}, headers=me.headers
    )
    assert done.json()["status"] == "READ"


async def test_scheduled_backup_covers_a_library_of_only_books(http, people, monkeypatch, tmp_path):
    _, other = people
    await _create(http, other, title="Only Books Here")
    monkeypatch.setattr(scheduler, "_BACKUP_ROOT", tmp_path)
    path = await scheduler.run_backup_for_user(other.id)
    assert path is not None
    payload = json.loads(path.read_text())
    assert [b["title"] for b in payload["books"]] == ["Only Books Here"]
