"""
[INPUT]: 依赖 application/bus 的命令、回合消息与 CommandHandler，依赖 application/intent_parser 的 IntentParser / scene_names，
         依赖 application/adjudication 的 AdjudicationSlot，依赖 application/options 的 ActionOption / OptionGenerator / compose，
         依赖 application/navigation 的 NavigationOption / navigation，依赖 application/npc_agent 的 NpcDirector，依赖 application/briefs/agenda 的 veil，
         依赖 application/narrator 的 Narrator / NarrationRequest / MenuPicks / ShortTermMemory / recollect，依赖 application/projections 的 ProjectionCoordinator，
         依赖 application/chronicle 的 describe / known_arts，依赖 application/status 的 bonds / pursuits / clocks / renown / referenced，
         依赖 application/world_clock 的 WorldClock，依赖 domain/heartbeat 的 Atlas（缺省的空地理），
         依赖 domain/aggregates 的 Player / PlayerState / evolve，依赖 domain/events 的 DomainEvent / EventEnvelope / FactEmerged / Moved，
         依赖 domain/ports 的 EventStore / WorldReader / NarrativeMemory / MemoryRecord，依赖 domain/rules 的 command / envelope / player_tier，
         依赖 app.errors 的 OptionExpiredError / ProjectionError / UnknownPlayerError / WorldNotSeededError
[OUTPUT]: 对外提供 TurnPipeline（一回合的完整生命周期；构造参数 clock 缺省为 atlas 上的 WorldClock、atlas 缺省为空地理、director 缺省为空——没有 H-Agent）、
          RecentMenus（有界的最近菜单缓存）与 MENUS_MAX，四个命令处理器 SpawnPlayerHandler / ResumePlayerHandler / SubmitTextHandler /
          ChooseOptionHandler，以及 register_handlers()（把它们挂上总线）
[POS]: application 的 CQRS 游戏环路：
       命令侧（持玩家锁，串行）：重放事件流 → 自愈投影 → 局部快照 → [Parse] 解析意图（点选不经大模型：按当前状态与快照重算 affordances ∪ navigation，
                                 按 id 取回 underlying_command 的意图）→ rules.command 定下这一招花几刻（Command.time_cost：驳回一刻、同一处所之内走动一刻、
                                 移动取所走那条出路的耗时、调息修习八刻）→
                                 [Validate] rules.envelope 圈出物理边界（出手 / 交涉 / 暗中三路的可裁区间，或结果已定之事 FIXED；驳回为 None）→
                                 [Resolve] 一席裁决（AdjudicationSlot）：自由文本在胜负未定或此景挂着时钟时请地下城主推演（语义物理引擎：
                                 属性碰撞 → 量级 → 代价 → 时钟与收敛 → ResolutionOutput），点选只在胜负未定时由气运确定性取值，其余谁也不请 →
                                 [Event] Player.decide 携提议定案（resolution.settle 过闸：推出结局、钳位、补足代价、时钟坍缩；再经 settle_any 落成路线事件）
                                 → 世界心跳 WorldClock.advance（余波：交手的往事与痕迹、人群溃散、公开之事成为消息 → TimePassed → 消息沿路扩散 → 黎明的风化与顺手牵羊
                                   → 微观行军：带议程的 NPC 沿最省时之路推进、相撞即 EncounterBegan；死者没有心跳）→ 定案与心跳一并追加（第一批，乐观并发）→ 同步投影图谱
                                 → H-Agent（director 在时）：state.encounters 非空即取新快照、NpcDirector.settle_encounters 坍缩中断（每回合至多一场请判官：
                                   撞见在 FIXED 物理边界里推演余波，狭路相逢在可裁区间里洗牌战力；其余确定性）→ 折叠 → NpcDirector.plan（只在 planning_due：
                                   初临江湖、新的一日、江湖震动——议程大模型一日至多一轮）→ 第二批追加 → 投影。被迫脱身的 Moved 同样算作这一回合跨了地方；
       查询侧（无锁）：新快照 → 记忆召回 → 推送结果白描（空串白描滤掉）→
                       [Options] 先算好三份：可供性目录 options.catalogue（编号 m1…，NarrationRequest.menu）、退路菜单 options.generate、方位导航 navigation →
                       [Render] 叙事流式渲染 ∥ 记忆写入：叙事流里的 str 是正文（NarrationDelta），MenuPicks 是说书人在同一次调用里交出的挑选（留着）→
                       options.compose 过闸（key 在目录里、风味文案合格才换上 flavor_text，不足由退路补，一招都没挑中即原样下发退路）→
                       记进最近菜单 → 推送终帧 TurnCompleted(options=过闸的 3~4 席, navigation=导航)。
       在场者的来意：离了家、带着议程的核心 NPC 的意图经 NarrationRequest.errands（本名 → 意图，过 veil：叫不出名的地方抹成「别处」）交给说书人，只作神色举止的端倪。
       局部认知：白描（turn_resolved.facts、<settled_facts>）与记忆只收玩家眼前的那一面——别处狭路相逢冒出的微观事实（FactEmerged 的主体
       不在出招前后的两张快照里）不宣告，它挂在人与地上，等玩家走到那里由快照的 <emerged> 照出来；其余别处之事由 describe 自己不出声。
       大模型在命令侧解析意图、在物理边界里推演、裁决撞见与狭路相逢、立议程，在查询侧只渲染（连同挑招配风味）；领域的定案隔在中间——
       它说什么都越不过闸门，更改不了已入账的结果，挑的招也只能落在引擎给定的目录里，执行的永远是 underlying_command。
       每回合的大模型调用：意图 + 地下城主 +（撞见 / 狭路相逢的判官 ≤1）+（议程 ≤1，只在规划时机）+ 叙事（含菜单，同一次）。
       推演交给叙事的只有入账之物：微观事实（FactEmerged）、时钟的挂上 / 推进 / 坍缩、名望经 describe 白描成 turn_resolved.facts 与 <settled_facts>，
       新快照的 clocks / emerged 进 <clocks> / <emerged>；没有散文旁路，结局作废的推演一个字也到不了叙事。
       重伤夺路而逃（Moved.fleeing）的回合，渲染用的新快照已是逃抵之地，交手前的快照经 NarrationRequest.fled 一并交给渲染器；
       有 Moved（含夺路而逃、撞见里被迫脱身）的回合，出发前的快照与那条 Moved 的此行所为经 recollect 成为短期记忆（NarrationRequest.recollection），
       说书人据此写出预期落差；NarrationRequest.motivation 恒取 PlayerState.motivation（最近一次移动的此行所为）；
       投胎与续前缘（含 quiet 续接）不走时间、不推演 NPC——它们不是玩家的命令；
       点选的笔墨（player_text）：交互选项取玩家看见的那句（最近菜单里的 flavor_text，缓存不在即朴素标签），导航取「动身前往去处（方位）」——未知之地只说未知区域；
       最近菜单（RecentMenus）每位玩家只留最后一个版本、至多 MENUS_MAX 位：quiet 续接版本相同即原样下发（风味不丢），否则退路菜单——它只是体验的缓存，
       点选核验从不依赖它（多进程部署照样一致）；
       记忆召回分两路、原话优先：原话 + 玩家名一路，焦点实体（PlayerState.focus）+ 在场者本名一路，各多取一倍再按字面去重（调息两次就是两条一模一样的白描）；
       续前缘（resume）与出手共用玩家锁，续上的必是落账之后的局面；quiet 续接不复述此景、不调大模型，只下发选项、导航与状态（断线重连、选项过期）；
       在场者的恩怨缘由（PlayerState.attitude_causes）经 NarrationRequest.causes 交给渲染器；死者的伤势栏写「气绝」，没有选项也没有导航；
       状态栏的人情（bonds）与心事（pursuits）两栏：status.referenced 列出要取名的 id，快照没有的名字一回合只向 reader.labels 取一次；
       眼前的暗流（clocks，快照召回的时钟）、名望（renown，聚合的语义标签）与时辰（time，快照的 time_label，死者停在最后时刻）随状态栏下发
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import asyncio
import logging
import weakref
import zlib
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from functools import reduce
from uuid import uuid4

from app.application import status
from app.application.adjudication import AdjudicationSlot
from app.application.briefs.agenda import veil
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
from app.application.intent_parser import IntentParser, scene_names
from app.application.narrator import MenuPicks, NarrationRequest, Narrator, ShortTermMemory, recollect
from app.application.navigation import NavigationOption, navigation
from app.application.npc_agent import NpcDirector
from app.application.options import ActionOption, OptionGenerator, compose
from app.application.projections import ProjectionCoordinator
from app.application.world_clock import WorldClock
from app.domain import rules
from app.domain.aggregates import Player, PlayerState, evolve
from app.domain.events import DomainEvent, EventEnvelope, FactEmerged, Moved
from app.domain.heartbeat import Atlas
from app.domain.intent import PlayerIntent
from app.domain.models import EntityKind, entity_id
from app.domain.ports import EventStore, MemoryRecord, NarrativeMemory, WorldReader
from app.domain.snapshot import LocalSnapshot
from app.errors import OptionExpiredError, ProjectionError, UnknownPlayerError, WorldNotSeededError

logger = logging.getLogger(__name__)

type IntentSource = Callable[[Player, LocalSnapshot], Awaitable[tuple[PlayerIntent, str]]]
type Menus = tuple[tuple[ActionOption, ...], tuple[ActionOption, ...], tuple[NavigationOption, ...]]

DEAD = "气绝"  # 死者的伤势栏：气血归零的「奄奄一息」还有一口气，死人没有
MENUS_MAX = 1024  # 最近菜单至多记这么多位玩家
JUDGED_PER_TURN = 1  # 每回合至多一场中断请判官，其余确定性


class RecentMenus:
    """
    最近菜单：每位玩家只留最后一个版本下发的 offered（说书人配过风味的那份），至多 capacity 位（最久没碰的先请走）。
    只是体验的缓存——quiet 续接时原样下发、点选时取回玩家看见的那句；点选核验从不依赖它。
    """

    def __init__(self, capacity: int = MENUS_MAX) -> None:
        self._capacity = capacity
        self._menus: OrderedDict[str, tuple[int, tuple[ActionOption, ...]]] = OrderedDict()

    def put(self, player_id: str, version: int, offered: tuple[ActionOption, ...]) -> None:
        self._menus[player_id] = (version, offered)
        self._menus.move_to_end(player_id)
        while len(self._menus) > self._capacity:
            self._menus.popitem(last=False)

    def get(self, player_id: str, version: int) -> tuple[ActionOption, ...] | None:
        """这一版本下发过的菜单；版本不同（世界已变）或从没记过即 None。"""
        hit = self._menus.get(player_id)
        if hit is None or hit[0] != version:
            return None
        self._menus.move_to_end(player_id)
        return hit[1]

    def __len__(self) -> int:
        return len(self._menus)


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
        clock: WorldClock | None = None,
        atlas: Atlas | None = None,
        director: NpcDirector | None = None,
        canon_names: frozenset[str] = frozenset(),
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
        self._atlas = atlas if atlas is not None else Atlas()  # 空 Atlas：时间照走，消息只留在发源地，没有核心 NPC
        self._clock = clock if clock is not None else WorldClock(self._atlas)
        self._director = director  # 没有即没有 H-Agent：NPC 不立议程、不行军，也就没有中断可裁
        self._canon = canon_names  # 风味文案的闸门：点了场景之外的原著名字即退回朴素标签
        self._recall_k = recall_k
        self.menus = RecentMenus()
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
        if command.quiet:  # 断线重连：此景玩家已经读过，不必再花一次大模型复述；同一版本的菜单原样下发（风味不丢）
            snap = await self.snapshot(player)
            offered = self.menus.get(player.id, player.version)
            if offered is None:
                offered = compose((), None, await asyncio.to_thread(self.options.generate, player.state, snap),
                                  scene_names=(), canon_names=())
            nav = navigation(player.state, snap)
            yield _completed(player, snap, "", offered, nav, await self._names(player, snap, {}))
            return
        async for message in self._render(player, [], None, None, labels={}):
            yield message

    def pick(self, player: Player, scene: LocalSnapshot, option_id: str) -> tuple[PlayerIntent, str] | None:
        """
        点选核验：按当前状态与快照重算 affordances ∪ navigation，按 id 取回 underlying_command 的意图与这一招的笔墨；伪造与过期的 id 落空。
        笔墨：交互选项取玩家看见的那句（最近菜单的 flavor_text，不在即朴素标签），导航取「动身前往去处（方位）」。
        """
        state = player.state
        way = next((n for n in navigation(state, scene) if n.id == option_id), None)
        if way is not None:
            return way.intent, _heading(way)
        option = next((o for o in self.options.affordances(state, scene) if o.id == option_id), None)
        if option is None:
            return None
        shown = {o.id: o.flavor_text for o in self.menus.get(player.id, player.version) or ()}
        return option.intent, shown.get(option.id, option.label)

    async def act(self, player_id: str, source: IntentSource, *, clicked: bool = False) -> AsyncIterator[TurnMessage]:
        """clicked：意图来自点选（不经大模型）——胜负未定之事交给气运而不是地下城主。"""
        lock = self._locks.setdefault(player_id, asyncio.Lock())
        async with lock:  # 同一玩家的命令侧串行；跨进程的并发由事件账本的乐观并发兜底
            player = await self.load(player_id)
            player.ensure_alive()
            before = await self.snapshot(player)
            intent, said = await source(player, before)  # [Parse]
            command = rules.command(intent, player.state, before)  # 这一招花几刻：驳回一刻，移动取那条出路的耗时
            env = rules.envelope(intent, player.state, before)  # [Validate] 获准之举的物理边界（三路赌注或结果已定），驳回为 None
            # [Resolve] 一席裁决：文本在胜负未定或挂着时钟时请地下城主推演、点选在胜负未定时交给气运，其余谁也不请；它们只提议，失灵即空提议
            resolution = await self._slot.resolve(env, before, player.state, intent, said, clicked=clicked)
            decided = player.decide(intent, before, resolution.proposal if resolution else None)  # 领域过闸定案
            # 世界心跳：余波 → 时间 → 扩散 → 生态 → 行军，与定案一并原子追加——静观、沉思也花时间；死者没有心跳
            events = [*decided, *self._clock.advance(command, before, player.state, decided)]
            envelopes = await self._commit(player, events)  # [Event] 第一批
            extra = await self._agents(player, events)  # H-Agent：坍缩中断、规划议程
            envelopes = [*envelopes, *await self._commit(player, extra)]  # 第二批
        moved = next((e for e in (*decided, *extra) if isinstance(e, Moved)), None)
        fled = before if moved is not None and moved.fleeing else None  # 交手现场留给叙事
        recollection = recollect(before, moved.motivation) if moved is not None else None  # 跨进新地方：出发前眼中所见与此行所为
        async for message in self._render(
            player, envelopes, intent, said, labels=before.labels, fled=fled, recollection=recollection, before=before
        ):
            yield message

    async def _commit(self, player: Player, events: Sequence[DomainEvent]) -> list[EventEnvelope]:
        """乐观并发追加一批、折进聚合、同步投影图谱（下一步裁决依赖它）。空批什么也不做。"""
        if not events:
            return []
        envelopes = await self._store.append(player.id, list(events), player.version)
        for stamped in envelopes:
            player.apply(stamped.event)
        await self._coordinator.publish(player.id, envelopes)
        return list(envelopes)

    async def _agents(self, player: Player, batch: Sequence[DomainEvent]) -> list[DomainEvent]:
        """
        H-Agent 的裁决层与宏观层，在第一批入账、投影之后：这一回合撞上的中断先坍缩（取新快照，至多 JUDGED_PER_TURN 场请判官），
        折叠之后再看该不该为核心 NPC 立一轮议程（planning_due 不到即零调用）。返回第二批事件；死者与没有 director 时为空。
        """
        if self._director is None or not player.state.alive:
            return []
        state = player.state
        settled: list[DomainEvent] = []
        if state.encounters:
            scene = await self.snapshot(player)
            settled = await self._director.settle_encounters(state, scene, dm_quota=JUDGED_PER_TURN)
        planned = await self._director.plan(reduce(evolve, settled, state), [*batch, *settled])
        return [*settled, *planned]

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
        recollection: ShortTermMemory | None = None,
        before: LocalSnapshot | None = None,
    ) -> AsyncIterator[TurnMessage]:
        first_new = envelopes[0].version if envelopes else player.version + 1
        snap = await self.snapshot(player)
        recalled = await self._recall(player, said, snap, labels, first_new)
        names = {**labels, **snap.labels}
        state = player.state
        # 在场者的恩怨缘由：叙事不必自己编仇从何来。交手现场（fled）是交手前的样子，那里新结的仇以 settled_facts 为准
        causes = {c.name: cause for c in snap.characters if (cause := state.attitude_causes.get(c.id))}
        # 玩家眼前的那一面：别处冒出的细节（狭路相逢的 FactEmerged）只挂在人与地上，不进白描、叙事与记忆——等他走到那里，快照的 <emerged> 自会照出来
        seen = _in_sight(snap) | (_in_sight(before) if before is not None else frozenset())
        envelopes = [e for e in envelopes if _perceived(e.event, seen)]
        facts = tuple(line for e in envelopes if (line := describe(e.event, names, state.name)))  # 空串白描不出声
        if intent is not None:
            yield TurnResolved(intent=intent, facts=facts)

        # [Options] 先于叙事：目录、退路与导航都是 (状态, 快照) 的纯函数；目录编号交给说书人挑，挑什么都落在这里
        menu, fallback, nav = await asyncio.to_thread(self._menus_of, state, snap)
        remember = asyncio.create_task(self._coordinator.chronicle(player.id, state.name, envelopes, names))
        picks: MenuPicks | None = None
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
                recollection=recollection,
                motivation=state.motivation,
                menu=menu,
                errands=self._errands(state, snap),
            )
            async for chunk in self._narrator.narrate(request):  # [Render] 流式：正文逐片推送，说书人的挑选留到流尽
                if isinstance(chunk, MenuPicks):
                    picks = chunk
                    continue
                parts.append(chunk)
                yield NarrationDelta(text=chunk)
            await remember
        finally:
            remember.cancel()
        offered = compose(menu, picks, fallback, scene_names=scene_names(snap), canon_names=self._canon)  # 迷雾里的去处不算此景之名
        self.menus.put(player.id, player.version, offered)
        yield _completed(player, snap, "".join(parts), offered, nav, await self._names(player, snap, names))

    def _menus_of(self, state: PlayerState, snap: LocalSnapshot) -> Menus:
        """可供性目录（交给说书人）、退路菜单（compose 补位）与方位导航：一回合只算这一次。"""
        return self.options.catalogue(state, snap), self.options.generate(state, snap), navigation(state, snap)

    def _errands(self, state: PlayerState, snap: LocalSnapshot) -> dict[str, str]:
        """
        在场者本名 → 他此行的议程意图：只给离了家、带着议程的核心 NPC——在自己家里的人谈不上来意。
        意图过 veil：玩家叫不出名的地方抹成「别处」，说书人笔下不会替迷雾里的地方报出名字。
        """
        homes = self._atlas.characters
        return {
            c.name: veil(agenda.intent, state, snap, self._atlas)
            for c in snap.characters
            if (agenda := state.agendas.get(c.id)) is not None
            and (home := homes.get(c.id)) is not None
            and home.location_id != snap.location.id
        }

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
    player: Player,
    snap: LocalSnapshot,
    narration: str,
    options: tuple[ActionOption, ...],
    nav: tuple[NavigationOption, ...],
    names: dict[str, str],
) -> TurnCompleted:
    """终帧：叙事全文、选项、导航与状态栏。状态栏只有语义标签，死者的伤势栏写「气绝」；人情、心事、暗流与名望由 status 的纯函数翻成人话。"""
    state = player.state
    return TurnCompleted(
        narration=narration,
        options=options,
        navigation=nav,
        status=PlayerStatus(
            name=state.name,
            location=snap.location.name,
            time=snap.time_label,  # 死者照写最后时刻：他的世界停在那一刻
            tier=rules.player_tier(state, snap).value,
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


def _in_sight(snap: LocalSnapshot) -> frozenset[str]:
    """一张快照里玩家看得见的：自己、此地、在场之人、可见之物。"""
    return frozenset({snap.player_id, snap.location.id, *(c.id for c in snap.characters), *(i.id for i in snap.items)})


def _perceived(event: DomainEvent, seen: frozenset[str]) -> bool:
    """白描与记忆只收玩家眼前的那一面：推演出的细节点了眼前之物（或什么也没点——那是这一招本身的细节）才算；其余事件由 describe 自己决定出不出声。"""
    return not isinstance(event, FactEmerged) or not event.subject_ids or not seen.isdisjoint(event.subject_ids)


def _heading(way: NavigationOption) -> str:
    """导航点选的笔墨：「动身前往剑湖宫（内部）」「动身前往未知区域（东）」——去处只用 shown_name，迷雾里的地名不进叙事与记忆。"""
    return f"动身前往{way.target}（{way.direction.value}）"


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
    """
    点选交互选项或导航项：不经大模型。按当前状态与快照重算 affordances ∪ navigation，所选 id 必须在其中——过期或伪造的 id 一律拒收；
    执行的是那一招的 underlying_command（风味文案只是玩家看见的那句）；胜负未定之事交给气运。
    """

    def __init__(self, pipeline: TurnPipeline) -> None:
        self._pipeline = pipeline

    def handle(self, command: ChooseOption) -> AsyncIterator[TurnMessage]:
        async def source(player: Player, scene: LocalSnapshot) -> tuple[PlayerIntent, str]:
            chosen = self._pipeline.pick(player, scene, command.option_id)
            if chosen is None:
                raise OptionExpiredError("此选项已不合时宜，请依眼前情势重新抉择。")
            return chosen

        return self._pipeline.act(command.player_id, source, clicked=True)


def register_handlers(bus: CommandBus, pipeline: TurnPipeline) -> CommandBus:
    bus.register(SpawnPlayer, SpawnPlayerHandler(pipeline))
    bus.register(ResumePlayer, ResumePlayerHandler(pipeline))
    bus.register(SubmitText, SubmitTextHandler(pipeline))
    bus.register(ChooseOption, ChooseOptionHandler(pipeline))
    return bus
