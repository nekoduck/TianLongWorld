"""
[INPUT]: 依赖 app.infrastructure.persistence 的 InMemoryEventStore / PostgresEventStore，依赖 tests/conftest 的 PG_DSN
[OUTPUT]: 事件账本契约测试：同一组用例在内存实现与真实 PostgreSQL（设置 TLBB_TEST_POSTGRES_DSN 时）上共跑
[POS]: tests 的里氏替换证明：两种账本对上层不可区分——原子追加、乐观并发、版本连续、JSONB 往返；
       另验证 PostgreSQL 由触发器守住"只追加"，历史在数据库层面不可篡改；库里的旧词汇（SkillLearned）读出时被上抛为现行事件
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import asyncio
import json
from collections.abc import AsyncIterator
from uuid import uuid4

import asyncpg
import pytest

from app.domain.events import LEGACY_MASTERY_POINTS, Conversed, Moved, PlayerSpawned, SkillPracticed
from app.domain.ports import EventStore
from app.errors import ConcurrencyError
from app.infrastructure.persistence.memory_event_store import InMemoryEventStore
from app.infrastructure.persistence.postgres_event_store import PostgresEventStore
from tests.conftest import PG_DSN

SPAWN = PlayerSpawned(player_id="ply:x", name="阿星", location_id="loc:无量山")
MOVE = Moved(from_location_id="loc:无量山", to_location_id="loc:大理城", exit_label="南下")


@pytest.fixture(params=["memory", pytest.param("postgres", marks=pytest.mark.postgres)])
async def store(request: pytest.FixtureRequest) -> AsyncIterator[EventStore]:
    if request.param == "memory":
        yield InMemoryEventStore()
        return
    if not PG_DSN:
        pytest.skip("未设置 TLBB_TEST_POSTGRES_DSN")
    pg = await PostgresEventStore.connect(PG_DSN)
    yield pg
    await pg.close()


def stream() -> str:
    return f"ply:{uuid4().hex}"  # 每个用例一条新流：真实数据库只追加，无法也不应清表


async def test_append_and_load_round_trip(store: EventStore) -> None:
    sid = stream()
    written = await store.append(sid, [SPAWN, MOVE], expected_version=0)
    assert [e.version for e in written] == [1, 2]
    loaded = await store.load(sid)
    assert [e.event for e in loaded] == [SPAWN, MOVE]
    assert [e.event_id for e in loaded] == [e.event_id for e in written]
    assert [e.event for e in await store.load(sid, after_version=1)] == [MOVE]
    assert await store.load(stream()) == []


async def test_optimistic_concurrency_writes_nothing_on_conflict(store: EventStore) -> None:
    sid = stream()
    await store.append(sid, [SPAWN], expected_version=0)
    with pytest.raises(ConcurrencyError):
        await store.append(sid, [MOVE, Conversed(npc_id="chr:段誉")], expected_version=0)
    assert len(await store.load(sid)) == 1  # 原子：冲突时一条也不写


async def test_concurrent_writers_only_one_wins(store: EventStore) -> None:
    sid = stream()
    await store.append(sid, [SPAWN], expected_version=0)
    results = await asyncio.gather(
        *(store.append(sid, [Conversed(npc_id=f"chr:{i}")], expected_version=1) for i in range(5)),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert all(isinstance(r, ConcurrencyError) for r in results if isinstance(r, Exception))
    assert [e.version for e in await store.load(sid)] == [1, 2]


@pytest.mark.postgres
async def test_postgres_history_is_append_only() -> None:
    if not PG_DSN:
        pytest.skip("未设置 TLBB_TEST_POSTGRES_DSN")
    pg = await PostgresEventStore.connect(PG_DSN)
    sid = stream()
    await pg.append(sid, [SPAWN], expected_version=0)
    conn = await asyncpg.connect(PG_DSN)
    try:
        for sql in ("UPDATE domain_events SET event_type = 'Forged' WHERE stream_id = $1",
                    "DELETE FROM domain_events WHERE stream_id = $1"):
            with pytest.raises(asyncpg.RaiseError, match="只追加"):
                await conn.execute(sql, sid)
        with pytest.raises(asyncpg.RaiseError, match="只追加"):
            await conn.execute("TRUNCATE domain_events")
        payload = await conn.fetchval("SELECT payload->>'location_id' FROM domain_events WHERE stream_id = $1", sid)
        assert payload == "loc:无量山"  # JSONB：可以直接在库里按字段查询事件
    finally:
        await conn.close()
        await pg.close()


@pytest.mark.postgres
async def test_postgres_upcasts_legacy_vocabulary_on_read() -> None:
    if not PG_DSN:
        pytest.skip("未设置 TLBB_TEST_POSTGRES_DSN")
    pg = await PostgresEventStore.connect(PG_DSN)
    sid = stream()
    await pg.append(sid, [SPAWN], expected_version=0)
    legacy = {"type": "SkillLearned", "skill_id": "art:一阳指", "source_id": "chr:段正淳"}  # 旧版引擎写下的"获得即学会"
    conn = await asyncpg.connect(PG_DSN)
    try:
        await conn.execute(
            "INSERT INTO domain_events (stream_id, version, event_id, event_type, payload) VALUES ($1, 2, $2, $3, $4::jsonb)",
            sid, uuid4(), "SkillLearned", json.dumps(legacy),
        )
        loaded = await pg.load(sid)
        assert loaded[1].event == SkillPracticed(
            skill_id="art:一阳指", proficiency_gained=LEGACY_MASTERY_POINTS, source_id="chr:段正淳"
        )
        stored = await conn.fetchval("SELECT event_type FROM domain_events WHERE stream_id = $1 AND version = 2", sid)
        assert stored == "SkillLearned"  # 账本里的字节一字未改
    finally:
        await conn.close()
        await pg.close()
