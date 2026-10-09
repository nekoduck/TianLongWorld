"""
[INPUT]: 依赖 application/bus 的命令、回合消息与 CommandHandler，依赖 application/intent_parser 的 IntentParser，
         依赖 application/resolution_agent 的 Resolver / Resolution，依赖 application/options 的 OptionGenerator，
         依赖 application/narrator 的 Narrator / NarrationRequest，依赖 application/projections 的 ProjectionCoordinator，
         依赖 application/chronicle 的 describe / known_arts，依赖 domain/aggregates 的 Player，依赖 domain/events 的 SkillExecuted / Moved，
         依赖 domain/ports 的 EventStore / WorldReader / NarrativeMemory / MemoryRecord，依赖 domain/rules 的 stakes / player_tier，
         依赖 app.errors 的 OptionExpiredError / ProjectionError / UnknownPlayerError / WorldNotSeededError
[OUTPUT]: 对外提供 TurnPipeline（一回合的完整生命周期）与四个命令处理器 SpawnPlayerHandler / ResumePlayerHandler / SubmitTextHandler /
          ChooseOptionHandler，以及 register_handlers()（把它们挂上总线）
[POS]: application 的 CQRS 游戏环路：
       命令侧（持玩家锁，串行）：重放事件流 → 自愈投影 → 局部快照 → [Parse] 解析意图（选项点选不经大模型）→
                                 [Validate] rules.stakes 圈出可裁区间 → [Resolve] 胜负未定（contested）才请地下城主在区间里提议 →
                                 [Event] Player.decide 携提议定案（combat.settle 钳进区间）→ 追加事件（乐观并发）→ 同步投影图谱；
       查询侧（无锁，并行）：新快照 → 记忆召回 → 推送结果白描 → [Options] 选项生成 ∥ [Render] 叙事流式渲染 ∥ 记忆写入 → 推送终帧。
       大模型在命令侧解析意图、在可裁区间里提议，在查询侧只渲染；领域的定案隔在中间——它说什么都越不过区间，更改不了已入账的结果。
       地下城主的招式速写只在其结局被采纳时（SkillExecuted.outcome 等于提议的结局）经 NarrationRequest 传给渲染器：
       它是散文，不入事件、不入记忆；结局未被采纳，速写与定案不符，当场作废。
       重伤夺路而逃（Moved.fleeing）的回合，渲染用的新快照已是逃抵之地，交手前的快照经 NarrationRequest.fled 一并交给渲染器；
       记忆召回分两路、原话优先：原话 + 玩家名一路，焦点实体（PlayerState.focus）+ 在场者本名一路，各多取一倍再按字面去重（调息两次就是两条一模一样的白描）；
       续前缘（resume）与出手共用玩家锁，续上的必是落账之后的局面；quiet 续接不复述此景、不调大模型，只下发选项与状态（断线重连、选项过期）；
       在场者的恩怨缘由（PlayerState.attitude_causes）经 NarrationRequest.causes 交给渲染器；死者的伤势栏写「气绝」
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
from app.application.chronicle import describe, known_arts
from app.application.intent_parser import IntentParser
from app.application.narrator import NarrationRequest, Narrator
from app.application.options import ActionOption, OptionGenerator
from app.application.projections import ProjectionCoordinator
from app.application.resolution_agent import Resolution, Resolver
from app.domain.aggregates import Player
from app.domain.events import DomainEvent, EventEnvelope, Moved, SkillExecuted
from app.domain.intent import PlayerIntent
from app.domain.models import EntityKind, entity_id
from app.domain.ports import EventStore, MemoryRecord, NarrativeMemory, WorldReader
from app.domain.rules import player_tier, stakes
from app.domain.snapshot import LocalSnapshot
from app.errors import OptionExpiredError, ProjectionError, UnknownPlayerError, WorldNotSeededError

logger = logging.getLogger(__name__)

type IntentSource = Callable[[Player, LocalSnapshot], Awaitable[tuple[PlayerIntent, str]]]

DEAD = "气绝"  # 死者的伤势栏：气血归零的「奄奄一息」还有一口气，死人没有


class TurnPipeline:
    def __init__(
        self,
        *,
        store: EventStore,
        reader: WorldReader,
        coordinator: ProjectionCoordinator,
        memory: NarrativeMemory,
        parser: IntentParser,
        resolver: Resolver,
        options: OptionGenerator,
        narrator: Narrator,
        recall_k: int = 4,
    ) -> None:
        self._store = store
        self._reader = reader
        self._coordinator = coordinator
        self._memory = memory
        self.parser = parser
        self._resolver = resolver
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
        lock = self._locks.setdefault(command.player_id, asyncio.Lock())
        async with lock:  # 与进行中的出手串行：续上的必是那一招落账之后的局面，否则续上的选项一点就过期
            player = await self.load(command.player_id)
        yield SessionOpened(player_id=player.id, name=player.state.name)
        if command.quiet:  # 断线重连：此景玩家已经读过，不必再花一次大模型复述
            snap = await self.snapshot(player)
            offered = await asyncio.to_thread(self.options.generate, player.state, snap)
            yield _completed(player, snap, "", offered)
            return
        async for message in self._render(player, [], None, None, labels={}):
            yield message

    async def act(self, player_id: str, source: IntentSource) -> AsyncIterator[TurnMessage]:
        lock = self._locks.setdefault(player_id, asyncio.Lock())
        async with lock:  # 同一玩家的命令侧串行；跨进程的并发由事件账本的乐观并发兜底
            player = await self.load(player_id)
            player.ensure_alive()
            before = await self.snapshot(player)
            intent, said = await source(player, before)  # [Parse]
            at_stake = stakes(intent, player.state, before)  # [Validate] 只有获准的出手才有赌注（可裁区间）
            resolution = (  # [Resolve] 胜负未定才请地下城主；它只提议，失灵即空提议
                await self._resolver.resolve(at_stake, before, player.state, said)
                if at_stake is not None and at_stake.contested else None
            )
            events = player.decide(intent, before, resolution.proposal if resolution else None)  # 领域定案
            envelopes = await self._store.append(player_id, events, player.version) if events else []  # [Event]
            for envelope in envelopes:
                player.apply(envelope.event)
            await self._coordinator.publish(player_id, envelopes)
        sketch = _adopted_sketch(resolution, events)
        fled = before if any(isinstance(e, Moved) and e.fleeing for e in events) else None  # 交手现场留给叙事
        async for message in self._render(player, envelopes, intent, said, labels=before.labels, hint=sketch, fled=fled):
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
        hint: str = "",
        fled: LocalSnapshot | None = None,
    ) -> AsyncIterator[TurnMessage]:
        first_new = envelopes[0].version if envelopes else player.version + 1
        snap = await self.snapshot(player)
        recalled = await self._recall(player, said, snap, labels, first_new)
        names = {**labels, **snap.labels}
        state = player.state
        # 在场者的恩怨缘由：叙事不必自己编仇从何来。交手现场（fled）是交手前的样子，那里新结的仇以 settled_facts 为准
        causes = {c.name: cause for c in snap.characters if (cause := state.attitude_causes.get(c.id))}
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
                memories=recalled,
                player_text=said,
                style=intent.narrative_style if intent else "",
                hint=hint,
                fled=fled,
                causes=causes,
            )
            async for chunk in self._narrator.narrate(request):  # [Render] 流式
                parts.append(chunk)
                yield NarrationDelta(text=chunk)
            offered = await options
            await remember
        finally:
            for task in (options, remember):
                task.cancel()
        yield _completed(player, snap, "".join(parts), offered)

    async def _recall(
        self, player: Player, said: str | None, snap: LocalSnapshot, labels: dict[str, str], before: int
    ) -> tuple[str, ...]:
        """
        两路召回、原话优先：一路查玩家原话（他此刻在说的事），一路查焦点实体与在场者（「再打他一拳」里的「他」是谁、此地站着谁）。
        合在一起时原话命中的在前——在场者的名字多，混进同一条查询会把原话的往事挤出名额。
        各取一倍再按字面去重（调息两次就是两条一模一样的白描），最后取 k 条。
        """
        if not said:
            return ()
        k, me = self._recall_k, player.state.name
        hints = _recall_hints(player, snap, labels)
        spoken, hinted = await asyncio.gather(
            self._memory.recall(player.id, f"{said} {me}", k * 2, before),
            self._memory.recall(player.id, " ".join(hints), k * 2, before) if hints else _nothing(),
        )
        return tuple(dict.fromkeys(m.text for m in (*spoken, *hinted)))[:k]


def _completed(player: Player, snap: LocalSnapshot, narration: str, options: tuple[ActionOption, ...]) -> TurnCompleted:
    """终帧：叙事全文、选项与状态栏。状态栏只有语义标签，死者的伤势栏写「气绝」。"""
    state = player.state
    return TurnCompleted(
        narration=narration,
        options=options,
        status=PlayerStatus(
            name=state.name,
            location=snap.location.name,
            tier=player_tier(state, snap).value,
            health=state.vitality.value if state.alive else DEAD,
            alive=state.alive,
            death_cause=state.death_cause,
            inventory=tuple(i.name for i in snap.inventory),
            skills=known_arts(snap),
        ),
        game_over=not state.alive,
    )


def _recall_hints(player: Player, snap: LocalSnapshot, labels: dict[str, str]) -> tuple[str, ...]:
    """原话之外的召回线索：焦点实体的名字与在场者的本名，都来自状态与快照，确定性的。"""
    names = {**labels, **snap.labels}
    focus = (names.get(x) or snap.label(x) for x in player.state.focus)
    return tuple(dict.fromkeys([*focus, *(c.name for c in snap.characters)]))


async def _nothing() -> list[MemoryRecord]:
    return []


def _adopted_sketch(resolution: Resolution | None, events: Sequence[DomainEvent]) -> str:
    """地下城主的速写只配它被采纳的结局：入账的 SkillExecuted 与它提议的结局不符（被钳回确定性裁决），速写就与定案矛盾，作废。"""
    if resolution is None or resolution.proposal is None or not resolution.narrative_hint:
        return ""
    executed = next((e for e in events if isinstance(e, SkillExecuted)), None)
    adopted = executed is not None and executed.outcome is resolution.proposal.outcome
    return resolution.narrative_hint if adopted else ""


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
