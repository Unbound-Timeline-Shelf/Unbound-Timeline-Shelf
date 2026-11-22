"""log of achievement catalogue re-keys, replayed backwards on downgrade

Revision ID: c558a537a476
Revises: edf13797f129
Create Date: 2026-10-31 20:05:00.000000

"""

import json
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "c558a537a476"
down_revision: Union[str, None] = "edf13797f129"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLE = "achievement_rekey_log"
_CATALOG = "catalog"


def upgrade() -> None:
    h.create_table_if_missing(
        _TABLE,
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("game_id", sa.UUID(), nullable=False),
        sa.Column("old_external_id", sa.String(length=200), nullable=False),
        sa.Column("new_external_id", sa.String(length=200), nullable=False),
        sa.Column("method", sa.String(length=16), nullable=False),
        sa.Column("subject", sa.String(length=16), nullable=False),
        sa.Column("dropped_override", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("at", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("seq"),
    )
    h.create_index_if_missing(op.f("ix_achievement_rekey_log_game_id"), _TABLE, ["game_id"])


def _exists(bind, table: str, game_id, external_id: str) -> bool:
    return bool(
        bind.scalar(
            sa.text(
                f"SELECT EXISTS (SELECT 1 FROM {table} "  # noqa: S608
                "WHERE game_id = :game AND provider = :provider AND external_id = :external_id)"
            ),
            {"game": game_id, "provider": _CATALOG, "external_id": external_id},
        )
    )


def _move(bind, table: str, game_id, source: str, target: str) -> bool:
    """Move the catalogue row or manual mark at ``source`` back to ``target`` if that is free."""
    if not _exists(bind, table, game_id, source) or _exists(bind, table, game_id, target):
        return False
    bind.execute(
        sa.text(
            f"UPDATE {table} SET external_id = :target "  # noqa: S608
            "WHERE game_id = :game AND provider = :provider AND external_id = :source"
        ),
        {"target": target, "game": game_id, "provider": _CATALOG, "source": source},
    )
    return True


def _restore_override(bind, game_id, saved: dict) -> bool:
    """Put back a manual mark the re-key replaced, unless its id is taken again."""
    external_id = saved["external_id"]
    if _exists(bind, "achievement_overrides", game_id, external_id):
        return False
    taken = bind.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM achievement_overrides WHERE id = :id)"),
        {"id": saved["id"]},
    )
    bind.execute(
        sa.text(
            "INSERT INTO achievement_overrides "
            "(id, game_id, provider, external_id, name, description, unlocked, unlocked_at, "
            "updated_at) VALUES (CAST(:id AS uuid), :game, :provider, :external_id, :name, :description, "
            ":unlocked, :unlocked_at, :updated_at)"
        ),
        {
            "id": str(uuid.uuid4()) if taken else saved["id"],
            "game": game_id,
            "provider": saved.get("provider") or _CATALOG,
            "external_id": external_id,
            "name": saved["name"],
            "description": saved.get("description"),
            "unlocked": bool(saved.get("unlocked")),
            "unlocked_at": saved.get("unlocked_at"),
            "updated_at": saved["updated_at"],
        },
    )
    return True


def _replay_backwards() -> None:
    """Undo every logged re-key, newest first, so catalogue rows and manual marks get their old
    external ids back. A step whose target is taken again is skipped and reported; nothing is
    deleted."""
    bind = op.get_bind()
    entries = bind.execute(
        sa.text(
            f"SELECT game_id, old_external_id, new_external_id, method, subject, "  # noqa: S608
            f"dropped_override FROM {_TABLE} ORDER BY seq DESC"
        )
    ).all()
    skipped = 0
    for game_id, old, new, method, subject, dropped in entries:
        if method == "dropped":
            saved = dropped if isinstance(dropped, dict) else json.loads(dropped or "null")
            done = bool(saved) and _restore_override(bind, game_id, saved)
        elif subject == "row":
            done = _move(bind, "achievements", game_id, new, old)
        else:
            done = _move(bind, "achievement_overrides", game_id, new, old)
        skipped += not done
    if entries:
        print(f"{_TABLE}: replayed {len(entries) - skipped} re-key step(s), skipped {skipped}")


def downgrade() -> None:
    if not h.has_table(_TABLE):
        return
    _replay_backwards()
    h.drop_index_if_exists(op.f("ix_achievement_rekey_log_game_id"), _TABLE)
    op.drop_table(_TABLE)
