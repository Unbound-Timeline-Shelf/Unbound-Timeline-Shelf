"""Explicit play logs, kept apart from lifetime playtime corrections (#467).

Two user actions look alike but mean different things:

- **Log play** says "I played this" now, or at an earlier time the user picks. It records a
  `game_play_events` row and moves the game's `last_played_at` forward to that moment, so Home
  Continue, the weekly digest and the group overview (which takes the latest member) see it.
- **Correct total playtime** (`counters.adjust_total`) fixes a lifetime number. It never
  touches `last_played_at`: fixing an old total, or a provider reporting a larger lifetime
  total on its next sync, is not evidence of play today.

A log never moves `last_played_at` backwards: logging an older session after a newer one
keeps the newer time. A retried request with the same `request_id` returns the first event
instead of recording the play twice. See wiki/docs/development/game-identity-manual.md.
"""

from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game import Game
from src.database.models.game_identity_tracking import GamePlayEvent
from src.features.game_identity import graph

# A browser clock a few minutes ahead of the server is still "now"; anything later is a typo.
FUTURE_TOLERANCE_SECONDS = 5 * 60
RECENT_LIMIT = 20


def event_out(event: GamePlayEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "game_id": event.game_id,
        "played_at": event.played_at,
        "recorded_at": event.recorded_at,
    }


async def _live_game(db: AsyncSession, user_id: UUID, game_id: UUID) -> Game:
    game = await db.scalar(
        select(Game).where(Game.id == game_id, Game.user_id == user_id).with_for_update()
    )
    if game is None or game.deleted_at is not None:
        raise LookupError("Game not found")
    return game


async def log_play(
    db: AsyncSession,
    user_id: UUID,
    game_id: UUID,
    played_at: int | None = None,
    request_id: str | None = None,
    *,
    now: int | None = None,
) -> dict[str, Any]:
    """Record one play of a game and move its last played time forward; the caller commits.

    The game row is locked first, so two logs of the same game (two tabs, a retry racing the
    original) serialise and the larger time wins."""
    stamp = int(time.time()) if now is None else now
    when = stamp if played_at is None else played_at
    if when < 0:
        raise graph.IdentityInvalid("The play time cannot be before 1970.")
    if when > stamp + FUTURE_TOLERANCE_SECONDS:
        raise graph.IdentityInvalid("The play time cannot be in the future.")
    game = await _live_game(db, user_id, game_id)
    if request_id is not None:
        earlier = await db.scalar(
            select(GamePlayEvent).where(
                GamePlayEvent.user_id == user_id, GamePlayEvent.request_id == request_id
            )
        )
        if earlier is not None:
            if earlier.game_id != game_id:
                raise graph.IdentityConflict("This request was already used for another game.")
            return {"event": event_out(earlier), "last_played_at": game.last_played_at}
    event = GamePlayEvent(
        user_id=user_id,
        game_id=game_id,
        played_at=min(when, stamp),
        recorded_at=stamp,
        request_id=request_id,
    )
    db.add(event)
    if game.last_played_at is None or event.played_at > game.last_played_at:
        game.last_played_at = event.played_at
    await db.flush()
    return {"event": event_out(event), "last_played_at": game.last_played_at}


async def recent_plays(db: AsyncSession, user_id: UUID, game_id: UUID) -> list[dict[str, Any]]:
    """The latest logged plays of one game, newest first."""
    game = await db.scalar(select(Game).where(Game.id == game_id, Game.user_id == user_id))
    if game is None or game.deleted_at is not None:
        raise LookupError("Game not found")
    rows = await db.scalars(
        select(GamePlayEvent)
        .where(GamePlayEvent.user_id == user_id, GamePlayEvent.game_id == game_id)
        .order_by(GamePlayEvent.played_at.desc(), GamePlayEvent.recorded_at.desc())
        .limit(RECENT_LIMIT)
    )
    return [event_out(row) for row in rows]


async def export_rows(db: AsyncSession, user_id: UUID) -> list[dict[str, Any]]:
    """Every logged play, for the library export."""
    rows = await db.scalars(
        select(GamePlayEvent)
        .where(GamePlayEvent.user_id == user_id)
        .order_by(GamePlayEvent.played_at, GamePlayEvent.id)
    )
    return [event_out(row) for row in rows]
