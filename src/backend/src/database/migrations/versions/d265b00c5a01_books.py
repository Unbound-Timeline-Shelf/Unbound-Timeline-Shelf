"""books, book editions and read-throughs (#265 phase 1)

One additive revision: it creates ``books``, ``book_editions`` and ``book_reads`` and alters no
existing table. ``books.active_edition_id`` and ``book_editions.book_id`` point at each other, so
the active-edition key is added once both tables exist. Downgrade drops the three tables.

Revision ID: d265b00c5a01
Revises: c4e7a1d2f905
Create Date: 2026-11-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from src.database import migration_helpers as h

# revision identifiers, used by Alembic.
revision: str = "d265b00c5a01"
down_revision: Union[str, None] = "c4e7a1d2f905"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_UUID = postgresql.UUID(as_uuid=True)
_PRECISION = "IN ('year', 'month', 'day')"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.BigInteger(), nullable=False),
    ]


def _string_list(name: str) -> sa.Column:
    return sa.Column(name, postgresql.ARRAY(sa.String()), nullable=False, server_default="{}")


def upgrade() -> None:
    h.create_table_if_missing(
        "books",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("user_id", _UUID, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("deleted_at", sa.BigInteger(), nullable=True),
        sa.Column(
            "provider_ids", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("sort_title", sa.String(500), nullable=False),
        sa.Column("subtitle", sa.String(300), nullable=True),
        _string_list("authors"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("first_published", sa.Date(), nullable=True),
        sa.Column("first_published_precision", sa.String(5), nullable=True),
        sa.Column("language", sa.String(16), nullable=True),
        _string_list("genres"),
        _string_list("tags"),
        sa.Column("series_name", sa.String(200), nullable=True),
        sa.Column("series_position", sa.String(16), nullable=True),
        sa.Column("cover_url", sa.Text(), nullable=True),
        _string_list("locked_fields"),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("priority", sa.String(20), nullable=True),
        sa.Column("favorite", sa.Boolean(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("rating_overall", sa.Numeric(4, 2), nullable=True),
        sa.Column("personal_rank", sa.Integer(), nullable=True),
        sa.Column("active_edition_id", _UUID, nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            f"first_published_precision IS NULL OR first_published_precision {_PRECISION}",
            name="ck_books_first_published_precision",
        ),
    )
    h.create_index_if_missing("ix_books_user_id", "books", ["user_id"])

    h.create_table_if_missing(
        "book_editions",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("book_id", _UUID, sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column("isbn_10", sa.String(10), nullable=True),
        sa.Column("isbn_13", sa.String(13), nullable=True),
        sa.Column(
            "other_identifiers",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("format", sa.String(20), nullable=False),
        sa.Column("publisher", sa.String(200), nullable=True),
        sa.Column("publish_date", sa.Date(), nullable=True),
        sa.Column("publish_date_precision", sa.String(5), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(16), nullable=True),
        sa.Column("cover_url", sa.Text(), nullable=True),
        sa.Column("ownership", sa.String(20), nullable=False),
        sa.Column("acquired_at", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            f"publish_date_precision IS NULL OR publish_date_precision {_PRECISION}",
            name="ck_book_editions_publish_date_precision",
        ),
        sa.CheckConstraint(
            "page_count IS NULL OR page_count BETWEEN 1 AND 100000",
            name="ck_book_editions_page_count",
        ),
    )
    h.create_index_if_missing("ix_book_editions_book_id", "book_editions", ["book_id"])
    h.create_index_if_missing(
        "uq_book_editions_book_isbn13",
        "book_editions",
        ["book_id", "isbn_13"],
        unique=True,
        postgresql_where=sa.text("isbn_13 IS NOT NULL"),
    )
    if not h.has_constraint("books", "fk_books_active_edition"):
        op.create_foreign_key(
            "fk_books_active_edition",
            "books",
            "book_editions",
            ["active_edition_id"],
            ["id"],
            ondelete="SET NULL",
        )

    h.create_table_if_missing(
        "book_reads",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("book_id", _UUID, sa.ForeignKey("books.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "edition_id",
            _UUID,
            sa.ForeignKey("book_editions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("started_on", sa.Date(), nullable=True),
        sa.Column("finished_on", sa.Date(), nullable=True),
        sa.Column("progress_unit", sa.String(10), nullable=False),
        sa.Column("progress_value", sa.Numeric(8, 2), nullable=True),
        sa.Column("outcome", sa.String(10), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "progress_value IS NULL OR progress_value >= 0", name="ck_book_reads_progress_min"
        ),
        sa.CheckConstraint(
            "progress_value IS NULL OR progress_unit <> 'percent' OR progress_value <= 100",
            name="ck_book_reads_percent_max",
        ),
        sa.CheckConstraint(
            "started_on IS NULL OR finished_on IS NULL OR finished_on >= started_on",
            name="ck_book_reads_dates",
        ),
    )
    h.create_index_if_missing("ix_book_reads_book_id", "book_reads", ["book_id"])


def downgrade() -> None:
    op.drop_table("book_reads")
    op.drop_constraint("fk_books_active_edition", "books", type_="foreignkey")
    op.drop_table("book_editions")
    op.drop_table("books")
