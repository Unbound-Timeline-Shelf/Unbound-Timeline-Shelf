"""Release dates a metadata refresh stores on a game (Plugin API 1.1.20, CONTRACTS-v1 C5.4).

``games.release_date`` stays a date: for a year or a month it is the first day of the period, so
sorting and the release-year statistics keep working, and ``release_date_precision`` says how
much of it is known (NULL is a day, as every date stored before 1.1.20 is). ``games.releases``
holds up to five provider-reported releases and, like ``provider_custom``, is replaced by a
refresh. The ``release_date`` lock covers the date and its precision; ``releases`` has its own.
"""

from collections.abc import Collection
from datetime import date
from typing import Any

from src.database.models.game import Game
from src.plugin_api.metadata_contracts import MetadataRelease, partial_date_start

MAX_RELEASES = 5
RELEASES_LOCK = "releases"


def _legacy_day(raw: object) -> date | None:
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        return None


def release_of(match: dict[str, Any]) -> tuple[date | None, str | None]:
    """The (date, precision) a refresh result reports: its ``first_release`` when it has one,
    otherwise the day-only ``release_date`` every older provider sends. A day is precision None.

    Fields of a merged result can come from different providers. A day that falls inside the
    reported year or month agrees with it and is more precise, so it wins (as in Atlas's
    hierarchical vote, C5.2)."""
    legacy = _legacy_day(match.get("release_date"))
    first = match.get("first_release")
    if isinstance(first, dict):
        try:
            release = MetadataRelease.model_validate(first)
        except ValueError:
            release = None
        if release is not None and release.precision != "day":
            if legacy is not None and legacy.isoformat().startswith(release.date + "-"):
                return legacy, None
            return partial_date_start(release.date), release.precision
        if release is not None:
            return partial_date_start(release.date), None
    return legacy, None


def _releases(match: dict[str, Any]) -> list[dict[str, Any]] | None:
    raw = match.get("releases")
    if not isinstance(raw, list) or not raw:
        return None
    rows: list[dict[str, Any]] = []
    for entry in raw[:MAX_RELEASES]:
        try:
            rows.append(MetadataRelease.model_validate(entry).model_dump(mode="json"))
        except ValueError:
            continue
    return rows or None


def add_release_updates(
    game: Game,
    match: dict[str, Any],
    updates: dict[str, object],
    skipped_locked: list[str],
    *,
    save_release_date: bool,
    held_by_preference: Collection[str] = (),
) -> None:
    """Add the precision and releases a refresh should store to ``updates``.

    Runs after the release date itself was considered. A changed date resets the stored
    precision (see ``Game``), so the precision is written whenever the date is, and whenever
    only the precision differs ("2011" becoming "2011-01-01")."""
    if not save_release_date:
        return
    day, precision = release_of(match)
    date_is_free = (
        "release_date" not in (game.locked_fields or [])
        and "release_date" not in held_by_preference
    )
    if (
        day is not None
        and date_is_free
        and (
            "release_date" in updates
            or (day == game.release_date and precision != game.release_date_precision)
        )
    ):
        updates["release_date_precision"] = precision
    releases = _releases(match)
    if releases is None:
        return
    if RELEASES_LOCK in (game.locked_fields or []):
        skipped_locked.append(RELEASES_LOCK)
    elif releases != game.releases:
        updates["releases"] = releases
