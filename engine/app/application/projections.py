"""
[INPUT]: 依赖 domain/ports 的 EventStore / WorldProjector / WorldReader / NarrativeMemory / MemoryRecord，依赖 domain/events 的 EventEnvelope / PlayerSpawned，
         依赖 application/chronicle 的 describe
[OUTPUT]: 对外提供 ProjectionCoordinator（publish 投影图谱 / heal 自愈追平 / chronicle 写入长线记忆 / rebuild 从事件流整体重建）
[POS]: application 的投影协调者：事件流是唯一真相，图谱与向量记忆都只是它的两份投影。
       图谱投影是强一致的——下一步的快照与裁决依赖它，所以同步执行，落后则在下一回合开始前用事件流自愈；
       记忆投影是尽力而为的——它只影响叙事的照应，失败只记日志，凭 (玩家, 版本) 幂等主键随时可重放补齐；
       不出声的事件（白描为空串，如服药那条 HealthChanged）不入记忆
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import logging
from collections.abc import Mapping, Sequence

from app.application.chronicle import describe
from app.domain.events import EventEnvelope, PlayerSpawned
from app.domain.ports import EventStore, MemoryRecord, NarrativeMemory, WorldProjector, WorldReader

logger = logging.getLogger(__name__)


class ProjectionCoordinator:
    def __init__(
        self, *, store: EventStore, projector: WorldProjector, reader: WorldReader, memory: NarrativeMemory
    ) -> None:
        self._store = store
        self._projector = projector
        self._reader = reader
        self._memory = memory

    async def publish(self, player_id: str, envelopes: Sequence[EventEnvelope]) -> None:
        if envelopes:
            await self._projector.project(player_id, envelopes)

    async def heal(self, player_id: str, version: int) -> None:
        """让图谱检查点追平事件流的版本。检查点超前于真相说明投影已损坏：抹掉覆盖层，从头重放。"""
        checkpoint = await self._projector.checkpoint(player_id)
        if checkpoint == version:
            return
        if checkpoint > version:
            logger.error("%s 的投影检查点 %d 超前于事件流 %d，整体重建", player_id, checkpoint, version)
            await self._projector.forget(player_id)
            checkpoint = 0
        else:
            logger.warning("%s 的投影落后（%d < %d），从事件流追平", player_id, checkpoint, version)
        await self._projector.project(player_id, await self._store.load(player_id, checkpoint))

    async def chronicle(
        self, player_id: str, player_name: str, envelopes: Sequence[EventEnvelope], labels: Mapping[str, str]
    ) -> None:
        records = [
            MemoryRecord(player_id, e.version, text) for e in envelopes if (text := describe(e.event, labels, player_name))
        ]
        if not records:
            return
        try:
            await self._memory.remember(records)
        except Exception:  # 记忆是尽力而为的投影，任何失败都不能拖垮回合
            logger.exception("长线记忆写入失败（%s 第 %s 版起），可经 rebuild 重放补齐", player_id,
                             envelopes[0].version if envelopes else "?")

    async def rebuild(self, player_id: str) -> int:
        """运维入口：抹去两份投影中该玩家的痕迹，从事件流第 1 版重放。返回重放的事件数。"""
        history = await self._store.load(player_id)
        await self._projector.forget(player_id)
        await self._projector.project(player_id, history)
        ids = {player_id}
        for envelope in history:
            ids |= {v for v in envelope.event.model_dump().values() if isinstance(v, str) and ":" in v}
        labels = await self._reader.labels(ids)
        name = next((e.event.name for e in history if isinstance(e.event, PlayerSpawned)), player_id)
        await self.chronicle(player_id, name, history, labels)
        return len(history)
