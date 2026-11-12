"""Migration a7c3e91f5b20 (Plugin API 1.1.20 metadata) on PostgreSQL: upgrade, downgrade, upgrade.

A library from before the revision holds the shapes crossplay was stored in (true, false, no
key, no details at all, provider-reported details). The upgrade must turn them into the
three-state vocabulary without inventing a "no", the downgrade must give older code the boolean
it reads, and a second upgrade must change nothing. The test migrates a schema of its own."""

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url

from src.core.config import settings
from src.database.models.game import Game
from src.database.models.user import User
from src.main import app  # noqa: F401 — register the complete application model graph

BEFORE = "38d06d75381e"
REVISION = "a7c3e91f5b20"

# title -> play_compatibility as stored before Plugin API 1.1.20
LEGACY_DETAILS: dict[str, dict | None] = {
    "Confirmed crossplay": {"min_players": 2, "max_players": 4, "crossplay": True},
    "No crossplay recorded": {"min_players": 1, "max_players": 8, "crossplay": False},
    "Provider reported": {
        "min_players": 2,
        "max_players": 6,
        "crossplay": True,
        "source": "provider",
    },
    "Provider without crossplay": {
        "min_players": 1,
        "max_players": 2,
        "crossplay": False,
        "source": "provider",
    },
    "Duration only": {"duration_minutes": 45},
    "No details": None,
}

UPGRADED = {
    "Confirmed crossplay": {"min_players": 2, "max_players": 4, "crossplay": "yes"},
    "No crossplay recorded": {"min_players": 1, "max_players": 8, "crossplay": None},
    "Provider reported": {
        "min_players": 2,
        "max_players": 6,
        "crossplay": "yes",
        "source": "provider",
    },
    "Provider without crossplay": {
        "min_players": 1,
        "max_players": 2,
        "crossplay": None,
        "source": "provider",
    },
    "Duration only": {"duration_minutes": 45},
    "No details": None,
}


def _owner_url() -> str:
    return (
        make_url(settings.DATABASE_URL)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )


def _run_alembic(environment: dict, *arguments: str) -> None:
    finished = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert finished.returncode == 0, finished.stdout + finished.stderr


def _details(connection: psycopg.Connection) -> dict[str, dict | None]:
    rows = connection.execute(
        "SELECT title, play_compatibility FROM games ORDER BY title"
    ).fetchall()
    return dict(rows)


def _columns(connection: psycopg.Connection, table: str) -> set[str]:
    rows = connection.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = %s",
        (table,),
    ).fetchall()
    return {name for (name,) in rows}


def _seed_library(url: URL) -> tuple[UUID, dict[str, UUID]]:
    """A person's library as the previous revision stores it, written through the table
    definitions so their Python-side defaults fill the other columns."""
    user_id = uuid4()
    name = "m7" + user_id.hex[:12]
    games: dict[str, UUID] = {}
    engine = create_engine(url.set(drivername="postgresql+psycopg"))
    try:
        with engine.begin() as db:
            db.execute(
                User.__table__.insert().values(
                    id=user_id, username=name, email=name + "@example.test", password_hash="x"
                )
            )
            for index, (title, details) in enumerate(LEGACY_DETAILS.items()):
                games[title] = uuid4()
                values = {
                    "id": games[title],
                    "user_id": user_id,
                    "title": title,
                    "sort_title": title.lower(),
                    "folder_location": f"m7_game_{index}",
                    "release_date": date(2010 + index, index + 1, 15),
                }
                if details is not None:  # left out, the column is SQL NULL rather than JSON null
                    values["play_compatibility"] = details
                db.execute(Game.__table__.insert().values(**values))
    finally:
        engine.dispose()
    return user_id, games


def test_plugin_api_1120_storage_upgrades_downgrades_and_upgrades_again():
    schema = f"m7_api_{uuid4().hex}"
    scoped = make_url(settings.DATABASE_URL).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    )
    environment = {**os.environ, "DATABASE_URL": scoped.render_as_string(hide_password=False)}
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        environment.pop(key, None)

    with psycopg.connect(_owner_url(), autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            _run_alembic(environment, "upgrade", BEFORE)
            user_id, games = _seed_library(scoped)

            _run_alembic(environment, "upgrade", REVISION)
            assert _details(connection) == UPGRADED
            assert {"release_date_precision", "releases"} <= _columns(connection, "games")
            assert "observed_release_precision" in _columns(connection, "game_source_observations")
            # the release dates themselves are untouched and read as days
            assert connection.execute(
                "SELECT count(*) FROM games WHERE release_date IS NOT NULL AND release_date_precision IS NULL"
            ).fetchone() == (len(LEGACY_DETAILS),)

            # a precision outside the vocabulary is refused by the database
            try:
                connection.execute(
                    "UPDATE games SET release_date_precision = 'week' WHERE id = %s",
                    (games["Duration only"],),
                )
            except psycopg.errors.CheckViolation:
                pass
            else:
                raise AssertionError("an unknown release date precision was stored")

            # new data the revision can hold: a year-precision date with releases, a partial
            # crossplay, a "no", and relations waiting for their other game
            connection.execute(
                "UPDATE games SET release_date = '2011-01-01', release_date_precision = 'year', "
                "releases = %s WHERE id = %s",
                (
                    json.dumps(
                        [
                            {
                                "date": "2011",
                                "precision": "year",
                                "platform": "pc",
                                "region": "ww",
                                "edition": None,
                                "category": "full",
                            }
                        ]
                    ),
                    games["Confirmed crossplay"],
                ),
            )
            connection.execute(
                "UPDATE games SET play_compatibility = jsonb_set(play_compatibility, '{crossplay}', "
                "'\"partial\"') WHERE id = %s",
                (games["Provider reported"],),
            )
            connection.execute(
                "UPDATE games SET play_compatibility = jsonb_set(play_compatibility, '{crossplay}', "
                "'\"no\"') WHERE id = %s",
                (games["No crossplay recorded"],),
            )
            for kind, external_id in (("dlc_of", "atlas:G1"), ("sequel_of", "atlas:G2")):
                connection.execute(
                    "INSERT INTO game_pending_relationships (id, user_id, game_id, kind, namespace, "
                    "external_id, first_seen_at, last_seen_at) VALUES (%s, %s, %s, %s, 'atlas', %s, 1, 1)",
                    (uuid4(), user_id, games["Confirmed crossplay"], kind, external_id),
                )
            # a waiting reference may only point from a game of the same person
            try:
                connection.execute(
                    "INSERT INTO game_pending_relationships (id, user_id, game_id, kind, namespace, "
                    "external_id, first_seen_at, last_seen_at) "
                    "VALUES (%s, %s, %s, 'dlc_of', 'atlas', 'atlas:G3', 1, 1)",
                    (uuid4(), uuid4(), games["Duration only"]),
                )
            except psycopg.errors.ForeignKeyViolation:
                pass
            else:
                raise AssertionError("a pending relation was stored for another person's game")

            _run_alembic(environment, "downgrade", BEFORE)
            downgraded = _details(connection)
            assert downgraded["Confirmed crossplay"]["crossplay"] is True
            assert downgraded["Provider reported"]["crossplay"] is True  # partial reads as yes
            assert downgraded["No crossplay recorded"]["crossplay"] is False  # "no"
            assert downgraded["Provider without crossplay"]["crossplay"] is False  # unknown
            assert downgraded["Duration only"] == {"duration_minutes": 45}
            assert downgraded["No details"] is None
            assert connection.execute(
                "SELECT to_regclass('game_pending_relationships')"
            ).fetchone() == (None,)
            assert not {"release_date_precision", "releases"} & _columns(connection, "games")
            assert "observed_release_precision" not in _columns(
                connection, "game_source_observations"
            )
            # a year-only date keeps its day value; every game is still there
            assert connection.execute(
                "SELECT release_date::text FROM games WHERE id = %s",
                (games["Confirmed crossplay"],),
            ).fetchone() == ("2011-01-01",)
            assert connection.execute("SELECT count(*) FROM games").fetchone() == (
                len(LEGACY_DETAILS),
            )

            _run_alembic(environment, "upgrade", "head")
            again = _details(connection)
            assert again["Confirmed crossplay"]["crossplay"] == "yes"
            assert again["Provider reported"]["crossplay"] == "yes"
            assert again["No crossplay recorded"]["crossplay"] is None
            assert connection.execute(
                "SELECT to_regclass('game_pending_relationships')"
            ).fetchone() != (None,)

            # the upgrade is safe to run over its own result
            before_rerun = _details(connection)
            connection.execute("UPDATE alembic_version SET version_num = %s", (BEFORE,))
            _run_alembic(environment, "upgrade", REVISION)
            assert _details(connection) == before_rerun
        finally:
            connection.execute(sql.SQL("RESET search_path"))
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
