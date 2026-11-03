"""Game relations reported by metadata providers (Plugin API 1.1.20, CONTRACTS-v1 C6.4).

A refresh that reports "this game is a DLC of X" records an edge in ``game_relationships``
with origin ``ingest`` when the person tracks X (found by the provider ids that name it). When
they do not, the reference waits in ``game_pending_relationships`` and becomes an edge on a
later refresh, of this game or of X.

A person's own decisions always win:

* an edge is never added between two games the person already related in any way (an active
  edge of any kind or direction made by them is their answer for that pair);
* an edge of the same kind that was ended (the person removed it) is not added again;
* an edge is never ended because a provider stopped reporting it.

Series and collection membership point at an umbrella, which provider ids cannot name yet, so
those references only wait. Everything stored here is re-derived by the next refresh.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.base import unix_timestamp
from src.database.models.game import Game
from src.database.models.game_identity import GameRelationship, RecordOrigin
from src.database.models.game_pending_relationship import (
    PENDING_RELATIONS_PER_GAME,
    GamePendingRelationship,
)
from src.features.game_identity import graph
from src.features.game_identity.provider_ids import find_provider_id_conflicts
from src.plugin_api.vocab import RELATION_TYPES

MEMBERSHIP_KINDS = frozenset({"series_member", "collection_member"})


async def _pair_is_decided(db: AsyncSession, user_id: UUID, kind: str, a: UUID, b: UUID) -> bool:
    """Whether the person already answered for this pair, or removed this very edge."""
    between = or_(
        and_(GameRelationship.from_game_id == a, GameRelationship.to_game_id == b),
        and_(GameRelationship.from_game_id == b, GameRelationship.to_game_id == a),
    )
    found = await db.scalar(
        select(GameRelationship.id)
        .where(
            GameRelationship.user_id == user_id,
            between,
            or_(
                # anything the person made between the two games, in either direction
                and_(
                    GameRelationship.ended_at.is_(None),
                    GameRelationship.origin != RecordOrigin.INGEST.value,
                ),
                # the same edge, still active or ended (removed) — never added twice
                and_(
                    GameRelationship.kind == kind,
                    GameRelationship.from_game_id == a,
                    GameRelationship.to_game_id == b,
                ),
            ),
        )
        .limit(1)
    )
    return found is not None


async def _link(
    db: AsyncSession, user_id: UUID, kind: str, from_id: UUID, to_id: UUID
) -> str | None:
    """Add one ingest edge. Returns "added", or None when nothing was added."""
    if from_id == to_id or await _pair_is_decided(db, user_id, kind, from_id, to_id):
        return None
    try:
        await graph.add_relationship(
            db, user_id, kind, from_id, to_id, origin=RecordOrigin.INGEST.value
        )
    except (graph.IdentityError, LookupError):
        # a provider cannot make the person's graph circular; the reference is dropped
        return None
    return "added"


async def _targets(
    db: AsyncSession, user_id: UUID, game_id: UUID, ids: Mapping[str, str]
) -> set[UUID]:
    found = await find_provider_id_conflicts(db, user_id, game_id, dict(ids))
    return {target for _namespace, _identity, target in found}


async def _remember(
    db: AsyncSession, game: Game, kind: str, ids: Mapping[str, str], now: int
) -> int:
    rows = [
        {
            "user_id": game.user_id,
            "game_id": game.id,
            "kind": kind,
            "namespace": namespace,
            "external_id": external_id,
            "first_seen_at": now,
            "last_seen_at": now,
        }
        for namespace, external_id in sorted(ids.items())
    ]
    statement = insert(GamePendingRelationship).values(rows)
    await db.execute(
        statement.on_conflict_do_update(
            constraint="uq_game_pending_relationships_target",
            set_={"last_seen_at": statement.excluded.last_seen_at},
        )
    )
    return len(rows)


async def _trim(db: AsyncSession, game_id: UUID) -> None:
    """Keep the most recently reported references of a game, at most the bound."""
    keep = (
        select(GamePendingRelationship.id)
        .where(GamePendingRelationship.game_id == game_id)
        .order_by(GamePendingRelationship.last_seen_at.desc(), GamePendingRelationship.id)
        .limit(PENDING_RELATIONS_PER_GAME)
    )
    await db.execute(
        delete(GamePendingRelationship).where(
            GamePendingRelationship.game_id == game_id,
            GamePendingRelationship.id.not_in(keep.scalar_subquery()),
        )
    )


async def _forget(db: AsyncSession, game_id: UUID, kind: str, ids: Mapping[str, str]) -> None:
    if ids:
        await db.execute(
            delete(GamePendingRelationship).where(
                GamePendingRelationship.game_id == game_id,
                GamePendingRelationship.kind == kind,
                tuple_(GamePendingRelationship.namespace, GamePendingRelationship.external_id).in_(
                    list(ids.items())
                ),
            )
        )


async def _resolve_waiting(db: AsyncSession, game: Game) -> int:
    """References other games made to this one, now that it holds the ids they named."""
    held = [(namespace, value) for namespace, value in (game.provider_ids or {}).items() if value]
    if not held:
        return 0
    waiting = list(
        await db.scalars(
            select(GamePendingRelationship).where(
                GamePendingRelationship.user_id == game.user_id,
                GamePendingRelationship.game_id != game.id,
                GamePendingRelationship.kind.not_in(MEMBERSHIP_KINDS),
                tuple_(GamePendingRelationship.namespace, GamePendingRelationship.external_id).in_(
                    held
                ),
            )
        )
    )
    added = 0
    for row in waiting:
        if await _link(db, game.user_id, row.kind, row.game_id, game.id):
            added += 1
        await db.delete(row)
    return added


async def ingest_relations(
    db: AsyncSession, game: Game, relations: Iterable[Mapping[str, Any]]
) -> dict[str, int]:
    """Record the relations a refresh reported for ``game``; returns what changed.

    Each relation is ``{"kind": <vocabulary type>, "targets": {namespace: id}}``, as
    ``provider_reports.provider_relations`` builds them. The caller holds the game's row lock."""
    summary = {"added": 0, "pending": 0}
    now = unix_timestamp()
    for relation in relations:
        kind, ids = str(relation.get("kind")), dict(relation.get("targets") or {})
        if kind not in RELATION_TYPES or not ids:
            continue
        targets = (
            set() if kind in MEMBERSHIP_KINDS else await _targets(db, game.user_id, game.id, ids)
        )
        if len(targets) == 1:
            (target,) = targets
            if await _link(db, game.user_id, kind, game.id, target):
                summary["added"] += 1
            await _forget(db, game.id, kind, ids)
        elif not targets:
            summary["pending"] += await _remember(db, game, kind, ids, now)
        # several tracked games hold the ids: which one is meant is the person's call
    if summary["pending"]:
        await _trim(db, game.id)
    summary["added"] += await _resolve_waiting(db, game)
    return summary
