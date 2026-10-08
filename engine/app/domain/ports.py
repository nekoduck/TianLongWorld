"""
[INPUT]: 依赖 abc 的 ABC / abstractmethod，依赖 domain/events 的 DomainEvent / EventEnvelope，依赖 domain/models 的 WorldBlueprint，
         依赖 domain/snapshot 的 LocalSnapshot
[OUTPUT]: 对外提供 持久化端口 EventStore（事件账本）、WorldReader（图谱查询侧）、WorldProjector（图谱投影侧）、WorldSeeder（原著播种）、
          NarrativeMemory（长线记忆）与 MemoryRecord
[POS]: domain 拥有的抽象边界（依赖倒置）：领域与应用层只依赖这些 ABC，infrastructure 提供 PostgreSQL / Neo4j / Qdrant 与内存实现；
       图谱端口按职责拆为读、投影、播种三个接口（接口隔离）——裁决只需要读，事件发布只需要投影，CLI 只需要播种
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.domain.events import DomainEvent, EventEnvelope
from app.domain.models import WorldBlueprint
from app.domain.snapshot import LocalSnapshot


# ============================================================
#  事件账本 —— 唯一的真相来源，只追加
# ============================================================
class EventStore(ABC):
    @abstractmethod
    async def append(self, stream_id: str, events: Sequence[DomainEvent], expected_version: int) -> list[EventEnvelope]:
        """原子追加。流的当前版本 ≠ expected_version 时抛 ConcurrencyError，一条也不写。"""

    @abstractmethod
    async def load(self, stream_id: str, after_version: int = 0) -> list[EventEnvelope]:
        """按版本升序读出 after_version 之后的全部事件。"""


# ============================================================
#  图谱 —— 原著本体（只读的正典）+ 每个平行世界的动态覆盖层（事件投影）
# ============================================================
class WorldReader(ABC):
    @abstractmethod
    async def local_snapshot(self, player_id: str) -> LocalSnapshot:
        """玩家此刻所在之处的局部真理快照；玩家不在图中时抛 ProjectionError。"""

    @abstractmethod
    async def spawn_points(self) -> tuple[str, ...]:
        """可以投胎的地点（至少有一条出路），按 id 排序。"""

    @abstractmethod
    async def labels(self, ids: Iterable[str]) -> dict[str, str]:
        """id → 显示名（含玩家）。查无此 id 的不出现在结果里。"""


class WorldProjector(ABC):
    @abstractmethod
    async def project(self, player_id: str, envelopes: Sequence[EventEnvelope]) -> int:
        """把事件投影进该玩家的覆盖层。版本不超过检查点的事件被跳过（幂等），返回新检查点。"""

    @abstractmethod
    async def checkpoint(self, player_id: str) -> int:
        """该玩家覆盖层已投影到的事件版本；未投影过为 0。"""

    @abstractmethod
    async def forget(self, player_id: str) -> None:
        """抹去该玩家的覆盖层（正典不动），用于从事件流整体重建投影。"""


class WorldSeeder(ABC):
    @abstractmethod
    async def seed(self, blueprint: WorldBlueprint, *, reset: bool = False) -> None:
        """把原著蓝图写成图谱正典。幂等；reset 时先清空正典与全部覆盖层（开启新纪元）。"""

    @abstractmethod
    async def is_seeded(self) -> bool: ...


# ============================================================
#  长线记忆 —— 事件的确定性白描，而非大模型的散文（散文可能有幻觉，不配当记忆）
# ============================================================
@dataclass(frozen=True, slots=True)
class MemoryRecord:
    player_id: str
    version: int
    text: str


class NarrativeMemory(ABC):
    @abstractmethod
    async def remember(self, records: Sequence[MemoryRecord]) -> None:
        """写入记忆。以 (player_id, version) 为主键幂等覆盖，重放安全。"""

    @abstractmethod
    async def recall(self, player_id: str, query: str, limit: int, before_version: int) -> list[MemoryRecord]:
        """召回与 query 最相关、且发生在 before_version 之前的记忆。"""
