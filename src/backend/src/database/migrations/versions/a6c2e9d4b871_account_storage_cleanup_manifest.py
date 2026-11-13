"""account storage cleanup manifest

Keeps, per deleted account, the ids of the games whose trash folders still have to be removed,
written in the account-delete transaction (issue #471, review on #490). New empty table only.

Revision ID: a6c2e9d4b871
Revises: b7d3a9e14c52
Create Date: 2026-11-08 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a6c2e9d4b871"
down_revision: Union[str, None] = "b7d3a9e14c52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("account_storage_cleanup"):
        return

    op.create_table(
        "account_storage_cleanup",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("game_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "game_id"),
    )


def downgrade() -> None:
    if not sa.inspect(op.get_bind()).has_table("account_storage_cleanup"):
        return

    op.drop_table("account_storage_cleanup")
