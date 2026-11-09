"""The server's date text (core/date_display.py) follows the same shared vectors as the frontend's
utils/dates.ts (SPEC-UI-POLISH-PASS section 6.7), and the sign-in notice uses the account's format."""

import json
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete

from src.core import session_manager
from src.core.auth import hash_password
from src.core.date_display import format_day, format_instant, style_of
from src.core.preferences import save_preferences
from src.database.models.auth import UserSession
from src.database.models.user import User
from src.database.session import SessionLocal

_VECTORS = json.loads(
    (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "src"
        / "tests"
        / "fixtures"
        / "date-format-vectors.json"
    ).read_text(encoding="utf-8")
)


def test_the_shared_vectors_are_version_one():
    assert _VECTORS["version"] == 1


@pytest.mark.parametrize(
    "row",
    [r for r in _VECTORS["days"] if r["precision"] in (None, "day") and len(r["input"]) == 10],
    ids=lambda r: f"{r['style']}-{r['input']}",
)
def test_days_match_the_shared_vectors(row):
    year, month, day = (int(part) for part in row["input"].split("-"))
    assert format_day(date(year, month, day), {"date_format": row["style"]}) == row["expected"]


@pytest.mark.parametrize(
    "row", _VECTORS["instants"], ids=lambda r: f"{r['style']}-{r['time_format']}-{r['iso']}"
)
def test_instants_match_the_shared_vectors_in_utc(row):
    prefs = {"date_format": row["style"], "time_format": row["time_format"]}
    expected = f"{row['expected']} UTC"
    assert format_instant(row["unix"], prefs) == expected
    moment = datetime.fromisoformat(row["iso"].replace("Z", "+00:00"))
    assert format_instant(moment, prefs) == expected


@pytest.mark.parametrize(
    ("prefs", "style"),
    [
        (None, "dmy"),
        ({}, "dmy"),
        ({"date_format": "locale"}, "dmy"),
        ({"date_format": "long"}, "long"),
        ({"date_format": "iso"}, "iso"),
    ],
)
def test_unknown_or_unset_styles_are_the_default(prefs, style):
    assert style_of(prefs) == style


def test_seconds_are_shown_when_asked():
    moment = datetime(2020, 9, 17, 14, 5, 9, tzinfo=UTC)
    assert format_instant(moment, None, seconds=True) == "17/09/2020 14:05:09 UTC"
    assert format_instant(moment, {"date_format": "mdy"}, seconds=True) == (
        "09/17/2020 2:05:09 PM UTC"
    )


async def test_the_new_sign_in_notice_uses_the_accounts_date_format(monkeypatch):
    """The body of the "New sign-in location" notice is written in the reader's format."""
    sent: list[dict] = []

    async def capture(_db, _user_id, rows, *_args, **_kwargs):
        sent.extend(rows)

    monkeypatch.setattr(session_manager, "emit_legacy_rows", capture)
    async with SessionLocal() as db:
        user = User(
            username=f"dates_{uuid4().hex}",
            email=f"{uuid4()}@example.test",
            password_hash=hash_password("Test-password!9"),
        )
        db.add(user)
        await db.commit()
        try:
            await save_preferences(db, user.id, {"date_format": "mdy"})
            session = UserSession(
                id=uuid4(),
                user_id=user.id,
                token_hash=uuid4().hex,
                expires_at=2_000_000_000,
                created_at=int(datetime(2020, 9, 17, 14, 5, 9, tzinfo=UTC).timestamp()),
                geo_country="Iceland",
                anomaly_reason="New geographic location: Iceland (previous: Norway).",
            )
            # pylint: disable-next=protected-access
            await session_manager._queue_anomaly_notification(db, user, session)
        finally:
            await db.execute(delete(User).where(User.id == user.id))
            await db.commit()
    assert len(sent) == 1
    assert "at 09/17/2020 2:05:09 PM UTC." in sent[0]["body"]
