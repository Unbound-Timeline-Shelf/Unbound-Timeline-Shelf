"""A person can type only a year or a month as a game's release date (SPEC-UI-POLISH-PASS 7):
the edit form sends the first day of the period with `release_date_precision`, and the game page
then shows "2011" or "April 2011", never a made-up day. The date stays ISO on the wire."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from src.api.schemas.game import GameCreate, GameUpdate
from src.database.models.game import Game
from src.database.session import SessionLocal
from tests.test_game_files_flow import game_flow  # noqa: F401


async def _stored(game_id):
    async with SessionLocal() as db:
        game = await db.scalar(select(Game).where(Game.id == game_id))
        return game.release_date.isoformat(), game.release_date_precision, game.locked_fields


async def _update(flow, payload):
    return await flow.client.patch(f"/api/game/update/{flow.game_id}", json=payload)


async def test_a_typed_year_is_stored_as_its_first_day_with_year_precision(flow):
    response = await _update(flow, {"release_date": "2011-01-01", "release_date_precision": "year"})
    assert response.status_code == 200, response.text
    assert response.json()["release_date"] == "2011-01-01"
    assert response.json()["release_date_precision"] == "year"
    date, precision, locked = await _stored(flow.game_id)
    assert (date, precision) == ("2011-01-01", "year")
    assert "release_date" in locked


async def test_a_month_then_a_full_day(flow):
    await _update(flow, {"release_date": "2011-04-01", "release_date_precision": "month"})
    assert (await _stored(flow.game_id))[:2] == ("2011-04-01", "month")
    # the same first day typed as a full day is now a day
    response = await _update(flow, {"release_date": "2011-04-01", "release_date_precision": "day"})
    assert response.status_code == 200, response.text
    assert (await _stored(flow.game_id))[:2] == ("2011-04-01", None)


async def test_a_date_without_precision_is_still_a_day_as_before(flow):
    await _update(flow, {"release_date": "2011-01-01", "release_date_precision": "year"})
    response = await _update(flow, {"release_date": "2012-06-30"})
    assert response.status_code == 200, response.text
    assert (await _stored(flow.game_id))[:2] == ("2012-06-30", None)


@pytest.mark.parametrize(
    "payload",
    [
        {"release_date_precision": "year"},
        {"release_date": None, "release_date_precision": "month"},
        {"release_date": "2011-04-19", "release_date_precision": "month"},
        {"release_date": "2011-04-01", "release_date_precision": "year"},
        {"release_date": "1949-01-01", "release_date_precision": "year"},
        {"release_date": "2101-05-01", "release_date_precision": "month"},
        {"release_date": "2011-01-01", "release_date_precision": "decade"},
    ],
)
async def test_a_precision_that_does_not_fit_the_date_is_refused(flow, payload):
    response = await _update(flow, payload)
    assert response.status_code == 422, response.text


def test_a_new_game_can_start_with_a_partial_release_date():
    game = GameCreate(
        title="Partial",
        folder_location="Partial",
        release_date="2011-04-01",
        release_date_precision="month",
    )
    assert game.release_date_precision == "month"
    assert GameCreate(title="Day", folder_location="Day").release_date_precision is None
    with pytest.raises(ValidationError):
        GameCreate(title="Bad", folder_location="Bad", release_date_precision="year")


def test_day_precision_is_stored_as_null_like_every_older_date():
    update = GameUpdate(release_date="2011-04-19", release_date_precision="day")
    assert update.release_date_precision is None
    assert "release_date_precision" in update.model_dump(exclude_unset=True)
