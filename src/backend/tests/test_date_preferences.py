"""How dates are shown (SPEC-UI-POLISH-PASS section 6): `date_format`, `time_format`,
`date_format_chosen` and `date_format_offered`, and the retired C5.5 keys `date_display` and
`partial_date_style`, which an older cached frontend may still send after an upgrade.

The values are stored with the account's other preferences and read back from PostgreSQL."""

from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from src.core.auth import hash_password
from src.core.preferences import (
    load_preferences,
    save_preferences,
    translate_legacy_date_preferences,
    validate_preference,
)
from src.database.models.user import User
from src.database.models.user_preferences import UserPreferences
from src.database.session import SessionLocal


@pytest.fixture
async def user_id():
    async with SessionLocal() as db:
        user = User(
            username=f"dates_{uuid4().hex}",
            email=f"{uuid4()}@example.test",
            password_hash=hash_password("Test-password!9"),
        )
        db.add(user)
        await db.commit()
        identity = user.id
    yield identity
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id == identity))
        await db.commit()


async def _stored(user):
    async with SessionLocal() as db:
        row = await db.scalar(select(UserPreferences).where(UserPreferences.user_id == user))
        return dict(row.data) if row else {}


async def _store_raw(user, data):
    async with SessionLocal() as db:
        db.add(UserPreferences(user_id=user, data=data))
        await db.commit()


async def test_an_account_starts_with_day_month_year_and_no_choice(user_id):
    async with SessionLocal() as db:
        prefs = await load_preferences(db, user_id)
    assert prefs["date_format"] == "dmy"
    assert prefs["time_format"] is None
    assert prefs["date_format_chosen"] is False
    assert prefs["date_format_offered"] is False
    assert "date_display" not in prefs and "partial_date_style" not in prefs


async def test_date_choices_are_saved_and_read_back(user_id):
    async with SessionLocal() as db:
        await save_preferences(
            db,
            user_id,
            {
                "date_format": "mdy",
                "time_format": "24h",
                "date_format_chosen": True,
                "date_format_offered": True,
            },
        )
    async with SessionLocal() as db:
        prefs = await load_preferences(db, user_id)
    assert (prefs["date_format"], prefs["time_format"]) == ("mdy", "24h")
    assert prefs["date_format_chosen"] is True and prefs["date_format_offered"] is True
    # other preferences keep their defaults
    assert prefs["title_language"] == "english"


@pytest.mark.parametrize("style", ["dmy", "long", "mdy", "iso"])
def test_every_date_format_is_accepted(style):
    assert validate_preference("date_format", style) == style


@pytest.mark.parametrize("value", [None, "24h", "12h"])
def test_time_formats_are_accepted(value):
    assert validate_preference("time_format", value) == value


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("date_format", "us"),
        ("date_format", "locale"),
        ("date_format", "ymd"),
        ("date_format", True),
        ("date_format", None),
        ("time_format", "am"),
        ("time_format", True),
        ("date_format_chosen", "yes"),
        ("date_format_offered", 1),
    ],
)
def test_unknown_date_choices_are_refused(key, value):
    with pytest.raises(ValueError, match=key):
        validate_preference(key, value)


async def test_a_stored_iso_display_reads_as_an_explicit_iso_choice(user_id):
    await _store_raw(user_id, {"date_display": "iso", "partial_date_style": "short"})
    async with SessionLocal() as db:
        prefs = await load_preferences(db, user_id)
    assert prefs["date_format"] == "iso" and prefs["date_format_chosen"] is True
    assert "date_display" not in prefs and "partial_date_style" not in prefs


async def test_a_stored_locale_display_falls_back_to_the_default_and_keeps_the_offer(user_id):
    await _store_raw(user_id, {"date_display": "locale", "partial_date_style": "long"})
    async with SessionLocal() as db:
        prefs = await load_preferences(db, user_id)
    assert prefs["date_format"] == "dmy" and prefs["date_format_chosen"] is False
    assert "date_display" not in prefs


async def test_an_old_frontend_writing_date_display_is_translated_not_refused(user_id):
    async with SessionLocal() as db:
        prefs = await save_preferences(
            db, user_id, {"date_display": "iso", "partial_date_style": "short"}
        )
    assert prefs["date_format"] == "iso" and prefs["date_format_chosen"] is True
    assert "date_display" not in prefs
    assert await _stored(user_id) == {"date_format": "iso", "date_format_chosen": True}


async def test_the_next_write_removes_stored_legacy_keys(user_id):
    await _store_raw(user_id, {"date_display": "iso", "partial_date_style": "short"})
    async with SessionLocal() as db:
        await save_preferences(db, user_id, {"title_language": "romaji"})
    assert await _stored(user_id) == {
        "date_format": "iso",
        "date_format_chosen": True,
        "title_language": "romaji",
    }


async def test_an_explicit_date_format_wins_over_a_legacy_key_in_the_same_write(user_id):
    async with SessionLocal() as db:
        prefs = await save_preferences(db, user_id, {"date_display": "iso", "date_format": "long"})
    assert prefs["date_format"] == "long"
    assert await _stored(user_id) == {"date_format": "long"}


@pytest.mark.parametrize(
    ("key", "value"), [("date_display", "us"), ("partial_date_style", "medium")]
)
async def test_invalid_legacy_values_are_still_refused(user_id, key, value):
    async with SessionLocal() as db:
        with pytest.raises(ValueError, match=key):
            await save_preferences(db, user_id, {key: value})


def test_translation_leaves_current_keys_alone():
    current = {"date_format": "mdy", "time_format": "12h"}
    assert translate_legacy_date_preferences(current) is current
