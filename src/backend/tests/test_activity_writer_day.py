"""#473: activity recorded as a calendar day uses the device's day, like Statistics reads it.

Episode progress, status changes and rewatches store a day with no time. The app sends the
device's IANA zone as X-Timezone (the zone Statistics already reads with), and the writers record
that zone's current day. Without a known zone they keep the server's own day, and rows already
stored are never moved.
"""

from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import delete, select

from src.core import viewer_day
from src.core.auth import get_current_user
from src.database.models.media_extras import ActivityEventType, ActivityLog, RewatchLog
from src.database.models.movies import Movie, MovieStatus
from src.database.models.tv_show import TVSeason, TVShow, TVShowStatus
from src.database.models.user import User
from src.database.session import SessionLocal
from src.main import app

# Near midnight UTC, for positive and negative offsets and across DST changes.
CASES = [
    # 23:30 UTC on 10 March is already 11 March in Perth (+8), still 10 March in New York.
    ("2026-03-10T23:30:00", "Australia/Perth", date(2026, 3, 11)),
    ("2026-03-10T23:30:00", "America/New_York", date(2026, 3, 10)),
    # 00:30 UTC on 11 March is still 10 March in Los Angeles (-7 after its DST start).
    ("2026-03-11T00:30:00", "America/Los_Angeles", date(2026, 3, 10)),
    ("2026-03-11T00:30:00", "UTC", date(2026, 3, 11)),
    # Kiritimati (+14) and Pago Pago (-11) straddle the date line.
    ("2026-06-30T10:30:00", "Pacific/Kiritimati", date(2026, 7, 1)),
    ("2026-06-30T10:30:00", "Pacific/Pago_Pago", date(2026, 6, 29)),
    # Lord Howe moves by half an hour at DST: 13:15 UTC on 4 October is 00:15 on the 5th
    # (+11 once it starts), but 13:15 UTC on 5 April is 23:45 that day (+10:30 once it ends).
    ("2026-10-04T13:15:00", "Australia/Lord_Howe", date(2026, 10, 5)),
    ("2026-04-05T13:15:00", "Australia/Lord_Howe", date(2026, 4, 5)),
    # Kathmandu's +5:45 offset.
    ("2026-01-01T18:20:00", "Asia/Kathmandu", date(2026, 1, 2)),
]


@pytest.mark.parametrize(("instant", "zone", "expected"), CASES)
def test_viewer_today_is_the_devices_day_near_midnight(instant, zone, expected):
    now = datetime.fromisoformat(instant).replace(tzinfo=timezone.utc)
    token = viewer_day._zone.set(ZoneInfo(zone))  # pylint: disable=protected-access
    try:
        assert viewer_day.viewer_today(now) == expected
    finally:
        viewer_day._zone.reset(token)  # pylint: disable=protected-access


def test_without_a_zone_the_servers_own_day_is_kept():
    now = datetime(2026, 3, 10, 23, 30, tzinfo=timezone.utc)
    assert viewer_day.viewer_today(now) == now.astimezone().date()
    assert viewer_day.viewer_today() == date.today()


@pytest.mark.parametrize(
    "value", ["", "Mars/Olympus", "localtime", "../../etc/passwd", "Europe/London" * 10]
)
def test_unknown_or_unsafe_zone_names_are_ignored(value):
    assert viewer_day.parse_zone(value) is None


def _a_zone_whose_day_differs_from_the_servers() -> str:
    """Kiritimati is a day ahead of UTC from 10:00 UTC and Pago Pago a day behind until 11:00
    UTC, so at any moment one of them is on another calendar day than a UTC server."""
    for name in ("Pacific/Kiritimati", "Pacific/Pago_Pago"):
        if datetime.now(ZoneInfo(name)).date() != date.today():
            return name
    pytest.skip("the server's own zone shares a day with both date-line zones right now")
    return "UTC"


@pytest.fixture
async def library():
    async with SessionLocal() as db:
        user = User(username=uuid4().hex, email=f"{uuid4().hex}@example.test", password_hash="x")
        db.add(user)
        await db.flush()
        movie = Movie(
            user_id=user.id, title="Film", sort_title="film", status=MovieStatus.WATCHLIST
        )
        show = TVShow(
            user_id=user.id, title="Show", sort_title="show", status=TVShowStatus.IN_PROGRESS
        )
        db.add_all([movie, show])
        await db.flush()
        season = TVSeason(show_id=show.id, season_number=1, episode_count=10, episodes_watched=0)
        db.add(season)
        await db.commit()
        state = SimpleNamespace(user=user, movie_id=movie.id, show_id=show.id, season_id=season.id)
    app.dependency_overrides[get_current_user] = lambda: state.user
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        state.client = client
        yield state
    app.dependency_overrides.pop(get_current_user, None)
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id == state.user.id))
        await db.commit()


async def _days(user_id, event_type) -> list[date]:
    async with SessionLocal() as db:
        return list(
            (
                await db.scalars(
                    select(ActivityLog.event_date).where(
                        ActivityLog.user_id == user_id, ActivityLog.event_type == event_type
                    )
                )
            ).all()
        )


async def test_writers_record_the_devices_day(library):
    zone = _a_zone_whose_day_differs_from_the_servers()
    headers = {"X-Timezone": zone}
    before = datetime.now(ZoneInfo(zone)).date()

    changed = await library.client.patch(
        f"/api/movie/update/{library.movie_id}", json={"status": "WATCHED"}, headers=headers
    )
    assert changed.status_code == 200, changed.text
    progressed = await library.client.patch(
        f"/api/tv/{library.show_id}/seasons/{library.season_id}",
        json={"episodes_watched": 3},
        headers=headers,
    )
    assert progressed.status_code == 200, progressed.text
    rewatched = await library.client.post(
        "/api/rewatches",
        json={"media_type": "movie", "media_id": str(library.movie_id)},
        headers=headers,
    )
    assert rewatched.status_code == 201, rewatched.text

    after = datetime.now(ZoneInfo(zone)).date()
    device_days = {before, after}
    assert set(await _days(library.user.id, ActivityEventType.STATUS_CHANGED)) <= device_days
    assert set(await _days(library.user.id, ActivityEventType.EPISODES_WATCHED)) <= device_days
    assert set(await _days(library.user.id, ActivityEventType.REWATCHED)) <= device_days
    assert date.fromisoformat(rewatched.json()["finished_on"]) in device_days


async def test_without_the_header_writers_keep_the_servers_day(library):
    before = date.today()
    progressed = await library.client.patch(
        f"/api/tv/{library.show_id}/seasons/{library.season_id}",
        json={"episodes_watched": 2},
        headers={"X-Timezone": "Not/AZone"},
    )
    assert progressed.status_code == 200
    rewatched = await library.client.post(
        "/api/rewatches", json={"media_type": "movie", "media_id": str(library.movie_id)}
    )
    assert rewatched.status_code == 201
    assert set(await _days(library.user.id, ActivityEventType.EPISODES_WATCHED)) <= {
        before,
        date.today(),
    }
    assert date.fromisoformat(rewatched.json()["finished_on"]) in {before, date.today()}


async def test_an_explicit_day_and_stored_history_are_never_moved(library):
    """A chosen finished_on is kept as given, and earlier rows keep their recorded day whatever
    zone a later request comes from."""
    async with SessionLocal() as db:
        db.add(
            ActivityLog(
                user_id=library.user.id,
                media_type="movie",
                media_id=library.movie_id,
                media_title="Film",
                event_type=ActivityEventType.EPISODES_WATCHED,
                event_date=date(2024, 2, 29),
                count=1,
            )
        )
        await db.commit()
    rewatched = await library.client.post(
        "/api/rewatches",
        json={
            "media_type": "movie",
            "media_id": str(library.movie_id),
            "finished_on": "2025-12-31",
        },
        headers={"X-Timezone": "Pacific/Kiritimati"},
    )
    assert rewatched.json()["finished_on"] == "2025-12-31"
    async with SessionLocal() as db:
        logs = (
            await db.scalars(
                select(RewatchLog.finished_on).where(RewatchLog.user_id == library.user.id)
            )
        ).all()
    assert logs == [date(2025, 12, 31)]
    assert date(2024, 2, 29) in await _days(library.user.id, ActivityEventType.EPISODES_WATCHED)
    assert date(2025, 12, 31) in await _days(library.user.id, ActivityEventType.REWATCHED)
