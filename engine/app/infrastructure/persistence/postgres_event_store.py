"""
[INPUT]: 依赖 asyncpg 的连接池与 UniqueViolationError，依赖 domain/ports 的 EventStore，依赖 domain/events 的 EVENT_ADAPTER / EventEnvelope / decode_event（读出即上抛旧账），
         依赖 app.errors 的 ConcurrencyError
[OUTPUT]: 对外提供 PostgresEventStore（connect / init_schema / append / load / close）与 SCHEMA_SQL
[POS]: persistence 的生产事件账本：PostgreSQL 一张只追加的 domain_events 表，领域事件以 JSONB 存放。
       不可变由数据库自己守：触发器拒绝一切 UPDATE / DELETE / TRUNCATE；
       乐观并发三道闸：流级咨询锁串行化同一条流的追加 → 事务内核对当前版本 → (stream_id, version) 唯一约束兜底
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
from collections.abc import Sequence
from uuid import uuid4

import asyncpg

from app.domain.events import EVENT_ADAPTER, DomainEvent, EventEnvelope, decode_event
from app.domain.ports import EventStore
from app.errors import ConcurrencyError

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS domain_events (
    global_position BIGSERIAL PRIMARY KEY,
    stream_id       TEXT        NOT NULL,
    version         INTEGER     NOT NULL CHECK (version > 0),
    event_id        UUID        NOT NULL UNIQUE,
    event_type      TEXT        NOT NULL,
    payload         JSONB       NOT NULL,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (stream_id, version)
);

CREATE INDEX IF NOT EXISTS domain_events_type ON domain_events (event_type);

CREATE OR REPLACE FUNCTION tlbb_forbid_event_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'domain_events 只追加：历史不可篡改（%）', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS domain_events_append_only ON domain_events;
CREATE TRIGGER domain_events_append_only
    BEFORE UPDATE OR DELETE ON domain_events
    FOR EACH ROW EXECUTE FUNCTION tlbb_forbid_event_mutation();

DROP TRIGGER IF EXISTS domain_events_no_truncate ON domain_events;
CREATE TRIGGER domain_events_no_truncate
    BEFORE TRUNCATE ON domain_events
    FOR EACH STATEMENT EXECUTE FUNCTION tlbb_forbid_event_mutation();
"""

_INSERT = """
INSERT INTO domain_events (stream_id, version, event_id, event_type, payload)
SELECT $1, v, e, t, p::jsonb
FROM unnest($2::int[], $3::uuid[], $4::text[], $5::text[]) AS x(v, e, t, p)
RETURNING version, event_id, recorded_at, payload::text
"""

_LOAD = """
SELECT stream_id, version, event_id, recorded_at, payload::text AS payload
FROM domain_events
WHERE stream_id = $1 AND version > $2
ORDER BY version
"""


class PostgresEventStore(EventStore):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    @classmethod
    async def connect(cls, dsn: str, *, min_size: int = 1, max_size: int = 10) -> "PostgresEventStore":
        pool = await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)
        store = cls(pool)
        await store.init_schema()
        return store

    async def init_schema(self) -> None:
        # 并发启动的多个进程同时建表会撞上系统目录的竞争：用全局咨询锁把建表串行化
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('tlbb_domain_events_schema'))")
            await conn.execute(SCHEMA_SQL)

    async def close(self) -> None:
        await self._pool.close()

    async def append(self, stream_id: str, events: Sequence[DomainEvent], expected_version: int) -> list[EventEnvelope]:
        if not events:
            return []
        payloads = [EVENT_ADAPTER.dump_json(e).decode() for e in events]  # type: ignore[arg-type]
        versions = list(range(expected_version + 1, expected_version + 1 + len(events)))
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", stream_id)
                current = await conn.fetchval(
                    "SELECT COALESCE(MAX(version), 0) FROM domain_events WHERE stream_id = $1", stream_id
                )
                if current != expected_version:
                    raise ConcurrencyError(f"世界线冲突：{stream_id} 已到第 {current} 版，期望第 {expected_version} 版")
                rows = await conn.fetch(
                    _INSERT,
                    stream_id,
                    versions,
                    [uuid4() for _ in events],
                    [json.loads(p)["type"] for p in payloads],
                    payloads,
                )
        except asyncpg.UniqueViolationError as exc:
            raise ConcurrencyError(f"世界线冲突：{stream_id} 的第 {expected_version + 1} 版已被写入") from exc
        return [
            EventEnvelope(
                stream_id=stream_id,
                version=row["version"],
                event_id=row["event_id"],
                recorded_at=row["recorded_at"],
                event=decode_event(row["payload"]),
            )
            for row in sorted(rows, key=lambda r: r["version"])
        ]

    async def load(self, stream_id: str, after_version: int = 0) -> list[EventEnvelope]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(_LOAD, stream_id, after_version)
        return [
            EventEnvelope(
                stream_id=row["stream_id"],
                version=row["version"],
                event_id=row["event_id"],
                recorded_at=row["recorded_at"],
                event=decode_event(row["payload"]),
            )
            for row in rows
        ]
