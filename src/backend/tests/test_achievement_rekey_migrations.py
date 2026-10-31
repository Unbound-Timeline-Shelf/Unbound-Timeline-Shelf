"""Migrations for the achievement re-key (CONTRACTS-v1 C2.5): the unique index on a database with
duplicates, and the downgrade that replays the re-key log to restore the old ids.

Each test migrates its own schema in the test database, so the shared schema is never touched."""

import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.core.config import settings
from src.database.models.achievement import Achievement
from src.database.models.achievement_override import AchievementOverride
from src.database.models.game import Game
from src.database.models.game_note_detail import GameNoteDetail
from src.database.models.user import User
from src.features.achievement_catalogue import store_catalogue
from src.main import app  # noqa: F401 — register the complete application model graph

BEFORE = "b7d3a9e14c52"
UNIQUE = "edf13797f129"
REKEY_LOG = "c558a537a476"
CONSTRAINT = "uq_achievements_game_provider_external_id"
KEY_WIN = "ak1:" + "a" * 26
KEY_LOSE = "ak1:" + "b" * 26


def _sync_url() -> str:
    return (
        make_url(settings.DATABASE_URL)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )


@contextmanager
def scratch_schema(label: str):
    """A schema of its own, the alembic runner for it, and a psycopg connection inside it."""
    schema = f"{label}_{uuid4().hex}"
    scoped = make_url(settings.DATABASE_URL).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    )
    environment = {**os.environ, "DATABASE_URL": scoped.render_as_string(hide_password=False)}
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        environment.pop(key, None)

    def migrate(*arguments) -> str:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
            cwd=Path(__file__).parents[1],
            env=environment,
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout + result.stderr

    with psycopg.connect(_sync_url(), autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            yield scoped, migrate, connection
        finally:
            connection.execute(sql.SQL("RESET search_path"))
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _seed_game(scoped) -> tuple:
    user_id, game_id = uuid4(), uuid4()
    engine = create_engine(scoped.set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as db:
            name = "rekey" + user_id.hex[:12]
            db.execute(
                User.__table__.insert().values(
                    id=user_id, username=name, email=name + "@example.test", password_hash="x"
                )
            )
            db.execute(
                Game.__table__.insert().values(
                    id=game_id, user_id=user_id, title="G", sort_title="G", folder_location="G"
                )
            )
    finally:
        engine.dispose()
    return engine, user_id, game_id


def _has_constraint(connection) -> bool:
    return bool(
        connection.execute(
            "SELECT 1 FROM pg_constraint WHERE conname = %s "
            "AND conrelid = to_regclass('achievements')",
            (CONSTRAINT,),
        ).fetchone()
    )


def test_the_unique_index_migration_removes_duplicates_and_keeps_their_links():
    with scratch_schema("ach_unique") as (scoped, migrate, connection):
        migrate("upgrade", BEFORE)
        _engine, _user_id, game_id = _seed_game(scoped)
        locked_new, unlocked_old, locked_old, other = uuid4(), uuid4(), uuid4(), uuid4()
        rows = [
            (locked_new, "Steam", "ACH_1", False, 300),
            (unlocked_old, "Steam", "ACH_1", True, 100),
            (locked_old, "Steam", "ACH_1", False, 50),
            (other, "catalog", "ACH_1", False, 10),  # another provider: not a duplicate
        ]
        for row in rows:
            connection.execute(
                "INSERT INTO achievements (id, game_id, provider, external_id, name, unlocked, "
                "hidden, created_at) VALUES (%s, %s, %s, %s, 'One', %s, false, %s)",
                (row[0], game_id, *row[1:]),
            )
        note = uuid4()
        connection.execute(
            "INSERT INTO game_note_details (id, game_id, name, created_at, pinned, tags, "
            "linked_achievement_id) VALUES (%s, %s, 'n', 1, false, '{}', %s)",
            (note, game_id, locked_new),
        )
        # without the index the database takes another copy
        assert not _has_constraint(connection)

        output = migrate("upgrade", UNIQUE)
        assert "removing 2 duplicate row(s)" in output
        assert _has_constraint(connection)
        kept = connection.execute(
            "SELECT id, provider FROM achievements ORDER BY provider"
        ).fetchall()
        assert set(kept) == {(unlocked_old, "Steam"), (other, "catalog")}
        # the note that pointed at a removed duplicate now points at the row that stayed
        assert connection.execute(
            "SELECT linked_achievement_id FROM game_note_details WHERE id = %s", (note,)
        ).fetchone() == (unlocked_old,)
        with pytest.raises(psycopg.errors.UniqueViolation):
            connection.execute(
                "INSERT INTO achievements (id, game_id, provider, external_id, name, unlocked, "
                "hidden, created_at) VALUES (%s, %s, 'Steam', 'ACH_1', 'x', false, false, 1)",
                (uuid4(), game_id),
            )

        migrate("upgrade", UNIQUE)  # a rerun changes nothing
        migrate("downgrade", BEFORE)
        assert not _has_constraint(connection)
        assert connection.execute("SELECT count(*) FROM achievements").fetchone() == (2,)
        migrate("upgrade", REKEY_LOG)
        assert _has_constraint(connection)


async def _rekey(scoped, game_id):
    """One legacy catalogue with manual marks, then the keyed refresh from the P1 plugin."""
    engine = create_async_engine(scoped)
    try:
        async with AsyncSession(engine) as db:
            await store_catalogue(
                db,
                game_id,
                "Atlas",
                [
                    {"external_id": "row-win", "name": "Winner", "unlocked": False},
                    {"external_id": "row-lose", "name": "Loser", "unlocked": False},
                    {"external_id": "row-gone", "name": "Hidden achievement", "unlocked": False},
                ],
            )
            await db.flush()
            for external_id, unlocked, updated in (
                ("row-win", True, 100),
                ("row-gone", True, 100),
                (KEY_LOSE, True, 900),  # left under the key by an earlier keyed catalogue
                ("row-lose", False, 100),
                ("row-loser-old", True, 50),  # orphan from before, recovered by name
            ):
                db.add(
                    AchievementOverride(
                        game_id=game_id,
                        provider="catalog",
                        external_id=external_id,
                        name="Old Loser" if external_id == "row-loser-old" else "x",
                        unlocked=unlocked,
                        unlocked_at=updated if unlocked else None,
                        updated_at=updated,
                    )
                )
            await db.commit()
            win = await db.scalar(
                select(Achievement.id).where(Achievement.external_id == "row-win")
            )
            db.add(GameNoteDetail(game_id=game_id, name="n", linked_achievement_id=win))
            await db.commit()
            result = await store_catalogue(
                db,
                game_id,
                "Atlas",
                [
                    {
                        "external_id": KEY_WIN,
                        "name": "Winner",
                        "unlocked": False,
                        "source_ids": {"atlas.row": "row-win", "steam": "W"},
                    },
                    {
                        "external_id": KEY_LOSE,
                        "name": "Loser",
                        "unlocked": False,
                        "source_ids": {"atlas.row": "row-lose"},
                    },
                    {
                        "external_id": "ak1:" + "c" * 26,
                        "name": "Old Loser",
                        "unlocked": False,
                        "source_ids": {"atlas.row": "row-new"},
                    },
                ],
            )
            await db.commit()
            return result, win
    finally:
        await engine.dispose()


async def test_downgrade_replays_the_rekey_log_and_restores_the_old_ids():
    with scratch_schema("ach_rekey") as (scoped, migrate, connection):
        migrate("upgrade", REKEY_LOG)
        _engine, _user_id, game_id = _seed_game(scoped)
        result, win = await _rekey(scoped, game_id)
        assert (result.rows, result.marks, result.dropped) == (2, 2, 1)

        def catalogue():
            return dict(
                connection.execute(
                    "SELECT external_id, id FROM achievements WHERE provider = 'catalog'"
                ).fetchall()
            )

        def marks():
            return dict(
                connection.execute(
                    "SELECT external_id, unlocked FROM achievement_overrides"
                ).fetchall()
            )

        after = catalogue()
        assert set(after) == {KEY_WIN, KEY_LOSE, "ak1:" + "c" * 26}
        assert after[KEY_WIN] == win
        # row-lose (100) lost to the newer mark under the key (900); row-gone stays orphaned
        assert marks() == {KEY_WIN: True, KEY_LOSE: True, "row-gone": True, "ak1:" + "c" * 26: True}
        logged = connection.execute(
            "SELECT method, subject, old_external_id, new_external_id FROM achievement_rekey_log "
            "ORDER BY seq"
        ).fetchall()
        assert ("dropped", "override", "row-lose", KEY_LOSE) in logged
        assert ("name", "override", "row-loser-old", "ak1:" + "c" * 26) in logged

        output = migrate("downgrade", UNIQUE)
        assert "replayed 5 re-key step(s), skipped 0" in output
        assert connection.execute("SELECT to_regclass('achievement_rekey_log')").fetchone() == (
            None,
        )
        restored = catalogue()
        assert restored["row-win"] == win and "row-lose" in restored
        assert marks() == {
            "row-win": True,
            "row-lose": False,  # the dropped mark is back, with its own state
            KEY_LOSE: True,
            "row-gone": True,
            "row-loser-old": True,
        }
        # the note still points at the same row
        assert connection.execute(
            "SELECT linked_achievement_id FROM game_note_details"
        ).fetchone() == (win,)

        migrate("upgrade", REKEY_LOG)  # and forward again, with an empty log
        assert connection.execute("SELECT count(*) FROM achievement_rekey_log").fetchone() == (0,)
