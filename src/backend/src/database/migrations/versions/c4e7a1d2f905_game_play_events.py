"""record explicit play logs separately from lifetime playtime corrections (#467)

"Log play now" (or a chosen earlier time) is an activity event. Correcting a lifetime total or
a provider catching up on one is not, so neither moves last played. The events are kept in
their own table; the game's ``last_played_at`` only moves forward to a logged play.

Revision ID: c4e7a1d2f905
Revises: b8d2f6a41c93
Create Date: 2026-11-07

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "c4e7a1d2f905"
down_revision: Union[str, None] = "b8d2f6a41c93"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "game_play_events"


def upgrade() -> None:
    h.create_table_if_missing(
        _TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("game_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("played_at", sa.BigInteger(), nullable=False),
        sa.Column("recorded_at", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id", "game_id"],
            ["games.user_id", "games.id"],
            ondelete="CASCADE",
            name="fk_game_play_events_game",
        ),
        sa.UniqueConstraint("user_id", "request_id", name="uq_game_play_events_request"),
        sa.CheckConstraint("played_at >= 0", name="ck_game_play_events_played_at"),
    )
    h.create_index_if_missing("ix_game_play_events_game_played", _TABLE, ["game_id", "played_at"])


def downgrade() -> None:
    op.drop_table(_TABLE)
