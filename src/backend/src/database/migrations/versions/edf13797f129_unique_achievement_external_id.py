"""one achievement row per (game, provider, external id)

Revision ID: edf13797f129
Revises: b7d3a9e14c52
Create Date: 2026-10-31 20:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "edf13797f129"
down_revision: Union[str, None] = "b7d3a9e14c52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "uq_achievements_game_provider_external_id"

# For every duplicate, the row that stays: an unlocked row first, then the newest, then a fixed
# order so a rerun picks the same row.
_DUPLICATES = """
    SELECT id, keep FROM (
        SELECT id, FIRST_VALUE(id) OVER (
            PARTITION BY game_id, provider, external_id
            ORDER BY unlocked DESC, created_at DESC, id
        ) AS keep
        FROM achievements
    ) ranked WHERE id <> keep
"""


def _references() -> list[tuple[str, str]]:
    """(table, column) of every foreign key that points at achievements.id."""
    inspector = sa.inspect(op.get_bind())
    found = []
    for table in inspector.get_table_names():
        for fk in inspector.get_foreign_keys(table):
            if fk["referred_table"] == "achievements" and fk["referred_columns"] == ["id"]:
                found.extend((table, column) for column in fk["constrained_columns"])
    return found


def upgrade() -> None:
    if h.has_constraint("achievements", _CONSTRAINT):
        return
    bind = op.get_bind()
    duplicates = bind.scalar(sa.text(f"SELECT count(*) FROM ({_DUPLICATES}) d"))  # noqa: S608
    if duplicates:
        print(f"achievements: removing {duplicates} duplicate row(s) before adding {_CONSTRAINT}")
        # Media and notes linked to a duplicate keep their link: they move to the row that stays
        # (the foreign keys are ON DELETE SET NULL, so deleting first would unlink them).
        for table, column in _references():
            # Table and column names come from the database catalogue, not from input.
            bind.execute(
                sa.text(
                    f"UPDATE {table} AS t SET {column} = d.keep "  # noqa: S608
                    f"FROM ({_DUPLICATES}) d WHERE t.{column} = d.id"
                )
            )
        remove = f"DELETE FROM achievements WHERE id IN (SELECT id FROM ({_DUPLICATES}) d)"  # noqa: S608
        bind.execute(sa.text(remove))
    op.create_unique_constraint(_CONSTRAINT, "achievements", ["game_id", "provider", "external_id"])


def downgrade() -> None:
    # Removed duplicates are not restored: they repeated a row that stays.
    if h.has_constraint("achievements", _CONSTRAINT):
        op.drop_constraint(_CONSTRAINT, "achievements", type_="unique")
