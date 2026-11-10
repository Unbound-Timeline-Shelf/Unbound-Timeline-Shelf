"""Local copies of the posters and backdrops of movies, TV shows and anime, and of book covers."""

import asyncio
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.image_responses import original_image_response
from src.core.auth import get_current_user
from src.database.models.anime import Anime, AnimeEpisode, AnimeSeason
from src.database.models.book import Book
from src.database.models.movies import Movie
from src.database.models.tv_show import TVEpisode, TVSeason, TVShow
from src.database.models.user import User
from src.database.session import get_db
from src.helpers.image_prefetch import MEDIA_ROOT, WIDTHS
from src.helpers.remote_images import RemoteImageError, cache_path
from src.helpers.remote_images import fetch_if_missing as fetch_and_store

router = APIRouter(
    prefix="/api/media-image",
    tags=["media"],
    dependencies=[Depends(get_current_user)],
)

_DATA_ROOT = MEDIA_ROOT
_MODELS: dict[str, Any] = {"movie": Movie, "tv": TVShow, "anime": Anime}
_EPISODES: dict[str, tuple[Any, Any, Any]] = {
    "tv": (TVEpisode, TVSeason, TVShow),
    "anime": (AnimeEpisode, AnimeSeason, Anime),
}
STILL_WIDTH = 640


async def _cached_image(
    url: str, user_id: UUID, kind: str, item_id: UUID, name: str, width: int
) -> Response:
    """Serve ``url`` from this server's disk, downloading and shrinking it the first time; if
    the download fails the browser is sent to the original address instead."""
    target = cache_path(_DATA_ROOT / str(user_id) / ".cache", kind, str(item_id), name, url)
    if not target.is_file():
        try:
            await asyncio.to_thread(fetch_and_store, url, target, width)
        except RemoteImageError:
            return original_image_response(url)
    return FileResponse(
        target,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=3600, must-revalidate"},
    )


# Declared before the generic route below so "/book/..." is not read as an unknown media type.
@router.get("/book/{book_id}/cover")
async def get_book_cover(
    book_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """A book's cover from this server's own disk (C15 M10 review).

    A cover chosen from a metadata provider is stored as the provider's address; loading it from
    there on every view of the library would send each viewer's browser to the provider's image
    host, so it goes through the same cache and private-address refusal as posters."""
    url = await db.scalar(
        select(Book.cover_url).where(Book.id == book_id, Book.user_id == current_user.id)
    )
    if not url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such image.")
    return await _cached_image(url, current_user.id, "book", book_id, "cover", WIDTHS["poster"])


@router.get("/{media_type}/{item_id}/{which}")
async def get_media_image(
    media_type: Literal["movie", "tv", "anime"],
    item_id: UUID,
    which: Literal["poster", "backdrop", "hero"],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """The title's poster or backdrop from this server's own disk, downloading
    and shrinking it the first time. If the download fails the browser is sent
    to the original address instead, so a picture never just goes missing."""
    model = _MODELS[media_type]
    row = await db.scalar(
        select(model).where(model.id == item_id, model.user_id == current_user.id)
    )
    url: str | None = None
    if row:
        # "hero" is the picture behind a page's title: the backdrop, or the poster
        # when the title has no backdrop
        url = (
            (row.backdrop_url or row.poster_url)
            if which == "hero"
            else getattr(row, f"{which}_url", None)
        )
    if not url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such image.")

    # the hero of a title with a backdrop is that same picture: one file, not two
    name = "backdrop" if which == "hero" and row and row.backdrop_url else which
    return await _cached_image(url, current_user.id, media_type, item_id, name, WIDTHS[which])


@router.get("/{media_type}/episodes/{episode_id}/still")
async def get_episode_still(
    media_type: Literal["tv", "anime"],
    episode_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """An episode still from this server's own disk (#398).

    Stills come from metadata providers and plugins and used to be loaded by the browser from
    whatever address was stored, private hosts included. They now go through the same cache and
    the same refusal of private addresses as posters."""
    episode_model, season_model, show_model = _EPISODES[media_type]
    url = await db.scalar(
        select(episode_model.still_url)
        .join(season_model, episode_model.season_id == season_model.id)
        .join(show_model, season_model.show_id == show_model.id)
        .where(episode_model.id == episode_id, show_model.user_id == current_user.id)
    )
    if not url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such image.")
    return await _cached_image(
        url, current_user.id, f"{media_type}-episode", episode_id, "still", STILL_WIDTH
    )
