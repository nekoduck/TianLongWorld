"""
[INPUT]: 依赖 application/bus 的命令、回合消息与 CommandHandler，依赖 application/intent_parser 的 IntentParser，
         依赖 application/options 的 OptionGenerator，依赖 application/narrator 的 Narrator / NarrationRequest，
         依赖 application/projections 的 ProjectionCoordinator，依赖 application/chronicle 的 describe，
         依赖 domain/aggregates 的 Player，依赖 domain/ports 的 EventStore / WorldReader / NarrativeMemory / MemoryRecord，依赖 domain/rules 的 player_tier，
         依赖 app.errors 的 OptionExpiredError / ProjectionError / UnknownPlayerError / WorldNotSeededError
[OUTPUT]: 对外提供 TurnPipeline（一回合的完整生命周期）与四个命令处理器 SpawnPlayerHandler / ResumePlayerHandler / SubmitTextHandler /
          ChooseOptionHandler，以及 register_handlers()（把它们挂上总线）
[POS]: application 的 CQRS 游戏环路：
       命令侧（持玩家锁，串行）：重放事件流 → 自愈投影 → 局部快照 → [Parse] 解析意图（选项点选不经大模型）→ [Validate] 规则裁决 →
                                 [Event] 追加事件（乐观并发）→ 同步投影图谱；
       查询侧（无锁，并行）：新快照 ∥ 记忆召回 → 推送结果白描 → [Options] 选项生成 ∥ [Render] 叙事流式渲染 ∥ 记忆写入 → 推送终帧。
       大模型在命令侧只解析、在查询侧只渲染，二者之间隔着不可变的事件流——它说什么都改不了已入账的结果
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import asyncio
import logging
import weakref
import zlib
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from uuid import uuid4

from app.application.bus import (
    ChooseOption,
    CommandBus,
    CommandHandler,
    NarrationDelta,
    PlayerStatus,
    ResumePlayer,
    SessionOpened,
    SpawnPlayer,
    SubmitText,
    TurnCompleted,
    TurnMessage,
    TurnResolved,
)
from app.application.chronicle import describe
from app.application.intent_parser import IntentParser
from app.application.narrator import NarrationRequest, Narrator
from app.application.options import ActionOption, OptionGenerator
from app.application.projections import ProjectionCoordinator
from app.domain.aggregates import Player
from app.domain.events import EventEnvelope
from app.domain.intent import PlayerIntent
from app.domain.models import EntityKind, entity_id
from app.domain.ports import EventStore, MemoryRecord, NarrativeMemory, WorldReader
from app.domain.rules import player_tier
from app.domain.snapshot import LocalSnapshot
from app.errors import OptionExpiredError, ProjectionError, UnknownPlayerError, WorldNotSeededError

logger = logging.getLogger(__name__)

type IntentSource = Callable[[Player, LocalSnapshot], Awaitable[tuple[PlayerIntent, str]]]


class TurnPipeline:
    def __init__(
        self,
        *,
        store: EventStore,
        reader: WorldReader,
        coordinator: ProjectionCoordinator,
        memory: NarrativeMemory,
        parser: IntentParser,
        options: OptionGenerator,
        narrator: Narrator,
        recall_k: int = 4,
    ) -> None:
        self._store = store
        self._reader = reader
        self._coordinator = coordinator
        self._memory = memory
        self.parser = parser
        self.options = options
        self._narrator = narrator
        self._recall_k = recall_k
        # 弱引用：锁只在有协程持有或等待时存活，长跑进程不会为每位来过的玩家攒下一把锁
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    # ============================================================
    #  命令侧
    # ============================================================
    async def load(self, player_id: str) -> Player:
        """不查状态表：当前状态 = 重放事件流。重放之后顺手让图谱投影追平到同一版本。"""
        player = Player.from_history(player_id, await self._store.load(player_id))
        if not player.spawned:
            raise UnknownPlayerError(f"江湖中查无此人：{player_id}")
        await self._coordinator.heal(player_id, player.version)
        return player

    async def snapshot(self, player: Player) -> LocalSnapshot:
        snap = await self._reader.local_snapshot(player.id)
        if snap.version != player.version or snap.location.id != player.state.location_id:
            raise ProjectionError(f"{player.id} 的图谱快照（第 {snap.version} 版）与事件流（第 {player.version} 版）不一致")
        return snap

    async def spawn(self, command: SpawnPlayer) -> AsyncIterator[TurnMessage]:
        points = await self._reader.spawn_points()
        if not points:
            raise WorldNotSeededError("图谱中没有任何可落脚之地：请先执行原著播种（python -m app.seed）")
        player_id = entity_id(EntityKind.PLAYER, uuid4().hex[:16])
        location = await self._spawn_point(points, command.location, player_id)
        envelopes = await self._store.append(player_id, Player.spawn(player_id, command.name, location), 0)
        await self._coordinator.publish(player_id, envelopes)
        player = Player.from_history(player_id, envelopes)
        yield SessionOpened(player_id=player_id, name=command.name)
        async for message in self._render(player, envelopes, None, None, labels={}):
            yield message

    async def _spawn_point(self, points: Sequence[str], wanted: str | None, player_id: str) -> str:
        if wanted:
            names = await self._reader.labels(points)
            match = next((p for p in points if wanted in (p, names.get(p))), None)
            if match is None:
                raise WorldNotSeededError(f"「{wanted}」不是可投胎之地。可选：{'、'.join(names.get(p, p) for p in points)}")
            return match
        return points[zlib.crc32(player_id.encode()) % len(points)]  # 确定性分配：同一 id 永远落在同一处

    async def resume(self, command: ResumePlayer) -> AsyncIterator[TurnMessage]:
        player = await self.load(command.player_id)
        yield SessionOpened(player_id=player.id, name=player.state.name)
        async for message in self._render(player, [], None, None, labels={}):
            yield message

    async def act(self, player_id: str, source: IntentSource) -> AsyncIterator[TurnMessage]:
        lock = self._locks.setdefault(player_id, asyncio.Lock())
        async with lock:  # 同一玩家的命令侧串行；跨进程的并发由事件账本的乐观并发兜底
            player = await self.load(player_id)
            player.ensure_alive()
            before = await self.snapshot(player)
            intent, said = await source(player, before)  # [Parse]
            events = player.decide(intent, before)  # [Validate] 纯函数裁决
            envelopes = await self._store.append(player_id, events, player.version) if events else []  # [Event]
            for envelope in envelopes:
                player.apply(envelope.event)
            await self._coordinator.publish(player_id, envelopes)
        async for message in self._render(player, envelopes, intent, said, labels=before.labels):
            yield message

    # ============================================================
    #  查询侧
    # ============================================================
    async def _render(
        self,
        player: Player,
        envelopes: Sequence[EventEnvelope],
        intent: PlayerIntent | None,
        said: str | None,
        *,
        labels: dict[str, str],
    ) -> AsyncIterator[TurnMessage]:
        first_new = envelopes[0].version if envelopes else player.version + 1
        snap, memories = await asyncio.gather(
            self.snapshot(player),
            self._memory.recall(player.id, f"{said or ''} {player.state.name}", self._recall_k, first_new)
            if said else _nothing(),
        )
        names = {**labels, **snap.labels}
        state = player.state
        facts = tuple(describe(e.event, names, state.name) for e in envelopes)
        if intent is not None:
            yield TurnResolved(intent=intent, facts=facts)

        options = asyncio.create_task(asyncio.to_thread(self.options.generate, state, snap))  # [Options] 与叙事并行
        remember = asyncio.create_task(self._coordinator.chronicle(player.id, state.name, envelopes, names))
        try:
            parts: list[str] = []
            request = NarrationRequest(
                snapshot=snap,
                facts=facts,
                memories=tuple(m.text for m in memories),
                player_text=said,
                style=intent.narrative_style if intent else "",
            )
            async for chunk in self._narrator.narrate(request):  # [Render] 流式
                parts.append(chunk)
                yield NarrationDelta(text=chunk)
            offered = await options
            await remember
        finally:
            for task in (options, remember):
                task.cancel()
        yield TurnCompleted(
            narration="".join(parts),
            options=offered,
            status=PlayerStatus(
                name=state.name,
                location=snap.location.name,
                tier=player_tier(state, snap).value,
                alive=state.alive,
                death_cause=state.death_cause,
                inventory=tuple(i.name for i in snap.inventory),
                skills=tuple(s.name for s in snap.known_skills),
            ),
            game_over=not state.alive,
        )


async def _nothing() -> list[MemoryRecord]:
    return []


# ============================================================
#  命令处理器 —— 薄适配：把命令翻成"意图从哪来"
# ============================================================
class SpawnPlayerHandler(CommandHandler[SpawnPlayer]):
    def __init__(self, pipeline: TurnPipeline) -> None:
        self._pipeline = pipeline

    def handle(self, command: SpawnPlayer) -> AsyncIterator[TurnMessage]:
        return self._pipeline.spawn(command)


class ResumePlayerHandler(CommandHandler[ResumePlayer]):
    def __init__(self, pipeline: TurnPipeline) -> None:
        self._pipeline = pipeline

    def handle(self, command: ResumePlayer) -> AsyncIterator[TurnMessage]:
        return self._pipeline.resume(command)


class SubmitTextHandler(CommandHandler[SubmitText]):
    """自由文本：经意图解析器（大模型或离线解析）降维。"""

    def __init__(self, pipeline: TurnPipeline) -> None:
        self._pipeline = pipeline

    def handle(self, command: SubmitText) -> AsyncIterator[TurnMessage]:
        async def source(player: Player, scene: LocalSnapshot) -> tuple[PlayerIntent, str]:
            return await self._pipeline.parser.parse(command.text, scene), command.text

        return self._pipeline.act(command.player_id, source)


class ChooseOptionHandler(CommandHandler[ChooseOption]):
    """点选选项：不经大模型。按当前快照重算合法选项，所选 id 必须在其中——过期或伪造的 id 一律拒收。"""

    def __init__(self, pipeline: TurnPipeline) -> None:
        self._pipeline = pipeline

    def handle(self, command: ChooseOption) -> AsyncIterator[TurnMessage]:
        async def source(player: Player, scene: LocalSnapshot) -> tuple[PlayerIntent, str]:
            offered: tuple[ActionOption, ...] = self._pipeline.options.generate(player.state, scene)
            chosen = next((o for o in offered if o.id == command.option_id), None)
            if chosen is None:
                raise OptionExpiredError("此选项已不合时宜，请依眼前情势重新抉择。")
            return chosen.intent, chosen.label

        return self._pipeline.act(command.player_id, source)


def register_handlers(bus: CommandBus, pipeline: TurnPipeline) -> CommandBus:
    bus.register(SpawnPlayer, SpawnPlayerHandler(pipeline))
    bus.register(ResumePlayer, ResumePlayerHandler(pipeline))
    bus.register(SubmitText, SubmitTextHandler(pipeline))
    bus.register(ChooseOption, ChooseOptionHandler(pipeline))
    return bus
