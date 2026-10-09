"""
[INPUT]: 依赖 tests/conftest 的 settings / play / spawned_at / kinds / ScriptedLLM，依赖 tests/world 的 WORLD，依赖 app.container 的 build_container，
         依赖 app.application 的 bus（命令与回合消息）/ handlers（RecentMenus）/ narrator（Narrator / NarrationRequest / MenuPick / MenuPicks）/
         options（ActionOption）/ projections（witnessed），依赖 domain 的 events / intent / lore（Persona）/ agenda（SkirmishOutcome / EncounterKind）/ approach（TacticalAxis）/
         geography（DiscoveryStatus / UNKNOWN_PLACE），依赖 app.errors 的 OptionExpiredError
[OUTPUT]: 选项风味封装、空间迷雾导航与分层 NPC 生态的端到端用例（经组合根装配的完整引擎）：
          问路解开迷雾（导航从「未知区域」变成去处名、认知「问路」，白描「某某为你指点了去处。」）→ 导航点选（指令是方位把手、耗时取那条出路、
          到过的来路认知「亲历」、换了地方旧的导航 id 即过期）；
          叙事流里的 MenuPicks 变成风味选项（每席 flavor_text 是说书人的那句、战术轴各不相同、都在可供性目录里），点选执行的是那一招的 underlying_command，
          下一份叙事请求的笔墨是玩家看见的风味；quiet 续接同一版本原样下发风味菜单、缓存没了即退路菜单；
          菜单读不出（空 MenuPicks、根本没交、key 不在目录、文案不合格）退回确定性菜单；真实 LLMNarrator 的 <menu> 尾巴一路过闸成风味；
          带议程的 NPC（议程大模型立下）沿路走来撞见玩家、经判官（ScriptedLLM 替地下城主作答）在结果已定的物理边界里推演：
          白描「来到此地 / 不期而遇」、时钟挂在来者身上、来意经 NarrationRequest.errands 交给说书人、一回合至多一场判官；
          狭路相逢（开篇仇敌同处一地，玩家不在场）由判官在可裁区间里定为两败俱伤：双方带伤（快照 wounded，战力洗牌）、议程中断、
          此地留下交手的往事与血迹、消息沿路传到玩家所在之处（白描不出声：别处的事玩家本不该知道；从事件流重建记忆时那条细节同样剔掉）；
          每回合大模型调用：意图 + 判官 ≤1 + 议程 ≤1 + 叙事；RecentMenus 有界且按版本命中
[POS]: tests 的 H-Agent / 意图风味封装 / 探索迷雾总装验收：测试与生产走同一条组合根
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

from app.application.bus import ChooseOption, ResumePlayer, SpawnPlayer, SubmitText, TurnCompleted, TurnResolved
from app.application.handlers import RecentMenus
from app.application.narrator import MenuPick, MenuPicks, NarrationRequest, Narrator
from app.application.options import ActionOption, OptionCategory
from app.application.projections import witnessed
from app.config import Settings
from app.container import Container, build_container
from app.domain.agenda import EncounterKind, SkirmishOutcome
from app.domain.approach import TacticalAxis
from app.domain.events import (
    ActivityStarted,
    AgendaConcluded,
    AgendaIssued,
    AgendaPlanned,
    ClockStarted,
    DomainEvent,
    EncounterBegan,
    EncounterResolved,
    FactEmerged,
    FactTokenSpawned,
    NpcMoved,
    NpcWounded,
    PlacesLearned,
    TimePassed,
    TraceLeft,
)
from app.domain.geography import UNKNOWN_PLACE, DiscoveryStatus
from app.domain.intent import ActionType, PlayerIntent
from app.domain.lore import Persona
from app.errors import OptionExpiredError
from app.infrastructure.persistence.neo4j_graph import Neo4jWorldGraph
from tests.conftest import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER, PG_DSN, ScriptedLLM, kinds, play, spawned_at
from tests.world import WORLD

ZUO, XIN = "chr:左子穆", "chr:辛双清"

# 左子穆与辛双清是开篇仇敌（WORLD 的关系边），各有执念即核心 NPC；辛双清改住大理城——左子穆去大理城，玩家在场是撞见，不在场是狭路相逢
AGENTS = WORLD.model_copy(update={
    "characters": tuple(c.model_copy(update={"location_id": "loc:大理城"}) if c.id == XIN else c for c in WORLD.characters),
    "personas": (
        Persona(character_id=ZUO, worry="西宗来夺剑湖宫", dislikes=("西宗",), sources=("chunk:1",)),
        Persona(character_id=XIN, worry="东宗占着剑湖宫", sources=("chunk:1",)),
    ),
})
THINK = json.dumps({"action_type": "THINK"})
AGENDA = json.dumps({"agendas": [{"npc": "左子穆", "target": "大理城", "intent": "去大理城寻辛双清", "priority": 2}]},
                    ensure_ascii=False)


def gm(**fields: Any) -> str:
    """一份结果已定之事的推演（撞见的判官作答）：缺省暗流、什么也不动。"""
    return json.dumps({"collision": "来者心有旁骛", "severity": "暗流", "cost": "不欠", "convergence": "无事",
                       "deltas": [], "clock_mutations": [], "new_facts": [], "action_trigger": "无", **fields},
                      ensure_ascii=False)


# ============================================================
#  替身
# ============================================================
class Listening(Narrator):
    """说书人替身：记下每一份 NarrationRequest，再交给原来的说书人照常渲染（MenuPicks 原样透传）。"""

    def __init__(self, inner: Narrator) -> None:
        self._inner = inner
        self.requests: list[NarrationRequest] = []

    async def narrate(self, request: NarrationRequest) -> AsyncIterator[str | MenuPicks]:
        self.requests.append(request)
        async for chunk in self._inner.narrate(request):
            yield chunk


type Picker = Callable[[NarrationRequest], MenuPicks | None]

FLAVOR = {  # 每根轴一句风味：替身按轴挑招
    TacticalAxis.ESCALATE: "按剑而前，逼对方亮出真章",
    TacticalAxis.TRICKERY: "绕到人后，暗里打个主意",
    TacticalAxis.PACIFY: "抱拳一揖，好言相商",
    TacticalAxis.OBSERVE: "按兵不动，冷眼旁观",
}


def by_axis(request: NarrationRequest) -> MenuPicks:
    """每根有招的轴挑目录里的头一招（至多四招），配上那根轴的风味。"""
    picks: dict[TacticalAxis, MenuPick] = {}
    for n, option in enumerate(request.menu, start=1):
        picks.setdefault(option.tactical_axis, MenuPick(key=f"m{n}", flavor=FLAVOR[option.tactical_axis]))
    return MenuPicks(tuple(picks.values())[:4])


class Picking(Narrator):
    """说书人替身：吐两段正文，再按 picker 从可供性目录里挑招（None 即不交菜单，像离线说书人）；记下每份请求。"""

    def __init__(self, picker: Picker) -> None:
        self._picker = picker
        self.requests: list[NarrationRequest] = []

    async def narrate(self, request: NarrationRequest) -> AsyncIterator[str | MenuPicks]:
        self.requests.append(request)
        yield "山风猎猎，"
        yield "四下无声。"
        if (picks := self._picker(request)) is not None:
            yield picks


def rig(container: Container, picker: Picker) -> Picking:
    narrator = Picking(picker)
    container.pipeline._narrator = narrator
    return narrator


async def click(container: Container, pid: str, option_id: str) -> tuple[TurnResolved, TurnCompleted, list[DomainEvent]]:
    before = len(await container.store.load(pid))
    messages = await play(container, ChooseOption(player_id=pid, option_id=option_id))
    assert kinds(messages)[0] == "TurnResolved" and kinds(messages)[-1] == "TurnCompleted"
    fresh = [e.event for e in (await container.store.load(pid))[before:]]
    return messages[0], messages[-1], fresh  # type: ignore[return-value]


async def say(
    container: Container, llm: ScriptedLLM, pid: str, text: str = "想想"
) -> tuple[TurnResolved, TurnCompleted, list[DomainEvent], int]:
    """一回合自由文本：结果帧、终帧、新入账的事件与这一回合的大模型调用次数。"""
    before, calls = len(await container.store.load(pid)), len(llm.calls)
    messages = await play(container, SubmitText(player_id=pid, text=text))
    assert kinds(messages)[0] == "TurnResolved" and kinds(messages)[-1] == "TurnCompleted"
    fresh = [e.event for e in (await container.store.load(pid))[before:]]
    spent = len(llm.calls) - calls
    assert spent <= 5  # 意图 + 地下城主 + 判官 ≤1 + 议程 ≤1 + 叙事
    return messages[0], messages[-1], fresh, spent  # type: ignore[return-value]


async def opening(container: Container, pid: str) -> TurnCompleted:
    done = (await play(container, ResumePlayer(player_id=pid, quiet=True)))[-1]
    assert isinstance(done, TurnCompleted)
    return done


async def affordances(container: Container, pid: str) -> tuple[ActionOption, ...]:
    player = await container.pipeline.load(pid)
    return container.pipeline.options.affordances(player.state, await container.pipeline.snapshot(player))


# ============================================================
#  空间迷雾：问路解开去处，导航点选走过去
# ============================================================
async def test_asking_the_way_lifts_the_fog_and_the_compass_walks_you_there(settings: Settings) -> None:
    container = await build_container(settings, blueprint=AGENTS)
    try:
        pid = await spawned_at(container, "无量山")
        first = await opening(container, pid)
        assert [(n.direction.value, n.target, n.discovery) for n in first.navigation] == [
            ("南", UNKNOWN_PLACE, DiscoveryStatus.UNKNOWN), ("下", UNKNOWN_PLACE, DiscoveryStatus.UNKNOWN),
        ]
        assert all(o.intent.action_type is not ActionType.MOVE for o in first.options)  # 移动不在交互选项里

        ask = next(o for o in await affordances(container, pid)
                   if o.intent.action_type is ActionType.TALK and o.intent.topic == "无量山")
        resolved, done, events = await click(container, pid, ask.id)
        learned = next(e for e in events if isinstance(e, PlacesLearned))
        assert set(learned.location_ids) == {"loc:大理城", "loc:无量玉洞"} and learned.source_id is not None
        guide = learned.source_id.split(":", 1)[1]
        assert f"{guide}为你指点了去处。" in resolved.facts
        assert [(n.direction.value, n.target, n.discovery) for n in done.navigation] == [
            ("南", "大理城", DiscoveryStatus.TOLD), ("下", "无量玉洞", DiscoveryStatus.TOLD),
        ]

        south = done.navigation[0]
        assert south.underlying_command.intent.target_entity == "南" and south.time_label  # 指令是方位把手，不是地名
        resolved, done, events = await click(container, pid, south.id)
        assert resolved.intent is not None and resolved.intent.action_type is ActionType.MOVE
        assert resolved.intent.target_entity == "南" and "阿星经「南下」离开无量山，来到大理城。" in resolved.facts
        assert next(e for e in events if isinstance(e, TimePassed)).ticks == south.time_cost  # 耗时取那条出路
        assert done.status.location == "大理城"
        north = next(n for n in done.navigation if n.direction.value == "北")
        assert (north.target, north.discovery) == ("无量山", DiscoveryStatus.VISITED)
        east = next(n for n in done.navigation if n.direction.value == "东")
        assert east.target == UNKNOWN_PLACE  # 无锡城没人指过路
        with pytest.raises(OptionExpiredError):  # 换了地方，同一个「南」是另一个 id
            await play(container, ChooseOption(player_id=pid, option_id=south.id))
    finally:
        await container.aclose()


# ============================================================
#  意图风味封装：说书人挑招配风味，点选执行的是 underlying_command
# ============================================================
async def test_the_narrators_picks_become_flavoured_options_and_a_click_runs_the_underlying_command(
    settings: Settings,
) -> None:
    container = await build_container(settings, blueprint=WORLD)
    try:
        narrator = rig(container, by_axis)
        pid = await spawned_at(container, "无量山")
        request = narrator.requests[-1]
        assert 3 <= len(request.menu) <= 12 and request.errands == {}
        done = (await play(container, ResumePlayer(player_id=pid)))[-1]
        assert isinstance(done, TurnCompleted)
        catalogue = {o.id: o for o in narrator.requests[-1].menu}
        assert 3 <= len(done.options) <= 4 and all(o.id in catalogue for o in done.options)
        assert len({o.tactical_axis for o in done.options}) == len(done.options)  # 说书人铺开了不同的战术维度
        assert all(o.flavor_text == FLAVOR[o.tactical_axis] != o.label for o in done.options)

        same = await opening(container, pid)  # quiet 续接：同一版本，风味菜单原样下发
        assert same.options == done.options and same.navigation == done.navigation

        chosen = next(o for o in done.options if o.tactical_axis is TacticalAxis.PACIFY)
        resolved, after, events = await click(container, pid, chosen.id)
        assert resolved.intent == chosen.underlying_command.intent  # 执行的是标准指令，不是那句风味
        assert next(e for e in events if isinstance(e, TimePassed)).ticks == chosen.underlying_command.time_cost
        assert narrator.requests[-1].player_text == chosen.flavor_text  # 叙事照应的是玩家看见的那句
        assert after.options and all(o.flavor_text == FLAVOR[o.tactical_axis] for o in after.options)  # 每回合同一次调用里重配

        container.pipeline.menus = RecentMenus()  # 缓存没了（重启、换进程）：quiet 续接退回确定性菜单
        plain = await opening(container, pid)
        assert plain.options and all(o.flavor_text == o.label for o in plain.options)
        assert {o.id for o in plain.options} <= {o.id for o in await affordances(container, pid)}
        with pytest.raises(OptionExpiredError):
            await play(container, ChooseOption(player_id=pid, option_id="social-deadbeef"))
    finally:
        await container.aclose()


@pytest.mark.parametrize(
    "picker",
    [
        lambda request: MenuPicks(),  # 读不出：空 MenuPicks
        lambda request: None,  # 根本没交（离线、降级）
        lambda request: MenuPicks((MenuPick("m99", "按剑而前"), MenuPick("x", "抱拳一揖"))),  # key 不在目录
    ],
    ids=["empty", "absent", "unknown-keys"],
)
async def test_an_unreadable_menu_falls_back_to_the_deterministic_slate(settings: Settings, picker: Picker) -> None:
    container = await build_container(settings, blueprint=WORLD)
    try:
        rig(container, picker)
        pid = await spawned_at(container, "无量山")
        done = (await play(container, ResumePlayer(player_id=pid)))[-1]
        assert isinstance(done, TurnCompleted)
        player = await container.pipeline.load(pid)
        fallback = container.pipeline.options.generate(player.state, await container.pipeline.snapshot(player))
        assert [o.id for o in done.options] == [o.id for o in fallback] and 3 <= len(done.options) <= 4
        assert all(o.flavor_text == o.label for o in done.options)
    finally:
        await container.aclose()


async def test_bad_flavour_keeps_the_seat_but_shows_the_plain_label(settings: Settings) -> None:
    """挑中的招保住席位（说书人可能已在正文里铺垫），文案不合格（结局字眼、英文）的退回朴素标签；不足三席由退路补。"""
    container = await build_container(settings, blueprint=WORLD)
    try:
        rig(container, lambda request: MenuPicks((MenuPick("m1", "一剑将他毙命"), MenuPick("m2", "Attack!"))))
        pid = await spawned_at(container, "无量山")
        done = (await play(container, ResumePlayer(player_id=pid)))[-1]
        assert isinstance(done, TurnCompleted) and len(done.options) == 3
        player = await container.pipeline.load(pid)
        snap = await container.pipeline.snapshot(player)
        catalogue = container.pipeline.options.catalogue(player.state, snap)
        assert [o.id for o in done.options[:2]] == [catalogue[0].id, catalogue[1].id]
        assert all(o.flavor_text == o.label for o in done.options)
    finally:
        await container.aclose()


async def test_the_llm_narrators_menu_tail_is_gated_into_flavour(settings: Settings) -> None:
    """真实 LLMNarrator：同一次调用里正文之后交 <menu>；正文里一个 <menu> 之后的字也不出现，挑中的招换上风味。"""
    llm = ScriptedLLM(
        '山风猎猎。\n<menu>[{"pick":"m1","flavor":"冷眼打量四下动静"},{"pick":"m2","flavor":"上前搭话，探一探口风"},'
        '{"pick":"m3","flavor":"按剑而前，逼他亮出真章"}]</menu>',
    )
    container = await build_container(settings.model_copy(update={"npc_agenda": False}), blueprint=WORLD, llm=llm)
    try:
        messages = await play(container, SpawnPlayer(name="阿星", location="无量山"))
        done = messages[-1]
        assert isinstance(done, TurnCompleted) and "<menu>" not in done.narration and done.narration.startswith("山风猎猎")
        assert "pick" not in "".join(getattr(m, "text", "") for m in messages)
        assert [o.flavor_text for o in done.options] == ["冷眼打量四下动静", "上前搭话，探一探口风", "按剑而前，逼他亮出真章"]
    finally:
        await container.aclose()


# ============================================================
#  分层 NPC 生态：议程 → 行军 → 撞见 / 狭路相逢 → 判官
# ============================================================
async def test_an_npc_with_an_agenda_walks_up_and_the_meeting_is_judged(settings: Settings) -> None:
    """
    初临江湖：议程大模型为左子穆立下「去大理城寻辛双清」；他一刻一刻地走来（四刻的路），走进玩家所在即撞见——
    同一回合请判官（ScriptedLLM 替地下城主作答）在结果已定的物理边界里推演：戒心挂在左子穆身上、留一条细节；来意交给说书人。
    """
    meet = gm(clock_mutations=[{"op": "新建", "clock": "左子穆的戒心", "kind": "疑心", "anchor": "左子穆", "maximum": 4,
                                "steps": 1, "consequence": "认定你是西宗的探子"}],
              new_facts=["左子穆按剑打量你，眉头微皱"])
    llm = ScriptedLLM("苍山如黛。", THINK, AGENDA, "一。", THINK, "二。", THINK, "三。", THINK, "四。",
                      THINK, meet, "左子穆大步走来。", THINK, gm(), "六。")
    container = await build_container(settings, blueprint=AGENTS, llm=llm)
    try:
        listening = Listening(container.pipeline._narrator)
        container.pipeline._narrator = listening
        pid = await spawned_at(container, "大理城")

        _, _, events, spent = await say(container, llm, pid)
        agenda_schema = llm.calls[-2][2]
        assert spent == 3 and agenda_schema is not None and "agendas" in agenda_schema["properties"]  # 意图 + 议程 + 叙事
        planned = next(e for e in events if isinstance(e, AgendaPlanned))
        issued = next(e for e in events if isinstance(e, AgendaIssued))
        assert planned.cause == "初临江湖" and (issued.agenda.npc_id, issued.agenda.target_id) == (ZUO, "loc:大理城")
        for _ in range(3):  # 四刻的路：一刻一刻地走，同日不再规划
            _, _, events, spent = await say(container, llm, pid)
            assert spent == 2 and not any(isinstance(e, NpcMoved | AgendaPlanned) for e in events)

        resolved, done, events, spent = await say(container, llm, pid)
        assert spent == 3  # 意图 + 判官 + 叙事：一回合至多一场判官
        system = llm.calls[-2][0]
        assert "撞见" in system
        assert [type(e) for e in events if not isinstance(e, TimePassed)] == [
            NpcMoved, EncounterBegan, ClockStarted, FactEmerged, EncounterResolved,
        ]
        ended = next(e for e in events if isinstance(e, EncounterResolved))
        assert (ended.kind, ended.by, ended.npc_ids, ended.witnessed) == (EncounterKind.MEET_PLAYER, "地下城主", (ZUO,), True)
        assert resolved.facts == ("左子穆来到此地。", "暗流：左子穆的戒心（1/4）。", "左子穆按剑打量你，眉头微皱。", "左子穆与你不期而遇。")
        assert [(c.name, c.progress) for c in done.status.clocks] == [("左子穆的戒心", 1)]
        assert listening.requests[-1].errands == {"左子穆": "去大理城寻辛双清"}  # 离了家、带议程：来意交给说书人
        assert "左子穆" in [c.name for c in listening.requests[-1].snapshot.characters]

        _, _, events, spent = await say(container, llm, pid)  # 驻足中：不走、不再撞；眼前挂着他的戒心，文本回合请地下城主一次
        assert spent == 3 and not any(isinstance(e, NpcMoved | EncounterBegan) for e in events)
    finally:
        await container.aclose()


async def test_rivals_crossing_paths_reshuffle_their_strength_and_the_news_travels(settings: Settings) -> None:
    """
    玩家远在无锡城：左子穆走进大理城，撞上开篇的仇敌辛双清——狭路相逢。判官在可裁区间（口角上下各一格）里定为两败俱伤：
    双方带伤一日（交手的战力打一档折扣）、左子穆的议程中断；大理城留下交手的往事与血迹，消息沿路传开，两刻后传到无锡城。
    别处的事玩家本不该知道：那一回合的白描一句也不提。
    """
    skirmish = json.dumps({"reasoning": "宿怨难消，一言不合便动了手", "outcome": "两败俱伤", "fact": "辛双清的剑鞘裂开一道细缝"},
                          ensure_ascii=False)
    llm = ScriptedLLM("松鹤楼上。", THINK, AGENDA, "一。", THINK, "二。", THINK, "三。", THINK, "四。",
                      THINK, skirmish, "五。", THINK, "六。", THINK, "七。",
                      json.dumps({"action_type": "MOVE", "target_entity": "西"}, ensure_ascii=False), "你回到大理城。")
    container = await build_container(settings, blueprint=AGENTS, llm=llm)
    try:
        pid = await spawned_at(container, "无锡城")
        for _ in range(4):
            await say(container, llm, pid)
        resolved, _, events, spent = await say(container, llm, pid)
        assert spent == 3 and "狭路相逢" in llm.calls[-2][0]  # 意图 + 判官 + 叙事
        ended = next(e for e in events if isinstance(e, EncounterResolved))
        assert (ended.kind, ended.outcome, ended.by, ended.npc_ids) == (
            EncounterKind.CROSS_PATHS, SkirmishOutcome.BOTH_HURT, "地下城主", (ZUO, XIN))
        assert ended.witnessed is False and resolved.facts == ()  # 别处的事一句也不出声
        assert {e.npc_id for e in events if isinstance(e, NpcWounded)} == {ZUO, XIN}
        assert any(isinstance(e, AgendaConcluded) and e.npc_id == ZUO and e.how.value == "中断" for e in events)
        assert {type(e) for e in events} >= {TraceLeft, ActivityStarted, FactTokenSpawned, FactEmerged}
        news = next(e for e in events if isinstance(e, FactTokenSpawned)).token.text
        assert news == "左子穆与辛双清在大理城交手，两败俱伤"

        heard = []
        for _ in range(2):  # 消息一刻传一处：先到无量山，再到无锡城
            await say(container, llm, pid)
            heard = [r.text for r in (await container.reader.local_snapshot(pid)).rumors]
        assert news in heard

        _, done, _, _ = await say(container, llm, pid, "西归")
        assert done.status.location == "大理城"
        scene = await container.reader.local_snapshot(pid)
        assert {c.id for c in scene.characters if c.wounded} == {ZUO, XIN}  # 战力洗牌：两人都带着伤
        assert [set(a.participants) for a in scene.activities] == [{ZUO, XIN}] and scene.traces  # 交手的往事与血迹

        history = await container.store.load(pid)  # 重建记忆同样守住局部认知：别处冒出的细节不入记忆，只挂在人与地上
        dropped = [e.event for e in history if e not in witnessed(history)]
        assert [type(e) for e in dropped] == [FactEmerged] and "剑鞘" in dropped[0].text  # type: ignore[attr-defined]
        assert await container.coordinator.rebuild(pid) == len(history)
    finally:
        await container.aclose()


# ============================================================
#  真实三件套后端：PostgreSQL 事件账本 + Neo4j 覆盖层（AT / VISITED / HEARD_OF）
# ============================================================
# WORLD 的超集（只添左子穆的执念，不挪任何人）：MERGE 进共享的 Neo4j 也不会让别的用例的正典变形
WATCHED = WORLD.model_copy(update={"personas": (Persona(character_id=ZUO, worry="西宗来夺剑湖宫", sources=("chunk:1",)),)})


@pytest.mark.postgres
@pytest.mark.neo4j
async def test_the_agents_loop_on_real_backends(settings: Settings) -> None:
    """
    同一条 H-Agent 旅程走在 PostgreSQL + Neo4j 上：左子穆的行军经 (:Character)-[:AT {world}]-> 投影，撞见时他在 Neo4j 快照里，判官才请得动；
    导航点选走进迷雾里的去处、来路记作亲历（VISITED）；抹去覆盖层凭事件流重建，左子穆仍在大理城、来路仍是亲历。
    """
    if not (PG_DSN and NEO4J_URI):
        pytest.skip("需要同时设置 TLBB_TEST_POSTGRES_DSN 与 TLBB_TEST_NEO4J_URI")
    real = settings.model_copy(update={
        "event_store": "postgres", "postgres_dsn": PG_DSN, "graph_backend": "neo4j",
        "neo4j_uri": NEO4J_URI, "neo4j_user": NEO4J_USER, "neo4j_password": NEO4J_PASSWORD,
    })
    fresh = await Neo4jWorldGraph.connect(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
    try:
        await fresh.seed(WATCHED, reset=True)
    finally:
        await fresh.close()
    meet = gm(clock_mutations=[{"op": "新建", "clock": "左子穆的戒心", "kind": "疑心", "anchor": "左子穆", "maximum": 4,
                                "steps": 1, "consequence": "认定你是西宗的探子"}])
    llm = ScriptedLLM("苍山如黛。", THINK, AGENDA, "一。", THINK, "二。", THINK, "三。", THINK, "四。",
                      THINK, meet, "五。", "六。", "七。")
    container = await build_container(real, blueprint=WATCHED, llm=llm)
    try:
        pid = await spawned_at(container, "大理城")
        for _ in range(4):
            await say(container, llm, pid)
        _, done, events, spent = await say(container, llm, pid)
        ended = next(e for e in events if isinstance(e, EncounterResolved))
        assert spent == 3 and ended.by == "地下城主"  # 来者在 Neo4j 快照里：判官请得动
        assert [(c.name, c.progress) for c in done.status.clocks] == [("左子穆的戒心", 1)]
        here = await container.reader.local_snapshot(pid)
        assert ZUO in {c.id for c in here.characters}  # 行军经 AT {world} 覆盖正典所在

        north = next(n for n in done.navigation if n.direction.value == "北")
        assert north.target == UNKNOWN_PLACE
        _, done, _ = await click(container, pid, north.id)
        assert done.status.location == "无量山"
        back = next(n for n in done.navigation if n.direction.value == "南")
        assert (back.target, back.discovery) == ("大理城", DiscoveryStatus.VISITED)

        await container.projector.forget(pid)  # 抹掉 Neo4j 覆盖层，凭 PostgreSQL 事件流重建
        rebuilt = (await play(container, ResumePlayer(player_id=pid)))[-1]
        assert isinstance(rebuilt, TurnCompleted) and rebuilt.status.location == "无量山"
        assert rebuilt.navigation == done.navigation
        player = await container.pipeline.load(pid)
        assert player.state.npc_at.get(ZUO) == "loc:大理城"
    finally:
        await container.aclose()


# ============================================================
#  最近菜单：有界、按版本命中
# ============================================================
def test_recent_menus_are_bounded_and_versioned() -> None:
    look = ActionOption.of(OptionCategory.EXPLORE, "静观四周", PlayerIntent(action_type=ActionType.OBSERVE))
    menus = RecentMenus(capacity=2)
    menus.put("ply:a", 3, (look,))
    assert menus.get("ply:a", 3) == (look,) and menus.get("ply:a", 4) is None and menus.get("ply:b", 3) is None
    menus.put("ply:b", 1, ())
    assert menus.get("ply:a", 3) == (look,)  # 碰过一次：ply:a 成了最近的
    menus.put("ply:c", 1, ())
    assert len(menus) == 2 and menus.get("ply:b", 1) is None and menus.get("ply:a", 3) == (look,)  # 最久没碰的先请走
    menus.put("ply:a", 5, ())
    assert menus.get("ply:a", 3) is None and menus.get("ply:a", 5) == ()
