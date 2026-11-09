"""Dates the server writes into text people read (notification bodies), in the account's
date format (SPEC-UI-POLISH-PASS section 6.3). It is the backend twin of the frontend's
``utils/dates.ts`` and follows the same shared vectors.

The server cannot see the reader's device, so an instant is written in UTC with a " UTC" suffix.
Machine formats (ICS feeds, file names, API values, signed or hashed text) never come here."""

from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any

STYLES = ("dmy", "long", "mdy", "iso")
DEFAULT_STYLE = "dmy"

_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def style_of(prefs: Mapping[str, Any] | None) -> str:
    """The account's date style; anything unknown or unset is the default (dmy)."""
    value = (prefs or {}).get("date_format")
    return value if value in STYLES else DEFAULT_STYLE


def _twelve_hour(prefs: Mapping[str, Any] | None, style: str) -> bool:
    chosen = (prefs or {}).get("time_format")
    return chosen == "12h" or (chosen not in ("24h", "12h") and style == "mdy")


def format_day(value: date, prefs: Mapping[str, Any] | None = None) -> str:
    """A calendar day: 17/09/2020, 17 September 2020, 09/17/2020 or 2020-09-17.

    Built with str.format rather than strftime so years below 1000 keep four digits."""
    style = style_of(prefs)
    year, month, day = f"{value.year:04d}", f"{value.month:02d}", f"{value.day:02d}"
    if style == "iso":
        return f"{year}-{month}-{day}"
    if style == "mdy":
        return f"{month}/{day}/{year}"
    if style == "long":
        return f"{value.day} {_MONTHS[value.month - 1]} {year}"
    return f"{day}/{month}/{year}"


def format_instant(
    value: int | float | datetime, prefs: Mapping[str, Any] | None = None, *, seconds=False
) -> str:
    """A moment in UTC: "17/09/2020 14:05 UTC", "09/17/2020 2:05 PM UTC" (mdy reads as 12h
    unless the account chose a time format)."""
    moment = (
        value.astimezone(UTC) if isinstance(value, datetime) else datetime.fromtimestamp(value, UTC)
    )
    style = style_of(prefs)
    tail = f"{moment.minute:02d}" + (f":{moment.second:02d}" if seconds else "")
    if _twelve_hour(prefs, style):
        clock = f"{moment.hour % 12 or 12}:{tail} {'AM' if moment.hour < 12 else 'PM'}"
    else:
        clock = f"{moment.hour:02d}:{tail}"
    joiner = ", " if style == "long" else " "
    return f"{format_day(moment.date(), prefs)}{joiner}{clock} UTC"
