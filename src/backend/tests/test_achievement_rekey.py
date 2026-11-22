"""Atlas achievement id changes keep catalogue rows and manual progress (CONTRACTS-v1 C2.5).

Catalogues go through the real row mapping (contract validation and ``catalogue_rows``) and
``store_catalogue``; every assertion reads the stored rows back from PostgreSQL."""

import base64
import hashlib
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select

from src.api.routes import game_achievements
from src.database.models.achievement import Achievement
from src.database.models.achievement_override import AchievementOverride
from src.database.models.achievement_rekey_log import AchievementRekeyLog
from src.database.models.game import Game
from src.database.models.game_note_detail import GameNoteDetail
from src.database.models.user import User
from src.database.session import SessionLocal
from src.features import achievement_refresh
from src.features.achievement_catalogue import catalogue_rows, store_catalogue
from src.features.achievement_rekey import unmatched_marks
from src.features.achievement_sync import replace_achievements
from src.plugin_api.metadata_contracts import MetadataPatch


def ak1(identity: str) -> str:
    """The Atlas wire key (C2.1) for a synthetic identity."""
    digest = base64.b32encode(hashlib.sha256(identity.encode()).digest()).decode()
    return "ak1:" + digest.lower().rstrip("=")[:26]


def row_id() -> str:
    """A fresh Atlas row id (Atlas re-minted these on every refresh before A4)."""
    return "01J" + uuid.uuid4().hex[:23].upper()


def atlas(*entries: tuple, keyed: bool = True) -> list[dict]:
    """Catalogue rows as the P1 plugin sends them: key as external id, row id as the bridge.

    ``entries`` are (name, identity, atlas_row_id[, steam_id]); ``keyed=False`` is an Atlas
    without keys, where the plugin falls back to the row id."""
    items = []
    for name, identity, atlas_row, *steam in entries:
        source_ids = {"atlas.row": atlas_row}
        if steam:
            source_ids["steam"] = steam[0]
        items.append(
            {
                "name": name,
                "external_id": ak1(identity) if keyed else atlas_row,
                "hidden": name == "Hidden achievement",
                "source_ids": source_ids,
            }
        )
    return _rows(items)


def legacy(*entries: tuple) -> list[dict]:
    """Catalogue rows as the plugin sent them before P1: the row id, no bridge."""
    return _rows([{"name": name, "external_id": atlas_row} for name, atlas_row in entries])


def _rows(items: list[dict]) -> list[dict]:
    patch = MetadataPatch.model_validate({"achievements": items}).model_dump(mode="json")
    return catalogue_rows({"metadata": {"achievements": patch["achievements"]}})


@pytest.fixture
async def game():
    async with SessionLocal() as db:
        user = User(
            username=f"t_{uuid.uuid4().hex[:10]}",
            email=f"{uuid.uuid4().hex[:10]}@example.test",
            password_hash="x",
        )
        db.add(user)
        await db.flush()
        title = f"Rekey {uuid.uuid4().hex[:8]}"
        row = Game(user_id=user.id, title=title, sort_title=title, folder_location=title)
        db.add(row)
        await db.commit()
        result = SimpleNamespace(id=row.id, user=SimpleNamespace(id=user.id))
    yield result
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id == result.user.id))
        await db.commit()


async def store(game, rows, provider="Atlas"):
    async with SessionLocal() as db:
        result = await store_catalogue(db, game.id, provider, rows)
        await db.commit()
    return result


async def catalogue(game) -> dict[str, Achievement]:
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(Achievement).where(
                Achievement.game_id == game.id, Achievement.provider == "catalog"
            )
        )
        return {row.external_id: row for row in rows.all()}


async def marks(game) -> dict[str, AchievementOverride]:
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(AchievementOverride).where(AchievementOverride.game_id == game.id)
        )
        return {row.external_id: row for row in rows.all()}


async def log(game) -> list[tuple]:
    async with SessionLocal() as db:
        rows = await db.scalars(
            select(AchievementRekeyLog)
            .where(AchievementRekeyLog.game_id == game.id)
            .order_by(AchievementRekeyLog.seq)
        )
        return [(r.method, r.subject, r.old_external_id, r.new_external_id) for r in rows.all()]


async def mark(game, external_id: str, unlocked: bool = True, updated_at: int | None = None):
    """Manual progress through the real route, as the game page sets it."""
    async with SessionLocal() as db:
        achievement = await db.scalar(
            select(Achievement).where(
                Achievement.game_id == game.id, Achievement.external_id == external_id
            )
        )
        await game_achievements.set_game_achievement_progress(
            game.id,
            achievement.id,
            game_achievements.AchievementProgressUpdate(unlocked=unlocked),
            db,
            game.user,
        )
        if updated_at is not None:
            override = await db.scalar(
                select(AchievementOverride).where(
                    AchievementOverride.game_id == game.id,
                    AchievementOverride.external_id == external_id,
                )
            )
            override.updated_at = updated_at
            await db.commit()


async def listed(game) -> dict[str, dict]:
    async with SessionLocal() as db:
        rows = await game_achievements.list_game_achievements(game.id, db, game.user)
    return {row["name"]: row for row in rows}


async def test_manual_progress_survives_repeated_atlas_refreshes(game):
    """Regression for V8: the first keyed refresh used to recreate the catalogue under new ids."""
    win, lose = row_id(), row_id()
    await store(game, legacy(("Winner", win), ("Loser", lose)))
    before = await catalogue(game)
    note_id = uuid.uuid4()
    async with SessionLocal() as db:
        db.add(
            GameNoteDetail(
                id=note_id, game_id=game.id, name="n", linked_achievement_id=before[win].id
            )
        )
        await db.commit()
    await mark(game, win)

    rows = atlas(("Winner", "steam:ACH_WIN", win, "ACH_WIN"), ("Loser", "steam:ACH_LOSE", lose))
    first = await store(game, rows)
    assert (first.rows, first.marks, first.dropped) == (2, 1, 0)
    after = await catalogue(game)
    assert set(after) == {ak1("steam:ACH_WIN"), ak1("steam:ACH_LOSE")}
    # the same database rows, re-keyed in place, and the bridge id is not stored
    assert after[ak1("steam:ACH_WIN")].id == before[win].id
    assert after[ak1("steam:ACH_LOSE")].id == before[lose].id
    assert after[ak1("steam:ACH_WIN")].source_ids == {"steam": "ACH_WIN"}
    assert after[ak1("steam:ACH_LOSE")].source_ids is None
    assert set(await marks(game)) == {ak1("steam:ACH_WIN")}
    assert (await listed(game))["Winner"]["unlocked"] is True
    async with SessionLocal() as db:
        assert (await db.get(GameNoteDetail, note_id)).linked_achievement_id == before[win].id

    entries = await log(game)
    assert ("row_id", "row", win, ak1("steam:ACH_WIN")) in entries
    assert ("row_id", "override", win, ak1("steam:ACH_WIN")) in entries
    assert len(entries) == 3

    for _ in range(2):  # idempotent: further refreshes change and log nothing
        again = await store(game, rows)
        assert (again.rows, again.marks, again.dropped) == (0, 0, 0)
        assert {k: v.id for k, v in (await catalogue(game)).items()} == {
            k: v.id for k, v in after.items()
        }
        assert await log(game) == entries
        listing = await listed(game)
        assert listing["Winner"]["unlocked"] is True and listing["Winner"]["manual_override"]


async def test_an_atlas_without_keys_keeps_progress_through_unique_names(game):
    """A P1 plugin against a pre-A4 Atlas: every refresh brings new row ids."""
    await store(game, atlas(("Winner", "", row_id()), ("Loser", "", row_id()), keyed=False))
    first = next(k for k, v in (await catalogue(game)).items() if v.name == "Winner")
    await mark(game, first)
    for _ in range(3):
        fresh = row_id()
        result = await store(
            game, atlas(("Winner", "", fresh), ("Loser", "", row_id()), keyed=False)
        )
        assert result.marks == 1
        assert set(await marks(game)) == {fresh}
        assert (await listed(game))["Winner"]["unlocked"] is True
        async with SessionLocal() as db:
            assert await unmatched_marks(db, game.id) == 0
    assert [e[0] for e in await log(game)] == ["name"] * 3


async def test_a_renamed_achievement_keeps_its_row_and_mark(game):
    win = row_id()
    await store(game, atlas(("Winner", "steam:ACH_WIN", win)))
    await mark(game, ak1("steam:ACH_WIN"))
    stored = (await catalogue(game))[ak1("steam:ACH_WIN")].id
    await store(game, atlas(("Champion", "steam:ACH_WIN", win)))
    after = await catalogue(game)
    assert (
        after[ak1("steam:ACH_WIN")].id == stored and after[ak1("steam:ACH_WIN")].name == "Champion"
    )
    assert (await listed(game))["Champion"]["unlocked"] is True
    assert await log(game) == []


async def test_a_renamed_achievement_is_still_rekeyed_through_the_bridge(game):
    win = row_id()
    await store(game, legacy(("Winner", win)))
    await mark(game, win)
    await store(game, atlas(("Champion", "steam:ACH_WIN", win)))
    assert set(await marks(game)) == {ak1("steam:ACH_WIN")}
    assert (await listed(game))["Champion"]["unlocked"] is True


async def test_a_removed_achievement_keeps_its_mark_until_it_comes_back(game):
    win, lose = row_id(), row_id()
    await store(game, atlas(("Winner", "steam:W", win), ("Loser", "steam:L", lose)))
    await mark(game, ak1("steam:L"))
    await store(game, atlas(("Winner", "steam:W", win)))
    assert set(await catalogue(game)) == {ak1("steam:W")}
    assert set(await marks(game)) == {ak1("steam:L")}  # orphaned, never deleted
    async with SessionLocal() as db:
        assert await unmatched_marks(db, game.id) == 1
    # back under a new Atlas row id, with the same key
    await store(game, atlas(("Winner", "steam:W", win), ("Loser", "steam:L", row_id())))
    assert (await listed(game))["Loser"]["unlocked"] is True
    async with SessionLocal() as db:
        assert await unmatched_marks(db, game.id) == 0


async def test_a_removed_achievement_that_returns_under_a_new_key_is_matched_by_name(game):
    await store(game, atlas(("Winner", "curator~winner", row_id())))
    await mark(game, ak1("curator~winner"))
    await store(game, atlas(("Other", "steam:O", row_id())))
    await store(game, atlas(("Other", "steam:O", row_id()), ("Winner", "steam:W", row_id())))
    assert set(await marks(game)) == {ak1("steam:W")}
    assert ("name", "override", ak1("curator~winner"), ak1("steam:W")) in await log(game)


async def test_ambiguous_names_and_hidden_placeholders_are_never_matched(game):
    await store(
        game,
        legacy(("Twin", "old-1"), ("Hidden achievement", "old-2"), ("Solo", "old-3")),
    )
    for external_id in ("old-1", "old-2"):
        await mark(game, external_id)
    rows = atlas(
        ("Twin", "steam:T1", row_id()),
        ("twin", "steam:T2", row_id()),
        ("Hidden achievement", "steam:H", row_id()),
    )
    await store(game, rows)
    assert set(await marks(game)) == {"old-1", "old-2"}  # kept, not moved, not deleted
    async with SessionLocal() as db:
        assert await unmatched_marks(db, game.id) == 2
    assert await log(game) == []


async def test_two_marks_on_one_achievement_keep_the_newer_and_log_the_other(game):
    """A host that ran the keyed plugin before this change can hold a mark under the key too."""
    win = row_id()
    await store(game, legacy(("Winner", win)))
    await mark(game, win, unlocked=False, updated_at=1_000)
    async with SessionLocal() as db:  # left behind by a catalogue recreated under the key
        db.add(
            AchievementOverride(
                game_id=game.id,
                provider="catalog",
                external_id=ak1("s:W"),
                name="Winner",
                unlocked=True,
                unlocked_at=1_500,
                updated_at=2_000,
            )
        )
        await db.commit()
    result = await store(game, atlas(("Winner", "s:W", win)))
    assert (result.rows, result.dropped) == (1, 1)
    saved = await marks(game)
    assert set(saved) == {ak1("s:W")} and saved[ak1("s:W")].unlocked is True
    async with SessionLocal() as db:
        dropped = await db.scalar(
            select(AchievementRekeyLog).where(
                AchievementRekeyLog.game_id == game.id, AchievementRekeyLog.method == "dropped"
            )
        )
    assert dropped.old_external_id == win and dropped.new_external_id == ak1("s:W")
    assert dropped.dropped_override["external_id"] == win
    assert dropped.dropped_override["unlocked"] is False
    assert dropped.dropped_override["updated_at"] == 1_000


async def test_a_mark_is_never_moved_by_name_onto_an_achievement_with_its_own_mark(game):
    await store(game, legacy(("Winner", "old-1")))
    await mark(game, "old-1", unlocked=False)
    async with SessionLocal() as db:
        db.add(
            Achievement(
                game_id=game.id,
                provider="catalog",
                external_id=ak1("s:W"),
                name="Winner",
                created_at=1,
            )
        )
        await db.commit()
    await mark(game, ak1("s:W"), unlocked=True)
    await store(game, atlas(("Winner", "s:W", row_id())))
    saved = await marks(game)
    assert set(saved) == {"old-1", ak1("s:W")}  # both kept, nothing dropped
    assert saved[ak1("s:W")].unlocked is True
    async with SessionLocal() as db:
        assert await unmatched_marks(db, game.id) == 1
    assert await log(game) == []


async def test_other_metadata_providers_are_stored_exactly_as_before(game):
    await store(game, legacy(("Winner", "x-1"), ("Loser", "x-2")), provider="IGDB")
    before = await catalogue(game)
    await mark(game, "x-1")
    await store(game, legacy(("Winner", "y-1"), ("Loser", "x-2")), provider="IGDB")
    after = await catalogue(game)
    assert set(after) == {"y-1", "x-2"}
    assert after["x-2"].id == before["x-2"].id and after["y-1"].id != before["x-1"].id
    assert set(await marks(game)) == {"x-1"}  # no name matching without the Atlas bridge
    assert await log(game) == []


async def test_sticky_aliases_keep_the_stored_id(game):
    """The 1.1.20 path (C2.5 step 3): no contract field feeds it yet, so rows carry it directly."""
    await store(game, atlas(("Winner", "steam:W", row_id())))
    await mark(game, ak1("steam:W"))
    stored = (await catalogue(game))[ak1("steam:W")].id
    rows = atlas(("Winner", "curator~winner", row_id()))
    rows[0]["aliases"] = [ak1("curator~winner"), ak1("steam:W")]
    await store(game, rows)
    after = await catalogue(game)
    assert set(after) == {ak1("steam:W")} and after[ak1("steam:W")].id == stored
    assert set(await marks(game)) == {ak1("steam:W")}
    assert await log(game) == []


async def test_a_bridge_that_names_a_row_another_entry_owns_is_ignored(game):
    keep, other = row_id(), row_id()
    await store(game, legacy(("A", keep), ("B", other)))
    # B's bridge points at A's stored id, but A is listed under its own id
    rows = legacy(("A", keep)) + atlas(("B", "s:B", keep))
    await store(game, rows)
    stored = await catalogue(game)
    assert set(stored) == {keep, ak1("s:B")}
    assert stored[keep].name == "A"


async def test_refresh_result_reports_marks_that_could_not_be_matched(game):
    await store(game, legacy(("Gone", "old-1")))
    await mark(game, "old-1")
    rows = atlas(("Kept", "s:K", row_id()))
    await store(game, rows)
    data = SimpleNamespace(
        progress_error=None,
        catalogue_errors=[],
        progress_rows=[],
        progress_provider=None,
        catalogue_rows=rows,
        catalogue_provider="Atlas",
    )
    async with SessionLocal() as db:
        # pylint: disable-next=protected-access
        result = await achievement_refresh._achievement_refresh_result(db, game.id, data)
    assert result["warnings"] == [
        "1 manual mark could not be matched to an achievement and was kept"
    ]


async def test_a_sync_with_repeated_ids_stores_one_row_and_keeps_the_unlock(game):
    async with SessionLocal() as db:
        await replace_achievements(
            db,
            game.id,
            "Steam",
            [
                {"external_id": "ACH_1", "name": "One", "unlocked": False},
                {"external_id": "ACH_1", "name": "One", "unlocked": True},
                {"external_id": "ACH_2", "name": "Two", "unlocked": False},
            ],
        )
        await db.commit()
        rows = await db.scalars(select(Achievement).where(Achievement.game_id == game.id))
        stored = {row.external_id: row.unlocked for row in rows.all()}
        assert stored == {"ACH_1": True, "ACH_2": False}
        count = await db.scalar(
            select(func.count()).select_from(Achievement).where(Achievement.game_id == game.id)
        )
        assert count == 2
