"""A metadata refresh with Plugin API 1.1.20 data: partial release dates, releases, followed
merges and redirects (C4.4 rules 2 and 4), game relations (C6.4) and three-state crossplay.

Every test drives the real refresh route and metadata handler with stand-in providers, and reads
the result back from PostgreSQL."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy import select

from src.database.models.game_identity import GameRelationship, RecordOrigin
from src.database.models.game_pending_relationship import GamePendingRelationship
from src.database.session import SessionLocal
from src.features.game_identity import graph
from src.features.game_identity.refresh_match import classify
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


# --- C6.4: game relations ---------------------------------------------------------------------


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
