"""
[INPUT]: 依赖 domain/ports 的 EventStore，依赖 domain/events 的 DomainEvent / EventEnvelope / EVENT_ADAPTER，依赖 app.errors 的 ConcurrencyError
[OUTPUT]: 对外提供 InMemoryEventStore —— EventStore 的进程内实现
[POS]: persistence 的零依赖事件账本，供测试与离线开发使用；与 PostgresEventStore 同守一份契约（tests/test_event_store.py 双实现共跑）：
       原子追加、乐观并发、版本从 1 严格连续；事件经 JSON 往返后才入账，与 JSONB 实现一样拒收不可序列化的事件
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from app.domain.events import EVENT_ADAPTER, DomainEvent, EventEnvelope
from app.domain.ports import EventStore
from app.errors import ConcurrencyError


class InMemoryEventStore(EventStore):
    def __init__(self) -> None:
        self._streams: dict[str, list[EventEnvelope]] = {}
        self._lock = asyncio.Lock()

    async def append(self, stream_id: str, events: Sequence[DomainEvent], expected_version: int) -> list[EventEnvelope]:
        async with self._lock:
            stream = self._streams.setdefault(stream_id, [])
            if len(stream) != expected_version:
                raise ConcurrencyError(f"世界线冲突：{stream_id} 已到第 {len(stream)} 版，期望第 {expected_version} 版")
            now = datetime.now(UTC)
            fresh = [
                EventEnvelope(
                    stream_id=stream_id,
                    version=expected_version + offset,
                    event_id=uuid4(),
                    recorded_at=now,
                    event=EVENT_ADAPTER.validate_json(EVENT_ADAPTER.dump_json(event)),  # type: ignore[arg-type]
                )
                for offset, event in enumerate(events, start=1)
            ]
            stream.extend(fresh)
            return fresh

    async def load(self, stream_id: str, after_version: int = 0) -> list[EventEnvelope]:
        return list(self._streams.get(stream_id, [])[after_version:])
