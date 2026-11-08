"""Follow-ups from the plugin-supplied content audit (#398).

- Episode stills are served from the host's image cache, owner-scoped, and an address that
  names a private network is never handed to the browser.
- A notification link into this app's own API is not an "external" link."""

import io
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import delete

from src.api.routes import media_images
from src.core.config import settings
from src.database.models.anime import Anime, AnimeEpisode, AnimeSeason
from src.database.models.tv_show import TVEpisode, TVSeason, TVShow
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features.notification_safety import url_is_safe
from src.helpers import remote_images
from tests.test_game_files_flow import game_flow  # noqa: F401  (registers the "flow" fixture)


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1280, 720), (30, 90, 160)).save(buffer, "PNG")
    return buffer.getvalue()


@pytest.fixture
def downloads(monkeypatch, tmp_path):
    monkeypatch.setattr(media_images, "_DATA_ROOT", tmp_path)
    calls: list[str] = []

    def fake_download(url: str) -> bytes:
        calls.append(url)
        if "broken" in url or "192.168." in url:
            raise remote_images.RemoteImageError("refused")
        return _png()

    monkeypatch.setattr(remote_images, "download_image", fake_download)
    return calls


async def _episode(user_id, kind: str, still_url: str | None):
    show_model, season_model, episode_model = (
        (TVShow, TVSeason, TVEpisode) if kind == "tv" else (Anime, AnimeSeason, AnimeEpisode)
    )
    async with SessionLocal() as db:
        show = show_model(user_id=user_id, title="Heat", sort_title="heat")
        db.add(show)
        await db.flush()
        season = season_model(show_id=show.id, season_number=1)
        db.add(season)
        await db.flush()
        episode = episode_model(season_id=season.id, episode_number=1, still_url=still_url)
        db.add(episode)
        await db.commit()
        return episode.id


@pytest.mark.parametrize("kind", ["tv", "anime"])
async def test_episode_still_is_cached_and_served_small(flow, downloads, kind):
    episode = await _episode(flow.user_id, kind, "https://img.example.test/still.jpg")
    url = f"/api/media-image/{kind}/episodes/{episode}/still"
    first = await flow.client.get(url)
    assert first.status_code == 200, first.text
    assert first.headers["content-type"] == "image/jpeg"
    assert Image.open(io.BytesIO(first.content)).width == media_images.STILL_WIDTH
    second = await flow.client.get(url)
    assert second.content == first.content
    assert downloads == ["https://img.example.test/still.jpg"]


async def test_a_private_or_credentialed_still_never_reaches_the_browser(flow, downloads):
    private = await _episode(flow.user_id, "tv", "http://192.168.1.1/admin/still.jpg")
    response = await flow.client.get(
        f"/api/media-image/tv/episodes/{private}/still", follow_redirects=False
    )
    assert response.status_code == 404
    public = await _episode(flow.user_id, "tv", "https://broken.example.test/s.jpg")
    response = await flow.client.get(
        f"/api/media-image/tv/episodes/{public}/still", follow_redirects=False
    )
    # a public address that could not be cached falls back to the original, as posters do
    assert response.status_code == 307
    assert response.headers["location"] == "https://broken.example.test/s.jpg"


async def test_stills_are_owner_scoped_and_missing_ones_are_not_found(flow, downloads):
    other_id = uuid4()
    async with SessionLocal() as db:
        db.add(
            User(
                id=other_id,
                username=f"o_{other_id.hex[:10]}",
                email=f"{other_id.hex[:10]}@example.test",
                password_hash="x",
            )
        )
        await db.commit()
    try:
        foreign = await _episode(other_id, "tv", "https://img.example.test/theirs.jpg")
        none = await _episode(flow.user_id, "anime", None)
        for path in (
            f"/api/media-image/tv/episodes/{foreign}/still",
            f"/api/media-image/anime/episodes/{foreign}/still",
            f"/api/media-image/anime/episodes/{none}/still",
            f"/api/media-image/tv/episodes/{uuid4()}/still",
        ):
            assert (await flow.client.get(path)).status_code == 404, path
        assert downloads == []
    finally:
        async with SessionLocal() as db:
            await db.execute(delete(User).where(User.id == other_id))
            await db.commit()


def test_a_link_into_this_apps_own_api_is_not_external(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_APP_URL", "https://tracker.example.com")
    assert not url_is_safe("https://tracker.example.com/api/plugins/x/frontend/index.html")
    assert not url_is_safe("https://TRACKER.example.com/api")
    assert url_is_safe("https://tracker.example.com/games/1")
    assert url_is_safe("https://other.example.com/api/plugins/x")
    assert not url_is_safe("/api/plugins/x")  # an application path to the API was refused before
    monkeypatch.setattr(settings, "PUBLIC_APP_URL", "")
    assert url_is_safe("https://tracker.example.com/api/x")  # no known own host: unchanged
