"""#469: measure one complete game-library read, the request every full-list consumer makes.

Not part of the test suite (the file name does not match ``test_*.py``, so CI never collects
it). Run it on purpose in the Linux lane, which gives PostgreSQL 18 and Python 3.12:

    tools/backend_linux_lane.sh tests/measure_full_library_reads.py -s

It seeds one account with a library of each size, then reads it the way
``fetchAllGamePages`` in ``src/frontend/src/services/gamePages.ts`` does: the first page of
1000 with counts, then the remaining pages four at a time. Each size is read cold once and then
``RUNS`` more times. It reports wall time in process (no network), how many requests one read
takes, and the body size both raw and gzip-compressed as GZipMiddleware sends it. The JSON goes
to ``/out/full-library-reads.json`` in the lane (or the working directory elsewhere).
"""

import asyncio
import gzip
import json
import os
import statistics
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete

from src.core.auth import get_current_user
from src.database.models.game import Game, GameStatus
from src.database.models.user import User
from src.database.session import SessionLocal
from src.main import app

SIZES = (250, 1000, 5000)
RUNS = 5
PAGE = 1000
CONCURRENCY = 4
_TAGS = ["Action", "RPG", "Indie", "Strategy", "Puzzle", "Co-op", "Open World", "Roguelike"]
_TEXT = "A description of the kind store metadata provides. " * 12


async def _seed(user_id, size: int) -> None:
    async with SessionLocal() as db:
        db.add_all(
            Game(
                user_id=user_id,
                title=f"Game {number:05d}",
                sort_title=f"game {number:05d}",
                folder_location=f"measure_{uuid4().hex}",
                status=list(GameStatus)[number % len(GameStatus)],
                description=_TEXT,
                tags=_TAGS[: 2 + number % 6],
                developer="Studio",
                publisher="Publisher",
                platform="PC",
                source="Steam",
                playtime_seconds=number * 60,
            )
            for number in range(size)
        )
        await db.commit()


async def _read_all(client: httpx.AsyncClient) -> tuple[int, int, int, int]:
    """One complete read: (games, requests, raw bytes, gzip bytes)."""
    bodies: list[bytes] = []

    async def page(skip: int, counts: bool) -> httpx.Response:
        response = await client.get(
            "/api/game/list",
            params={"skip": skip, "limit": PAGE, "include_counts": str(counts).lower()},
        )
        assert response.status_code == 200, response.text
        bodies.append(response.content)
        return response

    first = await page(0, True)
    total = int(first.headers["x-total-count"])
    offsets = list(range(PAGE, total, PAGE))
    for start in range(0, len(offsets), CONCURRENCY):
        await asyncio.gather(*(page(skip, False) for skip in offsets[start : start + CONCURRENCY]))
    games = sum(len(json.loads(body)) for body in bodies)
    raw = sum(len(body) for body in bodies)
    packed = sum(len(gzip.compress(body, compresslevel=9)) for body in bodies)
    return games, len(bodies), raw, packed


@pytest.fixture
async def account():
    async with SessionLocal() as db:
        user = User(username=uuid4().hex, email=f"{uuid4().hex}@example.test", password_hash="x")
        db.add(user)
        await db.commit()
    app.dependency_overrides[get_current_user] = lambda: user
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield user, client
    app.dependency_overrides.pop(get_current_user, None)
    async with SessionLocal() as db:
        await db.execute(delete(User).where(User.id == user.id))
        await db.commit()


async def test_measure_full_library_reads(account):
    user, client = account
    seeded = 0
    results = []
    for size in SIZES:
        await _seed(user.id, size - seeded)
        seeded = size
        timings = []
        for _ in range(RUNS + 1):
            started = time.perf_counter()
            games, requests, raw, packed = await _read_all(client)
            timings.append((time.perf_counter() - started) * 1000)
            assert games == size
        results.append(
            {
                "games": size,
                "requests_per_read": requests,
                "cold_ms": round(timings[0], 1),
                "warm_median_ms": round(statistics.median(timings[1:]), 1),
                "warm_max_ms": round(max(timings[1:]), 1),
                "raw_bytes": raw,
                "gzip_bytes": packed,
            }
        )
    out = Path("/out") if Path("/out").is_dir() else Path(os.getcwd())
    (out / "full-library-reads.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
