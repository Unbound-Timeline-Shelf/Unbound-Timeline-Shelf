"""Whether a metadata refresh result is the game being refreshed (CONTRACTS-v1 C4.4).

The refresh used to apply a result only when its title equalled the saved title, so a renamed
game, or one with a placeholder title such as "Steam app 12345", never refreshed (#463). The
decision now rests on the provider ids the result's sources reported themselves, never on ids
the request supplied (a record echoes those back):

1. A source reported an id the game already holds: the result is the game (`id`). The title
   is not consulted. An `atlas` id that differs from the saved one is not evidence either way:
   Atlas reports the live id after a merge, and following it needs Plugin API 1.1.20 (rule 2).
2. (Plugin API 1.1.20, not implemented here: follow an Atlas merge or redirect by id.)
3. No reported id matches the game, but a provider reported the game's exact title: `title`.
   It may be previewed, and is applied only when the person confirms it.
4. A source contradicts an id the game holds, or two sources disagree about one namespace:
   `conflict`. Nothing is applied.

A result that reports an id the person rejected for this game is `rejected`, even when another
reported id confirms the game: the record merges every source's fields, so the rejected source's
values cannot be told apart from the rest, and applying any of it would apply the rejected
candidate's data. Forgetting that one rejection lets the next refresh apply again.

A title-only match is confirmed by the candidate's ids, not by a flag: the route applies it only
when the person's confirmation equals `offered` for the lookup it just ran.

Within an id match, a provider that reported only ids the game does not hold (or none) may
have looked the game up by title and found another game. Its fields and ids are not applied
automatically: only the confirming sources' fields and the ids one of them reported
(`vouched_ids`) are. The same confirmation as a title-only match (`offered`) applies the
whole result.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.titles import normalize_metadata_title
from src.database.models.game import Game
from src.database.models.game_metadata_rejection import GameMetadataRejection

ATLAS = "atlas"
SAVED = "saved"

Tier = Literal["id", "title", "conflict", "rejected", "none"]


@dataclass(frozen=True)
class RefreshMatch:
    """How a refresh result relates to the game, and the ids it may offer."""

    tier: Tier
    # every id the sources reported, when they agree with each other
    offered: dict[str, str] = field(default_factory=dict)
    # the reported ids the game already holds (rule 1 evidence)
    shared: dict[str, str] = field(default_factory=dict)
    # namespace -> {value: [sources]}; "saved" is the game's own value
    conflicts: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    rejected: dict[str, str] = field(default_factory=dict)

    @property
    def applies_without_confirmation(self) -> bool:
        return self.tier == "id"

    def conflict_candidates(self) -> list[dict]:
        """Both (or all) sides of every disagreement, for the person to settle."""
        return [
            {
                "namespace": namespace,
                "candidates": [
                    {"value": value, "sources": sorted(sources)}
                    for value, sources in sorted(values.items())
                ],
            }
            for namespace, values in sorted(self.conflicts.items())
        ]


# a source's claim for one namespace: one value, or every value it reported when it disagreed
# with itself (for example a search candidate and a metadata patch naming different ids)
Claim = str | Sequence[str]


def _reported(claims: Mapping[str, Mapping[str, Claim]]) -> dict[str, dict[str, list[str]]]:
    values: dict[str, dict[str, list[str]]] = {}
    for source, ids in sorted(claims.items()):
        for namespace, claim in ids.items():
            for value in [claim] if isinstance(claim, str) else claim:
                if value:
                    sources = values.setdefault(namespace, {}).setdefault(str(value), [])
                    if source not in sources:
                        sources.append(source)
    return values


def classify(
    saved: Mapping[str, str],
    claims: Mapping[str, Mapping[str, Claim]],
    *,
    game_title: str,
    reported_title: str | None,
    rejected: Iterable[tuple[str, str]] = (),
) -> RefreshMatch:
    """Apply the C4.4 rules to one refresh result. Pure; the caller supplies the evidence."""
    reported = _reported(claims)
    conflicts: dict[str, dict[str, list[str]]] = {}
    offered: dict[str, str] = {}
    shared: dict[str, str] = {}
    for namespace, values in reported.items():
        if len(values) > 1:
            # two sources disagree about the same namespace
            conflicts[namespace] = {value: list(sources) for value, sources in values.items()}
            if namespace in saved:
                conflicts[namespace].setdefault(saved[namespace], []).append(SAVED)
            continue
        ((value, sources),) = values.items()
        offered[namespace] = value
        held = saved.get(namespace)
        if held == value:
            shared[namespace] = value
        elif held is not None and namespace != ATLAS:
            conflicts[namespace] = {held: [SAVED], value: list(sources)}
    if conflicts:
        return RefreshMatch("conflict", offered, shared, conflicts)
    refused = {
        namespace: value
        for namespace, value in offered.items()
        if saved.get(namespace) != value and (namespace, value) in set(rejected)
    }
    if refused:
        return RefreshMatch("rejected", offered, shared, rejected=refused)
    if shared:
        return RefreshMatch("id", offered, shared)
    if reported_title and normalize_metadata_title(reported_title) == normalize_metadata_title(
        game_title
    ):
        return RefreshMatch("title", offered)
    return RefreshMatch("none", offered)


def vouched_ids(
    offered: Mapping[str, str],
    claims: Mapping[str, Mapping[str, Claim]],
    unverified: Iterable[str],
) -> dict[str, str]:
    """The offered ids that at least one source outside ``unverified`` reported itself."""
    excluded = set(unverified)
    vouched: dict[str, str] = {}
    for namespace, value in offered.items():
        for source, ids in claims.items():
            claim = ids.get(namespace)
            if source in excluded or not claim:
                continue
            if value in ([claim] if isinstance(claim, str) else claim):
                vouched[namespace] = value
                break
    return vouched


async def rejected_ids(db: AsyncSession, game_id: UUID) -> set[tuple[str, str]]:
    rows = await db.execute(
        select(GameMetadataRejection.namespace, GameMetadataRejection.identity).where(
            GameMetadataRejection.game_id == game_id
        )
    )
    return {(str(namespace), str(identity)) for namespace, identity in rows}


async def reject(db: AsyncSession, game: Game, provider_ids: Mapping[str, str]) -> dict[str, str]:
    """Remember that these ids are not this game. Ids the game holds are not stored."""
    held = game.provider_ids or {}
    new = {
        namespace: value
        for namespace, value in provider_ids.items()
        if value and held.get(namespace) != value
    }
    if new:
        await db.execute(
            insert(GameMetadataRejection)
            .values(
                [
                    {
                        "user_id": game.user_id,
                        "game_id": game.id,
                        "namespace": namespace,
                        "identity": value,
                    }
                    for namespace, value in sorted(new.items())
                ]
            )
            .on_conflict_do_nothing(constraint="uq_game_metadata_rejections_game_identity")
        )
    return new


async def forget_rejections(
    db: AsyncSession, game_id: UUID, *, only: tuple[str, str] | None = None
) -> int:
    """Forget every rejection for the game, or only the one `(namespace, identity)`."""
    statement = delete(GameMetadataRejection).where(GameMetadataRejection.game_id == game_id)
    if only is not None:
        statement = statement.where(
            GameMetadataRejection.namespace == only[0],
            GameMetadataRejection.identity == only[1],
        )
    result = await db.execute(statement)
    return int(getattr(result, "rowcount", 0) or 0)
