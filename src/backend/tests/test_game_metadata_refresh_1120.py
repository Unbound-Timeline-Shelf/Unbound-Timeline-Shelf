"""A metadata refresh with Plugin API 1.1.20 data: partial release dates, releases, followed
merges and redirects (C4.4 rules 2 and 4), game relations (C6.4) and three-state crossplay.

Every test drives the real refresh route and metadata handler with stand-in providers, and reads
the result back from PostgreSQL."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import select

from src.api.routes import export_import, library_sync, media_extras
from src.database.models.game import Game, GameStatus
from src.database.models.game_field_change import GameFieldChange
from src.database.models.game_identity import GameRelationship, RecordOrigin
from src.database.models.game_pending_relationship import GamePendingRelationship
from src.database.session import SessionLocal
from src.features import game_duplicates, game_notifications
from src.features.game_identity import graph
from src.features.game_identity.refresh_match import classify
from src.features.notification_controller import NotificationEvent
from src.plugin_api.metadata_contracts import (
    MetadataCandidate,
    MetadataIdentity,
    MetadataPatch,
    MetadataRelation,
    MetadataRelease,
)
from tests.test_game_metadata_refresh_identity import (
    ATLAS_ID,
    OTHER_ATLAS_ID,
    StandInProvider,
    add_game,
    refresh,
    steam,
    stored,
)
from tests.test_game_metadata_refresh_identity import client as client  # noqa: F401 - fixture
from tests.test_game_metadata_refresh_identity import owner as owner  # noqa: F401 - fixture
from tests.test_game_metadata_refresh_identity import providers as providers  # noqa: F401

ATLAS = "official.metadata-atlas.atlas"
BASE_ATLAS_ID = "atlas:01JTESTBASEGAME000000000000"


def atlas_for(steam_id: str, **fields) -> StandInProvider:
    """An Atlas stand-in that answers for one linked Steam id with 1.1.20 fields."""

    def respond(candidate):
        if candidate is None or candidate.provider_ids.get("steam") != steam_id:
            return None
        fields.setdefault("provider_ids", {"atlas": ATLAS_ID})
        return MetadataPatch(**fields)

    return StandInProvider(ATLAS, "atlas", respond, priority=10)


def atlas_by_id(answers: dict[str, MetadataPatch]) -> StandInProvider:
    """An Atlas stand-in that answers by the atlas id it is asked about (no Steam id needed)."""

    def respond(candidate):
        return answers.get((candidate.provider_ids if candidate else {}).get("atlas", ""))

    return StandInProvider(ATLAS, "atlas", respond, priority=10)


def relation(kind: str, target: str, *, provider: str = ATLAS) -> MetadataRelation:
    return MetadataRelation(
        candidate=MetadataCandidate(external_id=target, title="Target", provider=provider),
        relation=kind,
    )


async def edges(user_id: UUID) -> list[tuple[str, UUID, UUID, str, bool]]:
    async with SessionLocal() as db:
        rows = await db.scalars(select(GameRelationship).where(GameRelationship.user_id == user_id))
        return sorted(
            (row.kind, row.from_game_id, row.to_game_id, row.origin, row.ended_at is None)
            for row in rows
        )


async def pending(user_id: UUID) -> list[tuple[UUID, str, str, str]]:
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(GamePendingRelationship).where(GamePendingRelationship.user_id == user_id)
        )
        return sorted((row.game_id, row.kind, row.namespace, row.external_id) for row in rows)


async def id_history(game_id: UUID) -> list[tuple[str | None, str | None]]:
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(GameFieldChange).where(
                GameFieldChange.game_id == game_id, GameFieldChange.field_name == "provider_ids"
            )
        )
        return [(row.old_value, row.new_value) for row in rows]


# --- C5: partial and multiple release dates ---------------------------------------------------


async def test_a_year_only_release_is_stored_as_the_start_of_the_year_with_its_precision(
    owner, providers, client
):
    user_id, _ = owner
    releases = (
        MetadataRelease(date="2011", precision="year", platform="pc", region="ww"),
        MetadataRelease(date="2011-05", precision="month", platform="ps3", region="eu"),
    )
    providers(
        steam(),
        atlas_for(
            "12345",
            first_release=MetadataRelease(date="2011", precision="year"),
            releases=releases,
        ),
    )
    game_id = await add_game(user_id, provider_ids={"steam": "12345"})

    result = await refresh(client, game_id)

    assert result["status"] == "updated"
    assert {"release_date", "release_date_precision", "releases"} <= set(result["changed_fields"])
    game = await stored(game_id)
    assert (game.release_date, game.release_date_precision) == (date(2011, 1, 1), "year")
    assert game.releases == [release.model_dump(mode="json") for release in releases]
    read = (await client.get(f"/api/game/get/{game_id}")).json()
    assert (read["release_date"], read["release_date_precision"]) == ("2011-01-01", "year")
    assert [entry["date"] for entry in read["releases"]] == ["2011", "2011-05"]


async def test_a_more_precise_day_replaces_a_year_and_reads_as_a_day(owner, providers, client):
    user_id, _ = owner
    providers(steam(release_date=date(2011, 4, 19)))
    game_id = await add_game(
        user_id,
        provider_ids={"steam": "12345"},
        release_date=date(2011, 1, 1),
        release_date_precision="year",
    )

    await refresh(client, game_id)

    game = await stored(game_id)
    assert (game.release_date, game.release_date_precision) == (date(2011, 4, 19), None)


async def test_only_the_precision_changes_when_the_day_was_already_the_first_of_the_year(
    owner, providers, client
):
    user_id, _ = owner
    providers(steam(release_date=date(2011, 1, 1)))
    game_id = await add_game(
        user_id,
        provider_ids={"steam": "12345"},
        release_date=date(2011, 1, 1),
        release_date_precision="year",
    )

    result = await refresh(client, game_id)

    assert "release_date_precision" in result["changed_fields"]
    assert (await stored(game_id)).release_date_precision is None


async def test_a_day_agreeing_with_a_reported_year_wins(owner, providers, client):
    user_id, _ = owner
    # Steam knows the day; Atlas (a different source of the merged result) only the year
    providers(
        steam(release_date=date(2011, 4, 19)),
        atlas_for("12345", first_release=MetadataRelease(date="2011", precision="year")),
    )
    game_id = await add_game(user_id, provider_ids={"steam": "12345"})

    await refresh(client, game_id)

    game = await stored(game_id)
    assert (game.release_date, game.release_date_precision) == (date(2011, 4, 19), None)


async def test_a_stored_day_survives_a_refresh_that_only_knows_its_year(owner, providers, client):
    user_id, _ = owner
    providers(
        steam(),
        atlas_for(
            "12345",
            first_release=MetadataRelease(date="2011", precision="year"),
            releases=(MetadataRelease(date="2011", precision="year", platform="pc"),),
        ),
    )
    game_id = await add_game(
        user_id, provider_ids={"steam": "12345"}, release_date=date(2011, 4, 19)
    )

    result = await refresh(client, game_id)

    assert result["status"] == "updated"
    assert not {"release_date", "release_date_precision"} & set(result["changed_fields"])
    game = await stored(game_id)
    assert (game.release_date, game.release_date_precision) == (date(2011, 4, 19), None)
    assert [entry["date"] for entry in game.releases] == ["2011"]


async def test_a_stored_month_survives_a_year_but_a_different_year_replaces_it(
    owner, providers, client
):
    user_id, _ = owner
    providers(
        steam(), atlas_for("12345", first_release=MetadataRelease(date="2011", precision="year"))
    )
    game_id = await add_game(
        user_id,
        provider_ids={"steam": "12345"},
        release_date=date(2011, 4, 1),
        release_date_precision="month",
    )
    await refresh(client, game_id)
    game = await stored(game_id)
    assert (game.release_date, game.release_date_precision) == (date(2011, 4, 1), "month")

    async with SessionLocal() as db:
        (await db.get(Game, game_id)).release_date = date(2009, 4, 19)
        await db.commit()
    await refresh(client, game_id)
    game = await stored(game_id)
    assert (game.release_date, game.release_date_precision) == (date(2011, 1, 1), "year")


async def test_a_first_release_that_is_not_a_full_or_early_access_release_is_ignored(
    owner, providers, client
):
    user_id, _ = owner
    providers(
        steam(),
        atlas_for(
            "12345", first_release=MetadataRelease(date="2010", precision="year", category="beta")
        ),
    )
    game_id = await add_game(user_id, provider_ids={"steam": "12345"})

    await refresh(client, game_id)

    game = await stored(game_id)
    assert (game.release_date, game.release_date_precision) == (None, None)


async def test_the_release_date_lock_covers_its_precision_and_releases_have_their_own(
    owner, providers, client
):
    user_id, _ = owner
    providers(
        steam(),
        atlas_for(
            "12345",
            first_release=MetadataRelease(date="2012", precision="year"),
            releases=(MetadataRelease(date="2012", precision="year", platform="pc"),),
        ),
    )
    game_id = await add_game(
        user_id,
        provider_ids={"steam": "12345"},
        release_date=date(2011, 4, 19),
        locked_fields=["release_date", "releases"],
    )

    result = await refresh(client, game_id)

    assert {"release_date", "releases"} <= set(result["skipped_locked_fields"])
    game = await stored(game_id)
    assert (game.release_date, game.release_date_precision, game.releases) == (
        date(2011, 4, 19),
        None,
        None,
    )


async def test_an_edited_date_is_a_day_again(owner, client):
    user_id, _ = owner
    game_id = await add_game(user_id, release_date=date(2011, 1, 1), release_date_precision="year")
    response = await client.patch(
        f"/api/game/update/{game_id}", json={"release_date": "2011-06-01"}
    )
    assert response.status_code == 200, response.text
    game = await stored(game_id)
    assert (game.release_date, game.release_date_precision) == (date(2011, 6, 1), None)


# --- C4.4 rule 2: a merge or redirect is followed by id ---------------------------------------


async def test_a_redirected_atlas_id_is_rewritten_to_the_live_one(owner, providers, client):
    user_id, _ = owner
    providers(
        atlas_by_id(
            {
                OTHER_ATLAS_ID: MetadataPatch(
                    title="Portal 2",
                    provider_ids={"atlas": ATLAS_ID},
                    identity=MetadataIdentity(
                        match="redirect", previous_ids={"atlas": OTHER_ATLAS_ID}
                    ),
                )
            }
        )
    )
    game_id = await add_game(
        user_id, title="Portal 2 (old)", provider_ids={"atlas": OTHER_ATLAS_ID}
    )

    preview = await refresh(client, game_id, dry_run=True)
    assert (preview["status"], preview["match"]) == ("preview", "id")
    assert preview["followed_ids"] == {
        "atlas": {"from": OTHER_ATLAS_ID, "to": ATLAS_ID, "provider": ATLAS}
    }
    assert (await stored(game_id)).provider_ids == {"atlas": OTHER_ATLAS_ID}

    applied = await refresh(client, game_id)
    assert (applied["status"], applied["match"]) == ("updated", "id")
    assert (await stored(game_id)).provider_ids == {"atlas": ATLAS_ID}
    # the id the game held stays in its history, so a wrong merge can be undone (C4.2)
    assert await id_history(game_id) == [
        (f"atlas: {OTHER_ATLAS_ID}", f"atlas: {ATLAS_ID} (moved by {ATLAS})")
    ]
    # a further refresh moves nothing and records nothing more
    await refresh(client, game_id)
    assert len(await id_history(game_id)) == 1


async def test_without_an_identity_verdict_a_changed_atlas_id_is_not_followed(
    owner, providers, client
):
    user_id, _ = owner
    providers(
        atlas_by_id(
            {OTHER_ATLAS_ID: MetadataPatch(title="Portal 2", provider_ids={"atlas": ATLAS_ID})}
        )
    )
    game_id = await add_game(
        user_id, title="Portal 2 (old)", provider_ids={"atlas": OTHER_ATLAS_ID}
    )

    result = await refresh(client, game_id)

    assert result["match"] == "none" and result["followed_ids"] == {}
    assert (await stored(game_id)).provider_ids == {"atlas": OTHER_ATLAS_ID}


async def test_a_live_id_another_game_holds_is_left_for_the_duplicate_review(
    owner, providers, client
):
    user_id, _ = owner
    providers(
        atlas_by_id(
            {
                OTHER_ATLAS_ID: MetadataPatch(
                    title="Portal 2",
                    provider_ids={"atlas": ATLAS_ID},
                    identity=MetadataIdentity(match="id", previous_ids={"atlas": OTHER_ATLAS_ID}),
                )
            }
        )
    )
    game_id = await add_game(user_id, provider_ids={"atlas": OTHER_ATLAS_ID})
    other_id = await add_game(user_id, title="Portal 2", provider_ids={"atlas": ATLAS_ID})

    result = await refresh(client, game_id)

    assert result["status"] == "updated"
    assert any("Review duplicates" in error for error in result["provider_errors"])
    assert (await stored(game_id)).provider_ids == {"atlas": OTHER_ATLAS_ID}
    assert (await stored(other_id)).provider_ids == {"atlas": ATLAS_ID}
    # the pair is in the duplicate review, though their atlas ids differ
    review = (await client.get("/api/game-duplicates")).json()
    assert [
        ({pair["first"]["id"], pair["second"]["id"]}, pair["reason"]) for pair in review["pairs"]
    ] == [({str(game_id), str(other_id)}, "shared_identity")]
    async with SessionLocal() as db:
        assert len((await game_duplicates.suggestions(db, user_id))["pairs"]) == 1
    # a second refresh remembers the merge once, and it is never an edge
    await refresh(client, game_id)
    assert await pending(user_id) == [(game_id, "same_game", "atlas", ATLAS_ID)]
    assert await edges(user_id) == []

    # the person keeps both: the pair leaves the review and nothing was merged
    kept = await client.post(
        "/api/game-duplicates/keep-both",
        json={"first_id": str(game_id), "second_id": str(other_id)},
    )
    assert kept.status_code == 204, kept.text
    assert (await client.get("/api/game-duplicates")).json()["pairs"] == []
    assert (await stored(game_id)).deleted_at is None
    assert (await stored(other_id)).deleted_at is None


async def test_a_followed_merge_clears_the_remembered_duplicate(owner, providers, client):
    user_id, _ = owner
    providers(
        atlas_by_id(
            {
                OTHER_ATLAS_ID: MetadataPatch(
                    title="Portal 2",
                    provider_ids={"atlas": ATLAS_ID},
                    identity=MetadataIdentity(match="id", previous_ids={"atlas": OTHER_ATLAS_ID}),
                )
            }
        )
    )
    game_id = await add_game(user_id, provider_ids={"atlas": OTHER_ATLAS_ID})
    other_id = await add_game(user_id, title="Portal 2", provider_ids={"atlas": ATLAS_ID})
    await refresh(client, game_id)
    assert len(await pending(user_id)) == 1

    # the other game goes to the trash; the next refresh follows the move
    async with SessionLocal() as db:
        (await db.get(Game, other_id)).deleted_at = 1
        await db.commit()
    assert (await client.get("/api/game-duplicates")).json()["pairs"] == []
    await refresh(client, game_id)

    assert (await stored(game_id)).provider_ids == {"atlas": ATLAS_ID}
    assert await pending(user_id) == []


# --- C4.4 rule 4: a conflict verdict applies nothing ------------------------------------------


async def test_an_identity_conflict_is_refused_with_both_candidates(owner, providers, client):
    user_id, _ = owner
    providers(
        steam(description="Store text"),
        atlas_for(
            "12345",
            provider_ids={"atlas": ATLAS_ID},
            developer="Valve",
            identity=MetadataIdentity(match="conflict"),
        ),
    )
    game_id = await add_game(
        user_id, provider_ids={"steam": "12345", "atlas": OTHER_ATLAS_ID}, description="Mine"
    )

    result = await refresh(client, game_id)

    assert (result["status"], result["match"]) == ("conflict", "conflict")
    assert result["conflicts"] == [
        {
            "namespace": "atlas",
            "candidates": sorted(
                [
                    {"value": ATLAS_ID, "sources": [ATLAS]},
                    {"value": OTHER_ATLAS_ID, "sources": ["saved"]},
                ],
                key=lambda entry: entry["value"],
            ),
        }
    ]
    game = await stored(game_id)
    assert (game.description, game.developer) == ("Mine", None)
    assert game.provider_ids == {"steam": "12345", "atlas": OTHER_ATLAS_ID}


def test_an_ambiguous_provider_is_no_evidence_and_an_older_one_is_unchanged():
    saved = {"steam": "1"}
    claims = {"core.steam": {"steam": ["1"]}, ATLAS: {"atlas": ["atlas:A"], "steam": ["2"]}}
    ambiguous = classify(
        saved,
        claims,
        game_title="G",
        reported_title="G",
        identities={ATLAS: {"match": "ambiguous", "previous_ids": {}}},
    )
    assert ambiguous.tier == "id" and "atlas" not in ambiguous.offered
    # without verdicts (a provider before 1.1.20) Atlas's steam claim is a conflict, as before
    assert classify(saved, claims, game_title="G", reported_title="G").tier == "conflict"


async def test_nothing_an_ambiguous_provider_reported_is_stored(owner, providers, client):
    user_id, _ = owner
    providers(
        steam(),
        atlas_for(
            "12345",
            developer="Someone Else",
            first_release=MetadataRelease(date="1999", precision="year"),
            releases=(MetadataRelease(date="1999", precision="year", platform="pc"),),
            relations=(relation("dlc_of", BASE_ATLAS_ID),),
            identity=MetadataIdentity(match="ambiguous"),
        ),
    )
    dlc_id = await add_game(user_id, provider_ids={"steam": "12345"})
    await add_game(user_id, title="Base Game", provider_ids={"atlas": BASE_ATLAS_ID})

    result = await refresh(client, dlc_id)

    # Steam confirms the game by id, so the refresh applies; only Steam's fields count
    assert (result["status"], result["match"]) == ("updated", "id")
    assert (result["relations_added"], result["relations_pending"]) == (0, 0)
    game = await stored(dlc_id)
    assert (game.developer, game.release_date, game.releases) == (None, None, None)
    assert game.provider_ids == {"steam": "12345"}
    assert await edges(user_id) == []
    assert await pending(user_id) == []


# --- C6.4: game relations ---------------------------------------------------------------------


@dataclass
class OlderPlugin(StandInProvider):
    """A stand-in for a plugin whose manifest declares Plugin API 1.1.19."""

    api_contract: str = "1.1.19"


async def test_relations_from_an_older_plugin_or_outside_the_related_group_are_not_ingested(
    owner, providers, client
):
    user_id, _ = owner

    def older(candidate):
        if candidate is None or candidate.provider_ids.get("steam") != "12345":
            return None
        return MetadataPatch(
            provider_ids={"atlas": ATLAS_ID}, relations=(relation("dlc_of", BASE_ATLAS_ID),)
        )

    providers(steam(), OlderPlugin(ATLAS, "atlas", older, priority=10))
    dlc_id = await add_game(user_id, provider_ids={"steam": "12345"})
    await add_game(user_id, title="Base Game", provider_ids={"atlas": BASE_ATLAS_ID})

    result = await refresh(client, dlc_id)
    assert result["status"] == "updated" and result["relations_added"] == 0
    assert await edges(user_id) == [] and await pending(user_id) == []

    recommended = relation("dlc_of", BASE_ATLAS_ID).model_copy(update={"group": "recommendation"})
    providers(steam(), atlas_for("12345", relations=(recommended,)))
    result = await refresh(client, dlc_id)
    assert result["relations_added"] == 0
    assert await edges(user_id) == [] and await pending(user_id) == []


async def test_a_waiting_relation_needs_one_target_and_a_referrer_still_in_the_library(
    owner, providers, client
):
    user_id, _ = owner
    providers(steam(), atlas_for("12345", relations=(relation("dlc_of", BASE_ATLAS_ID),)))
    dlc_id = await add_game(user_id, provider_ids={"steam": "12345"})
    await refresh(client, dlc_id)
    assert len(await pending(user_id)) == 1

    # two games hold the base id: which one is meant is the person's call
    providers(steam("777", title="Base Game"))
    base_id = await add_game(
        user_id, title="Base Game", provider_ids={"steam": "777", "atlas": BASE_ATLAS_ID}
    )
    copy_id = await add_game(
        user_id, title="Base Game (copy)", provider_ids={"atlas": BASE_ATLAS_ID}
    )
    result = await refresh(client, base_id)
    assert result["relations_added"] == 0
    assert await edges(user_id) == []
    assert len(await pending(user_id)) == 1

    # the copy is gone, but the referring game is in the trash: still no edge to it
    async with SessionLocal() as db:
        await db.execute(Game.__table__.delete().where(Game.id == copy_id))
        (await db.get(Game, dlc_id)).deleted_at = 1
        await db.commit()
    result = await refresh(client, base_id)
    assert result["relations_added"] == 0
    assert await edges(user_id) == []
    assert len(await pending(user_id)) == 1

    # restored from the trash, the reference resolves
    async with SessionLocal() as db:
        (await db.get(Game, dlc_id)).deleted_at = None
        await db.commit()
    result = await refresh(client, base_id)
    assert result["relations_added"] == 1
    assert await edges(user_id) == [("dlc_of", dlc_id, base_id, RecordOrigin.INGEST.value, True)]
    assert await pending(user_id) == []


async def test_a_reported_relation_becomes_an_ingest_edge_or_waits_for_its_game(
    owner, providers, client
):
    user_id, _ = owner
    providers(
        steam(),
        atlas_for(
            "12345",
            relations=(
                relation("dlc_of", BASE_ATLAS_ID),
                relation("adaptation_of", BASE_ATLAS_ID),  # not a game relation type
                relation("sequel", "atlas:SEQ"),  # anime vocabulary: ignored
            ),
        ),
    )
    dlc_id = await add_game(user_id, provider_ids={"steam": "12345"})

    first = await refresh(client, dlc_id)
    assert (first["relations_added"], first["relations_pending"]) == (0, 1)
    assert await pending(user_id) == [(dlc_id, "dlc_of", "atlas", BASE_ATLAS_ID)]
    assert await edges(user_id) == []

    base_id = await add_game(user_id, title="Base Game", provider_ids={"atlas": BASE_ATLAS_ID})
    second = await refresh(client, dlc_id)
    assert second["relations_added"] == 1
    assert await edges(user_id) == [("dlc_of", dlc_id, base_id, RecordOrigin.INGEST.value, True)]
    assert await pending(user_id) == []

    # reported again: still one edge
    await refresh(client, dlc_id)
    assert len(await edges(user_id)) == 1


async def test_a_waiting_relation_resolves_when_the_other_game_is_refreshed(
    owner, providers, client
):
    user_id, _ = owner
    dlc = atlas_for("12345", relations=(relation("expansion_of", BASE_ATLAS_ID),))
    providers(steam(), dlc)
    dlc_id = await add_game(user_id, provider_ids={"steam": "12345"})
    await refresh(client, dlc_id)
    assert len(await pending(user_id)) == 1

    # the base game is added later and gains its atlas id from its own refresh
    providers(
        steam("777", title="Base Game"),
        atlas_for("777", provider_ids={"atlas": BASE_ATLAS_ID}),
    )
    base_id = await add_game(user_id, title="Base Game", provider_ids={"steam": "777"})
    result = await refresh(client, base_id)

    assert result["relations_added"] == 1
    assert await edges(user_id) == [
        ("expansion_of", dlc_id, base_id, RecordOrigin.INGEST.value, True)
    ]
    assert await pending(user_id) == []


async def test_a_persons_own_edges_are_never_overridden(owner, providers, client):
    user_id, _ = owner
    providers(steam(), atlas_for("12345", relations=(relation("dlc_of", BASE_ATLAS_ID),)))
    dlc_id = await add_game(user_id, provider_ids={"steam": "12345"})
    base_id = await add_game(user_id, title="Base Game", provider_ids={"atlas": BASE_ATLAS_ID})
    async with SessionLocal() as db:
        mine = await graph.add_relationship(db, user_id, "related_to", base_id, dlc_id)
        mine_id = mine.id
        await db.commit()

    result = await refresh(client, dlc_id)

    assert result["relations_added"] == 0
    assert await edges(user_id) == [("related_to", base_id, dlc_id, "user", True)]

    # the person ends their edge and the ingest edge arrives; ending that one is final too
    async with SessionLocal() as db:
        await graph.end_relationship(db, user_id, mine_id)
        await db.commit()
    assert (await refresh(client, dlc_id))["relations_added"] == 1
    async with SessionLocal() as db:
        ingest = await db.scalar(
            select(GameRelationship).where(
                GameRelationship.user_id == user_id, GameRelationship.kind == "dlc_of"
            )
        )
        await graph.end_relationship(db, user_id, ingest.id)
        await db.commit()
    assert (await refresh(client, dlc_id))["relations_added"] == 0
    assert [edge for edge in await edges(user_id) if edge[4]] == []


async def test_a_relation_that_would_be_circular_is_dropped(owner, providers, client):
    user_id, _ = owner
    providers(steam(), atlas_for("12345", relations=(relation("dlc_of", BASE_ATLAS_ID),)))
    dlc_id = await add_game(user_id, provider_ids={"steam": "12345"})
    base_id = await add_game(user_id, title="Base Game", provider_ids={"atlas": BASE_ATLAS_ID})
    async with SessionLocal() as db:
        # an earlier report already says the base game is a DLC of this one
        await graph.add_relationship(
            db, user_id, "dlc_of", base_id, dlc_id, origin=RecordOrigin.INGEST.value
        )
        await db.commit()

    result = await refresh(client, dlc_id)

    assert result["status"] == "updated" and result["relations_added"] == 0
    assert await edges(user_id) == [("dlc_of", base_id, dlc_id, RecordOrigin.INGEST.value, True)]


# --- C7.2: crossplay from a provider ----------------------------------------------------------


async def test_provider_crossplay_is_stored_as_reported_and_read_as_both_shapes(
    owner, providers, client
):
    user_id, _ = owner
    providers(steam(players_min=1, players_max=4, crossplay="partial"))
    game_id = await add_game(user_id, provider_ids={"steam": "12345"})

    await refresh(client, game_id)

    game = await stored(game_id)
    assert game.play_compatibility == {
        "min_players": 1,
        "max_players": 4,
        "crossplay": "partial",
        "source": "provider",
    }
    read = (await client.get(f"/api/game/get/{game_id}")).json()["play_compatibility"]
    assert (read["crossplay"], read["crossplay_status"], read["source"]) == (
        True,
        "partial",
        "provider",
    )


# --- export and import keep the precision -----------------------------------------------------


async def test_an_export_restores_a_year_only_date_as_a_year(owner, client, monkeypatch):
    user_id, _ = owner
    monkeypatch.setattr(export_import, "create_game_folder", lambda *_args: None)
    releases = [
        MetadataRelease(date="2011", precision="year", platform="pc").model_dump(mode="json")
    ]
    await add_game(
        user_id,
        title="Year Only",
        release_date=date(2011, 1, 1),
        release_date_precision="year",
        releases=releases,
    )
    await add_game(user_id, title="A Day", release_date=date(2012, 3, 4))
    snapshot = (await client.get("/api/export/library")).json()
    async with SessionLocal() as db:
        await db.execute(Game.__table__.delete().where(Game.user_id == user_id))
        await db.commit()

    done = await client.post("/api/import/library", json=snapshot)

    assert done.status_code == 200 and done.json()["created"] == 2, done.text
    async with SessionLocal() as db:
        rows = {
            game.title: (game.release_date, game.release_date_precision, game.releases)
            for game in await db.scalars(select(Game).where(Game.user_id == user_id))
        }
    assert rows == {
        "Year Only": (date(2011, 1, 1), "year", releases),
        "A Day": (date(2012, 3, 4), None, None),
    }


async def test_an_import_keeps_only_releases_and_precision_a_refresh_would_store(
    owner, client, monkeypatch
):
    user_id, _ = owner
    monkeypatch.setattr(export_import, "create_game_folder", lambda *_args: None)
    good = {"date": "2011", "precision": "year", "platform": "pc"}
    games = [
        {
            "title": "Hand Edited",
            "folder_location": "hand-edited",
            "release_date": "2011-01-01",
            "release_date_precision": "year",
            "releases": [
                good,
                {**good, "date": "2011-05"},  # a month written under a year precision
                {"date": "1900", "precision": "year"},  # before 1950 (C5.1)
                {"date": "not-a-date", "precision": "day"},
                {**good, "date": "2012"},  # repeats (platform, region, edition, category)
            ],
        },
        # a year precision whose date is not 1 January: the date wins and is a day
        {
            "title": "Mid Year",
            "folder_location": "mid-year",
            "release_date": "2011-05-14",
            "release_date_precision": "year",
        },
        {
            "title": "Out Of Range",
            "folder_location": "out-of-range",
            "release_date": "1900-01-01",
            "release_date_precision": "year",
        },
        {
            "title": "A Month",
            "folder_location": "a-month",
            "release_date": "2011-04-01",
            "release_date_precision": "month",
        },
    ]

    done = await client.post("/api/import/library", json={"games": games})

    assert done.status_code == 200 and done.json()["created"] == 4, done.text
    async with SessionLocal() as db:
        rows = {
            game.title: (game.release_date, game.release_date_precision, game.releases)
            for game in await db.scalars(select(Game).where(Game.user_id == user_id))
        }
    expected = MetadataRelease.model_validate(good).model_dump(mode="json")
    assert rows == {
        "Hand Edited": (date(2011, 1, 1), "year", [expected]),
        "Mid Year": (date(2011, 5, 14), None, None),
        "Out Of Range": (date(1900, 1, 1), None, None),
        "A Month": (date(2011, 4, 1), "month", None),
    }


# --- server-built feeds never show a year or a month as a day (C5.5) ---------------------------


async def test_the_calendar_leaves_out_releases_known_only_to_the_year_or_month(owner, client):
    user_id, _ = owner
    today = date.today()
    ahead = date(today.year + 1, 1, 1)
    await add_game(user_id, title="Next Year", release_date=ahead, release_date_precision="year")
    await add_game(user_id, title="Next Month", release_date=ahead, release_date_precision="month")
    await add_game(user_id, title="That Day", release_date=ahead)
    async with SessionLocal() as db:
        upcoming = await media_extras.build_calendar_entries(
            db, user_id, 14, game_releases=True, window=(ahead, ahead)
        )
    assert [entry["title"] for entry in upcoming] == ["That Day"]

    # the past side: a year-only game is not "released on 1 January"
    last_year = date(today.year - 1, 1, 1)
    await add_game(
        user_id, title="Last Year", release_date=last_year, release_date_precision="year"
    )
    await add_game(user_id, title="Last Year Day", release_date=last_year)
    response = await client.get("/api/calendar/games")
    assert response.status_code == 200, response.text
    released = [entry["title"] for entry in response.json() if entry["kind"] == "game_released"]
    assert released == ["Last Year Day"]


async def test_release_notifications_skip_a_date_known_only_to_the_year(owner, monkeypatch):
    user_id, _ = owner
    day = date(2026, 1, 1)
    for title, precision in (("Year", "year"), ("Month", "month"), ("Day", None)):
        await add_game(
            user_id,
            title=title,
            status=GameStatus.WISHLIST,
            release_date=day,
            release_date_precision=precision,
        )
    announced: list[str] = []

    async def emit(db, event):
        announced.append((await db.get(Game, event.entity_id)).title)
        return event

    monkeypatch.setattr(game_notifications, "emit", emit)
    now = int(datetime(2026, 1, 3, 12, tzinfo=timezone.utc).timestamp())
    async with SessionLocal() as db:
        created = await game_notifications.generate_game_releases(db, user_id, now=now)
        assert announced == ["Day"] and created == 1
        # the interpreter refuses a year-only release too, should an event name one
        year_game = await db.scalar(
            select(Game).where(Game.user_id == user_id, Game.title == "Year")
        )
        event = NotificationEvent(
            type="game.released",
            user_id=user_id,
            entity_type="game",
            entity_id=year_game.id,
            occurred_at=int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()),
            dedupe_key=f"game-release:{year_game.id}:2026-01-01",
            data={},
        )
        assert await game_notifications.interpret_game_event(db, event) is None


# --- a library sync stores the same release a refresh does ------------------------------------


async def test_a_library_sync_stores_a_partial_first_release_with_its_precision(monkeypatch):
    record: dict = {
        "metadata": {
            "first_release": {"date": "2027", "precision": "year"},
            "releases": [{"date": "2027", "precision": "year", "platform": "pc"}],
        },
        "provider_ids": {},
    }

    async def resolve(*_args, **_kwargs):
        return record, None

    async def unclaimed(*_args, **_kwargs):
        return {}

    async def artwork(_game, _match, _max_bytes, outcome, **_kwargs):
        return outcome

    monkeypatch.setattr(library_sync, "resolve_library_record", resolve)
    monkeypatch.setattr(library_sync, "unclaimed_provider_ids", unclaimed)
    monkeypatch.setattr(library_sync, "_fetch_missing_artwork", artwork)
    user = SimpleNamespace(id=None)

    async def enrich(**values) -> Game:
        values.setdefault("locked_fields", [])
        game = Game(sort_title="synced", folder_location="synced", **values)
        # pylint: disable-next=protected-access
        await library_sync._enrich_new_game(game, user, {})
        return game

    game = await enrich(title="Synced")
    assert (game.release_date, game.release_date_precision) == (date(2027, 1, 1), "year")
    assert [entry["date"] for entry in game.releases] == ["2027"]

    # an older provider's day is stored as a day, and a locked date keeps its value
    record["metadata"] = {"release_date": "2027-03-04"}
    game = await enrich(title="Day")
    assert (game.release_date, game.release_date_precision) == (date(2027, 3, 4), None)
    record["metadata"] = {"first_release": {"date": "2027", "precision": "year"}}
    game = await enrich(
        title="Locked", locked_fields=["release_date"], release_date=date(2027, 3, 4)
    )
    assert (game.release_date, game.release_date_precision) == (date(2027, 3, 4), None)
