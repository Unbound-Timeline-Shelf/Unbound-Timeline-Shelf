import time
from typing import Any

from sqlalchemy import ForeignKey
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, MappedColumn, mapped_column


def unix_timestamp() -> int:
    """Generate integer seconds for BigInteger model defaults and API responses."""
    return int(time.time())


def owner_user_id_column() -> MappedColumn[Any]:
    """The `user_id` of a row a person owns: required, indexed, and deleted with the user.

    The index keeps the ON DELETE CASCADE from users from scanning the owning table."""
    return mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class Base(DeclarativeBase):
    """
    Base class for all SQLAlchemy models.

    Every model that inherits from Base is included
    in SQLAlchemy's metadata.
    """
