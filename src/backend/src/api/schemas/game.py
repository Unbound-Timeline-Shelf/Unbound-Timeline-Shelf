"""Pydantic schemas for games and game metadata."""

from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)

from src.api.schemas.title_protection import TitleProtectionUpdate
from src.core.page_settings import validate_page_settings
from src.database.models.game import (
    FOLDER_NAME_MAX_LENGTH,
    FOLDER_NAME_PATTERN,
    GameStatus,
    crossplay_projection,
    crossplay_value,
)
from src.helpers.currency_codes import CURRENCY_CODES

# games.playtime_seconds is an INTEGER column; larger totals cannot be stored.
PLAYTIME_SECONDS_MAX = 2_147_483_647

GameRelationshipType = Literal[
    "mod", "modpack", "expansion", "dlc", "standalone_expansion", "total_conversion", "owned_copy"
]


class GameLinkSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    label: str = Field(min_length=1, max_length=50)
    url: str = Field(min_length=1, max_length=2_048)


class CollectionIndexGame(BaseModel):
    """Only the fields needed to build the client-owned collection overview."""

    id: UUID
    title: str
    status: GameStatus
    favorite: bool
    source: str | None
    tags: list[str]
    collections: list[str]
    playtime_seconds: int


ProviderIds = dict[
    Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$")],
    Annotated[str, Field(min_length=1, max_length=512)],
]


class GamePlayCompatibility(BaseModel):
    """Group play details. Without ``source`` they are user-confirmed; ``provider`` marks values
    a metadata provider reported (replaced on refresh, never over user-confirmed details)."""

    model_config = ConfigDict(extra="forbid")
    min_players: int | None = Field(default=None, ge=1, le=32, strict=True)
    max_players: int | None = Field(default=None, ge=1, le=32, strict=True)
    duration_minutes: int | None = Field(default=None, ge=1, le=1440, strict=True)
    # The yes/no flag every client has always sent. "true" is a confirmed yes; "false" is not a
    # confirmed no, so it is stored as unknown unless the stored value already says "no".
    crossplay: bool = Field(default=False, strict=True)
    # The stored value itself (contracts/vocab-v1.json crossplay): set it to say "partial" or a
    # confirmed "no", or null for unknown. Sent alone it also sets ``crossplay``.
    crossplay_status: Literal["yes", "partial", "no"] | None = None
    source: Literal["provider"] | None = None

    @model_validator(mode="before")
    @classmethod
    def _flag_and_status_agree(cls, data: Any) -> Any:
        """A client that echoes what it read and changes only the flag sends a status that no
        longer agrees with it: the flag is the edit, so that status is dropped."""
        if not isinstance(data, dict) or "crossplay_status" not in data:
            return data
        status = data["crossplay_status"]
        if "crossplay" not in data:
            return {**data, "crossplay": status in {"yes", "partial"}}
        flag = data["crossplay"]
        if isinstance(flag, bool) and flag != (status in {"yes", "partial"}):
            return {key: value for key, value in data.items() if key != "crossplay_status"}
        return data

    @model_serializer(mode="wrap")
    def _omit_unset_source(self, handler: Any) -> dict[str, Any]:
        """User-confirmed details keep their established shape: no ``source`` key, and no
        ``crossplay_status`` unless the client sent one (a sent null clears it to unknown)."""
        data = handler(self)
        if data.get("source") is None:
            data.pop("source", None)
        if "crossplay_status" not in self.model_fields_set:
            data.pop("crossplay_status", None)
        return data

    @model_validator(mode="after")
    def ordered_players(self) -> "GamePlayCompatibility":
        if (self.min_players is None) != (self.max_players is None):
            raise ValueError("Set both player limits, or leave both unknown.")
        if (
            self.min_players is not None
            and self.max_players is not None
            and self.min_players > self.max_players
        ):
            raise ValueError("Maximum players must be at least the minimum.")
        return self


class GamePlayCompatibilityRead(GamePlayCompatibility):
    """Stored group-play details as clients read them: ``crossplay`` stays the yes/no flag
    ("yes" and "partial" are true) and ``crossplay_status`` is the stored value."""

    @model_validator(mode="before")
    @classmethod
    def _from_stored(cls, data: Any) -> Any:
        return play_compatibility_view(data)

    @model_serializer(mode="wrap")
    def _omit_unset_source(self, handler: Any) -> dict[str, Any]:
        data = handler(self)
        if data.get("source") is None:
            data.pop("source", None)
        return data


def play_compatibility_view(stored: Any) -> Any:
    """Stored play_compatibility in the shape clients read (see GamePlayCompatibilityRead)."""
    if not isinstance(stored, dict) or isinstance(stored.get("crossplay"), bool):
        return stored
    status = crossplay_value(stored.get("crossplay"))
    return {**stored, "crossplay": crossplay_projection(status), "crossplay_status": status}


class GameBase(BaseModel):
    """Fields shared by create and update payloads."""

    title: str = Field(min_length=1, max_length=500)
    description: str | None = None
    release_date: date | None = None
    developer: str | None = Field(default=None, max_length=200)
    publisher: str | None = Field(default=None, max_length=200)
    series: str | None = Field(default=None, max_length=200)
    tags: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    play_compatibility: GamePlayCompatibility | None = None
    collections: list[str] = Field(default_factory=list)
    links: list[GameLinkSchema] = Field(default_factory=list)
    source: str | None = Field(default=None, max_length=50)
    provider_ids: ProviderIds = Field(default_factory=dict, max_length=32)
    platform: str | None = Field(default=None, max_length=50)
    region: str | None = Field(default=None, max_length=50)
    language: str | None = Field(default=None, max_length=50)
    parent_game_id: UUID | None = None
    relationship_type: GameRelationshipType | None = None
    age_rating: str | None = Field(default=None, max_length=20)
    time_to_beat_hours: Decimal | None = Field(default=None, ge=0)
    time_to_beat_extras_hours: Decimal | None = Field(default=None, ge=0, le=Decimal("99999.9"))
    time_to_beat_completionist_hours: Decimal | None = Field(
        default=None, ge=0, le=Decimal("99999.9")
    )
    time_to_beat_all_hours: Decimal | None = Field(default=None, ge=0, le=Decimal("99999.9"))

    folder_location: str = Field(
        min_length=1,
        max_length=FOLDER_NAME_MAX_LENGTH,
        pattern=FOLDER_NAME_PATTERN,
        description="Folder name only — letters, digits, underscore, hyphen. No spaces or path separators.",
    )
    playnite_guid: UUID | None = Field(
        default=None,
        description="Optional GUID of the corresponding Playnite library entry.",
    )

    status: GameStatus = GameStatus.BACKLOG
    priority: str | None = Field(default=None, max_length=20)
    favorite: bool = False
    profiles_enabled: bool = Field(
        default=False,
        description="Per-game opt-in for the account/profile switcher (Notes checklist + Screenshots scoping).",
    )
    osrs_stats_enabled: bool = Field(
        default=False,
        description="Per-game opt-in for OSRS-specific account features (WiseOldMan sync, skill/boss icons).",
    )
    page_settings: dict | None = Field(
        default=None,
        description="This game's overrides of the page defaults (which tabs show, which buttons are hidden).",
    )

    @field_validator("page_settings")
    @classmethod
    def _check_page_settings(cls, value: dict | None) -> dict | None:
        return validate_page_settings(value, partial=True) or None if value is not None else None

    notes: str | None = None
    resume_note: str | None = Field(default=None, max_length=2_000)
    playtime_seconds: int = Field(default=0, ge=0, le=PLAYTIME_SECONDS_MAX)

    purchase_date: int | None = Field(
        default=None, ge=0, description="Unix timestamp in seconds for the purchase date."
    )
    purchase_price: Decimal | None = Field(default=None, ge=0)
    purchase_price_currency_code: str | None = Field(default=None, max_length=3)
    physical_condition: str | None = Field(default=None, max_length=200)
    completion_date: int | None = Field(
        default=None,
        ge=0,
        description="Unix timestamp in seconds — when this game was first 100%-completed (Mastered).",
    )

    rating_story: Decimal | None = Field(default=None, ge=0, le=10)
    rating_gameplay: Decimal | None = Field(default=None, ge=0, le=10)
    rating_soundtrack: Decimal | None = Field(default=None, ge=0, le=10)
    rating_overall: Decimal | None = Field(default=None, ge=0, le=10)

    personal_rank: int | None = None

    @field_validator("purchase_price_currency_code")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None

        value = value.upper()

        if value not in CURRENCY_CODES:
            raise ValueError(f"Invalid currency code: {value}")

        return value


_CREATED_AT_DESCRIPTION = (
    "Unix timestamp in seconds for when the game was added to the library. "
    "Defaults to now; set it to back-date a game you've had for a while."
)


class GameCreate(GameBase):
    """Payload for creating a game. sort_title is derived if not given."""

    sort_title: str | None = Field(default=None, max_length=500)
    created_at: int | None = Field(default=None, ge=0, description=_CREATED_AT_DESCRIPTION)


class GameUpdate(TitleProtectionUpdate):
    """Payload for partial updates — every field optional."""

    title: str | None = Field(default=None, min_length=1, max_length=500)
    sort_title: str | None = Field(default=None, max_length=500)
    created_at: int | None = Field(default=None, ge=0, description=_CREATED_AT_DESCRIPTION)
    description: str | None = None
    release_date: date | None = None
    developer: str | None = Field(default=None, max_length=200)
    publisher: str | None = Field(default=None, max_length=200)
    series: str | None = Field(default=None, max_length=200)
    tags: list[str] | None = None
    features: list[str] | None = None
    play_compatibility: GamePlayCompatibility | None = None
    collections: list[str] | None = None
    links: list[GameLinkSchema] | None = None
    source: str | None = Field(default=None, max_length=50)
    provider_ids: ProviderIds | None = Field(default=None, max_length=32)
    platform: str | None = Field(default=None, max_length=50)
    region: str | None = Field(default=None, max_length=50)
    language: str | None = Field(default=None, max_length=50)
    age_rating: str | None = Field(default=None, max_length=20)
    time_to_beat_hours: Decimal | None = Field(default=None, ge=0)
    time_to_beat_extras_hours: Decimal | None = Field(default=None, ge=0, le=Decimal("99999.9"))
    time_to_beat_completionist_hours: Decimal | None = Field(
        default=None, ge=0, le=Decimal("99999.9")
    )
    time_to_beat_all_hours: Decimal | None = Field(default=None, ge=0, le=Decimal("99999.9"))
    parent_game_id: UUID | None = None
    relationship_type: GameRelationshipType | None = None

    folder_location: str | None = Field(
        default=None,
        min_length=1,
        max_length=FOLDER_NAME_MAX_LENGTH,
        pattern=FOLDER_NAME_PATTERN,
    )
    playnite_guid: UUID | None = Field(
        default=None,
        description="Optional GUID of the corresponding Playnite library entry.",
    )

    status: GameStatus | None = None
    priority: str | None = Field(default=None, max_length=20)
    favorite: bool | None = None
    profiles_enabled: bool | None = None
    osrs_stats_enabled: bool | None = None
    page_settings: dict | None = None

    @field_validator("page_settings")
    @classmethod
    def _check_page_settings(cls, value: dict | None) -> dict | None:
        return validate_page_settings(value, partial=True) or None if value is not None else None

    notes: str | None = None
    resume_note: str | None = Field(default=None, max_length=2_000)
    playtime_seconds: int | None = Field(default=None, ge=0, le=PLAYTIME_SECONDS_MAX)

    purchase_date: int | None = Field(
        default=None, ge=0, description="Unix timestamp in seconds for the purchase date."
    )
    purchase_price: Decimal | None = Field(default=None, ge=0)
    purchase_price_currency_code: str | None = Field(default=None, max_length=3)
    physical_condition: str | None = Field(default=None, max_length=200)
    completion_date: int | None = Field(
        default=None,
        ge=0,
        description="Unix timestamp in seconds — when this game was first 100%-completed (Mastered).",
    )

    rating_story: Decimal | None = Field(default=None, ge=0, le=10)
    rating_gameplay: Decimal | None = Field(default=None, ge=0, le=10)
    rating_soundtrack: Decimal | None = Field(default=None, ge=0, le=10)
    rating_overall: Decimal | None = Field(default=None, ge=0, le=10)

    personal_rank: int | None = None

    @field_validator("purchase_price_currency_code")
    @classmethod
    def validate_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None

        value = value.upper()

        if value not in CURRENCY_CODES:
            raise ValueError(f"Invalid currency code: {value}")

        return value


# Each removal moves a whole game folder, so one request stays small; the app sends a larger
# selection in several requests (#462).
GAME_BULK_DELETE_LIMIT = 100


class GameBulkDelete(BaseModel):
    """Move several of the caller's games to trash, each exactly as a single delete does."""

    game_ids: list[UUID] = Field(min_length=1, max_length=GAME_BULK_DELETE_LIMIT)


class GameBulkUpdate(BaseModel):
    """Apply the same field values to many games at once. Deliberately a
    narrower field set than GameUpdate — title/folder_location/notes/
    playtime/purchase info/ratings are per-game by nature and excluded so a
    bulk edit can't accidentally stamp one game's specifics onto many
    others. Only fields explicitly set here are touched; a field left unset
    leaves every selected game's existing value alone (same partial-update
    semantics as the single-game PATCH)."""

    game_ids: list[UUID] = Field(min_length=1, max_length=500)
    status: GameStatus | None = None
    favorite: bool | None = None
    play_compatibility: GamePlayCompatibility | None = None
    developer: str | None = Field(default=None, max_length=200)
    publisher: str | None = Field(default=None, max_length=200)
    series: str | None = Field(default=None, max_length=200)
    age_rating: str | None = Field(default=None, max_length=20)
    platform: str | None = Field(default=None, max_length=50)
    priority: str | None = Field(default=None, max_length=20)
    tags: list[str] | None = None
    features: list[str] | None = None


class GameReleaseRead(BaseModel):
    """One provider-reported release (Plugin API 1.1.20 MetadataRelease)."""

    date: str = Field(description="YYYY, YYYY-MM or YYYY-MM-DD, as precise as `precision` says.")
    precision: Literal["year", "month", "day"]
    platform: str | None = None
    region: str | None = None
    edition: str | None = None
    category: Literal["full", "early_access", "beta", "rerelease", "port", "demo"] = "full"


class GameRead(GameBase):
    """Full representation returned to clients."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    sort_title: str
    # Games list responses set this from the per-game asset manifest check so
    # clients can paint a local placeholder without requesting generated cover art.
    has_key_art: bool | None = None
    play_compatibility: GamePlayCompatibilityRead | None = None
    release_date_precision: Literal["year", "month", "day"] | None = Field(
        default=None,
        description=(
            "How much of release_date is known: for 'year' and 'month' release_date is the first "
            "day of that period. Null is a day (every date stored before Plugin API 1.1.20)."
        ),
    )
    releases: list[GameReleaseRead] | None = Field(
        default=None,
        description="Up to five releases reported by a metadata provider; replaced by refreshes.",
    )
    locked_fields: list[str] = Field(default_factory=list)
    provider_custom: dict[str, Any] | None = Field(
        default=None,
        description="Read-only extra fields reported by metadata providers; replaced by refreshes.",
    )
    created_at: int = Field(description="Unix timestamp in seconds when the game was created.")
    updated_at: int = Field(description="Unix timestamp in seconds when the game was last updated.")
    last_played_at: int | None = Field(
        default=None,
        description="Unix timestamp in seconds — set by a library sync, never user-editable.",
    )
    stale_since: int | None = Field(
        default=None,
        description=(
            "Unix timestamp in seconds since a library sync last noticed this game missing "
            "from the account's owned-games list. NULL means currently present (or never "
            "synced). Never causes deletion by itself, set for the user to review."
        ),
    )


class GameFieldChangeRead(BaseModel):
    """One row of a game's metadata history."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    field_name: str
    old_value: str | None
    new_value: str | None
    changed_at: int = Field(description="Unix timestamp in seconds.")
