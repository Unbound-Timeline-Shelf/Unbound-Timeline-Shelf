"""#462: several games move to trash at once, each exactly as a single delete moves it."""

import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete as sql_delete

from src.api.schemas.game import GAME_BULK_DELETE_LIMIT
from src.database.models.game import Game
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features import game_folders
from src.features.trash import game_trash
from tests.test_game_files_flow import game_flow  # noqa: F401  # pylint: disable=unused-import


def write(root: Path, relative: str, data: bytes) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def make_game(flow, title: str) -> str:
    response = await flow.client.post(
        "/api/game/create", json={"title": title, "folder_location": title}
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["id"]


def folder(flow, name: str) -> Path:
    return flow.tmp / str(flow.user_id) / "games" / name


async def bulk_delete(flow, ids):
    return await flow.client.post("/api/game/bulk-delete", json={"game_ids": ids})


async def deleted_at(game_id) -> int | None:
    async with SessionLocal() as db:
        row = await db.get(Game, uuid.UUID(str(game_id)), populate_existing=True)
        return row.deleted_at


@pytest.fixture
async def stranger():
    """Another account with one game of its own."""
    async with SessionLocal() as db:
        user = User(
            username=f"s_{uuid.uuid4().hex[:10]}",
            email=f"{uuid.uuid4().hex[:10]}@example.test",
            password_hash="x",
        )
        db.add(user)
        await db.flush()
        game = Game(
            user_id=user.id, title="Not yours", sort_title="not yours", folder_location="Not-yours"
        )
        db.add(game)
        await db.commit()
        ids = (user.id, game.id)
    yield ids[1]
    async with SessionLocal() as db:
        await db.execute(sql_delete(User).where(User.id == ids[0]))
        await db.commit()


async def test_bulk_delete_reports_each_outcome_and_restores_attachments(flow, stranger):
    first = await make_game(flow, "First")
    second = await make_game(flow, "Second")
    write(folder(flow, "First"), "notes/n.md", b"first note")
    write(folder(flow, "Second"), "screenshots/s.png", b"second shot")
    missing = str(uuid.uuid4())

    response = await bulk_delete(flow, [first, second, first, missing, str(stranger)])

    assert response.status_code == 200, response.text
    assert response.json() == {
        "deleted": [first, second],  # a repeated ID counts once
        "not_found": [missing, str(stranger)],  # another account's game looks like no game
        "already_deleted": [],
        "failed": [],
    }
    assert await deleted_at(first) is not None and await deleted_at(second) is not None
    assert await deleted_at(stranger) is None
    assert not folder(flow, "First").exists()

    # the games are in the ordinary trash, restorable with their files
    trashed = {row["id"] for row in (await flow.client.get("/api/game/trash")).json()}
    assert {first, second} <= trashed
    assert (await flow.client.post(f"/api/game/{first}/restore")).status_code == 200
    assert (folder(flow, "First") / "notes/n.md").read_bytes() == b"first note"
    assert await deleted_at(first) is None

    # repeating the request is safe: what is already in trash is reported, not moved again
    again = (await bulk_delete(flow, [second])).json()
    assert again["already_deleted"] == [second] and again["deleted"] == []
    assert (
        game_trash.game_trash_dir(flow.tmp, uuid.UUID(second)) / "screenshots/s.png"
    ).read_bytes() == b"second shot"


async def test_a_failed_game_never_undoes_the_others(flow, monkeypatch):
    good = await make_game(flow, "Good")
    bad = await make_game(flow, "Bad")
    write(folder(flow, "Bad"), "keep.txt", b"keep")
    real = game_trash.trash_game

    async def trash_game(db, game, data_root):
        if str(game.id) == bad:
            raise game_folders.FolderStorageError("storage is busy")
        await real(db, game, data_root)

    monkeypatch.setattr(game_trash, "trash_game", trash_game)
    response = await bulk_delete(flow, [bad, good])

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted"] == [good]
    assert body["failed"] == [{"id": bad, "detail": "storage is busy"}]
    assert await deleted_at(bad) is None
    assert (folder(flow, "Bad") / "keep.txt").read_bytes() == b"keep"
    assert await deleted_at(good) is not None


async def test_an_unexpected_error_is_reported_without_internal_detail(flow, monkeypatch):
    game_id = await make_game(flow, "Odd")

    async def trash_game(*_args):
        raise RuntimeError("secret internal path /data/x")

    monkeypatch.setattr(game_trash, "trash_game", trash_game)
    body = (await bulk_delete(flow, [game_id])).json()
    assert body["failed"] == [{"id": game_id, "detail": "Could not move this game to trash."}]
    assert await deleted_at(game_id) is None


@pytest.mark.parametrize("count", [0, GAME_BULK_DELETE_LIMIT + 1])
async def test_empty_and_oversized_requests_are_rejected(flow, count):
    response = await bulk_delete(flow, [str(uuid.uuid4()) for _ in range(count)])
    assert response.status_code == 422
