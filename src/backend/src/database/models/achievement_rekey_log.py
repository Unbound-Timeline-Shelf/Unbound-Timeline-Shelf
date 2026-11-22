"""What the catalogue re-key changed, so a downgrade can put every external id back.

``store_catalogue`` moves a catalogue row and its manual progress to a new external id when a
metadata provider changes how it identifies the same achievement (Atlas row ids becoming stable
``ak1:`` keys). Each change is one row here, replayed newest first by the downgrade of the
migration that created this table."""

from uuid import UUID, uuid4

from sqlalchemy import BigInteger, ForeignKey, Identity, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base, unix_timestamp


class AchievementRekeyLog(Base):
    __tablename__ = "achievement_rekey_log"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    # the order of the changes; ``at`` is in seconds and several changes share one
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False, unique=True)
    game_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    old_external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    new_external_id: Mapped[str] = mapped_column(String(200), nullable=False)
    # "row_id": matched through the provider's bridge id; "name": an orphaned manual mark matched
    # by its unique name; "dropped": a manual mark that lost to a newer one at the same id
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    # "row": the catalogue achievement row; "override": the manual mark on it
    subject: Mapped[str] = mapped_column(String(16), nullable=False)
    # the whole manual mark that was removed ("dropped" only), so a downgrade can restore it
    dropped_override: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=unix_timestamp)
