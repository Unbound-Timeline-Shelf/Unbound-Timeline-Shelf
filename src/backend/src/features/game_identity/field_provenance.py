"""Where a game's metadata values came from (#348).

`games` columns keep the value every consumer reads. For the displayable metadata fields this
module records, in `game_field_values`, who supplied the current value: the user (a manual
edit) or a metadata provider (a refresh). The title keeps its own, richer rules in `names`.

Recording is best effort and never changes a game: a value is written only when it differs
from the one already recorded, and an earlier value stays as `superseded`.
"""

from __future__ import annotations

import time
from collections.abc import Collection, Mapping
from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.game import Game
from src.database.models.game_identity_tracking import (
    FieldValueOrigin,
    FieldValueState,
    GameFieldValue,
)

PROVENANCE_FIELDS = frozenset(
    {
        "age_rating",
        "description",
        "features",
        "release_date",
        "series",
        "tags",
        "publisher",
        "developer",
    }
)

_LABEL_LENGTH = 64


def stored(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple, set)):
        return sorted(str(item) for item in value)
    return value


def stored_field(game: Game, field: str, value: Any) -> Any:
    """`stored(value)` for `game`'s `field`, where a release date that is the game's own keeps
    its precision: a year-only date is recorded as ``"2011"`` and a month as ``"2011-04"``, so
    preferring that source later restores the precision instead of a day (C5.5)."""
    if (
        field == "release_date"
        and isinstance(value, date)
        and value == game.release_date
        and game.release_date_precision in ("year", "month")
    ):
        return value.isoformat()[: 4 if game.release_date_precision == "year" else 7]
    return stored(value)


def field_values_of(game: Game, fields: Collection[str]) -> Select[tuple[GameFieldValue]]:
    """The recorded values of `fields` for one game (callers narrow by state and origin)."""
    return select(GameFieldValue).where(
        GameFieldValue.user_id == game.user_id,
        GameFieldValue.game_id == game.id,
        GameFieldValue.field.in_(fields),
    )


def changed_values(game: Game, updates: Mapping[str, Any]) -> dict[str, Any]:
    """The provenance fields in `updates` whose value differs from the game's current one."""
    return {
        field: value
        for field, value in updates.items()
        if field in PROVENANCE_FIELDS and getattr(game, field, None) != value
    }


async def record_field_sources(
    db: AsyncSession,
    game: Game,
    values: Mapping[str, Any],
    *,
    origin: FieldValueOrigin,
    provider: str | None = None,
) -> None:
    """Record who supplied each of `values` (field -> new value) for `game`.

    `origin` is `user` for a manual edit or `metadata` for a refresh; a refresh names its
    `provider`. Fields outside `PROVENANCE_FIELDS` are ignored."""
    fields = PROVENANCE_FIELDS & values.keys()
    if not fields:
        return
    label = (provider or "").strip()[:_LABEL_LENGTH] or None
    if origin == FieldValueOrigin.METADATA and label is None:
        origin = FieldValueOrigin.SYSTEM
    if origin == FieldValueOrigin.USER:
        label = None
    current = {
        row.field: row
        for row in await db.scalars(
            field_values_of(game, fields)
            .where(GameFieldValue.state == FieldValueState.CURRENT.value)
            .with_for_update()
        )
    }
    stamp = int(time.time())
    for field in sorted(fields):
        value = stored_field(game, field, values[field])
        existing = current.get(field)
        if (
            existing is not None
            and existing.value == value
            and existing.origin == origin.value
            and existing.source_label == label
        ):
            continue
        if existing is not None:
            existing.state = FieldValueState.SUPERSEDED.value
            existing.resolved_at = stamp
            await db.flush()
        locked = field in (game.locked_fields or [])
        db.add(
            GameFieldValue(
                user_id=game.user_id,
                game_id=game.id,
                field=field,
                value=value,
                state=FieldValueState.CURRENT.value,
                origin=origin.value,
                source_label=label,
                observed_at=stamp if origin == FieldValueOrigin.METADATA else None,
                edited_at=stamp if origin == FieldValueOrigin.USER else None,
                recorded_at=stamp,
                locked=locked,
                lock_origin="user" if locked else None,
            )
        )


async def relabel_unchanged(
    db: AsyncSession, game: Game, values: Mapping[str, Any], *, provider: str | None
) -> None:
    """Re-attribute fields a refresh left unchanged (field -> the game's value) to `provider`
    when their current row is a refresh's under another label or value.

    Before per-field sources (#348) a refresh recorded every field under the merged record's
    lead, "Library"; a value that has not changed since would otherwise keep that label for
    good. A value the person entered (origin `user`) or one with no row yet is left as it is."""
    label = (provider or "").strip()[:_LABEL_LENGTH] or None
    fields = PROVENANCE_FIELDS & values.keys()
    if label is None or not fields:
        return
    rows = await db.scalars(
        field_values_of(game, fields).where(
            GameFieldValue.state == FieldValueState.CURRENT.value,
            GameFieldValue.origin == FieldValueOrigin.METADATA.value,
        )
    )
    stale = {
        row.field: values[row.field]
        for row in rows
        if row.source_label != label
        or row.value != stored_field(game, row.field, values[row.field])
    }
    if stale:
        await record_field_sources(
            db, game, stale, origin=FieldValueOrigin.METADATA, provider=label
        )


async def field_sources(
    db: AsyncSession, user_id: UUID, game_id: UUID, fields: Collection[str] | None = None
) -> dict[str, dict[str, Any]]:
    """The current provenance of each recorded metadata field of a game."""
    wanted = PROVENANCE_FIELDS if fields is None else PROVENANCE_FIELDS & set(fields)
    rows = await db.scalars(
        select(GameFieldValue).where(
            GameFieldValue.user_id == user_id,
            GameFieldValue.game_id == game_id,
            GameFieldValue.field.in_(wanted),
            GameFieldValue.state == FieldValueState.CURRENT.value,
        )
    )
    return {
        row.field: {
            "origin": row.origin,
            "provider": row.source_label,
            "recorded_at": row.recorded_at,
            "locked": row.locked,
        }
        for row in rows
    }
