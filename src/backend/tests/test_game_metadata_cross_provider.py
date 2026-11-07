"""One game refreshed from four providers at once (Steam, IGDB, RAWG and Atlas).

The metadata handler merges every provider's patch into one record: each field comes from the
highest-priority provider that has it. These tests drive the real refresh route and handler
with stand-in providers and read the result back from PostgreSQL. They check that the merged
record is one game, that every field's recorded source is the provider that supplied it, that
a person's locks, preferences and play history survive, and that a second refresh (or a
library re-import) changes nothing."""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from src.database.models.game import Game, GameStatus
from src.database.models.game_identity_tracking import (
    FieldValueState,
    GameFieldPreference,
    GameFieldValue,
)
from src.database.session import SessionLocal
from src.features.game_identity import decisions
from src.features.game_identity.field_provenance import PROVENANCE_FIELDS
from src.features.imports.library_games import get_or_create_game
from src.plugin_api.metadata_contracts import MetadataPatch, MetadataRelease
from tests.test_game_metadata_refresh_identity import (
    ATLAS_ID,
    StandInProvider,
    add_game,
    refresh,
    stored,
)
from tests.test_game_metadata_refresh_identity import client as client  # noqa: F401 - fixture
from tests.test_game_metadata_refresh_identity import owner as owner  # noqa: F401 - fixture
from tests.test_game_metadata_refresh_identity import providers as providers  # noqa: F401

STEAM_ID = "620"
IGDB_ID = "72"
RAWG_ID = "4200"
ATLAS = "official.metadata-atlas.atlas"
RAWG = "community.metadata-rawg.rawg"


def _linked(provider_id: str, namespace: str, priority: int, **fields) -> StandInProvider:
    """A provider that answers for the game's Steam id and reports its own id back."""

    def respond(candidate):
        if candidate is None or candidate.provider_ids.get("steam") != STEAM_ID:
            return None
        return MetadataPatch(**fields)

    return StandInProvider(provider_id, namespace, respond, priority=priority)


def four_providers(**overrides: dict) -> tuple[StandInProvider, ...]:
    """Steam (first), IGDB, RAWG and Atlas, each holding a different part of the record."""
    fields = {
        "steam": {"title": "Portal 2", "description": "A puzzle game from Steam."},
        "igdb": {"developer": "Valve", "publisher": "Electronic Arts", "tags": ["Puzzle"]},
        "rawg": {"age_rating": "PEGI 12", "series": "Portal", "developer": "Valve (RAWG)"},
        "atlas": {
            "first_release": MetadataRelease(date="2011-04", precision="month"),
            "releases": (MetadataRelease(date="2011-04", precision="month", region="eu"),),
            "description": "An Atlas description that Steam outranks.",
        },
    }
    for name, changed in overrides.items():
        fields[name] = {**fields[name], **changed}
    return (
        _linked("core.steam", "steam", 0, provider_ids={"steam": STEAM_ID}, **fields["steam"]),
        _linked("core.igdb", "igdb", 20, provider_ids={"igdb": IGDB_ID}, **fields["igdb"]),
        _linked(RAWG, "rawg", 30, provider_ids={"rawg": RAWG_ID}, **fields["rawg"]),
        _linked(ATLAS, "atlas", 40, provider_ids={"atlas": ATLAS_ID}, **fields["atlas"]),
    )


async def current_sources(game_id) -> dict[str, tuple[str | None, object]]:
    """field -> (recorded provider, recorded value) of the current provenance rows (the title
    keeps its own rules in `names`)."""
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(GameFieldValue).where(
                GameFieldValue.game_id == game_id,
                GameFieldValue.state == FieldValueState.CURRENT.value,
                GameFieldValue.field.in_(PROVENANCE_FIELDS),
            )
        )
        return {row.field: (row.source_label, row.value) for row in rows}


async def games_of(user_id) -> int:
    async with SessionLocal() as db:
        return int(await db.scalar(select(func.count()).where(Game.user_id == user_id)) or 0)


async def test_four_providers_make_one_record_with_every_field_attributed_to_its_provider(
    owner, providers, client
):
    user_id, _ = owner
    providers(*four_providers())
    game_id = await add_game(user_id, provider_ids={"steam": STEAM_ID})

    result = await refresh(client, game_id)

    assert result["status"] == "updated"
    game = await stored(game_id)
    assert game.provider_ids == {
        "steam": STEAM_ID,
        "igdb": IGDB_ID,
        "rawg": RAWG_ID,
        "atlas": ATLAS_ID,
    }
    # each field from the highest-priority provider that has it
    assert (game.title, game.description) == ("Portal 2", "A puzzle game from Steam.")
    assert (game.developer, game.publisher, game.tags) == ("Valve", "Electronic Arts", ["Puzzle"])
    assert (game.age_rating, game.series) == ("PEGI 12", "Portal")
    assert (game.release_date, game.release_date_precision) == (date(2011, 4, 1), "month")
    assert await games_of(user_id) == 1

    sources = await current_sources(game_id)
    assert sources == {
        "description": ("core.steam", "A puzzle game from Steam."),
        "developer": ("core.igdb", "Valve"),
        "publisher": ("core.igdb", "Electronic Arts"),
        "tags": ("core.igdb", ["Puzzle"]),
        "age_rating": (RAWG, "PEGI 12"),
        "series": (RAWG, "Portal"),
        "release_date": (ATLAS, "2011-04-01"),
    }
    read = (await client.get(f"/api/game/{game_id}/field-sources")).json()
    assert read["developer"]["provider"] == "core.igdb"
    assert read["release_date"]["provider"] == ATLAS


async def test_a_day_from_one_provider_inside_another_providers_month_names_the_day_source(
    owner, providers, client
):
    user_id, _ = owner
    # Steam knows the day, Atlas only the month: the day wins (C5.2) and it is Steam's
    providers(*four_providers(steam={"release_date": date(2011, 4, 19)}))
    game_id = await add_game(user_id, provider_ids={"steam": STEAM_ID})

    await refresh(client, game_id)

    game = await stored(game_id)
    assert (game.release_date, game.release_date_precision) == (date(2011, 4, 19), None)
    assert (await current_sources(game_id))["release_date"] == ("core.steam", "2011-04-19")


async def test_a_preferred_provider_keeps_its_field_when_a_higher_priority_one_disagrees(
    owner, providers, client
):
    user_id, _ = owner
    providers(*four_providers())
    game_id = await add_game(user_id, provider_ids={"steam": STEAM_ID})
    await refresh(client, game_id)

    # the person prefers IGDB's developer; IGDB's value must be known by its own name
    response = await client.put(
        f"/api/game/{game_id}/field-preferences/developer",
        json={"source_key": "provider:core.igdb"},
    )
    assert response.status_code == 200, response.text

    # IGDB now names someone else; the preference follows IGDB, not the merged record's lead
    providers(*four_providers(igdb={"developer": "Valve Corporation"}))
    result = await refresh(client, game_id)

    assert result["status"] == "updated"
    game = await stored(game_id)
    assert game.developer == "Valve Corporation"
    assert (await current_sources(game_id))["developer"] == ("core.igdb", "Valve Corporation")


async def test_a_preference_for_a_lower_priority_provider_wins_over_the_merged_value(
    owner, providers, client
):
    user_id, _ = owner
    # RAWG's developer loses to IGDB's in the merge; the person prefers RAWG's
    providers(*four_providers())
    game_id = await add_game(user_id, provider_ids={"steam": STEAM_ID})
    async with SessionLocal() as db:
        db.add(
            GameFieldPreference(
                user_id=user_id,
                game_id=game_id,
                field="developer",
                source_key=f"provider:{RAWG}",
                created_at=1,
            )
        )
        await db.commit()

    await refresh(client, game_id)

    # RAWG had no recorded value yet, so the refresh fills the field as usual (IGDB's)
    assert (await stored(game_id)).developer == "Valve"
    # and a second refresh still does not hand IGDB's value to the preferred RAWG
    await refresh(client, game_id)
    sources = await current_sources(game_id)
    assert sources["developer"] == ("core.igdb", "Valve")


async def test_locks_and_play_history_survive_a_four_provider_refresh(owner, providers, client):
    user_id, _ = owner
    providers(*four_providers())
    game_id = await add_game(
        user_id,
        provider_ids={"steam": STEAM_ID},
        developer="My own developer",
        release_date=date(2011, 4, 18),
        locked_fields=["developer", "release_date", "releases"],
        status=GameStatus.PLAYING,
        playtime_seconds=7200,
        last_played_at=1_700_000_000,
        rating_overall=9,
    )

    result = await refresh(client, game_id)

    assert result["status"] == "updated"
    assert {"developer", "release_date", "releases"} <= set(result["skipped_locked_fields"])
    game = await stored(game_id)
    assert game.developer == "My own developer"
    assert (game.release_date, game.release_date_precision, game.releases) == (
        date(2011, 4, 18),
        None,
        None,
    )
    assert game.locked_fields == ["developer", "release_date", "releases"]
    assert (game.status, game.playtime_seconds, game.last_played_at) == (
        GameStatus.PLAYING,
        7200,
        1_700_000_000,
    )
    assert float(game.rating_overall) == 9
    # unlocked fields still arrive
    assert (game.publisher, game.series) == ("Electronic Arts", "Portal")


async def test_a_second_refresh_with_the_same_answers_changes_nothing(owner, providers, client):
    user_id, _ = owner
    providers(*four_providers())
    game_id = await add_game(user_id, provider_ids={"steam": STEAM_ID})
    await refresh(client, game_id)
    before = await stored(game_id)
    sources = await current_sources(game_id)

    again = await refresh(client, game_id)

    assert again["status"] == "updated"
    assert again["changed_fields"] == []
    after = await stored(game_id)
    assert after.updated_at == before.updated_at
    assert await current_sources(game_id) == sources
    async with SessionLocal() as db:
        rows = await db.scalar(
            select(func.count()).where(
                GameFieldValue.game_id == game_id, GameFieldValue.field.in_(PROVENANCE_FIELDS)
            )
        )
    assert rows == len(sources)


async def test_a_steam_reimport_after_merge_and_unmerge_reuses_both_games(owner, providers, client):
    user_id, _ = owner
    providers(*four_providers())
    steam_game = await add_game(
        user_id,
        title="Portal 2",
        source="steam",
        external_id=STEAM_ID,
        provider_ids={"steam": STEAM_ID},
        playtime_seconds=600,
    )
    other_copy = await add_game(user_id, title="Portal 2", source="manual", playtime_seconds=60)
    await refresh(client, steam_game)

    async with SessionLocal() as db:
        await decisions.merge_identities(db, user_id, other_copy, steam_game)
        await db.commit()
    async with SessionLocal() as db:
        game, created = await get_or_create_game(db, user_id, "Portal 2", "steam", STEAM_ID)
        await db.commit()
        assert (game.id, created) == (steam_game, False)
    async with SessionLocal() as db:
        await decisions.unmerge_identity(db, user_id, other_copy)
        await db.commit()
    async with SessionLocal() as db:
        game, created = await get_or_create_game(db, user_id, "Portal 2", "steam", STEAM_ID)
        await db.commit()
        assert (game.id, created) == (steam_game, False)

    assert await games_of(user_id) == 2
    merged = await stored(steam_game)
    assert merged.playtime_seconds == 600
    assert merged.provider_ids["igdb"] == IGDB_ID
    assert (await stored(other_copy)).playtime_seconds == 60
