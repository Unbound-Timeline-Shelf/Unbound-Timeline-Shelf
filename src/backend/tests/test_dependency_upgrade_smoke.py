"""Smoke tests for the major runtime libraries upgraded in Package-Updates.

These are intentionally small integration checks: they exercise the public APIs
most exposed to cross-version incompatibilities, rather than asserting versions.
"""

from datetime import datetime, timezone

from authlib.integrations.starlette_client import OAuth
from dateutil.rrule import DAILY, rrule
from fastapi import FastAPI
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, insert, select
from starlette.testclient import TestClient


def test_fastapi_starlette_httpx_test_client_round_trip() -> None:
    app = FastAPI()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_sqlalchemy_core_sqlite_round_trip() -> None:
    metadata = MetaData()
    records = Table(
        "records",
        metadata,
        Column("id", Integer, primary_key=True),
    )

    with create_engine("sqlite+pysqlite:///:memory:").begin() as connection:
        metadata.create_all(connection)
        connection.execute(insert(records).values(id=1))
        result = connection.execute(select(records.c.id)).scalar_one()

    assert result == 1


def test_dateutil_recurrence_and_authlib_starlette_import() -> None:
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    occurrences = list(rrule(DAILY, count=3, dtstart=start))

    assert len(occurrences) == 3
    assert occurrences[-1].day == 3
    assert OAuth is not None
