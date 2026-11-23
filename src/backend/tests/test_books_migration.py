"""The books revision (#265 4.3): upgrade from the revision before it, downgrade, upgrade again.

It runs in its own schema, like the upgrade matrix, so the shared test database is untouched.
The revision before it is read from the script directory, so this test keeps working when the
chain is restacked and the revision gets a new parent.
"""

from uuid import uuid4

import psycopg
from alembic.config import Config
from alembic.script import ScriptDirectory
from psycopg import sql

from tests.test_migration_upgrade_matrix import BACKEND, _alembic, _urls

BOOKS_REVISION = "d265b00c5a01"
TABLES = {"books", "book_editions", "book_reads"}


def _tables(connection: psycopg.Connection, schema: str) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (schema,)
        )
    }


def test_books_revision_upgrades_downgrades_and_upgrades_again():
    script = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    parent = script.get_revision(BOOKS_REVISION).down_revision
    assert isinstance(parent, str)
    schema = "books_migration_" + uuid4().hex
    admin_url, environment = _urls(schema)
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            _alembic(environment, "upgrade", parent)
            before = _tables(connection, schema)
            assert not TABLES & before
            _alembic(environment, "upgrade", BOOKS_REVISION)
            assert TABLES <= _tables(connection, schema)
            constraint = connection.execute(
                "SELECT confdeltype FROM pg_constraint WHERE conname = 'fk_books_active_edition'"
                " AND connamespace = %s::regnamespace",
                (schema,),
            ).fetchone()
            assert constraint is not None and constraint[0] == "n"  # ON DELETE SET NULL
            _alembic(environment, "downgrade", parent)
            assert _tables(connection, schema) == before
            _alembic(environment, "upgrade", "head")
            assert TABLES <= _tables(connection, schema)
        finally:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
