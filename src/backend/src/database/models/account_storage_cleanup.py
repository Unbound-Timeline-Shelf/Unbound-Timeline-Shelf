"""Durable list of storage a deleted account still has to have removed (issue #471).

A deleted game's trash folder is named for the game, not its owner, so once the game rows are
gone nothing else links that folder to the account. One row per game is written in the same
transaction that deletes the account and is removed only when the file cleanup has finished,
so an interruption between the two (a crash, a restart, a failed audit write) can always be
retried. No foreign keys: the rows must outlive the account and its games.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import BigInteger
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, unix_timestamp


class AccountStorageCleanup(Base):
    __tablename__ = "account_storage_cleanup"

    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    game_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
