"""Migration c4e7a1d2f905 (explicit play logs, #467) on PostgreSQL: upgrade, downgrade, upgrade.

The upgrade adds `game_play_events`; nothing existing is rewritten, so every game keeps its
`last_played_at`. Logs belong to their game and owner (a log for another account's game is
refused), a retried request id is unique per owner, and deleting the game removes its logs.
The test migrates a schema of its own."""

import os
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from src.core.config import settings
from tests.test_plugin_api_1120_migration import _owner_url, _run_alembic, _seed_library

BEFORE = "b8d2f6a41c93"
REVISION = "c4e7a1d2f905"


def _log(connection: psycopg.Connection, user_id, game_id, played_at: int, request=None) -> None:
    connection.execute(
        "INSERT INTO game_play_events (id, user_id, game_id, played_at, recorded_at, request_id) "
        "VALUES (%s, %s, %s, %s, 1, %s)",
        (uuid4(), user_id, game_id, played_at, request),
    )


def _refused(connection: psycopg.Connection, error: type[Exception], *args) -> None:
    try:
        _log(connection, *args)
    except error:
        return
    raise AssertionError(f"insert {args!r} was accepted")


def test_game_play_events_upgrade_downgrade_and_upgrade_again():
    schema = f"plays_{uuid4().hex}"
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
            other_id, _ = _seed_library(scoped)
            game_id, second_id = list(games.values())[:2]
            connection.execute(
                "UPDATE games SET last_played_at = 1600000000 WHERE id = %s", (game_id,)
            )

            _run_alembic(environment, "upgrade", REVISION)
            assert connection.execute(
                "SELECT last_played_at FROM games WHERE id = %s", (game_id,)
            ).fetchone() == (1_600_000_000,)
            _log(connection, user_id, game_id, 1_700_000_000, "request-1")
            _log(connection, user_id, game_id, 1_700_000_100)
            _log(connection, user_id, game_id, 1_700_000_200)  # no request id: never unique
            _refused(connection, psycopg.errors.UniqueViolation, user_id, second_id, 1, "request-1")
            _refused(connection, psycopg.errors.CheckViolation, user_id, game_id, -1)
            # the owner is part of the game key: another account cannot log against this game
            _refused(connection, psycopg.errors.ForeignKeyViolation, other_id, game_id, 1)
            connection.execute("DELETE FROM games WHERE id = %s", (game_id,))
            assert connection.execute("SELECT count(*) FROM game_play_events").fetchone() == (0,)

            _run_alembic(environment, "downgrade", BEFORE)
            assert (
                connection.execute("SELECT to_regclass('game_play_events')").fetchone()[0] is None
            )

            _run_alembic(environment, "upgrade", "head")
            _log(connection, user_id, second_id, 1_700_000_000, "request-1")
            # the upgrade is safe to run over its own result
            connection.execute("UPDATE alembic_version SET version_num = %s", (BEFORE,))
            _run_alembic(environment, "upgrade", REVISION)
            assert connection.execute("SELECT count(*) FROM game_play_events").fetchone() == (1,)
        finally:
            connection.execute(sql.SQL("RESET search_path"))
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
