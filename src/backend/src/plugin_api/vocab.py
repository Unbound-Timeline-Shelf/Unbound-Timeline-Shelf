"""Closed vocabularies the host shares with Atlas and the metadata-atlas plugin (CONTRACTS-v1 C7.4).

Every enum that crosses the Plugin API boundary and also exists in Atlas is listed here once.
``contracts/vocab-v1.json`` at the repository root is generated from this module
(``python tools/vocab_check.py generate``) and compared with the file Atlas publishes
(``python tools/vocab_check.py compare``). The order of the values is the order Atlas uses; the
cross-repository comparison treats each vocabulary as a set.

Absent means unknown everywhere (C1 rule 1). ``not_applicable`` crossplay is derived from
``players_max == 1`` and is never stored or sent (C7.2), so it is not a vocabulary value.
"""

from __future__ import annotations

import json
from typing import Any, Final

VOCAB_VERSION: Final = 1
FILE: Final = "contracts/vocab-v1.json"

# The host calls the achievement tier a "medal" (``achievements.medal``); ``platinum`` arrived
# with Plugin API 1.1.20.
ACHIEVEMENT_TIERS: Final = ("bronze", "silver", "gold", "platinum")
# Stored as ``achievements.tier`` for legacy reasons; the wire calls it ``kind`` (C7.1).
ACHIEVEMENT_KINDS: Final = ("progression", "missable", "win_condition")
CROSSPLAY: Final = ("yes", "partial", "no")
# The host's ``RelationshipKind`` minus ``same_game`` (a merge), ``related_to`` and ``custom``.
RELATION_TYPES: Final = (
    "version_of",
    "remake_of",
    "remaster_of",
    "port_of",
    "dlc_of",
    "expansion_of",
    "standalone_expansion_of",
    "mod_of",
    "modpack_of",
    "total_conversion_of",
    "series_member",
    "collection_member",
)
DATE_PRECISIONS: Final = ("year", "month", "day")
RELEASE_CATEGORIES: Final = ("full", "early_access", "beta", "rerelease", "port", "demo")
# Release categories that can be a game's first release (C5.1).
FIRST_RELEASE_CATEGORIES: Final = frozenset({"full", "early_access"})


def export() -> dict[str, Any]:
    """The shared file's content, in Atlas's key order. ``feed_kinds`` is Atlas-only."""
    return {
        "version": VOCAB_VERSION,
        "achievement_tier": list(ACHIEVEMENT_TIERS),
        "achievement_kind": list(ACHIEVEMENT_KINDS),
        "crossplay": list(CROSSPLAY),
        "relation_type": list(RELATION_TYPES),
        "date_precision": list(DATE_PRECISIONS),
        "release_category": list(RELEASE_CATEGORIES),
    }


def render(data: dict[str, Any]) -> str:
    """Two-space indent, keys in the given order, LF and a trailing newline (as Atlas renders)."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"
