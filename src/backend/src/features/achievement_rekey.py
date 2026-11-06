"""Keeping catalogue rows and manual progress when a provider changes an achievement's id.

Atlas used to re-mint its achievement ids on every refresh, and its plugin now sends a stable
``ak1:`` key instead (CONTRACTS-v1 C2). Either change would make ``store_catalogue`` delete the
catalogue and recreate it under new external ids, leaving every ``achievement_overrides`` row
(the user's manual progress) pointing at an id that no longer exists. The plugin therefore sends
the old id as ``source_ids["atlas.row"]``, and the catalogue is re-keyed in place:

1. an incoming row whose external id is already stored updates that row;
2. else a row whose ``atlas.row`` bridge names a stored row re-keys it, and moves its manual
   mark with it (``method="row_id"``);
3. else a row listing a stored id among its ``aliases`` keeps that stored id (sticky). Plugin
   API 1.1.19 has no ``aliases`` field, so this step only runs once the host contract carries one;
4. before rows that are no longer listed are deleted, a manual mark that matches no catalogue
   row moves to the one incoming row with its name (``method="name"``). Marks that match none,
   or several, are kept as they are.

Every change is written to ``achievement_rekey_log`` so the migration's downgrade can undo it.
Manual marks are moved, never deleted, except when two land on one id: the newer one stays and
the other is kept whole in the log. Steps 2 to 4 only run for a catalogue that carries the
bridge, so other metadata providers are stored exactly as before."""

# pylint: disable=not-callable

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.achievement import Achievement
from src.database.models.achievement_override import AchievementOverride
from src.database.models.achievement_rekey_log import AchievementRekeyLog
from src.features.achievement_folding import CATALOG, HIDDEN_NAME

ATLAS_ROW = "atlas.row"


@dataclass
class Incoming:
    """One catalogue row as it will be stored, with the ids it may be known by."""

    values: dict[str, Any]
    bridge: str | None = None
    aliases: tuple[str, ...] = ()
    current: Achievement | None = None

    @property
    def external_id(self) -> str:
        return self.values["external_id"]


@dataclass
class Rekey:
    """The changes one ``store_catalogue`` call made, for its log and its tests."""

    rows: int = 0
    marks: int = 0
    dropped: int = 0
    entries: list[AchievementRekeyLog] = field(default_factory=list)


def incoming(rows: Iterable[dict[str, Any]], provider: str) -> list[Incoming]:
    """The rows to store, without the bridge id and the aliases (neither is a stored field)."""
    result = []
    for row in rows:
        values = {**row, "source_provider": provider}
        aliases = tuple(a for a in values.pop("aliases", None) or () if isinstance(a, str))
        source_ids = dict(values.get("source_ids") or {})
        bridge = source_ids.pop(ATLAS_ROW, None)
        values["source_ids"] = source_ids or None
        result.append(
            Incoming(values, bridge if isinstance(bridge, str) and bridge else None, aliases)
        )
    return result


def match(items: list[Incoming], existing: dict[str, Achievement]) -> list[Incoming]:
    """Pair incoming rows with stored rows, one pass per rule so a weaker rule never takes a
    row a stronger one names. Returns the rows matched through the bridge (to be re-keyed)."""
    claimed: set[UUID] = set()
    for item in items:
        stored = existing.get(item.external_id)
        if stored is not None and stored.id not in claimed:
            item.current = stored
            claimed.add(stored.id)
    rekeyed = []
    for item in items:
        stored = existing.get(item.bridge) if item.current is None and item.bridge else None
        if stored is not None and stored.id not in claimed:
            item.current = stored
            claimed.add(stored.id)
            rekeyed.append(item)
    for item in items:
        if item.current is not None:
            continue
        for alias in item.aliases:
            stored = existing.get(alias)
            if stored is not None and stored.id not in claimed:
                # sticky: the stored id stays, so nothing else has to move
                item.current = stored
                item.values["external_id"] = stored.external_id
                claimed.add(stored.id)
                break
    return rekeyed


def _log(
    db: AsyncSession, result: Rekey, game_id: UUID, old: str, new: str, *, method: str, subject: str
) -> AchievementRekeyLog:
    entry = AchievementRekeyLog(
        game_id=game_id,
        old_external_id=old,
        new_external_id=new,
        method=method,
        subject=subject,
    )
    db.add(entry)
    result.entries.append(entry)
    return entry


def _saved(override: AchievementOverride) -> dict[str, Any]:
    return {
        "id": str(override.id),
        "provider": override.provider,
        "external_id": override.external_id,
        "name": override.name,
        "description": override.description,
        "unlocked": override.unlocked,
        "unlocked_at": override.unlocked_at,
        "updated_at": override.updated_at,
    }


class Marks:
    """The game's manual marks on catalogue rows, indexed by external id while they move."""

    def __init__(self, db: AsyncSession, game_id: UUID, result: Rekey) -> None:
        self.db = db
        self.game_id = game_id
        self.result = result
        self.by_id: dict[str, AchievementOverride] = {}

    async def load(self) -> Marks:
        rows = await self.db.scalars(
            select(AchievementOverride)
            .where(
                AchievementOverride.game_id == self.game_id,
                AchievementOverride.provider == CATALOG,
            )
            .with_for_update()
        )
        self.by_id = {row.external_id: row for row in rows.all()}
        return self

    async def move(self, old: str, new: str, method: str) -> bool:
        """Move the mark at ``old`` to ``new``. If ``new`` has one too, the newer one stays."""
        moving = self.by_id.get(old)
        if moving is None or old == new:
            return False
        present = self.by_id.get(new)
        del self.by_id[old]
        if present is not None:
            loser = moving if present.updated_at >= moving.updated_at else present
            entry = _log(
                self.db,
                self.result,
                self.game_id,
                loser.external_id,
                new,
                method="dropped",
                subject="override",
            )
            entry.dropped_override = _saved(loser)
            self.result.dropped += 1
            await self.db.delete(loser)
            await self.db.flush()
            if loser is moving:
                return True
        moving.external_id = new
        self.by_id[new] = moving
        _log(self.db, self.result, self.game_id, old, new, method=method, subject="override")
        self.result.marks += 1
        return True

    async def recover_by_name(self, items: list[Incoming]) -> None:
        """Move each orphaned mark to the one incoming row with its name, if there is exactly one
        and it has no mark yet. Hidden placeholders never match: different secrets share one."""
        stored = {item.external_id for item in items}
        by_name: dict[str, list[str]] = {}
        for item in items:
            name = str(item.values.get("name") or "").casefold()
            if name and name != HIDDEN_NAME.casefold():
                by_name.setdefault(name, []).append(item.external_id)
        orphans = sorted(
            (mark for external_id, mark in self.by_id.items() if external_id not in stored),
            key=lambda mark: (-mark.updated_at, mark.external_id),
        )
        for mark in orphans:
            name = (mark.name or "").casefold()
            targets = by_name.get(name) if name != HIDDEN_NAME.casefold() else None
            if targets and len(targets) == 1 and targets[0] not in self.by_id:
                await self.move(mark.external_id, targets[0], "name")


async def rekey(
    db: AsyncSession, game_id: UUID, items: list[Incoming], rekeyed: list[Incoming]
) -> Rekey:
    """Apply steps 2 and 4 for a catalogue that carries the bridge; return what changed."""
    result = Rekey()
    marks = await Marks(db, game_id, result).load()
    for item in rekeyed:
        if item.current is None:
            continue
        old = item.current.external_id
        item.current.external_id = item.external_id
        _log(db, result, game_id, old, item.external_id, method="row_id", subject="row")
        result.rows += 1
        await marks.move(old, item.external_id, "row_id")
    await marks.recover_by_name(items)
    return result


async def unmatched_marks(db: AsyncSession, game_id: UUID) -> int:
    """Manual marks on catalogue achievements that no catalogue row carries any more."""
    stored = select(Achievement.external_id).where(
        Achievement.game_id == game_id, Achievement.provider == CATALOG
    )
    count = await db.scalar(
        select(func.count())
        .select_from(AchievementOverride)
        .where(
            AchievementOverride.game_id == game_id,
            AchievementOverride.provider == CATALOG,
            AchievementOverride.external_id.not_in(stored),
        )
    )
    return int(count or 0)
