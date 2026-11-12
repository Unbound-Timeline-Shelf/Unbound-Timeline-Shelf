"""The game metadata refresh matches by provider id, not title (CONTRACTS-v1 C4.4, #463).

Every test drives the real refresh route, the real `resolve_library_record` and the real
metadata handler; only the providers are stand-ins. Results are read back from PostgreSQL."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, func, select

from src.api.routes import game_metadata
from src.core.auth import hash_password
from src.database.models.game import Game, GameStatus
from src.database.models.game_metadata_rejection import GameMetadataRejection
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.game_identity import decisions, graph
from src.features.game_identity.refresh_match import classify, vouched_ids
from src.features.imports.library_games import get_or_create_game
from src.features.metadata import service
from src.main import app
from src.plugin_api.metadata_contracts import (
    MetadataCandidate,
    MetadataPatch,
    MetadataProviderRegistration,
    ProviderHealth,
    ProviderOperations,
    ProviderResponse,
)

PASSWORD = "Test-password!9"
ATLAS_ID = "atlas:01JTESTPORTAL2RECORD000000"
OTHER_ATLAS_ID = "atlas:01JTESTOTHERRECORD00000000"

Responder = Callable[[MetadataCandidate | None], MetadataPatch | None]


@dataclass
class StandInProvider:
    """A provider that answers from the ids it is asked about, as Steam and Atlas do."""

    id: str
    namespace: str | None
    respond: Responder
    search_results: tuple[MetadataCandidate, ...] = ()
    priority: int = 100
    state: ProviderHealth = ProviderHealth.HEALTHY
    revision: str = "test"
    gate: asyncio.Event | None = None
    asked: list[dict] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.id

    @property
    def declaration(self) -> MetadataProviderRegistration:
        return MetadataProviderRegistration(
            provider_id=self.id,
            name=self.id,
            media_types=("game",),
            identifier_namespace=self.namespace,
            operations=ProviderOperations(
                search="search" if self.search_results else None,
                metadata="metadata",
                health="health",
            ),
        )

    async def invoke(self, operation, request, candidate=None):
        del request
        if operation == "search":
            return ProviderResponse(candidates=self.search_results)
        if operation == "metadata":
            self.asked.append(dict(candidate.provider_ids) if candidate else {})
            if self.gate is not None:
                await self.gate.wait()
            patch = self.respond(candidate)
            return ProviderResponse(metadata=patch) if patch else ProviderResponse()
        return ProviderResponse(health=ProviderHealth.HEALTHY)


class _AllAvailable:
    @staticmethod
    def available(provider):
        return provider


def steam(app_id: str = "12345", title: str = "Portal 2", **fields) -> StandInProvider:
    """Answers only for the Steam app id it is asked about, and reports that id back."""

    def respond(candidate):
        if candidate is None or candidate.provider_ids.get("steam") != app_id:
            return None
        return MetadataPatch(title=title, provider_ids={"steam": app_id}, **fields)

    return StandInProvider("core.steam", "steam", respond, priority=0)


def atlas(
    atlas_id: str = ATLAS_ID, *, provider_id: str = "official.metadata-atlas.atlas", **fields
) -> StandInProvider:
    """Resolves any linked Steam id to one Atlas record (as the plugin's /resolve does)."""

    def respond(candidate):
        if candidate is None or "steam" not in candidate.provider_ids:
            return None
        return MetadataPatch(provider_ids={"atlas": atlas_id}, **fields)

    return StandInProvider(provider_id, "atlas", respond, priority=10)


@pytest.fixture
async def owner():
    async with SessionLocal() as db:
        user = User(
            username=f"refresh_{uuid4().hex}",
            email=f"{uuid4()}@example.test",
            password_hash=hash_password(PASSWORD),
        )
        db.add(user)
        await db.commit()
        identity = user.id
        username = user.username
    yield identity, username
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id == identity))
        await db.commit()


@pytest.fixture
def providers(monkeypatch, tmp_path):
    """Install stand-in providers for every metadata lookup in the test."""
    monkeypatch.setattr(game_metadata, "_DATA_ROOT", tmp_path)
    monkeypatch.setattr(service, "monitor", _AllAvailable())
    installed: list[StandInProvider] = []

    async def discover(_db, _user_id, _media_type=None, _preferences=None):
        return list(installed)

    monkeypatch.setattr(service, "discover_providers", discover)

    def install(*items: StandInProvider) -> list[StandInProvider]:
        installed[:] = items
        return installed

    return install


async def add_game(user_id: UUID, **values) -> UUID:
    values.setdefault("title", "Steam app 12345")
    values.setdefault("sort_title", values["title"].lower())
    values.setdefault("folder_location", uuid4().hex)
    values.setdefault("locked_fields", [])
    async with SessionLocal() as db:
        game = Game(user_id=user_id, **values)
        db.add(game)
        await db.commit()
        return game.id


async def stored(game_id: UUID) -> Game:
    async with SessionLocal() as db:
        game = await db.get(Game, game_id)
        assert game is not None
        db.expunge(game)
        return game


async def game_count(user_id: UUID) -> int:
    async with SessionLocal() as db:
        return int(await db.scalar(select(func.count()).where(Game.user_id == user_id)) or 0)


@pytest.fixture
async def client(owner):
    _user_id, username = owner
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as http:
        login = await http.post(
            "/api/auth/login", json={"username_or_email": username, "password": PASSWORD}
        )
        assert login.status_code == 200, login.text
        yield http


async def refresh(client, game_id: UUID, **body) -> dict:
    body.setdefault("fill_missing_art", False)
    response = await client.post(f"/api/game/{game_id}/metadata/refresh", json=body)
    assert response.status_code == 200, response.text
    return response.json()


# --- rule 1: a shared provider id is the match; the title never gates it ---------------------


async def test_a_placeholder_title_refreshes_by_its_steam_id(owner, providers, client):
    user_id, _ = owner
    providers(steam(description="Portal 2 is a puzzle game."), atlas(developer="Valve"))
    game_id = await add_game(
        user_id,
        source="steam",
        external_id="12345",
        provider_ids={"steam": "12345"},
        playtime_seconds=7200,
        status=GameStatus.PLAYING,
    )
    before = await game_count(user_id)

    preview = await refresh(client, game_id, dry_run=True)
    assert (preview["status"], preview["match"]) == ("preview", "id")
    assert preview["candidate"] == {
        "title": "Portal 2",
        "provider_ids": {"steam": "12345", "atlas": ATLAS_ID},
    }
    assert (await stored(game_id)).title == "Steam app 12345"

    # Atlas confirmed none of the game's ids, so its id and fields wait for a confirmation
    assert preview["unverified_sources"] == {"official.metadata-atlas.atlas": {"atlas": [ATLAS_ID]}}
    assert "developer" not in preview["changed_fields"]

    applied = await refresh(client, game_id)
    assert (applied["status"], applied["match"]) == ("updated", "id")
    assert {"title", "description"} <= set(applied["changed_fields"])
    assert "developer" not in applied["changed_fields"]
    game = await stored(game_id)
    assert (game.title, game.description, game.developer) == (
        "Portal 2",
        "Portal 2 is a puzzle game.",
        None,
    )
    assert game.provider_ids == {"steam": "12345"}

    confirmed = await refresh(
        client, game_id, confirm_candidate=preview["candidate"]["provider_ids"]
    )
    assert (confirmed["status"], confirmed["match"]) == ("updated", "id")
    assert confirmed["unverified_sources"] == {}
    assert "developer" in confirmed["changed_fields"]

    game = await stored(game_id)
    assert game.title == "Portal 2"
    assert game.description == "Portal 2 is a puzzle game."
    assert game.developer == "Valve"
    # only ids a provider reported: never the request's echo (the "local" lookup identity)
    assert game.provider_ids == {"steam": "12345", "atlas": ATLAS_ID}
    # library and play history untouched, no new game
    assert (game.source, game.external_id, game.playtime_seconds, game.status) == (
        "steam",
        "12345",
        7200,
        GameStatus.PLAYING,
    )
    assert await game_count(user_id) == before


async def test_a_renamed_game_refreshes_by_its_unchanged_id_and_keeps_its_name(
    owner, providers, client
):
    user_id, _ = owner
    providers(steam(description="Store description", developer="Valve"))
    game_id = await add_game(
        user_id,
        title="Portal Two (my copy)",
        provider_ids={"steam": "12345"},
        description="My own notes on it",
        locked_fields=["title", "description"],
    )

    result = await refresh(client, game_id)

    assert (result["status"], result["match"]) == ("updated", "id")
    assert set(result["skipped_locked_fields"]) == {"title", "description"}
    game = await stored(game_id)
    assert game.title == "Portal Two (my copy)"
    assert game.description == "My own notes on it"
    assert game.developer == "Valve"
    assert game.locked_fields == ["title", "description"]


async def test_two_sources_reporting_the_same_id_agree(owner, providers, client):
    user_id, _ = owner
    providers(
        steam(),
        atlas(),
        atlas(provider_id="community.atlas-mirror", description="From the mirror"),
    )
    game_id = await add_game(user_id, provider_ids={"steam": "12345"})
    candidate = {"steam": "12345", "atlas": ATLAS_ID}

    result = await refresh(client, game_id, confirm_candidate=candidate)

    assert (result["status"], result["match"]) == ("updated", "id")
    assert result["candidate"]["provider_ids"] == candidate
    assert (await stored(game_id)).provider_ids == candidate


# --- an id match applies only what a confirming source reported (#492 review) ----------------


def title_fallback(
    provider_id: str, namespace: str, value: str, priority: int = 5, **fields
) -> StandInProvider:
    """A provider that cannot use the held ids and answers its own same-title game instead."""

    def respond(candidate):
        if candidate is None:
            return None
        return MetadataPatch(provider_ids={namespace: value}, **fields)

    return StandInProvider(provider_id, namespace, respond, priority=priority)


async def test_an_unconfirmed_provider_is_not_applied_with_an_id_match(owner, providers, client):
    """Steam confirms 12345; IGDB falls back to a different same-title game, igdb:73."""
    user_id, _ = owner
    providers(
        steam(),
        title_fallback("core.igdb", "igdb", "73", description="A different game entirely."),
    )
    game_id = await add_game(user_id, provider_ids={"steam": "12345"})

    preview = await refresh(client, game_id, dry_run=True)
    assert (preview["status"], preview["match"]) == ("preview", "id")
    assert preview["unverified_sources"] == {"core.igdb": {"igdb": ["73"]}}
    assert "description" not in preview["changed_fields"]

    result = await refresh(client, game_id)

    assert (result["status"], result["match"]) == ("updated", "id")
    assert result["unverified_sources"] == {"core.igdb": {"igdb": ["73"]}}
    assert "description" not in result["changed_fields"]
    game = await stored(game_id)
    assert (game.title, game.description) == ("Portal 2", None)
    assert game.provider_ids == {"steam": "12345"}


async def test_mixed_providers_apply_only_the_confirming_sources(owner, providers, client):
    """IGDB confirms its held id, a plugin without an identity verdict does not."""
    user_id, _ = owner

    def igdb_by_id(candidate):
        if candidate is None or candidate.provider_ids.get("igdb") != "72":
            return None
        return MetadataPatch(provider_ids={"igdb": "72"}, developer="Valve")

    providers(
        steam(),
        StandInProvider("core.igdb", "igdb", igdb_by_id, priority=20),
        title_fallback(
            "community.rawg", "rawg", "portal-2-fan-remake", publisher="Someone Else", priority=1
        ),
    )
    game_id = await add_game(user_id, provider_ids={"steam": "12345", "igdb": "72"})

    result = await refresh(client, game_id)

    assert (result["status"], result["match"]) == ("updated", "id")
    assert set(result["unverified_sources"]) == {"community.rawg"}
    game = await stored(game_id)
    assert (game.title, game.developer, game.publisher) == ("Portal 2", "Valve", None)
    assert game.provider_ids == {"steam": "12345", "igdb": "72"}


async def test_a_confirmed_candidate_applies_the_unconfirmed_sources_too(owner, providers, client):
    user_id, _ = owner
    providers(steam(), title_fallback("core.igdb", "igdb", "73", description="IGDB text"))
    game_id = await add_game(user_id, provider_ids={"steam": "12345"})
    preview = await refresh(client, game_id, dry_run=True)
    assert preview["candidate"]["provider_ids"] == {"steam": "12345", "igdb": "73"}

    # a confirmation of other ids is not a confirmation of this candidate
    partial = await refresh(client, game_id, confirm_candidate={"steam": "12345"})
    assert set(partial["unverified_sources"]) == {"core.igdb"}
    assert (await stored(game_id)).provider_ids == {"steam": "12345"}

    confirmed = await refresh(
        client, game_id, confirm_candidate=preview["candidate"]["provider_ids"]
    )

    assert (confirmed["status"], confirmed["unverified_sources"]) == ("updated", {})
    game = await stored(game_id)
    assert game.description == "IGDB text"
    assert game.provider_ids == {"steam": "12345", "igdb": "73"}


async def test_a_title_fallback_that_reports_no_id_is_not_applied(owner, providers, client):
    user_id, _ = owner

    def no_ids(candidate):
        return None if candidate is None else MetadataPatch(series="Wrong Series")

    providers(steam(), StandInProvider("community.rawg", "rawg", no_ids, priority=1))
    game_id = await add_game(user_id, provider_ids={"steam": "12345"})

    result = await refresh(client, game_id)

    assert result["unverified_sources"] == {"community.rawg": {}}
    assert (await stored(game_id)).series is None


# --- rule 4 (host side): conflicting ids are refused, nothing is applied ----------------------


async def test_two_sources_claiming_one_namespace_with_different_ids_is_a_conflict(
    owner, providers, client
):
    user_id, _ = owner
    providers(steam(), atlas(), atlas(OTHER_ATLAS_ID, provider_id="community.atlas-mirror"))
    game_id = await add_game(user_id, provider_ids={"steam": "12345"})
    before = await stored(game_id)

    result = await refresh(client, game_id)

    assert result["status"] == "conflict"
    assert result["changed_fields"] == []
    assert result["conflicts"] == [
        {
            "namespace": "atlas",
            "candidates": [
                # sorted by value
                {"value": OTHER_ATLAS_ID, "sources": ["community.atlas-mirror"]},
                {"value": ATLAS_ID, "sources": ["official.metadata-atlas.atlas"]},
            ],
        }
    ]
    after = await stored(game_id)
    assert (after.title, after.provider_ids, after.updated_at) == (
        before.title,
        {"steam": "12345"},
        before.updated_at,
    )


async def test_a_source_contradicting_a_saved_id_is_a_conflict(owner, providers, client):
    user_id, _ = owner

    def igdb_by_title(_candidate):
        return MetadataPatch(
            title="Portal 2", description="Wrong game", provider_ids={"igdb": "73"}
        )

    providers(steam(), StandInProvider("core.igdb", "igdb", igdb_by_title))
    game_id = await add_game(user_id, provider_ids={"steam": "12345", "igdb": "72"})

    preview = await refresh(client, game_id, dry_run=True)
    applied = await refresh(client, game_id)

    for result in (preview, applied):
        assert result["status"] == "conflict"
        assert result["conflicts"] == [
            {
                "namespace": "igdb",
                "candidates": [
                    {"value": "72", "sources": ["saved"]},
                    {"value": "73", "sources": ["core.igdb"]},
                ],
            }
        ]
    game = await stored(game_id)
    assert (game.title, game.description) == ("Steam app 12345", None)
    assert game.provider_ids == {"steam": "12345", "igdb": "72"}


async def test_a_different_atlas_id_is_not_adopted(owner, providers, client):
    """Atlas reports the live id after a merge; following it waits for Plugin API 1.1.20."""
    user_id, _ = owner
    providers(steam(), atlas(OTHER_ATLAS_ID, developer="Valve"))
    game_id = await add_game(user_id, provider_ids={"steam": "12345", "atlas": ATLAS_ID})

    automatic = await refresh(client, game_id)
    assert (automatic["status"], automatic["match"]) == ("updated", "id")
    assert set(automatic["unverified_sources"]) == {"official.metadata-atlas.atlas"}
    assert (await stored(game_id)).developer is None

    result = await refresh(
        client, game_id, confirm_candidate=automatic["candidate"]["provider_ids"]
    )

    assert (result["status"], result["match"]) == ("updated", "id")
    assert any("different atlas ID" in error for error in result["provider_errors"])
    game = await stored(game_id)
    assert game.provider_ids == {"steam": "12345", "atlas": ATLAS_ID}
    assert game.developer == "Valve"


async def test_an_id_another_game_holds_is_never_adopted(owner, providers, client):
    user_id, _ = owner
    providers(steam(), atlas())
    placeholder = await add_game(user_id, provider_ids={"steam": "12345"})
    holder = await add_game(user_id, title="Portal 2", provider_ids={"atlas": ATLAS_ID})

    result = await refresh(
        client, placeholder, confirm_candidate={"steam": "12345", "atlas": ATLAS_ID}
    )

    assert result["status"] == "updated"
    assert any("already linked to another game" in e for e in result["provider_errors"])
    assert (await stored(placeholder)).provider_ids == {"steam": "12345"}
    assert (await stored(holder)).provider_ids == {"atlas": ATLAS_ID}


# --- rule 3: a title-only match is preview-only until the person confirms it -----------------


def igdb_search() -> StandInProvider:
    found = MetadataCandidate(
        title="Celeste", provider="core.igdb", external_id="99", provider_ids={"igdb": "99"}
    )

    def respond(candidate):
        if candidate is None or candidate.provider_ids.get("igdb") != "99":
            return None
        return MetadataPatch(description="A climbing game.", provider_ids={"igdb": "99"})

    return StandInProvider("core.igdb", "igdb", respond, search_results=(found,))


async def test_a_game_without_ids_is_matched_by_title_for_preview_only(owner, providers, client):
    user_id, _ = owner
    providers(igdb_search())
    game_id = await add_game(user_id, title="Celeste")

    preview = await refresh(client, game_id, dry_run=True)
    assert (preview["status"], preview["match"]) == ("preview", "title")
    assert preview["changed_fields"] == ["description"]

    unconfirmed = await refresh(client, game_id)
    assert (unconfirmed["status"], unconfirmed["match"]) == ("needs-confirmation", "title")
    game = await stored(game_id)
    assert (game.description, game.provider_ids) == (None, {})

    # the old boolean flag no longer confirms anything
    flagged = await refresh(client, game_id, confirm_title_match=True)
    assert flagged["status"] == "needs-confirmation"
    # neither does a confirmation of some other candidate
    other = await refresh(client, game_id, confirm_candidate={"igdb": "100"})
    assert other["status"] == "needs-confirmation"
    assert other["candidate"] == {"title": "Celeste", "provider_ids": {"igdb": "99"}}
    assert any("changed after the preview" in error for error in other["provider_errors"])
    game = await stored(game_id)
    assert (game.description, game.provider_ids) == (None, {})

    confirmed = await refresh(
        client, game_id, confirm_candidate=preview["candidate"]["provider_ids"]
    )
    assert (confirmed["status"], confirmed["match"]) == ("updated", "title")
    game = await stored(game_id)
    assert (game.description, game.provider_ids) == ("A climbing game.", {"igdb": "99"})


async def test_a_title_match_that_changed_after_the_preview_is_not_applied(
    owner, providers, client
):
    """Two games share a title (a remake): the preview shows one, the apply finds the other."""
    user_id, _ = owner
    original = MetadataCandidate(
        title="Celeste", provider="core.igdb", external_id="99", provider_ids={"igdb": "99"}
    )
    remake = MetadataCandidate(
        title="Celeste", provider="core.igdb", external_id="500", provider_ids={"igdb": "500"}
    )
    descriptions = {"99": "A climbing game.", "500": "The remake."}

    def respond(candidate):
        identity = candidate.provider_ids.get("igdb") if candidate else None
        if identity not in descriptions:
            return None
        return MetadataPatch(description=descriptions[identity], provider_ids={"igdb": identity})

    igdb = StandInProvider("core.igdb", "igdb", respond, search_results=(original,))
    providers(igdb)
    game_id = await add_game(user_id, title="Celeste")

    preview = await refresh(client, game_id, dry_run=True)
    assert (preview["match"], preview["candidate"]["provider_ids"]) == ("title", {"igdb": "99"})

    # the provider now ranks the remake for the same title (a new revision: not a cached search)
    igdb.search_results = (remake,)
    igdb.revision = "test-reranked"
    applied = await refresh(client, game_id, confirm_candidate=preview["candidate"]["provider_ids"])

    assert (applied["status"], applied["match"]) == ("needs-confirmation", "title")
    assert applied["candidate"] == {"title": "Celeste", "provider_ids": {"igdb": "500"}}
    game = await stored(game_id)
    assert (game.description, game.provider_ids) == (None, {})
    assert await game_count(user_id) == 1

    # confirming what is now shown applies it
    confirmed = await refresh(
        client, game_id, confirm_candidate=applied["candidate"]["provider_ids"]
    )
    assert confirmed["status"] == "updated"
    game = await stored(game_id)
    assert (game.description, game.provider_ids) == ("The remake.", {"igdb": "500"})


async def test_a_provider_naming_two_ids_for_one_namespace_is_a_conflict(owner, providers, client):
    """A search candidate and the same provider's metadata patch disagree: nothing is adopted."""
    user_id, _ = owner
    found = MetadataCandidate(
        title="Celeste", provider="core.igdb", external_id="99", provider_ids={"igdb": "99"}
    )

    def respond(candidate):
        if candidate is None or "igdb" not in candidate.provider_ids:
            return None
        return MetadataPatch(description="Which one?", provider_ids={"igdb": "100"})

    providers(StandInProvider("core.igdb", "igdb", respond, search_results=(found,)))
    game_id = await add_game(user_id, title="Celeste")

    result = await refresh(client, game_id, confirm_candidate={"igdb": "99"})

    assert result["status"] == "conflict"
    assert result["conflicts"] == [
        {
            "namespace": "igdb",
            "candidates": [
                {"value": "100", "sources": ["core.igdb"]},
                {"value": "99", "sources": ["core.igdb"]},
            ],
        }
    ]
    game = await stored(game_id)
    assert (game.description, game.provider_ids) == (None, {})


async def test_a_title_that_differs_without_shared_ids_is_no_match(owner, providers, client):
    user_id, _ = owner

    def other_game(_candidate):
        return MetadataPatch(title="Portal", provider_ids={"igdb": "71"})

    providers(StandInProvider("core.igdb", "igdb", other_game))
    game_id = await add_game(user_id, title="Steam app 12345", provider_ids={"steam": "12345"})

    result = await refresh(client, game_id, confirm_candidate={"igdb": "71"})

    assert result["status"] == "no-match"
    assert (await stored(game_id)).provider_ids == {"steam": "12345"}


# --- explicit rejections are remembered --------------------------------------------------------


async def test_a_rejected_candidate_is_refused_until_the_rejection_is_forgotten(
    owner, providers, client
):
    user_id, _ = owner
    providers(steam(), atlas(developer="Valve"))
    game_id = await add_game(user_id, provider_ids={"steam": "12345"})
    preview = await refresh(client, game_id, dry_run=True)

    response = await client.post(
        f"/api/game/{game_id}/metadata/rejections",
        json={"provider_ids": preview["candidate"]["provider_ids"]},
    )
    assert response.status_code == 200, response.text
    # the id the game already holds is not a rejection
    assert response.json() == {"rejected": [{"namespace": "atlas", "identity": ATLAS_ID}]}
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(GameMetadataRejection.namespace, GameMetadataRejection.identity).where(
                    GameMetadataRejection.game_id == game_id
                )
            )
        ).all()
    assert rows == [("atlas", ATLAS_ID)]

    refused = await refresh(client, game_id)
    assert (refused["status"], refused["rejected_ids"]) == ("rejected", {"atlas": ATLAS_ID})
    game = await stored(game_id)
    assert (game.developer, game.provider_ids) == (None, {"steam": "12345"})

    held_only = await client.post(
        f"/api/game/{game_id}/metadata/rejections", json={"provider_ids": {"steam": "12345"}}
    )
    assert held_only.status_code == 422

    listed = await client.get(f"/api/game/{game_id}/metadata/rejections")
    assert listed.json() == {"rejected": [{"namespace": "atlas", "identity": ATLAS_ID}]}
    forgotten = await client.delete(f"/api/game/{game_id}/metadata/rejections")
    assert forgotten.status_code == 204
    async with SessionLocal() as db:
        remaining = await db.scalar(
            select(func.count()).where(GameMetadataRejection.game_id == game_id)
        )
    assert remaining == 0

    applied = await refresh(client, game_id, confirm_candidate=preview["candidate"]["provider_ids"])
    assert applied["status"] == "updated"
    assert (await stored(game_id)).provider_ids == {"steam": "12345", "atlas": ATLAS_ID}


async def test_one_rejection_can_be_forgotten_and_the_others_stay(owner, providers, client):
    del providers
    game_id = await add_game(owner[0], provider_ids={"steam": "12345"})
    rejections = f"/api/game/{game_id}/metadata/rejections"
    created = await client.post(rejections, json={"provider_ids": {"atlas": ATLAS_ID, "igdb": "7"}})
    assert created.status_code == 200, created.text

    half = await client.delete(rejections, params={"namespace": "atlas"})
    assert half.status_code == 422
    one = await client.delete(rejections, params={"namespace": "atlas", "identity": ATLAS_ID})
    assert one.status_code == 204

    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(GameMetadataRejection.namespace, GameMetadataRejection.identity).where(
                    GameMetadataRejection.game_id == game_id
                )
            )
        ).all()
    assert rows == [("igdb", "7")]


async def test_rejections_are_scoped_to_the_owner(owner, providers, client):
    del providers
    other = await add_game(owner[0], provider_ids={})
    async with SessionLocal() as db:
        stranger = User(
            username=f"stranger_{uuid4().hex}",
            email=f"{uuid4()}@example.test",
            password_hash=hash_password(PASSWORD),
        )
        db.add(stranger)
        await db.commit()
        stranger_id = stranger.id
    try:
        foreign = await add_game(stranger_id)
        response = await client.post(
            f"/api/game/{foreign}/metadata/rejections", json={"provider_ids": {"atlas": "x"}}
        )
        assert response.status_code == 404
        async with SessionLocal() as db:
            assert (
                await db.scalar(
                    select(func.count()).where(GameMetadataRejection.game_id.in_([foreign, other]))
                )
                == 0
            )
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id == stranger_id))
            await db.commit()


# --- merges, unmerges and repeated imports ----------------------------------------------------


async def test_reimport_after_merge_and_unmerge_never_duplicates_and_keeps_history(
    owner, providers, client
):
    user_id, _ = owner
    providers(steam(), atlas())
    imported = await add_game(
        user_id,
        source="steam",
        external_id="12345",
        provider_ids={"steam": "12345"},
        playtime_seconds=3600,
        status=GameStatus.PLAYING,
    )
    manual = await add_game(user_id, title="Portal 2", provider_ids={"atlas": ATLAS_ID})
    async with SessionLocal() as db:
        await decisions.merge_identities(db, user_id, imported, manual)
        await db.commit()

    async def reimport() -> tuple[UUID, bool]:
        async with SessionLocal() as db:
            game, created = await get_or_create_game(
                db, user_id, "Steam app 12345", "steam", "12345"
            )
            await db.commit()
            return game.id, created

    for _round in range(2):
        result = await refresh(client, imported)
        assert (result["status"], result["match"]) == ("updated", "id")
        assert await reimport() == (imported, False)

    async def shown_as(game_id: UUID) -> UUID:
        async with SessionLocal() as db:
            return await graph.resolve_identity(db, user_id, game_id)

    # the refreshes and re-imports left the merge as it was
    assert await shown_as(imported) == await shown_as(manual)

    async with SessionLocal() as db:
        await decisions.unmerge_identity(db, user_id, imported)
        await db.commit()
    assert await shown_as(imported) != await shown_as(manual)
    result = await refresh(client, imported)
    assert result["status"] == "updated"
    assert await reimport() == (imported, False)

    game = await stored(imported)
    # the placeholder never replaces the refreshed name, and the other game's id is not taken
    assert game.title == "Portal 2"
    assert game.provider_ids == {"steam": "12345"}
    assert (game.playtime_seconds, game.status) == (3600, GameStatus.PLAYING)
    assert (await stored(manual)).provider_ids == {"atlas": ATLAS_ID}
    assert await game_count(user_id) == 2


# --- a person's edit during the provider lookup wins ------------------------------------------


async def test_an_id_edited_during_the_lookup_is_the_one_compared(owner, providers, client):
    user_id, _ = owner
    gate = asyncio.Event()
    slow_steam = steam(description="Store text")
    slow_steam.gate = gate
    providers(slow_steam)
    game_id = await add_game(user_id, provider_ids={"steam": "12345"})

    pending = asyncio.create_task(refresh(client, game_id))
    async with asyncio.timeout(5):
        while not slow_steam.asked:
            await asyncio.sleep(0.01)
    async with SessionLocal() as db:
        game = await db.get(Game, game_id, with_for_update=True)
        game.provider_ids = {"steam": "54321"}
        await db.commit()
    gate.set()
    result = await asyncio.wait_for(pending, timeout=10)

    assert result["status"] == "conflict"
    game = await stored(game_id)
    assert (game.provider_ids, game.description) == ({"steam": "54321"}, None)


async def test_a_lock_set_during_the_lookup_is_respected(owner, providers, client):
    user_id, _ = owner
    gate = asyncio.Event()
    slow_steam = steam(description="Store text", developer="Valve")
    slow_steam.gate = gate
    providers(slow_steam)
    game_id = await add_game(user_id, provider_ids={"steam": "12345"})

    pending = asyncio.create_task(refresh(client, game_id))
    async with asyncio.timeout(5):
        while not slow_steam.asked:
            await asyncio.sleep(0.01)
    async with SessionLocal() as db:
        game = await db.get(Game, game_id, with_for_update=True)
        game.description = "Edited while the refresh ran"
        game.locked_fields = ["description"]
        await db.commit()
    gate.set()
    result = await asyncio.wait_for(pending, timeout=10)

    assert result["status"] == "updated"
    assert "description" in result["skipped_locked_fields"]
    game = await stored(game_id)
    assert (game.description, game.developer) == ("Edited while the refresh ran", "Valve")


# --- the rules on their own -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("saved", "claims", "reported", "tier"),
    [
        ({"steam": "1"}, {"core.steam": {"steam": "1"}}, "Anything", "id"),
        ({"steam": "1"}, {"core.steam": {"steam": "2"}}, "Same", "conflict"),
        ({"atlas": "a"}, {"atlas": {"atlas": "b"}}, "Same", "title"),
        ({"atlas": "a"}, {"atlas": {"atlas": "b"}}, "Other", "none"),
        ({"atlas": "a", "steam": "1"}, {"atlas": {"atlas": "b"}, "s": {"steam": "1"}}, None, "id"),
        ({}, {"core.igdb": {"igdb": "9"}}, "Same™", "title"),
        ({}, {}, None, "none"),
        ({}, {"x": {"atlas": "a"}, "y": {"atlas": "b"}}, "Same", "conflict"),
        # one source naming two ids for one namespace disagrees with itself
        ({}, {"x": {"igdb": ["9", "10"]}}, "Same", "conflict"),
        ({"igdb": "9"}, {"x": {"igdb": ["9", "10"]}}, "Same", "conflict"),
        ({"igdb": "9"}, {"x": {"igdb": ["9"]}}, None, "id"),
    ],
)
def test_classify(saved, claims, reported, tier):
    assert classify(saved, claims, game_title="Same", reported_title=reported).tier == tier


def test_vouched_ids_keep_only_ids_a_confirming_source_reported():
    claims = {"core.steam": {"steam": "1"}, "core.igdb": {"igdb": ["73"]}, "a": {"atlas": "x"}}
    offered = {"steam": "1", "igdb": "73", "atlas": "x"}
    assert vouched_ids(offered, claims, {"core.igdb", "a"}) == {"steam": "1"}
    assert vouched_ids(offered, claims, ()) == offered
    # a namespace another, confirming source also reported stays
    claims["core.steam"]["atlas"] = "x"
    assert vouched_ids(offered, claims, {"a"}) == offered


def test_classify_rejection_ignores_ids_the_game_holds():
    claims = {"core.steam": {"steam": "1"}, "atlas": {"atlas": "a"}}
    assert (
        classify({"steam": "1"}, claims, game_title="G", reported_title=None, rejected=[]).tier
        == "id"
    )
    assert (
        classify(
            {"steam": "1"},
            claims,
            game_title="G",
            reported_title=None,
            rejected=[("steam", "1")],
        ).tier
        == "id"
    )
    # refused although steam:1 confirms the game: the rejected source's fields are merged into
    # the same record, so none of it can be applied without applying the rejected candidate
    rejected = classify(
        {"steam": "1"}, claims, game_title="G", reported_title=None, rejected=[("atlas", "a")]
    )
    assert (rejected.tier, rejected.rejected) == ("rejected", {"atlas": "a"})


# --- id match: only corroborated sources apply automatically (#492 review) -------------------


def igdb_title_fallback(igdb_id: str = "73", **fields) -> StandInProvider:
    """Answers any lookup, as a provider that falls back to a title search does."""

    def respond(candidate):
        if candidate is None:
            return None
        return MetadataPatch(title="Portal 2", provider_ids={"igdb": igdb_id}, **fields)

    return StandInProvider("core.igdb", "igdb", respond, priority=5)


async def test_an_uncorroborated_provider_is_not_applied_on_an_id_match(owner, providers, client):
    """Steam confirms 12345; IGDB, looked up by title, returns a different same-title game."""
    user_id, _ = owner
    providers(steam(developer="Valve"), igdb_title_fallback(description="Another game entirely."))
    game_id = await add_game(user_id, provider_ids={"steam": "12345"})

    preview = await refresh(client, game_id, dry_run=True)
    assert (preview["status"], preview["match"]) == ("preview", "id")
    assert preview["unverified_sources"] == {"core.igdb": {"igdb": ["73"]}}
    assert "description" not in preview["changed_fields"]

    applied = await refresh(client, game_id)
    assert (applied["status"], applied["match"]) == ("updated", "id")
    assert applied["unverified_sources"] == {"core.igdb": {"igdb": ["73"]}}
    game = await stored(game_id)
    assert game.provider_ids == {"steam": "12345"}
    assert game.description is None
    assert (game.title, game.developer) == ("Portal 2", "Valve")


async def test_confirming_the_candidate_applies_the_uncorroborated_provider(
    owner, providers, client
):
    user_id, _ = owner
    providers(steam(), igdb_title_fallback(description="The IGDB description."))
    game_id = await add_game(user_id, provider_ids={"steam": "12345"})

    preview = await refresh(client, game_id, dry_run=True)
    applied = await refresh(client, game_id, confirm_candidate=preview["candidate"]["provider_ids"])

    assert applied["status"] == "updated" and applied["unverified_sources"] == {}
    game = await stored(game_id)
    assert game.provider_ids == {"steam": "12345", "igdb": "73"}
    assert game.description == "The IGDB description."


async def test_a_provider_confirming_a_held_id_is_applied(owner, providers, client):
    user_id, _ = owner
    providers(steam(), igdb_title_fallback(description="Confirmed by its own id."))
    game_id = await add_game(user_id, provider_ids={"steam": "12345", "igdb": "73"})

    applied = await refresh(client, game_id)

    assert applied["status"] == "updated" and applied["unverified_sources"] == {}
    assert (await stored(game_id)).description == "Confirmed by its own id."
