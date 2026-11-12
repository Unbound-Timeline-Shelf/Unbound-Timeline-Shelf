"""Three-state crossplay (CONTRACTS-v1 C7.2): what is stored, and what clients read back.

Older clients only know the yes/no flag. Saving that flag must not erase a "partial" or a "no"
it agrees with, "false" alone is never a confirmed "no", and every read gives both the flag and
the stored value."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import Response
from sqlalchemy import delete

from src.api.routes import games
from src.api.schemas.game import GameCreate, GameRead, GameUpdate, play_compatibility_view
from src.database.models.game import Game, stored_play_compatibility
from src.database.models.user import User
from src.database.session import SessionLocal

PLAYERS = {"min_players": 2, "max_players": 4}
# a created game is saved with every field of the write model; an update only with the ones sent
SAVED = {**PLAYERS, "duration_minutes": None}


@pytest.fixture(autouse=True)
def no_game_folders(monkeypatch):
    monkeypatch.setattr(games, "create_game_folder", lambda *_args: None)


@pytest.fixture
async def owner():
    async with SessionLocal() as db:
        user = User(
            username=f"x_{uuid.uuid4().hex[:10]}",
            email=f"{uuid.uuid4().hex[:10]}@example.test",
            password_hash="x",
        )
        db.add(user)
        await db.commit()
        user_id = user.id
    yield SimpleNamespace(id=user_id)
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


@pytest.mark.parametrize(
    ("previous", "written", "stored"),
    [
        (None, {"crossplay": True}, "yes"),
        (None, {"crossplay": False}, None),
        ({"crossplay": "partial"}, {"crossplay": True}, "partial"),
        ({"crossplay": "no"}, {"crossplay": False}, "no"),
        ({"crossplay": "no"}, {"crossplay": True}, "yes"),
        ({"crossplay": "partial"}, {"crossplay": False}, None),
        ({"crossplay": "yes"}, {"crossplay": True, "crossplay_status": "no"}, "no"),
        ({"crossplay": "yes"}, {"crossplay": False, "crossplay_status": None}, None),
        (None, {"crossplay": "partial"}, "partial"),
        (None, {"crossplay": "maybe"}, None),
    ],
)
def test_a_write_is_stored_as_a_vocabulary_value(previous, written, stored):
    result = stored_play_compatibility({**PLAYERS, **written}, previous)
    assert result == {**PLAYERS, "crossplay": stored}


def test_details_without_crossplay_and_cleared_details_are_left_alone():
    assert stored_play_compatibility({"duration_minutes": 30}, None) == {"duration_minutes": 30}
    assert stored_play_compatibility(None, {"crossplay": "yes"}) is None


@pytest.mark.parametrize(
    ("stored", "flag"), [("yes", True), ("partial", True), ("no", False), (None, False)]
)
def test_reads_carry_the_flag_and_the_stored_value(stored, flag):
    view = play_compatibility_view({**PLAYERS, "crossplay": stored})
    assert view == {**PLAYERS, "crossplay": flag, "crossplay_status": stored}


async def test_an_older_client_saving_the_flag_keeps_a_partial(owner):
    async with SessionLocal() as db:
        created = await games.create_game(
            GameCreate(
                title="Partial Crossplay",
                folder_location="Partial_Crossplay",
                play_compatibility={**PLAYERS, "crossplay": True, "crossplay_status": "partial"},
            ),
            Response(),
            db,
            owner,
        )
        game_id = created.id
    async with SessionLocal() as db:
        row = await db.get(Game, game_id)
        assert row.play_compatibility == {**SAVED, "crossplay": "partial"}
        read = GameRead.model_validate(row).model_dump(mode="json")
        assert read["play_compatibility"]["crossplay"] is True
        assert read["play_compatibility"]["crossplay_status"] == "partial"
        # an older client echoes what it read, without crossplay_status
        await games.update_game(
            game_id,
            GameUpdate(play_compatibility={**PLAYERS, "max_players": 6, "crossplay": True}),
            db,
            owner,
        )
    async with SessionLocal() as db:
        row = await db.get(Game, game_id)
        assert row.play_compatibility == {**PLAYERS, "max_players": 6, "crossplay": "partial"}
        # a newer client says "no" explicitly; the older flag then reads false
        await games.update_game(
            game_id,
            GameUpdate(play_compatibility={**PLAYERS, "crossplay_status": "no"}),
            db,
            owner,
        )
    async with SessionLocal() as db:
        row = await db.get(Game, game_id)
        assert row.play_compatibility == {**PLAYERS, "crossplay": "no"}
        read = GameRead.model_validate(row).model_dump(mode="json")
        assert read["play_compatibility"]["crossplay"] is False
        assert read["play_compatibility"]["crossplay_status"] == "no"
        # a client that echoes what it read and ticks the box: the tick is the edit
        echoed = {**read["play_compatibility"], "crossplay": True}
        await games.update_game(game_id, GameUpdate(play_compatibility=echoed), db, owner)
    async with SessionLocal() as db:
        assert (await db.get(Game, game_id)).play_compatibility["crossplay"] == "yes"


@pytest.mark.parametrize(
    ("sent", "stored_flag", "status"),
    [
        ({"crossplay_status": "partial"}, True, "partial"),  # the status alone sets the flag
        ({"crossplay": False, "crossplay_status": "no"}, False, "no"),
        ({"crossplay": True, "crossplay_status": "no"}, True, None),  # disagreeing: flag wins
        ({"crossplay": False}, False, None),
    ],
)
def test_the_write_model_keeps_flag_and_status_consistent(sent, stored_flag, status):
    model = GameUpdate(play_compatibility={**PLAYERS, **sent}).play_compatibility
    assert model is not None
    dumped = model.model_dump()
    assert dumped["crossplay"] is stored_flag
    assert dumped.get("crossplay_status") == status


def test_unknown_crossplay_status_is_refused():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        GameUpdate(play_compatibility={**PLAYERS, "crossplay_status": "sometimes"})
