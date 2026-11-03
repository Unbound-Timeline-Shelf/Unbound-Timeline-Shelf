import time
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from src.database.base import Base
from src.plugin_api.vocab import CROSSPLAY

if TYPE_CHECKING:
    from src.database.models.user import User

# Folder name rules: letters, digits, underscore, hyphen only — no spaces,
# no path separators, no reserved filesystem characters. Adjust the
# character class here and in schemas/game.py if you want to allow more.
FOLDER_NAME_PATTERN = r"^[A-Za-z0-9_-]+$"
FOLDER_NAME_MAX_LENGTH = 150


class GameStatus(str, Enum):
    """Game status aligned with Playnite."""

    DROPPED = "DROPPED"
    WISHLIST = "WISHLIST"
    BACKLOG = "BACKLOG"
    ON_HOLD = "ON_HOLD"
    PLAYING = "PLAYING"
    PLAYED = "PLAYED"
    BEATEN = "BEATEN"
    MASTERED = "MASTERED"


# Repeated column declarations preserve this table's explicit schema contract.
class Game(Base):
    # pylint: disable=duplicate-code
    __tablename__ = "games"
    __table_args__ = (
        # folder names only need to be unique within a user's storage
        # namespace. Different users can therefore safely use the same
        # folder name while still keeping active games unique per user.
        Index(
            "ix_games_user_folder_location_active",
            "user_id",
            "folder_location",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # the library list pages through a user's live games in title order; this index
        # returns each page without sorting the whole library (#103)
        Index(
            "ix_games_user_sort_title_live",
            "user_id",
            "sort_title",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # target of the identity model's composite foreign keys, which make a
        # reference to another user's game impossible (see models/game_identity.py)
        UniqueConstraint("user_id", "id", name="uq_games_user_id_id"),
        CheckConstraint(
            "release_date_precision IS NULL OR release_date_precision IN ('year', 'month', 'day')",
            name="ck_games_release_date_precision",
        ),
    )

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user: Mapped["User"] = relationship()

    folder_location: Mapped[str] = mapped_column(
        String(FOLDER_NAME_MAX_LENGTH),
        nullable=False,
    )

    # Optional identifier of the corresponding Playnite library entry.
    # This is deliberately not unique: the same app account can receive
    # imports from multiple Playnite libraries, while ordinary app-created
    # games simply leave this unset.
    playnite_guid: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=True,
        index=True,
    )

    # set instead of actually deleting the row — see features/trash/sweep.py,
    # which purges the row and moves the on-disk folder to trash for good
    # after 7 days. NULL means active/not deleted.
    deleted_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # ------------------------------------------------------------------
    # Basic metadata
    # ------------------------------------------------------------------

    title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    sort_title: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    provider_ids: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'")
    )

    # Metadata fields a user has deliberately changed in the game editor.
    # Provider refreshes skip these values rather than silently replacing a manual override.
    locked_fields: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
    )

    # The first day of the release period ("2011" is stored as 2011-01-01), so sorting and the
    # release-year statistics keep working; ``release_date_precision`` says how much of it is known.
    release_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )
    # "year", "month" or "day"; NULL is a legacy value, which is a day (Plugin API 1.1.20, C5.4).
    release_date_precision: Mapped[str | None] = mapped_column(String(5), nullable=True)
    # Up to five provider-reported releases (MetadataRelease objects: date, precision, platform,
    # region, edition, category). Provider-owned like provider_custom: replaced by a refresh.
    releases: Mapped[list[dict] | None] = mapped_column(JSONB, nullable=True)

    developer: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    publisher: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    series: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    tags: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
    )
    play_compatibility: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # provider-reported fields the host has no column for (read-only; replaced by refreshes)
    provider_custom: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    features: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
    )

    # collection names this game belongs to — plain names, same shape as
    # tags/features, not a join table. A collection with zero games still
    # only exists client-side (localStorage) until a game is added to it.
    collections: Mapped[list[str]] = mapped_column(
        ARRAY(String),
        nullable=False,
        default=list,
    )

    age_rating: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    time_to_beat_hours: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 1),
        nullable=True,
    )
    time_to_beat_extras_hours: Mapped[Decimal | None] = mapped_column(Numeric(6, 1), nullable=True)
    time_to_beat_completionist_hours: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 1), nullable=True
    )
    time_to_beat_all_hours: Mapped[Decimal | None] = mapped_column(Numeric(6, 1), nullable=True)

    # eager (selectin, not the default lazy) — unlike every other
    # relationship on this model, GameRead serializes `links` straight into
    # the API response, and a lazy load can't run once the async session
    # that produced the row is out of the request's await context
    links: Mapped[list["GameLink"]] = relationship(
        back_populates="game",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    source: Mapped[str | None] = mapped_column(  # source (string — e.g. "Steam", "GOG", "physical")
        String(50),
        nullable=True,
    )

    # the system it's played on ("PC", "PlayStation 5", "Switch"...) —
    # separate from `source`, which is where the copy came from: a Steam
    # game and a GOG game are both PC, a physical disc could be any console.
    # NULL means not recorded; the UI then falls back to the source.
    platform: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    # edition details ("NA", "PAL", "JP"... / "English", "Japanese"...),
    # mostly meaningful for physical or imported copies
    region: Mapped[str | None] = mapped_column(String(50), nullable=True)
    language: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # a library-sync provider's own stable id for this game (Steam appid,
    # RetroAchievements GameID, PSN npCommunicationId) — NULL for
    # manually-added/search-added games. A title alone isn't a stable
    # identity: Steam occasionally reports a different display name for the
    # same appid across calls (e.g. briefly appending "- GOTY Edition"),
    # which was creating duplicate rows for the same real game on re-sync.
    external_id: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # epoch seconds a library sync (Steam/RetroAchievements/PlayStation)
    # first noticed this game missing from the account's current
    # owned-games/library pull; NULL means either not synced from an
    # account or still present there. Never causes a deletion or an
    # overwrite by itself — a sync only sets/clears this flag so the user
    # can review and decide (uninstalled vs. refunded vs. temporarily
    # delisted are all indistinguishable from the API alone).
    stale_since: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # per-game opt-in for the account/profile switcher (Notes checklist +
    # Screenshots gallery scoping) — off by default since most games never
    # need more than one account; a multi-account game like OSRS turns it
    # on for just that game instead of a global setting cluttering every
    # other game's page.
    profiles_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # a second, independent opt-in — accounts/profiles work for any game
    # (checklist + media grouping, no game-specific assumptions), but the
    # WiseOldMan sync/stat-icon/skill-boss-grouping UI on the Stats card is
    # OSRS-specific and would be nonsense noise on every other game's
    # accounts. Off by default, same as profiles_enabled.
    osrs_stats_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # this game's overrides of the page defaults in Settings: which tabs show,
    # which buttons are hidden, the tab it opens on. Only what differs from the
    # defaults is stored, validated by core/page_settings.py. NULL means none.
    page_settings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # ------------------------------------------------------------------
    # Modpack / mod / DLC / expansion relationships
    # ------------------------------------------------------------------
    # A generic self-referential tree, not a boolean is_modded — "modded"
    # and "DLC/expansion" are related but distinct concepts (GTNH is a
    # MODPACK of Minecraft; Phantom Liberty is an EXPANSION of Cyberpunk
    # 2077), and a title can have several of these off the same base game.
    # Each variant (a modpack, a DLC, a total conversion...) is its own
    # full Game row — not a sub-object — so saves/achievements/screenshots
    # already scope correctly to it via their existing game_id FK, with no
    # separate "configuration" layer needed. relationship_type is a plain
    # string, not a hard DB enum: new relationship kinds should never need
    # a migration, just a code change to the allowed set in the schema.
    parent_game_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("games.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # "mod" | "modpack" | "expansion" | "dlc" | "standalone_expansion" |
    # "total_conversion" | "owned_copy" — meaningless (should be NULL) when parent_game_id
    # is NULL, i.e. for a base/standalone game
    relationship_type: Mapped[str | None] = mapped_column(String(30), nullable=True)

    parent_game: Mapped["Game | None"] = relationship(
        remote_side=[id], back_populates="child_games"
    )
    child_games: Mapped[list["Game"]] = relationship(back_populates="parent_game")

    # ------------------------------------------------------------------
    # Personal library state
    # ------------------------------------------------------------------

    status: Mapped[GameStatus] = mapped_column(
        SAEnum(GameStatus, native_enum=False, length=30),
        nullable=False,
        default=GameStatus.WISHLIST,
    )

    priority: Mapped[str | None] = mapped_column(
        String(20),
        nullable=True,
    )

    favorite: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    resume_note: Mapped[str | None] = mapped_column(
        String(2000),
        nullable=True,
    )

    playtime_seconds: Mapped[int] = mapped_column(
        nullable=False,
        default=0,
    )
    # epoch seconds — populated from Steam's rtime_last_played during a
    # library sync; NULL when unknown (manually-added games, or a provider
    # that doesn't report this, e.g. RetroAchievements/PSN today)
    last_played_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # epoch seconds (midnight = no time set, just a date — same convention
    # as purchase_date below) — set automatically the first time a game's
    # status transitions to Mastered (see update_game/library_sync.py), but
    # user-editable too in case that auto-set date needs correcting
    completion_date: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # ownership ({ format: "digital" | "physical", purchase_date, price, condition })

    purchase_date: Mapped[int | None] = (
        mapped_column(  # Unix timestamp, midnight = no time set just date
            BigInteger,
            nullable=True,
        )
    )

    purchase_price: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2),
        nullable=True,
    )

    purchase_price_currency_code: Mapped[str | None] = mapped_column(  # ISO 4217 currency code
        String(3),  # Should always be uppercase
        nullable=True,
    )

    physical_condition: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
    )

    # ------------------------------------------------------------------
    # Ratings
    # ------------------------------------------------------------------

    rating_story: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 2),
        nullable=True,
    )

    rating_gameplay: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 2),
        nullable=True,
    )

    rating_soundtrack: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 2),
        nullable=True,
    )

    rating_overall: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 2),
        nullable=True,
    )

    # ------------------------------------------------------------------
    # Personal ranking
    # ------------------------------------------------------------------

    personal_rank: Mapped[int | None] = mapped_column(
        nullable=True,
    )

    # ------------------------------------------------------------------
    # Timestamps
    # ------------------------------------------------------------------

    created_at: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=lambda: int(time.time()),
    )

    updated_at: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=lambda: int(time.time()),
        onupdate=lambda: int(time.time()),
    )

    @validates("release_date")
    def _new_day_is_a_day(self, _key: str, value: date | None) -> date | None:
        """A different date from an editor, import or sync is a day; a refresh that knows a
        coarser precision sets ``release_date_precision`` after the date. A new row keeps
        whatever precision it was built with."""
        if sa_inspect(self).has_identity and value != self.release_date:
            self.release_date_precision = None
        return value

    @validates("play_compatibility")
    def _store_crossplay(self, _key: str, value: dict | None) -> dict | None:
        return stored_play_compatibility(value, self.play_compatibility)


def crossplay_value(raw: object) -> str | None:
    """A stored crossplay value: "yes", "partial", "no" or None (unknown).

    Before Plugin API 1.1.20 the value was a boolean; ``true`` was a "yes", while ``false`` could
    not tell "no" from "nobody said", so it is unknown (CONTRACTS-v1 C7.2, decision H3)."""
    if raw is True:
        return "yes"
    if isinstance(raw, str) and raw in CROSSPLAY:
        return raw
    return None


def crossplay_projection(value: str | None) -> bool:
    """The yes/no flag older API clients read: "yes" and "partial" are true, the rest false."""
    return value in {"yes", "partial"}


def stored_play_compatibility(value: dict | None, previous: object) -> dict | None:
    """The group-play details as stored: ``crossplay`` holds the tri-state value, never a bool.

    A write may name the value (``crossplay_status``, or a vocabulary string in ``crossplay``),
    or send only the old yes/no flag. A flag that agrees with what is stored keeps the stored
    detail (a "partial" stays partial when an older client saves "true"; a "no" stays "no" on
    "false"); otherwise "true" is "yes" and "false" is unknown."""
    if not isinstance(value, dict):
        return value
    stored = dict(value)
    has_status = "crossplay_status" in stored
    status = stored.pop("crossplay_status", None)
    raw = stored.get("crossplay")
    if has_status:
        stored["crossplay"] = crossplay_value(status)
    elif isinstance(raw, bool):
        before = crossplay_value(previous.get("crossplay")) if isinstance(previous, dict) else None
        if before is not None and crossplay_projection(before) == raw:
            stored["crossplay"] = before
        else:
            stored["crossplay"] = "yes" if raw else None
    elif "crossplay" in stored:
        stored["crossplay"] = crossplay_value(raw)
    return stored


##########################
#          Links         #
##########################


class GameLink(Base):
    __tablename__ = "game_links"

    id: Mapped[int] = mapped_column(
        primary_key=True,
    )

    game_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
    )

    label: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    url: Mapped[str] = mapped_column(
        String(2_048),
        nullable=False,
    )

    game: Mapped["Game"] = relationship(
        back_populates="links",
    )
