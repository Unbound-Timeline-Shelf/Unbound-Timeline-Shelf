"""Achievement definitions from metadata providers, stored beside synced ones without progress.

Rows are stored with ``provider == "catalog"`` and the metadata provider's name in
``source_provider``; ``achievement_folding`` matches them to synced rows."""

# pylint: disable=not-callable

import time
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.models.achievement import Achievement
from src.database.models.game import Game
from src.features.achievement_folding import CATALOG
from src.features.achievement_rekey import Rekey, incoming, match, rekey
from src.features.metadata.providers import discover_providers
from src.features.metadata.service import collect_record, game_candidate

MAX_ROWS = 2000
_EXTERNAL_ID_LENGTH = 200
PROVIDER_LABEL_LENGTH = 128


def catalogue_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Rows for the achievements table from a provider record; never unlocked, never trusted twice."""
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for item in (record.get("metadata") or {}).get("achievements") or []:
        external_id = item.get("external_id")
        if isinstance(external_id, str):
            # the stored id is clipped, so de-duplicate on what is stored
            external_id = external_id[:_EXTERNAL_ID_LENGTH]
        if not isinstance(external_id, str) or external_id in seen:
            continue
        seen.add(external_id)
        rows.append(
            {
                "external_id": external_id,
                "name": item["name"],
                "description": item.get("description"),
                "icon_url": item.get("icon_url"),
                "locked_icon_url": item.get("locked_icon_url"),
                "hidden": bool(item.get("hidden")),
                "global_percent": item.get("global_percent"),
                "tier": item.get("kind"),
                "medal": item.get("tier"),
                "points": item.get("points"),
                "source_ids": dict(item.get("source_ids") or {}) or None,
                # Plugin API 1.1.20: earlier ids of this achievement, for the sticky re-key step;
                # not a stored field (``achievement_rekey.incoming`` takes them off)
                "aliases": [alias[:_EXTERNAL_ID_LENGTH] for alias in item.get("aliases") or ()],
                "unlocked": False,
            }
        )
    return rows[:MAX_ROWS]


def provider_label(record: dict[str, Any], providers: list[Any] | None = None) -> str:
    contributors = set(record.get("contributors") or [])
    names = list(
        dict.fromkeys(
            provider.name
            for provider in providers or []
            if provider.id in contributors
            and isinstance(provider.name, str)
            and provider.name.strip()
        )
    )
    if names:
        return " + ".join(names)[:PROVIDER_LABEL_LENGTH]
    name = str(record.get("provider_name") or "Metadata providers").strip()
    return name[:PROVIDER_LABEL_LENGTH] or "Metadata providers"


async def fetch_catalogue(
    db: AsyncSession, user_id: UUID, game: Game
) -> tuple[str, list[dict[str, Any]], list[str]]:
    """Ask the user's metadata providers for this game's achievement definitions."""
    candidate = game_candidate(game)
    record, errors = await collect_record(db, user_id, candidate, resource="achievements")
    providers = await discover_providers(db, user_id, candidate.media_type)
    return provider_label(record, providers), catalogue_rows(record), errors


async def has_catalogue(db: AsyncSession, game_id: UUID) -> bool:
    count = await db.scalar(
        select(func.count())
        .select_from(Achievement)
        .where(Achievement.game_id == game_id, Achievement.provider == CATALOG)
    )
    return bool(count)


async def store_catalogue(
    db: AsyncSession, game_id: UUID, provider: str, rows: list[dict[str, Any]]
) -> Rekey:
    """Upsert by external id, so a media item linked to an achievement keeps its link.

    Definitions the provider no longer lists are removed, an empty answer changes nothing, and
    synced rows and unlock state are never touched. A catalogue that carries the Atlas bridge
    id (``source_ids["atlas.row"]``) is re-keyed in place first, moving manual progress with it
    (see ``achievement_rekey``); the bridge itself is never stored."""
    if not rows:
        return Rekey()
    existing = {
        a.external_id: a
        for a in (
            await db.scalars(
                select(Achievement)
                .where(Achievement.game_id == game_id, Achievement.provider == CATALOG)
                .with_for_update()
            )
        ).all()
    }
    items = incoming(rows, provider)
    rekeyed = match(items, existing)
    result = Rekey()
    if any(item.bridge for item in items):
        result = await rekey(db, game_id, items, rekeyed)
    wanted = {item.current.id for item in items if item.current is not None}
    # delete first, so a definition that comes back under a freed id never meets the old row
    for achievement in existing.values():
        if achievement.id not in wanted:
            await db.delete(achievement)
    await db.flush()
    now = int(time.time())
    for item in items:
        if item.current is None:
            db.add(Achievement(game_id=game_id, provider=CATALOG, created_at=now, **item.values))
            continue
        for field, value in item.values.items():
            if value is not None or field in ("hidden", "source_ids", "tier", "medal", "points"):
                setattr(item.current, field, value)
    await db.flush()
    return result
