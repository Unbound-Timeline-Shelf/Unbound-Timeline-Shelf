"""Whether a metadata refresh result is the game being refreshed (CONTRACTS-v1 C4.4).

The refresh used to apply a result only when its title equalled the saved title, so a renamed
game, or one with a placeholder title such as "Steam app 12345", never refreshed (#463). The
decision now rests on the provider ids the result's sources reported themselves, never on ids
the request supplied (a record echoes those back):

1. A source reported an id the game already holds: the result is the game (`id`). The title
   is not consulted. An `atlas` id that differs from the saved one is not evidence either way:
   Atlas reports the live id after a merge, and only rule 2 follows it.
2. (Plugin API 1.1.20) A provider's `identity` says it matched by id, redirect or crosswalk, and
   its `previous_ids` holds the id the game has saved in that namespace: the result is the game
   (`id`), and the saved id is to be rewritten to the live one the provider reported
   (`followed`). A merge or redirect is followed by id, never by title, and only in the
   provider's own namespaces (they are confined before the response reaches the host).
3. No reported id matches the game, but a provider reported the game's exact title: `title`.
   It may be previewed, and is applied only when the person confirms it.
4. A source contradicts an id the game holds, two sources disagree about one namespace, or a
   provider's `identity.match` is `conflict` (Plugin API 1.1.20): `conflict`. Nothing is applied.

A provider whose `identity.match` is `ambiguous` named several possible games, so the ids it
reported are not evidence of anything and are left out of the decision.

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
    # rule 2: namespace -> {"from": saved id, "to": live id, "provider": who reported the move}
    followed: dict[str, dict[str, str]] = field(default_factory=dict)

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


FOLLOWED_MATCHES = frozenset({"id", "redirect", "crosswalk"})


def _identity_conflicts(
    saved: Mapping[str, str],
    claims: Mapping[str, Mapping[str, Claim]],
    identities: Mapping[str, Mapping[str, object]],
) -> dict[str, dict[str, list[str]]]:
    """Rule 4: what each provider that answered `conflict` reported, beside the saved ids."""
    conflicts: dict[str, dict[str, list[str]]] = {}
    for provider, identity in sorted(identities.items()):
        if identity.get("match") != "conflict":
            continue
        reported = _reported({provider: claims.get(provider, {})})
        if not reported:
            conflicts.setdefault("identity", {}).setdefault("conflict", []).append(provider)
        for namespace, values in reported.items():
            entry = conflicts.setdefault(namespace, {})
            for value, sources in values.items():
                entry.setdefault(value, []).extend(sources)
            if namespace in saved:
                entry.setdefault(saved[namespace], [SAVED])
    return conflicts


def _followed(
    saved: Mapping[str, str],
    claims: Mapping[str, Mapping[str, Claim]],
    identities: Mapping[str, Mapping[str, object]],
) -> dict[str, dict[str, str]]:
    """Rule 2: saved ids a provider says it moved from, and the one live id it now reports."""
    moves: dict[str, dict[str, str]] = {}
    for provider, identity in sorted(identities.items()):
        previous = identity.get("previous_ids")
        if identity.get("match") not in FOLLOWED_MATCHES or not isinstance(previous, Mapping):
            continue
        reported = _reported({provider: claims.get(provider, {})})
        for namespace, old in previous.items():
            live = list(reported.get(namespace, {}))
            if saved.get(namespace) == old and len(live) == 1 and live[0] != old:
                moves.setdefault(namespace, {"from": str(old), "to": live[0], "provider": provider})
    return moves


def _apply_verdicts(
    saved: Mapping[str, str],
    claims: Mapping[str, Mapping[str, Claim]],
    identities: Mapping[str, Mapping[str, object]],
) -> tuple[dict[str, Mapping[str, Claim]], RefreshMatch | dict[str, dict[str, str]]]:
    """The claims that count (an ambiguous provider's do not), and either the rule 4 conflict
    or the ids rule 2 follows."""
    counted = {
        provider: ids
        for provider, ids in claims.items()
        if (identities.get(provider) or {}).get("match") != "ambiguous"
    }
    conflicts = _identity_conflicts(saved, counted, identities)
    if conflicts:
        agreed = {
            namespace: next(iter(values))
            for namespace, values in _reported(counted).items()
            if len(values) == 1
        }
        return counted, RefreshMatch("conflict", agreed, conflicts=conflicts)
    return counted, _followed(saved, counted, identities)


def _compare(
    saved: Mapping[str, str], reported: Mapping[str, Mapping[str, list[str]]]
) -> tuple[dict[str, str], dict[str, str], dict[str, dict[str, list[str]]]]:
    """Rules 1 and 4 on the reported ids: what is offered, shared with the game, or disputed."""
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
    return offered, shared, conflicts


def classify(
    saved: Mapping[str, str],
    claims: Mapping[str, Mapping[str, Claim]],
    *,
    game_title: str,
    reported_title: str | None,
    rejected: Iterable[tuple[str, str]] = (),
    identities: Mapping[str, Mapping[str, object]] | None = None,
) -> RefreshMatch:
    """Apply the C4.4 rules to one refresh result. Pure; the caller supplies the evidence.

    ``identities`` holds each provider's Plugin API 1.1.20 identity verdict (``match`` and
    ``previous_ids``); an older provider sends none, and rules 1, 3 and 4 then work as before."""
    claims, verdict = _apply_verdicts(saved, claims, identities or {})
    if isinstance(verdict, RefreshMatch):
        return verdict
    followed = verdict
    saved = {**saved, **{namespace: move["to"] for namespace, move in followed.items()}}
    offered, shared, conflicts = _compare(saved, _reported(claims))
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
        return RefreshMatch("id", offered, shared, followed=followed)
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
