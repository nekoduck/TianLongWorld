"""
[INPUT]: 依赖标准库 sqlite3 / threading，依赖 app.events 的 LifeBegan / TurnResolved / WorldEventRecorded / LIFE_EVENTS / WORLD_EVENTS，
         依赖 app.errors 的 SessionBusyError
[OUTPUT]: 对外提供 EventStore（load_life / load_world / world_exists / append / close）
[POS]: app 的持久化层，事件溯源的唯一事实来源：只有 INSERT，没有 UPDATE / DELETE（由数据库触发器强制）；
       一条命一条 life 流，一个世界一条 world 流，一回合的 life 事件与 world 事件在同一事务里原子追加；
       engine.py 从这里读出的事件纯函数投影出全部状态，进程重启后会话照常延续
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import sqlite3
import threading
from collections.abc import Sequence
from uuid import UUID

from app.errors import SessionBusyError
from app.events import LIFE_EVENTS, WORLD_EVENTS, LifeBegan, LifeEvent, TurnResolved, WorldEventRecorded

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    stream   TEXT    NOT NULL,
    seq      INTEGER NOT NULL,
    world_id TEXT    NOT NULL,
    kind     TEXT    NOT NULL,
    payload  TEXT    NOT NULL,
    PRIMARY KEY (stream, seq)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS events_by_world ON events (world_id);
CREATE TRIGGER IF NOT EXISTS events_append_only_update BEFORE UPDATE ON events
    BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_append_only_delete BEFORE DELETE ON events
    BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
"""


def _life(life_id: UUID) -> str:
    return f"life:{life_id}"


def _world(world_id: UUID) -> str:
    return f"world:{world_id}"


class EventStore:
    def __init__(self, path: str) -> None:
        # FastAPI 的事件循环与 TestClient 的工作线程都会访问同一连接：允许跨线程，用锁串行化
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------------
    #  读
    # ------------------------------------------------------------------
    def load_life(self, life_id: UUID) -> tuple[LifeEvent, ...]:
        return tuple(LIFE_EVENTS.validate_json(row) for row in self._payloads(_life(life_id)))

    def load_world(self, world_id: UUID) -> tuple[WorldEventRecorded, ...]:
        return tuple(WORLD_EVENTS.validate_json(row) for row in self._payloads(_world(world_id)))

    def world_exists(self, world_id: UUID) -> bool:
        with self._lock:
            row = self._db.execute("SELECT 1 FROM events WHERE world_id = ? LIMIT 1", (str(world_id),)).fetchone()
        return row is not None

    def _payloads(self, stream: str) -> list[str]:
        with self._lock:
            rows = self._db.execute("SELECT payload FROM events WHERE stream = ? ORDER BY seq", (stream,)).fetchall()
        return [str(payload) for (payload,) in rows]

    # ------------------------------------------------------------------
    #  写：只追加
    # ------------------------------------------------------------------
    def append(
        self,
        *,
        life_id: UUID,
        world_id: UUID,
        expected_version: int,
        life: Sequence[LifeBegan | TurnResolved],
        world: Sequence[WorldEventRecorded] = (),
    ) -> None:
        """
        原子追加一回合的全部事实。expected_version 是调用方投影时看到的 life 流长度（乐观并发）：
        若期间已有别的请求追加，主键冲突，整个事务回滚并报 SessionBusyError——世界线不会分叉。
        """
        rows: list[tuple[str, int, str, str, str]] = [
            (_life(life_id), expected_version + i, str(world_id), event.kind, event.model_dump_json())
            for i, event in enumerate(life)
        ]
        with self._lock:
            try:
                self._db.execute("BEGIN IMMEDIATE")
                (next_seq,) = self._db.execute(
                    "SELECT COALESCE(MAX(seq) + 1, 0) FROM events WHERE stream = ?", (_world(world_id),)
                ).fetchone()
                rows += [
                    (_world(world_id), int(next_seq) + i, str(world_id), event.kind, event.model_dump_json())
                    for i, event in enumerate(world)
                ]
                self._db.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?)", rows)
                self._db.execute("COMMIT")
            except sqlite3.IntegrityError as exc:
                self._rollback()
                raise SessionBusyError("此局已被另一招抢先落定，请稍候再出招。") from exc
            except BaseException:
                self._rollback()
                raise

    def _rollback(self) -> None:
        if self._db.in_transaction:
            self._db.execute("ROLLBACK")
