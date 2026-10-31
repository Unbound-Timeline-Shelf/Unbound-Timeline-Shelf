"""Provider identities a person said are not this game, so a metadata refresh never adopts them.

A refresh candidate is described by the provider ids its sources reported. Rejecting it stores
each id the game did not already hold; a later refresh whose candidate reports any of them is
answered `rejected` and changes nothing. A rejection is forgotten one id at a time or all at
once (DELETE /api/game/{id}/metadata/rejections)."""

from uuid import UUID, uuid4

from sqlalchemy import BigInteger, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, unix_timestamp


class GameMetadataRejection(Base):
    __tablename__ = "game_metadata_rejections"
    __table_args__ = (
        UniqueConstraint(
            "game_id", "namespace", "identity", name="uq_game_metadata_rejections_game_identity"
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    game_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("games.id", ondelete="CASCADE"), nullable=False
    )
    # the same shapes as the Plugin API's provider_ids keys and values
    namespace: Mapped[str] = mapped_column(String(128), nullable=False)
    identity: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
