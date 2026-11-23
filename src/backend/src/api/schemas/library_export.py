"""The portable library snapshot shared by manual exports and backups."""

from typing import Any

from pydantic import BaseModel

from src.api.schemas.anime import AnimeRead
from src.api.schemas.book import BookExport
from src.api.schemas.game import GameRead
from src.api.schemas.movie import MovieRead
from src.api.schemas.tv_show import TVShowRead

# 3 added books (#265 4.4). Imports refuse a newer format instead of dropping what they cannot
# read; every older format still imports (its missing sections are empty).
LIBRARY_EXPORT_FORMAT = 3


def unsupported_format(payload: object) -> str | None:
    """The refusal for an export made by a newer build, or None when this build can read it."""
    version = payload.get("format_version") if isinstance(payload, dict) else None
    if (
        isinstance(version, bool)
        or not isinstance(version, int)
        or version <= LIBRARY_EXPORT_FORMAT
    ):
        return None
    return (
        f"This export uses library format {version}, made by a newer version of the app. "
        f"This version reads formats up to {LIBRARY_EXPORT_FORMAT}; update the app to import it."
    )


class LibraryExport(BaseModel):
    format_version: int = LIBRARY_EXPORT_FORMAT
    exported_at: int
    game_count: int
    games: list[GameRead]
    movies: list[MovieRead] = []
    tv_shows: list[TVShowRead] = []
    anime: list[AnimeRead] = []
    # works with their editions and reads nested (format 3); absent in older exports
    books: list[BookExport] = []
    # the user's own achievements and completion choices (#97); absent in older exports
    manual_achievements: list[dict[str, Any]] = []
    manual_completion: list[dict[str, Any]] = []
    # explicit "Log play" events (#467); absent in older exports. Import keeps each game's own
    # last_played_at and does not replay these.
    play_events: list[dict[str, Any]] = []
    # private tag labels, aliases and suppressed defaults (#267); absent in older exports
    tag_catalogue: dict[str, Any] | None = None
