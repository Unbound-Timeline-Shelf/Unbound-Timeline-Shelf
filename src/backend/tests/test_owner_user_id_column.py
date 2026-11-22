"""The shared owner `user_id` column: a required, indexed foreign key deleted with its user.

Rows a person owns (metadata refresh rejections, notification verification challenges) must go
when the user is deleted, and the delete cascade must not scan the table. Both models declare the
column through one helper, so the two definitions cannot drift apart."""

import pytest
from sqlalchemy import Column

from src.database.base import owner_user_id_column
from src.database.models.game_metadata_rejection import GameMetadataRejection
from src.database.models.notification_verification import NotificationVerification


def _assert_owner_column(column: Column) -> None:
    assert column.nullable is False
    assert column.index is True
    assert column.type.as_uuid is True
    (foreign_key,) = column.foreign_keys
    assert foreign_key.target_fullname == "users.id"
    assert foreign_key.ondelete == "CASCADE"


def test_helper_builds_a_required_indexed_cascading_user_fk() -> None:
    _assert_owner_column(owner_user_id_column().column)


@pytest.mark.parametrize("model", [GameMetadataRejection, NotificationVerification])
def test_owned_models_declare_user_id_through_the_helper(model: type) -> None:
    _assert_owner_column(model.__table__.c.user_id)
