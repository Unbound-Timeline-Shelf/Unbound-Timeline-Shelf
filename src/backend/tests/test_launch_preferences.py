"""The account-level default launch client (#319) follows the user to every browser."""

import pytest

from src.core.preferences import load_preferences, save_preferences, validate_preference
from src.database.session import SessionLocal
from tests.test_date_preferences import user_id  # noqa: F401  (registers the fixture)


async def test_the_default_is_automatic_and_a_choice_is_saved(user_id):  # noqa: F811
    async with SessionLocal() as db:
        assert (await load_preferences(db, user_id))["launch_default_client"] == ""
        await save_preferences(db, user_id, {"launch_default_client": "steam"})
    async with SessionLocal() as db:
        assert (await load_preferences(db, user_id))["launch_default_client"] == "steam"
        await save_preferences(db, user_id, {"launch_default_client": ""})
    async with SessionLocal() as db:
        assert (await load_preferences(db, user_id))["launch_default_client"] == ""


@pytest.mark.parametrize(
    "value", ["steam-store", "epic", "steam://rungameid/1", "STEAM", None, True, 1]
)
def test_only_known_launch_clients_are_accepted(value):
    with pytest.raises(ValueError, match="launch_default_client"):
        validate_preference("launch_default_client", value)


@pytest.mark.parametrize("value", ["", "playnite", "steam", "gog"])
def test_known_launch_clients_are_kept(value):
    assert validate_preference("launch_default_client", value) == value
