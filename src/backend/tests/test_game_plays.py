"""Logging play is activity; correcting lifetime playtime is not (#467).

A log records one `game_play_events` row and moves `last_played_at` forward, so Home Continue,
the weekly digest and the group overview see it once. A playtime correction, a status edit and
a provider catching up on a larger lifetime total never manufacture a session today."""

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import func, select

from src.api.routes import library_sync, stats
from src.database.models.game import Game
from src.database.models.game_identity_tracking import GamePlayEvent
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.game_identity import counters, decisions, overview, plays
from src.features.game_identity.graph import IdentityConflict, IdentityInvalid
from src.features.imports.library_export import build_library_export
from tests.test_connection_jobs import connected  # noqa: F401
from tests.test_game_counters import PLAYNITE, report, run
from tests.test_game_ingestion import make_game, users  # noqa: F401
from tests.test_game_manual import client_for

DAY = 86_400
HOUR = 3_600


async def last_played(game):
    async with SessionLocal() as db:
        return (await db.get(Game, game)).last_played_at


async def events(user, game=None):
    async with SessionLocal() as db:
        query = select(func.count(GamePlayEvent.id)).where(GamePlayEvent.user_id == user)
        if game is not None:
            query = query.where(GamePlayEvent.game_id == game)
        return await db.scalar(query)


async def digest(user):
    async with SessionLocal() as db:
        return await stats.get_weekly_digest(db=db, current_user=SimpleNamespace(id=user))


async def test_log_now_moves_last_played_once_and_never_backwards(users):  # noqa: F811
    user = await users()
    game = await make_game(user, "Celeste")
    now = int(time.time())

    logged = await run(plays.log_play, user, game, now=now)
    assert logged["last_played_at"] == now and logged["event"]["played_at"] == now
    assert await last_played(game) == now and await events(user, game) == 1

    # an older session logged later is history: it is recorded but does not move the time back
    older = await run(plays.log_play, user, game, now - 3 * DAY, now=now)
    assert older["event"]["played_at"] == now - 3 * DAY
    assert older["last_played_at"] == now and await last_played(game) == now
    assert [row["played_at"] for row in await run(plays.recent_plays, user, game)] == [
        now,
        now - 3 * DAY,
    ]

    # a browser clock a little ahead is "now"; a real future time is refused
    ahead = await run(plays.log_play, user, game, now + 60, now=now)
    assert ahead["event"]["played_at"] == now
    with pytest.raises(IdentityInvalid, match="future"):
        await run(plays.log_play, user, game, now + DAY, now=now)
    with pytest.raises(IdentityInvalid, match="1970"):
        await run(plays.log_play, user, game, -1, now=now)
    assert await events(user, game) == 3


async def test_a_retry_with_the_same_request_records_one_play(users):  # noqa: F811
    user = await users()
    game, other_game = await make_game(user, "Hades"), await make_game(user, "Tunic")
    first = await run(plays.log_play, user, game, request_id="tab-1-request")
    again = await run(plays.log_play, user, game, request_id="tab-1-request")
    assert again["event"]["id"] == first["event"]["id"] and await events(user, game) == 1
    with pytest.raises(IdentityConflict):
        await run(plays.log_play, user, other_game, request_id="tab-1-request")
    assert await events(user, other_game) == 0


async def test_corrections_status_edits_and_larger_totals_are_not_play(users):  # noqa: F811
    user = await users()
    game = await make_game(user, "Outer Wilds")
    async with SessionLocal() as db:
        (await db.get(Game, game)).last_played_at = 1_600_000_000
        await db.commit()
    before = await digest(user)

    # a source reports a lifetime total, then a larger one (provider catch-up)
    await report(user, game, PLAYNITE, "p1", 20, 10)
    await report(user, game, PLAYNITE, "p1", 60, 20)
    await run(counters.adjust_total, user, game, 40 * HOUR, reason="old save")
    await run(counters.adjust_total, user, game, 80 * HOUR)
    async with client_for(user) as client:
        for change in ({"status": "PLAYING"}, {"playtime_seconds": 90 * HOUR}):
            edited = await client.patch(f"/api/game/update/{game}", json=change)
            assert edited.status_code == 200, edited.text
    assert await last_played(game) == 1_600_000_000
    assert await events(user) == 0
    # the edits are recorded as edits (metadata_changes), never as a game played this week
    assert (await digest(user))["games_played"] == before["games_played"]

    await run(plays.log_play, user, game)
    after = await digest(user)
    assert after["games_played"] == before["games_played"] + 1


async def test_merged_copies_show_the_latest_logged_play(users):  # noqa: F811
    user = await users()
    first, second = await make_game(user, "Hades"), await make_game(user, "Hades (Steam)")
    await run(decisions.merge_identities, user, first, second, decisions.Actor(reason="t"))
    now = int(time.time())
    await run(plays.log_play, user, second, now - HOUR, now=now)
    for member in (first, second):
        view = await run(overview.game_overview, user, member)
        assert view["aggregate"]["last_played_at"] == now - HOUR


async def test_http_log_list_validation_and_isolation(users):  # noqa: F811
    user, other = await users(), await users()
    game = await make_game(user, "Inscryption")
    base = f"/api/game-identity/games/{game}/plays"
    async with client_for(user) as client:
        logged = await client.post(base, json={"request_id": "request-0001"})
        assert logged.status_code == 201, logged.text
        retried = await client.post(base, json={"request_id": "request-0001"})
        assert retried.json()["event"]["id"] == logged.json()["event"]["id"]
        dated = await client.post(base, json={"played_at": 1_700_000_000})
        assert dated.status_code == 201 and dated.json()["event"]["played_at"] == 1_700_000_000
        for bad in ({"played_at": -5}, {"request_id": "x"}, {"request_id": "bad id!!"}, {"x": 1}):
            assert (await client.post(base, json=bad)).status_code == 422, bad
        listed = (await client.get(base)).json()
        assert [item["played_at"] for item in listed][1] == 1_700_000_000
        assert len(listed) == 2
    async with client_for(other) as client:
        assert (await client.post(base, json={})).status_code == 404
        assert (await client.get(base)).status_code == 404
    assert await events(other) == 0


async def test_export_lists_logged_plays(users):  # noqa: F811
    user = await users()
    game = await make_game(user, "Celeste")
    await run(plays.log_play, user, game, 1_700_000_000)
    async with SessionLocal() as db:
        exported = await build_library_export(db, user)
    assert [(e["game_id"], e["played_at"]) for e in exported.play_events] == [(game, 1_700_000_000)]


async def test_steam_resync_keeps_a_newer_logged_play(connected, monkeypatch):  # noqa: F811
    actor_id, _ = connected
    week_ago = int(time.time()) - 7 * DAY
    owned = Mock(
        return_value=[
            {
                "appid": 123,
                "name": "Fixture game",
                "playtime_forever": 20,
                "rtime_last_played": week_ago,
            }
        ]
    )
    monkeypatch.setattr(
        library_sync, "_enrich_new_game", AsyncMock(return_value=library_sync.EnrichOutcome())
    )
    monkeypatch.setattr(library_sync.steam, "resolve_steam_id", lambda sid, _key: sid)
    monkeypatch.setattr(library_sync.steam, "get_owned_games", owned)
    monkeypatch.setattr(library_sync.steam, "get_schema_for_game", Mock(return_value={}))
    monkeypatch.setattr(library_sync.steam, "get_player_achievements", Mock(return_value=[]))

    async def sync():
        async with SessionLocal() as db:
            actor = await db.get(User, actor_id)
            await library_sync.sync_steam_library(db=db, current_user=actor, achievements="now")

    await sync()
    async with SessionLocal() as db:
        game = await db.scalar(select(Game).where(Game.user_id == actor_id))
        assert game.last_played_at == week_ago
    logged = await run(plays.log_play, actor_id, game.id)

    # Steam reports a larger lifetime total but the same last played time: no new session
    owned.return_value[0]["playtime_forever"] = 600
    await sync()
    assert await last_played(game.id) == logged["last_played_at"]
    assert await events(actor_id, game.id) == 1
