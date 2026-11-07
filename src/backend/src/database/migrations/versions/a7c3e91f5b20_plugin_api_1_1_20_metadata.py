"""store Plugin API 1.1.20 metadata: partial release dates, releases, pending game relations and
three-state crossplay

Revision ID: a7c3e91f5b20
Revises: 38d06d75381e
Create Date: 2026-11-03 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "a7c3e91f5b20"
down_revision: Union[str, None] = "38d06d75381e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_PRECISION_CHECK = "ck_games_release_date_precision"
_PENDING = "game_pending_relationships"
_PENDING_USER_INDEX = "ix_game_pending_relationships_user_id"

# Crossplay was a boolean. ``true`` was a confirmed yes; ``false`` could not tell "no" from
# "nobody said", so it becomes unknown (JSON null) rather than a "no" nobody reported
# (CONTRACTS-v1 C7.2, decision H3). Only boolean values are rewritten, so a second run is a no-op.
_CROSSPLAY_TO_VOCAB = """
UPDATE games
SET play_compatibility = jsonb_set(
    play_compatibility,
    '{crossplay}',
    CASE WHEN (play_compatibility->>'crossplay')::boolean THEN '"yes"'::jsonb ELSE 'null'::jsonb END
)
WHERE jsonb_typeof(play_compatibility) = 'object'
  AND jsonb_typeof(play_compatibility->'crossplay') = 'boolean'
"""

# The boolean the older code reads: a "yes" or a "partial" is true, anything else false.
_CROSSPLAY_TO_BOOLEAN = """
UPDATE games
SET play_compatibility = jsonb_set(
    play_compatibility,
    '{crossplay}',
    to_jsonb(COALESCE(play_compatibility->>'crossplay' IN ('yes', 'partial'), false))
)
WHERE jsonb_typeof(play_compatibility) = 'object'
  AND play_compatibility ? 'crossplay'
  AND jsonb_typeof(play_compatibility->'crossplay') <> 'boolean'
"""


def upgrade() -> None:
    h.add_column_if_missing("games", sa.Column("release_date_precision", sa.String(5)))
    h.add_column_if_missing("games", sa.Column("releases", postgresql.JSONB(astext_type=sa.Text())))
    if not h.has_constraint("games", _PRECISION_CHECK):
        op.create_check_constraint(  # pylint: disable=no-member
            _PRECISION_CHECK,
            "games",
            "release_date_precision IS NULL OR release_date_precision IN ('year', 'month', 'day')",
        )
    h.add_column_if_missing(
        "game_source_observations", sa.Column("observed_release_precision", sa.String(5))
    )
    h.create_table_if_missing(
        _PENDING,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("game_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("namespace", sa.String(length=128), nullable=False),
        sa.Column("external_id", sa.String(length=512), nullable=False),
        sa.Column("first_seen_at", sa.BigInteger(), nullable=False),
        sa.Column("last_seen_at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id", "game_id"],
            ["games.user_id", "games.id"],
            ondelete="CASCADE",
            name="fk_game_pending_relationships_game",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "game_id",
            "kind",
            "namespace",
            "external_id",
            name="uq_game_pending_relationships_target",
        ),
        sa.CheckConstraint("btrim(external_id) <> ''", name="ck_game_pending_relationships_target"),
    )
    h.create_index_if_missing(_PENDING_USER_INDEX, _PENDING, ["user_id"])
    op.execute(_CROSSPLAY_TO_VOCAB)  # pylint: disable=no-member


def downgrade() -> None:
    # Everything dropped here came from a metadata provider and is reported again by the next
    # refresh after upgrading: waiting relation references, the releases list and how precise
    # a release date is (a year-only date stays as the first day of that year). Crossplay keeps
    # its yes/no meaning; "no" and unknown both read as false, as they did before.
    op.execute(_CROSSPLAY_TO_BOOLEAN)  # pylint: disable=no-member
    if h.has_table(_PENDING):
        h.drop_index_if_exists(_PENDING_USER_INDEX, _PENDING)
        op.drop_table(_PENDING)  # pylint: disable=no-member
    if h.has_column("game_source_observations", "observed_release_precision"):
        op.drop_column(  # pylint: disable=no-member
            "game_source_observations", "observed_release_precision"
        )
    if h.has_constraint("games", _PRECISION_CHECK):
        op.drop_constraint(_PRECISION_CHECK, "games", type_="check")  # pylint: disable=no-member
    for column in ("releases", "release_date_precision"):
        if h.has_column("games", column):
            op.drop_column("games", column)  # pylint: disable=no-member
