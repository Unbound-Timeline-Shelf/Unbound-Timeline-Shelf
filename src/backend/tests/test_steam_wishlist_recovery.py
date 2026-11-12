"""#465: a later Steam import finishes wishlist records an earlier import left unfinished."""

import uuid
from unittest.mock import AsyncMock, Mock

import pytest

from src.api.routes import library_sync, steam_import_steps
from src.core.preferences import save_preferences
from src.database.models.game import Game, GameStatus
from src.database.session import SessionLocal
from src.helpers import save_game_asset
from tests.test_game_artwork_storage import picture
from tests.test_game_files_flow import game_flow  # noqa: F401
from tests.test_steam_import_steps import connect_steam

pytestmark = pytest.mark.usefixtures("storage")


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(save_game_asset, "DATA_ROOT", tmp_path)


def store_record(title: str, kinds=("key_art",)):
    return {
        "title": title,
        "metadata": {"description": "from the store", "developer": "Studio"},
        "assets": [{"kind": kind, "url": f"https://art.test/{kind}.png"} for kind in kinds],
    }


async def wishlist_import(flow, monkeypatch, app_ids):
    monkeypatch.setattr(
        steam_import_steps.steam_wishlist, "get_wishlist_app_ids", lambda *_: list(app_ids)
    )
    monkeypatch.setattr(steam_import_steps.steam_connection, "recheck", AsyncMock())
    response = await flow.client.post("/api/library-sync/steam/wishlist")
    assert response.status_code == 200, response.text
    return response.json()


async def enrich(flow, ids, **body):
    response = await flow.client.post(
        "/api/library-sync/steam/enrich", json={"game_ids": ids, **body}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def enable_wishlist(flow):
    await connect_steam(flow)
    async with SessionLocal() as db:
        await save_preferences(db, flow.user_id, {"steam_import_wishlist": True})


async def test_reimport_with_no_new_games_finishes_an_interrupted_wishlist_record(
    flow, monkeypatch
):
    await enable_wishlist(flow)
    first = await wishlist_import(flow, monkeypatch, [1086940])
    [game_id] = first["game_ids"]
    assert first["recover_game_ids"] == []

    # the first enrichment never finishes: the provider is down
    monkeypatch.setattr(
        library_sync, "resolve_library_record", AsyncMock(side_effect=TimeoutError("down"))
    )
    failed = await enrich(flow, [game_id])
    assert failed["failed_game_ids"] == [game_id]
    async with SessionLocal() as db:
        assert (await db.get(Game, uuid.UUID(game_id))).title == "Steam app 1086940"

    # a later import adds nothing, yet still offers the unfinished record for recovery
    second = await wishlist_import(flow, monkeypatch, [1086940])
    assert second["added"] == 0 and second["game_ids"] == []
    assert second["recover_game_ids"] == [game_id] and second["recover_remaining"] == 0

    monkeypatch.setattr(
        library_sync,
        "resolve_library_record",
        AsyncMock(return_value=(store_record("Baldur's Gate 3"), [])),
    )
    monkeypatch.setattr(library_sync, "download_image", Mock(return_value=picture()))
    recovered = await enrich(flow, second["recover_game_ids"], recover=True)
    assert recovered["enriched"] == 1 and recovered["failed_game_ids"] == []
    assert recovered["artwork_missing_game_ids"] == []
    async with SessionLocal() as db:
        game = await db.get(Game, uuid.UUID(game_id))
        assert game.title == "Baldur's Gate 3"
        assert game.description == "from the store"
        assert game.status == GameStatus.WISHLIST
        folder = flow.tmp / str(flow.user_id) / "games" / game.folder_location
    assert (folder / "key_art.png").is_file()

    # once finished, the record is no longer offered: retrying is not a library-wide refresh
    third = await wishlist_import(flow, monkeypatch, [1086940])
    assert third["recover_game_ids"] == []


async def test_recovery_skips_deliberate_titles_and_keeps_saved_details(flow, monkeypatch):
    await enable_wishlist(flow)
    first = await wishlist_import(flow, monkeypatch, [10, 20, 30])
    renamed, locked, edited = (uuid.UUID(game_id) for game_id in first["game_ids"])
    async with SessionLocal() as db:
        (await db.get(Game, renamed)).title = "My own name"
        locked_game = await db.get(Game, locked)
        locked_game.locked_fields = ["title"]  # a person kept the placeholder on purpose
        edited_game = await db.get(Game, edited)
        edited_game.description = "my notes on this one"
        edited_game.locked_fields = ["developer"]
        edited_game.developer = None
        await db.commit()

    second = await wishlist_import(flow, monkeypatch, [10, 20, 30])
    assert second["recover_game_ids"] == [str(edited)]

    # the record already has a cover a person chose; recovery must not replace it
    async with SessionLocal() as db:
        game = await db.get(Game, edited)
        folder = flow.tmp / str(flow.user_id) / "games" / game.folder_location
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "key_art.png").write_bytes(b"chosen cover")
    download = Mock(return_value=picture())
    monkeypatch.setattr(library_sync, "download_image", download)
    monkeypatch.setattr(
        library_sync,
        "resolve_library_record",
        AsyncMock(return_value=(store_record("Store Title", kinds=("key_art", "icon")), [])),
    )
    result = await enrich(flow, [str(edited)], recover=True)
    assert result["enriched"] == 1
    async with SessionLocal() as db:
        game = await db.get(Game, edited)
        assert game.title == "Store Title"  # the placeholder is still upgraded
        assert game.description == "my notes on this one"  # saved details are kept
        assert game.developer is None  # a locked (cleared) field stays cleared
    assert (folder / "key_art.png").read_bytes() == b"chosen cover"
    assert (folder / "icon.png").is_file()  # only the missing picture was fetched
    assert [call.args[0] for call in download.call_args_list] == ["https://art.test/icon.png"]


async def test_recovery_is_bounded_per_import_and_ignores_deleted_records(flow, monkeypatch):
    await enable_wishlist(flow)
    first = await wishlist_import(flow, monkeypatch, [1, 2, 3])
    deleted = first["game_ids"][2]
    response = await flow.client.delete(f"/api/game/delete/{deleted}")
    assert response.status_code == 204, response.text

    monkeypatch.setattr(steam_import_steps, "_RECOVER_LIMIT", 1)
    second = await wishlist_import(flow, monkeypatch, [1, 2])
    assert len(second["recover_game_ids"]) == 1 and second["recover_remaining"] == 1
    assert deleted not in second["recover_game_ids"]
    assert set(second["recover_game_ids"]) <= set(first["game_ids"][:2])
