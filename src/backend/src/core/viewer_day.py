"""The calendar day of the person making a request, for activity that is stored as a day.

Episode progress, status changes and rewatches are recorded as a calendar day with no time of
day (``ActivityLog.event_date``, ``RewatchLog.finished_on``). Statistics already reads timestamped
activity in the viewer's IANA zone, which the app sends from the device (#193). The writers used
the server process's own day instead (UTC in the official image), so a watch logged at 08:30 in
Perth or 19:00 in Los Angeles could land on the neighbouring day (#473).

The app now sends the device's zone as ``X-Timezone`` on its own ``/api`` requests, the same zone
Statistics reads with. ``ViewerTimezoneMiddleware`` keeps it for the request and ``viewer_today``
returns that zone's current day. A missing or unknown zone (other clients, plugins, scheduled
work) keeps the server's day exactly as before, so nothing changes for them.

Only new writes use it. Rows already stored are calendar days with no time to convert, so they
are never moved.
"""

from contextvars import ContextVar
from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo, available_timezones

from starlette.types import ASGIApp, Receive, Scope, Send

HEADER = b"x-timezone"
_zone: ContextVar[ZoneInfo | None] = ContextVar("viewer_timezone", default=None)


@lru_cache(maxsize=1)
def _zone_names() -> frozenset[str]:
    # "localtime" is the server's own zone file, not a zone a viewer can mean.
    return frozenset(available_timezones() - {"localtime"})


def parse_zone(value: str | None) -> ZoneInfo | None:
    """A known IANA zone name, or None for anything else."""
    if not value or len(value) > 64 or value not in _zone_names():
        return None
    return ZoneInfo(value)


def viewer_zone() -> ZoneInfo | None:
    return _zone.get()


def viewer_today(now: datetime | None = None) -> date:
    """Today in the requesting device's zone, or the server's own day when it sent none."""
    zone = _zone.get()
    if zone is None:
        return (now.astimezone() if now else datetime.now()).date()
    return (now or datetime.now(zone)).astimezone(zone).date()


class ViewerTimezoneMiddleware:  # pylint: disable=too-few-public-methods
    """Keeps a request's ``X-Timezone`` for the activity writers it reaches."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        raw = next((value for key, value in scope.get("headers", []) if key == HEADER), None)
        zone = parse_zone(raw.decode("latin-1")) if raw else None
        token = _zone.set(zone)
        try:
            await self.app(scope, receive, send)
        finally:
            _zone.reset(token)
