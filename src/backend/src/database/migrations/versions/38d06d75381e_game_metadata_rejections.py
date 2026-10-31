"""remember provider identities a person rejected for a game's metadata refresh

Revision ID: 38d06d75381e
Revises: c558a537a476
Create Date: 2026-10-31 22:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "38d06d75381e"
down_revision: Union[str, None] = "c558a537a476"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "game_metadata_rejections"
_USER_INDEX = "ix_game_metadata_rejections_user_id"


def upgrade() -> None:
    h.create_table_if_missing(
        _TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("game_id", sa.UUID(), nullable=False),
        sa.Column("namespace", sa.String(length=128), nullable=False),
        sa.Column("identity", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "game_id", "namespace", "identity", name="uq_game_metadata_rejections_game_identity"
        ),
    )
    # deleting a user cascades here; without an index that is a scan of the whole table
    h.create_index_if_missing(_USER_INDEX, _TABLE, ["user_id"])


def downgrade() -> None:
    # A rejection is a person's decision; dropping it would let the next refresh adopt the
    # identity again without saying so.
    h.refuse_if_populated(_TABLE, "metadata refresh rejections")
    if h.has_table(_TABLE):
        h.drop_index_if_exists(_USER_INDEX, _TABLE)
        op.drop_table(_TABLE)
