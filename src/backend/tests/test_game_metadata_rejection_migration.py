"""Migration 38d06d75381e (metadata refresh rejections): upgrade, refused and clean downgrade.

The test migrates a schema of its own in the test database, so the shared schema is untouched."""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import psycopg
from psycopg import sql
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from src.core.config import settings
from src.database.models.game import Game
from src.database.models.user import User
from src.main import app  # noqa: F401 — register the complete application model graph

BEFORE = "c558a537a476"
REJECTIONS = "38d06d75381e"


def _sync_url() -> str:
    return (
        make_url(settings.DATABASE_URL)
        .set(drivername="postgresql")
        .render_as_string(hide_password=False)
    )


def _alembic(environment: dict, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
        cwd=Path(__file__).parents[1],
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def test_rejections_table_upgrade_and_a_downgrade_that_never_drops_decisions():
    schema = f"meta_reject_{uuid4().hex}"
    scoped = make_url(settings.DATABASE_URL).update_query_dict(
        {"options": f"-csearch_path={schema}"}
    )
    environment = {**os.environ, "DATABASE_URL": scoped.render_as_string(hide_password=False)}
    for key in ("POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"):
        environment.pop(key, None)

    with psycopg.connect(_sync_url(), autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            upgraded = _alembic(environment, "upgrade", REJECTIONS)
            assert upgraded.returncode == 0, upgraded.stdout + upgraded.stderr
            user_id, game_id = uuid4(), uuid4()
            # through the models, so their Python-side defaults fill the other columns
            engine = create_engine(scoped.set(drivername="postgresql+psycopg"))
            try:
                with engine.begin() as db:
                    name = "rej" + user_id.hex[:12]
                    db.execute(
                        User.__table__.insert().values(
                            id=user_id,
                            username=name,
                            email=name + "@example.test",
                            password_hash="x",
                        )
                    )
                    db.execute(
                        Game.__table__.insert().values(
                            id=game_id,
                            user_id=user_id,
                            title="G",
                            sort_title="g",
                            folder_location="G",
                        )
                    )
            finally:
                engine.dispose()
            # deleting a user cascades through an index, not a table scan
            assert connection.execute(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = %s AND indexname = %s",
                (schema, "ix_game_metadata_rejections_user_id"),
            ).fetchone() == (
                f"CREATE INDEX ix_game_metadata_rejections_user_id ON {schema}"
                ".game_metadata_rejections USING btree (user_id)",
            )
            connection.execute(
                "INSERT INTO game_metadata_rejections "
                "(id, user_id, game_id, namespace, identity, created_at) "
                "VALUES (%s, %s, %s, 'atlas', 'atlas:X', 1)",
                (uuid4(), user_id, game_id),
            )
            # one rejection per game and id
            try:
                connection.execute(
                    "INSERT INTO game_metadata_rejections "
                    "(id, user_id, game_id, namespace, identity, created_at) "
                    "VALUES (%s, %s, %s, 'atlas', 'atlas:X', 2)",
                    (uuid4(), user_id, game_id),
                )
            except psycopg.errors.UniqueViolation:
                pass
            else:
                raise AssertionError("a repeated rejection was stored twice")

            refused = _alembic(environment, "downgrade", BEFORE)
            assert refused.returncode != 0
            assert "Refusing to drop populated metadata refresh rejections" in refused.stderr
            assert connection.execute(
                "SELECT count(*) FROM game_metadata_rejections"
            ).fetchone() == (1,)

            # deleting the game removes its rejections (ON DELETE CASCADE)
            connection.execute("DELETE FROM games WHERE id = %s", (game_id,))
            assert connection.execute(
                "SELECT count(*) FROM game_metadata_rejections"
            ).fetchone() == (0,)

            downgraded = _alembic(environment, "downgrade", BEFORE)
            assert downgraded.returncode == 0, downgraded.stdout + downgraded.stderr
            assert connection.execute(
                "SELECT to_regclass('game_metadata_rejections')"
            ).fetchone() == (None,)

            again = _alembic(environment, "upgrade", "head")
            assert again.returncode == 0, again.stdout + again.stderr
            assert connection.execute(
                "SELECT to_regclass('game_metadata_rejections')"
            ).fetchone() != (None,)
        finally:
            connection.execute(sql.SQL("RESET search_path"))
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
