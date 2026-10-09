"""
[INPUT]: 依赖 application/bus 的命令、回合消息与 CommandHandler，依赖 application/intent_parser 的 IntentParser，
         依赖 application/adjudication 的 AdjudicationSlot，依赖 application/options 的 OptionGenerator，
         依赖 application/narrator 的 Narrator / NarrationRequest / hooks，依赖 application/projections 的 ProjectionCoordinator，
         依赖 application/chronicle 的 describe / known_arts，依赖 application/status 的 bonds / pursuits / clocks / renown / referenced，
         依赖 domain/aggregates 的 Player，依赖 domain/events 的 Moved，
         依赖 domain/ports 的 EventStore / WorldReader / NarrativeMemory / MemoryRecord，依赖 domain/rules 的 envelope / player_tier，
         依赖 app.errors 的 OptionExpiredError / ProjectionError / UnknownPlayerError / WorldNotSeededError
[OUTPUT]: 对外提供 TurnPipeline（一回合的完整生命周期）与四个命令处理器 SpawnPlayerHandler / ResumePlayerHandler / SubmitTextHandler /
          ChooseOptionHandler，以及 register_handlers()（把它们挂上总线）
[POS]: application 的 CQRS 游戏环路：
       命令侧（持玩家锁，串行）：重放事件流 → 自愈投影 → 局部快照 → [Parse] 解析意图（选项点选不经大模型）→
                                 [Validate] rules.envelope 圈出物理边界（出手 / 交涉 / 暗中三路的可裁区间，或结果已定之事 FIXED；驳回为 None）→
                                 [Resolve] 一席裁决（AdjudicationSlot）：自由文本在胜负未定或此景挂着时钟时请地下城主推演（语义物理引擎：
                                 属性碰撞 → 量级 → 代价 → 时钟与收敛 → ResolutionOutput），点选只在胜负未定时由气运确定性取值，其余谁也不请 →
                                 [Event] Player.decide 携提议定案（resolution.settle 过闸：推出结局、钳位、补足代价、时钟坍缩；再经 settle_any 落成路线事件）
                                 → 追加事件（乐观并发：属性变化、时钟四事件、微观事实、名望一并入账）→ 同步投影图谱（时钟与事实挂上覆盖层）；
       查询侧（无锁）：新快照 → 记忆召回 → 推送结果白描（空串白描滤掉：服药那条 HealthChanged 不出声）→
                       [Options] 先算菜单，「标签（why）」作端倪经 NarrationRequest.hooks 交给说书人 → [Render] 叙事流式渲染 ∥ 记忆写入 → 推送终帧。
       大模型在命令侧解析意图、在物理边界里推演，在查询侧只渲染；领域的定案隔在中间——它说什么都越不过闸门，更改不了已入账的结果。
       推演交给叙事的只有入账之物：微观事实（FactEmerged）、时钟的挂上 / 推进 / 坍缩、名望经 describe 白描成 turn_resolved.facts 与 <settled_facts>，
       新快照的 clocks / emerged 进 <clocks> / <emerged>；没有散文旁路，结局作废的推演一个字也到不了叙事。
       重伤夺路而逃（Moved.fleeing）的回合，渲染用的新快照已是逃抵之地，交手前的快照经 NarrationRequest.fled 一并交给渲染器；
       记忆召回分两路、原话优先：原话 + 玩家名一路，焦点实体（PlayerState.focus）+ 在场者本名一路，各多取一倍再按字面去重（调息两次就是两条一模一样的白描）；
       续前缘（resume）与出手共用玩家锁，续上的必是落账之后的局面；quiet 续接不复述此景、不调大模型，只下发选项与状态（断线重连、选项过期）；
       在场者的恩怨缘由（PlayerState.attitude_causes）经 NarrationRequest.causes 交给渲染器；死者的伤势栏写「气绝」；
       状态栏的人情（bonds）与心事（pursuits）两栏：status.referenced 列出要取名的 id，快照没有的名字一回合只向 reader.labels 取一次；
       眼前的暗流（clocks，快照召回的时钟）与名望（renown，聚合的语义标签）随状态栏下发
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import asyncio
import logging
import weakref
import zlib
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from uuid import uuid4

from app.application import status
from app.application.adjudication import AdjudicationSlot
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
from app.application.narrator import NarrationRequest, Narrator, hooks
from app.application.options import ActionOption, OptionGenerator
from app.application.projections import ProjectionCoordinator
from app.domain.aggregates import Player
from app.domain.events import EventEnvelope, Moved
from app.domain.intent import PlayerIntent
from app.domain.models import EntityKind, entity_id
from app.domain.ports import EventStore, MemoryRecord, NarrativeMemory, WorldReader
from app.domain.rules import envelope, player_tier
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
        slot: AdjudicationSlot,
        options: OptionGenerator,
        narrator: Narrator,
        recall_k: int = 4,
    ) -> None:
        self._store = store
        self._reader = reader
        self._coordinator = coordinator
        self._memory = memory
        self.parser = parser
        self._slot = slot
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
            yield _completed(player, snap, "", offered, await self._names(player, snap, {}))
            return
        async for message in self._render(player, [], None, None, labels={}):
            yield message

    async def act(self, player_id: str, source: IntentSource, *, clicked: bool = False) -> AsyncIterator[TurnMessage]:
        """clicked：意图来自点选（不经大模型）——胜负未定之事交给气运而不是地下城主。"""
        lock = self._locks.setdefault(player_id, asyncio.Lock())
        async with lock:  # 同一玩家的命令侧串行；跨进程的并发由事件账本的乐观并发兜底
            player = await self.load(player_id)
            player.ensure_alive()
            before = await self.snapshot(player)
            intent, said = await source(player, before)  # [Parse]
            env = envelope(intent, player.state, before)  # [Validate] 获准之举的物理边界（三路赌注或结果已定），驳回为 None
            # [Resolve] 一席裁决：文本在胜负未定或挂着时钟时请地下城主推演、点选在胜负未定时交给气运，其余谁也不请；它们只提议，失灵即空提议
            resolution = await self._slot.resolve(env, before, player.state, intent, said, clicked=clicked)
            events = player.decide(intent, before, resolution.proposal if resolution else None)  # 领域过闸定案
            envelopes = await self._store.append(player_id, events, player.version) if events else []  # [Event]
            for stamped in envelopes:
                player.apply(stamped.event)
            await self._coordinator.publish(player_id, envelopes)
        fled = before if any(isinstance(e, Moved) and e.fleeing for e in events) else None  # 交手现场留给叙事
        async for message in self._render(player, envelopes, intent, said, labels=before.labels, fled=fled):
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
        fled: LocalSnapshot | None = None,
    ) -> AsyncIterator[TurnMessage]:
        first_new = envelopes[0].version if envelopes else player.version + 1
        snap = await self.snapshot(player)
        recalled = await self._recall(player, said, snap, labels, first_new)
        names = {**labels, **snap.labels}
        state = player.state
        # 在场者的恩怨缘由：叙事不必自己编仇从何来。交手现场（fled）是交手前的样子，那里新结的仇以 settled_facts 为准
        causes = {c.name: cause for c in snap.characters if (cause := state.attitude_causes.get(c.id))}
        facts = tuple(line for e in envelopes if (line := describe(e.event, names, state.name)))  # 空串白描不出声
        if intent is not None:
            yield TurnResolved(intent=intent, facts=facts)

        # [Options] 先于叙事：菜单是 (状态, 快照) 的纯函数，算好了作端倪交给说书人——它只许露在场面里，不许写成结果
        offered = await asyncio.to_thread(self.options.generate, state, snap)
        remember = asyncio.create_task(self._coordinator.chronicle(player.id, state.name, envelopes, names))
        try:
            parts: list[str] = []
            request = NarrationRequest(
                snapshot=snap,
                facts=facts,
                memories=recalled,
                player_text=said,
                style=intent.narrative_style if intent else "",
                fled=fled,
                causes=causes,
                hooks=hooks(offered),
            )
            async for chunk in self._narrator.narrate(request):  # [Render] 流式
                parts.append(chunk)
                yield NarrationDelta(text=chunk)
            await remember
        finally:
            remember.cancel()
        yield _completed(player, snap, "".join(parts), offered, await self._names(player, snap, names))

    async def _names(self, player: Player, snap: LocalSnapshot, known: dict[str, str]) -> dict[str, str]:
        """状态栏两栏要用的名字：已在手的（交手前后的快照）之外，一回合只向图谱取一次——不在眼前的仇人与心事的标的。"""
        names = {**known, **snap.labels}
        missing = status.referenced(player.state) - names.keys()
        return {**names, **await self._reader.labels(sorted(missing))} if missing else names

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


def _completed(
    player: Player, snap: LocalSnapshot, narration: str, options: tuple[ActionOption, ...], names: dict[str, str]
) -> TurnCompleted:
    """终帧：叙事全文、选项与状态栏。状态栏只有语义标签，死者的伤势栏写「气绝」；人情、心事、暗流与名望由 status 的纯函数翻成人话。"""
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
            bonds=status.bonds(state, snap, names),
            pursuits=status.pursuits(state, names),
            clocks=status.clocks(snap),
            renown=status.renown(state),
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
    """点选选项：不经大模型。按当前快照重算合法选项，所选 id 必须在其中——过期或伪造的 id 一律拒收；胜负未定之事交给气运。"""

    def __init__(self, pipeline: TurnPipeline) -> None:
        self._pipeline = pipeline

    def handle(self, command: ChooseOption) -> AsyncIterator[TurnMessage]:
        async def source(player: Player, scene: LocalSnapshot) -> tuple[PlayerIntent, str]:
            offered: tuple[ActionOption, ...] = self._pipeline.options.generate(player.state, scene)
            chosen = next((o for o in offered if o.id == command.option_id), None)
            if chosen is None:
                raise OptionExpiredError("此选项已不合时宜，请依眼前情势重新抉择。")
            return chosen.intent, chosen.label

        return self._pipeline.act(command.player_id, source, clicked=True)


def register_handlers(bus: CommandBus, pipeline: TurnPipeline) -> CommandBus:
    bus.register(SpawnPlayer, SpawnPlayerHandler(pipeline))
    bus.register(ResumePlayer, ResumePlayerHandler(pipeline))
    bus.register(SubmitText, SubmitTextHandler(pipeline))
    bus.register(ChooseOption, ChooseOptionHandler(pipeline))
    return bus
