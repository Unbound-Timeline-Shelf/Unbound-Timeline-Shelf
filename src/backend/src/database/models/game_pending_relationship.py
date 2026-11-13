"""Game relations a metadata provider reported whose other game is not in the library yet.

Plugin API 1.1.20 lets a provider say "this game is a DLC of / sequel to / remaster of X" by X's
provider ids (CONTRACTS-v1 C6.4). When the person tracks X, the refresh records the edge in
``game_relationships`` with origin ``ingest``. When they do not, the reference waits here and is
turned into an edge by a later refresh of either game once one of them holds the matching id.

Everything in this table is derived from provider data and is reported again by the next
refresh, so it is not a person's decision: the migration that adds it drops it on downgrade."""

from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, unix_timestamp

# A refresh keeps at most this many waiting references per game (oldest seen are dropped).
PENDING_RELATIONS_PER_GAME = 200


class GamePendingRelationship(Base):
    __tablename__ = "game_pending_relationships"
    __table_args__ = (
        # the composite key proves the game belongs to the same person as the row
        ForeignKeyConstraint(
            ["user_id", "game_id"],
            ["games.user_id", "games.id"],
            ondelete="CASCADE",
            name="fk_game_pending_relationships_game",
        ),
        UniqueConstraint(
            "game_id",
            "kind",
            "namespace",
            "external_id",
            name="uq_game_pending_relationships_target",
        ),
        CheckConstraint("btrim(external_id) <> ''", name="ck_game_pending_relationships_target"),
        Index("ix_game_pending_relationships_user_id", "user_id"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # the game the provider described; the edge runs from it to the referenced game
    game_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # a vocabulary relation type (contracts/vocab-v1.json relation_type), or ``same_game``: a
    # provider merged this game's id into one another tracked game holds (duplicate review)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    # the referenced game, as the provider identifies it
    namespace: Mapped[str] = mapped_column(String(128), nullable=False)
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    first_seen_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
    last_seen_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=unix_timestamp, onupdate=unix_timestamp
    )
