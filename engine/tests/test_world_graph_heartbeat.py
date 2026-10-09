"""
[INPUT]: 依赖 app.infrastructure.persistence 的 InMemoryWorldGraph / Neo4jWorldGraph，依赖 app.infrastructure.cypher 的编译与标签表，
         依赖 app.domain 的 events / clocks / resolution（fact_id）/ aggregates（EMERGED_MAX）/ ambient（活动、痕迹、消息与上限）/ commands / heartbeat / lore（SwarmNode），
         依赖 app.application.world_clock 的 WorldClock，依赖 tests/conftest 的 graph 双实现夹具 / Graph / envelopes / pid 与 NEO4J_*，依赖 tests/world 的 WORLD
[OUTPUT]: 图谱覆盖层的语义物理引擎与世界心跳契约测试（内存与真实 Neo4j 共跑 + 同构证明）：
          语义物理引擎（clock_journey）：时钟挂在在场者 / 此地 / 地上之物 / 玩家自己 / 远方之人身上，推进与回退越界钳位、坍缩与销毁退场、
          同名重挂即覆盖、不存在的时钟推进无事发生，快照只召回挂在眼前之物与玩家身上的（行囊之物随身走）且与聚合根同构；
          微观事实超出 EMERGED_MAX 挤掉最旧的、重提即刷新，主体与此地 / 在场者 / 可见之物有交集才进快照，主体都有 label；
          抹去重放一并重建；双实现在这段旅程每个版本上逐字段相等，Neo4j 每个世界只留 24 个 (:Emerged)、forget 后不留 (:Clock|Emerged)；
          世界心跳（HEART 蓝图 = WORLD + 三群人 + 露天的无主干粮）：交手在此地留下活动与痕迹、东宗弟子受惊溃散而阈值高的山民不散、
          消息传到之处才进快照（大理城已知、无锡城未知 → 传到）、走开再回来往事与痕迹仍在且痕迹按 tick 少几刻、恰在 ends / expires 那一刻
          狼藉散尽而溃散了结（人群回来）、血迹散尽而交手的往事随之了结、消息只增不减、朽坏之物不在任何地方、顺手拿走之物到了此地之人手里，
          平行世界互不干扰、labels 认 swm:；活动 / 痕迹 / 消息超上限请走最旧的、同 id 再起即覆盖、消息再生重置传到之处、被挤掉的消息再传无事发生；
          人群编译为参数化 Cypher（地点之后入图、LOCATED_IN、覆盖节点的 (world, id) 约束与 world 索引）；经 WorldClock 算出的心跳
          （交手 → 去大理城 → 调息二十回跨过两个黎明 → 回无量山）每个版本上与内存参照逐字段相等；抹去重放一并重建；
          双实现在心跳旅程与上限旅程每个版本上逐字段相等，Neo4j 每个世界恰留上限个 (:Activity|Trace|Rumor)、forget 后不留，stale_canon 认人群
[POS]: tests 的覆盖层（时钟、微观事实、活动 / 痕迹 / 消息 / 人群）投影正确性：过去式持久化在图里，两套实现逐字段同构
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from typing import Literal

import pytest

from app.application.world_clock import WorldClock
from app.domain.aggregates import EMERGED_MAX, Player
from app.domain.ambient import (
    ACTIVITIES_MAX,
    TOKENS_MAX,
    TRACES_MAX,
    Activity,
    ActivityKind,
    ActivityState,
    EnvironmentalTrace,
    FactToken,
    activity_id,
    token_id,
    trace_id,
)
from app.domain.clocks import ClockKind, NarrativeClock, clock_id
from app.domain.combat import CombatOutcome
from app.domain.commands import SPAWN_TICK, Command
from app.domain.events import (
    ActivityStarted,
    ClockAdvanced,
    ClockCleared,
    ClockCollapsed,
    ClockStarted,
    DomainEvent,
    FactEmerged,
    FactTokenSpawned,
    HealthChanged,
    ItemDecayed,
    ItemPilfered,
    ItemTransferred,
    Moved,
    PlayerSpawned,
    RumorSpread,
    SkillExecuted,
    TimePassed,
    TraceLeft,
)
from app.domain.heartbeat import BLOOD, LITTER, ROUT_TICKS, Atlas, Mark
from app.domain.intent import ActionType, PlayerIntent
from app.domain.lore import SwarmNode
from app.domain.models import EntityKind, Material, Ownership, WorldBlueprint
from app.domain.resolution import fact_id
from app.domain.snapshot import LocalSnapshot
from app.infrastructure.cypher import CANON_LABELS, KIND_LABELS, OVERLAY_LABELS, compile_blueprint, render_script
from app.infrastructure.persistence.memory_graph import InMemoryWorldGraph
from tests.conftest import Graph, _neo4j, envelopes, pid
from tests.world import WORLD


# ============================================================
#  语义物理引擎：叙事时钟与微观事实挂在实体上，随快照召回；抹去重放一并重建
# ============================================================
def _clock(anchor: str, name: str, kind: ClockKind, maximum: Literal[4, 6, 8], progress: int = 0,
           consequence: str = "") -> NarrativeClock:
    return NarrativeClock(id=clock_id(anchor, name), name=name, kind=kind, anchor_id=anchor, progress=progress,
                          maximum=maximum, consequence=consequence)


EMERGED_TOTAL = EMERGED_MAX + 2  # 多出两条：最旧的两条被挤掉，而重提的那条刷新后留下


def _emerged_subjects(i: int) -> tuple[str, ...]:
    """轮着挂：此地、在场者、远方之人（段誉，在大理城）、此地与远方之人兼有、地上的玉佩。"""
    return (("loc:无量山",), ("chr:左子穆",), ("chr:段誉",), ("chr:段誉", "loc:无量山"), ("itm:玉佩",))[i % 5]


def _emerged_text(i: int) -> str:
    return f"崖边松针落了第{i}回"


def _emergence(i: int) -> FactEmerged:
    text = _emerged_text(i)
    return FactEmerged(fact_id=fact_id(text), text=text, subject_ids=_emerged_subjects(i))


def clock_journey(pid: str) -> list[DomainEvent]:
    """
    挂上五只时钟（在场者、此地、地上之物、玩家自己、远方之人各一）→ 推进（含越界钳位）、回退（含钳到零）→ 坍缩、销毁 → 同名重挂；
    再冒出 EMERGED_TOTAL 条微观事实并重提第一条；最后拾起玉佩去大理城：挂在行囊之物、玩家与段誉身上的时钟随之进出快照。
    """
    suspicion = _clock("chr:左子穆", "左子穆的疑心", ClockKind.SUSPICION, 4, 1, "识破你的手脚")
    storm = _clock("loc:无量山", "山雨欲来", ClockKind.PERIL, 6, 2, "山洪冲下崖来")
    bond = _clock("chr:辛双清", "与辛双清的交情", ClockKind.PROGRESS, 6, 1)
    jade = _clock("itm:玉佩", "玉佩的来历", ClockKind.PROGRESS, 8)
    wound = _clock(pid, "旧伤发作", ClockKind.PERIL, 4, 1, "旧伤迸裂")
    longing = _clock("chr:段誉", "段誉的牵挂", ClockKind.PROGRESS, 6, 2)
    return [
        PlayerSpawned(player_id=pid, name="阿星", location_id="loc:无量山"),
        ClockStarted(clock=suspicion, cause="你在左子穆面前顺走了东西"),
        ClockStarted(clock=storm, cause="天色骤暗"),
        ClockStarted(clock=bond, cause="辛双清多看了你一眼"),
        ClockStarted(clock=jade, cause="玉佩背面有字"),
        ClockStarted(clock=wound, cause="与龚光杰交手"),
        ClockStarted(clock=longing, cause="远方的人"),
        ClockAdvanced(clock_id=suspicion.id, steps=2, name=suspicion.name, progress=3, maximum=4),
        ClockAdvanced(clock_id=suspicion.id, steps=3, name=suspicion.name, progress=3, maximum=4),  # 钳在满格之下
        ClockAdvanced(clock_id=storm.id, steps=-3, name=storm.name, progress=0, maximum=6),  # 回退钳到零
        ClockAdvanced(clock_id=wound.id, steps=1, name=wound.name, progress=2, maximum=4),
        ClockCollapsed(clock_id=suspicion.id, name=suspicion.name, consequence=suspicion.consequence),
        ClockCleared(clock_id=bond.id, name=bond.name, cause="误会冰释"),
        ClockStarted(clock=_clock("chr:左子穆", "左子穆的疑心", ClockKind.SUSPICION, 6, 2, "又起疑心"), cause="旧事重提"),
        ClockAdvanced(clock_id="clk:0000000000", steps=2),  # 不存在的时钟：两边都当无事发生
        *(_emergence(i) for i in range(EMERGED_TOTAL)),
        _emergence(0),  # 重提最旧的一条：刷新，不被挤掉
        ItemTransferred(item_id="itm:玉佩", from_holder="loc:无量山", to_holder=pid),
        Moved(from_location_id="loc:无量山", to_location_id="loc:大理城", exit_label="南下"),
    ]


CLOCKS_DONE = 15  # 时钟操作走完之后的版本（PlayerSpawned + 14 条时钟事件）
EMERGED_DONE = CLOCKS_DONE + EMERGED_TOTAL + 1


def _clocks(snap: LocalSnapshot) -> dict[str, tuple[str, int, int]]:
    return {c.name: (c.anchor_id, c.progress, c.maximum) for c in snap.clocks}


async def test_clocks_hang_on_entities_and_follow_the_scene(graph: Graph) -> None:
    p = pid()
    story = envelopes(p, clock_journey(p))

    async def at(version: int) -> LocalSnapshot:
        await graph.project(p, story[:version])
        return await graph.local_snapshot(p)

    snap = await at(7)
    assert _clocks(snap) == {  # 段誉不在场：他身上的时钟照样悬着，只是不进此地的快照
        "左子穆的疑心": ("chr:左子穆", 1, 4), "山雨欲来": ("loc:无量山", 2, 6), "与辛双清的交情": ("chr:辛双清", 1, 6),
        "玉佩的来历": ("itm:玉佩", 0, 8), "旧伤发作": (p, 1, 4),
    }
    clock = next(c for c in snap.clocks if c.name == "左子穆的疑心")
    assert clock.kind is ClockKind.SUSPICION and clock.consequence == "识破你的手脚"
    assert snap.clocks_on("chr:左子穆") == (clock,)
    assert [c.id for c in snap.clocks] == sorted(c.id for c in snap.clocks)
    assert {"chr:左子穆", "loc:无量山", "itm:玉佩", p} <= set(snap.labels)  # labels 覆盖挂处

    snap = await at(11)
    assert _clocks(snap)["左子穆的疑心"] == ("chr:左子穆", 3, 4)  # 推进越界钳在满格之下
    assert _clocks(snap)["山雨欲来"] == ("loc:无量山", 0, 6)  # 回退越界钳到零
    assert _clocks(snap)["旧伤发作"] == (p, 2, 4)

    snap = await at(CLOCKS_DONE)
    assert _clocks(snap) == {  # 坍缩与销毁的退场；同名重挂是一只新钟（同 id、新阈值与进度）
        "左子穆的疑心": ("chr:左子穆", 2, 6), "山雨欲来": ("loc:无量山", 0, 6), "玉佩的来历": ("itm:玉佩", 0, 8),
        "旧伤发作": (p, 2, 4),
    }
    assert next(c for c in snap.clocks if c.name == "左子穆的疑心").consequence == "又起疑心"

    snap = await at(len(story))  # 拾起玉佩去大理城：行囊之物与自己身上的跟着走，此地与左子穆的留在原处，段誉的现身
    assert _clocks(snap) == {"玉佩的来历": ("itm:玉佩", 0, 8), "旧伤发作": (p, 2, 4), "段誉的牵挂": ("chr:段誉", 2, 6)}
    other = pid()  # 平行世界：同一挂处同名的时钟 id 相同，却互不相干
    await graph.project(other, envelopes(other, clock_journey(other)[:1]))
    assert (await graph.local_snapshot(other)).clocks == ()


async def test_emerged_facts_keep_the_newest_and_follow_their_subjects(graph: Graph) -> None:
    p = pid()
    story = envelopes(p, clock_journey(p))
    await graph.project(p, story[:EMERGED_DONE])
    snap = await graph.local_snapshot(p)
    survivors = {0, *range(3, EMERGED_TOTAL)}  # 1、2 最旧被挤掉；0 重提后刷新留下
    assert len(survivors) == EMERGED_MAX
    here = {"loc:无量山", "chr:左子穆", "itm:玉佩"}  # 此地、在场者、地上之物；只点了段誉的不进
    expected = {i for i in survivors if here & set(_emerged_subjects(i))}
    assert {e.text for e in snap.emerged} == {_emerged_text(i) for i in expected}
    both = next(e for e in snap.emerged if e.text == _emerged_text(3))
    assert both.id == fact_id(_emerged_text(3)) and both.subject_ids == ("chr:段誉", "loc:无量山")
    assert snap.labels["chr:段誉"] == "段誉"  # 事实主体（不在场的也算）都有名字
    await graph.project(p, story)
    snap = await graph.local_snapshot(p)  # 大理城：只有点了段誉或行囊里玉佩之名的
    city = {"chr:段誉", "itm:玉佩"}
    assert {e.text for e in snap.emerged} == {_emerged_text(i) for i in survivors if city & set(_emerged_subjects(i))}


async def test_forget_and_replay_rebuilds_clocks_and_emerged(graph: Graph) -> None:
    p = pid()
    history = envelopes(p, clock_journey(p))
    await graph.project(p, history)
    before = await graph.local_snapshot(p)
    assert before.clocks and before.emerged
    await graph.forget(p)
    await graph.project(p, history[:1])  # 抹去后只重放到落脚：时钟与事实一并抹去
    fresh = await graph.local_snapshot(p)
    assert fresh.clocks == () and fresh.emerged == ()
    await graph.forget(p)
    await graph.project(p, history)
    assert await graph.local_snapshot(p) == before


async def test_clock_snapshot_matches_the_aggregate(graph: Graph) -> None:
    """快照里的时钟就是聚合根的时钟（挂在眼前的那几只）：投影与 evolve 同构。"""
    p = pid()
    story = envelopes(p, clock_journey(p))
    await graph.project(p, story[:EMERGED_DONE])
    snap = await graph.local_snapshot(p)
    truth = Player.replay(e.event for e in story[:EMERGED_DONE])
    assert truth is not None
    assert snap.clocks == tuple(c for c in truth.clocks if c.anchor_id != "chr:段誉")


@pytest.mark.neo4j
async def test_memory_and_neo4j_agree_on_clocks_and_emerged() -> None:
    neo = await _neo4j()
    memory = InMemoryWorldGraph()
    await memory.seed(WORLD)
    try:
        p = pid()
        story = envelopes(p, clock_journey(p))
        for upto in range(1, len(story) + 1):
            await neo.project(p, story[:upto])
            await memory.project(p, story[:upto])
            assert await neo.local_snapshot(p) == await memory.local_snapshot(p), f"第 {upto} 版快照分叉"
        records, _, _ = await neo._driver.execute_query(
            "MATCH (f:Emerged {world: $pid}) RETURN count(f) AS n", pid=p)
        assert records[0]["n"] == EMERGED_MAX  # 每个世界只留最新的二十四条
        await neo.forget(p)
        records, _, _ = await neo._driver.execute_query(
            "MATCH (n:Clock|Emerged {world: $pid}) RETURN count(n) AS n", pid=p)
        assert records[0]["n"] == 0
    finally:
        await neo.close()


# ============================================================
#  世界心跳：活动、痕迹、人群、消息投影进覆盖层；时间一走，痕迹消散、往事了结、人群回来；朽坏与顺手拿走之物各有去处
# ============================================================
HERE, CITY, WUXI = "loc:无量山", "loc:大理城", "loc:无锡城"
T0 = SPAWN_TICK
DISCIPLES = SwarmNode(id="swm:东宗弟子", name="东宗弟子", location_id=HERE, size=30, panic_threshold=3,
                      routine="围观比剑", faction="无量剑东宗", sources=("chunk:3",))
WOODCUTTERS = SwarmNode(id="swm:砍柴山民", name="砍柴山民", location_id=HERE, size=5, panic_threshold=8,
                        routine="挑柴歇脚", sources=("chunk:3",))
MARKET = SwarmNode(id="swm:赶集百姓", name="赶集百姓", location_id=CITY, size=200, panic_threshold=2,
                   routine="赶集", sources=("chunk:1",))
HEART = WorldBlueprint.model_validate({  # WORLD + 三群人（两群在无量山、一群在大理城）+ 一块露天的无主干粮
    **WORLD.model_dump(),
    "items": [*WORLD.model_dump()["items"], {"id": "itm:干粮", "name": "干粮", "kind": "食物", "location_id": HERE}],
    "swarms": [s.model_dump() for s in (DISCIPLES, WOODCUTTERS, MARKET)],
})
NEWS = "阿星与龚光杰动手，挂了彩"


def _trace(where: str, mark: Mark, born: int) -> EnvironmentalTrace:
    return EnvironmentalTrace(id=trace_id(where, mark.description, born), location_id=where,
                              description=mark.description, born_tick=born, decay_ticks=mark.decay_ticks)


def _activity(kind: ActivityKind, where: str, who: tuple[str, ...], started: int, ends: int,
              trace: str | None = None) -> Activity:
    return Activity(id=activity_id(kind, where, who, started), kind=kind, participants=who, location_id=where,
                    started_tick=started, ends_tick=ends, trace_id=trace)


def _token(text: str, subjects: tuple[str, ...], born: int, *, speed: Literal[1, 2] = 1) -> FactToken:
    return FactToken(id=token_id(text, HERE, born), text=text, subject_ids=subjects, origin_id=HERE, born_tick=born,
                     speed=speed, radius=3, reached=(HERE,))


def heart_journey(pid: str) -> list[DomainEvent]:
    """
    在无量山与龚光杰交手（血迹 + 交手活动）、东宗弟子受惊溃散（狼藉 + 溃散活动）、消息从无量山传起 →
    走到大理城（消息已到）、再去无锡城（消息未到 → 传到）→ 回到无量山（往事与痕迹仍在）→
    狼藉散尽、溃散了结、东宗弟子回来（边界：恰在 ends / expires 那一刻）→ 干粮朽坏、玉佩被龚光杰顺手拿走 → 血迹散尽、交手的往事随之了结。
    """
    blood, litter = _trace(HERE, BLOOD, T0), _trace(HERE, LITTER, T0)
    news = _token(NEWS, (pid, "chr:龚光杰"), T0, speed=2)
    return [
        PlayerSpawned(player_id=pid, name="阿星", location_id=HERE),  # 1 · tick 32
        SkillExecuted(skill_id=None, target_id="chr:龚光杰", outcome=CombatOutcome.MINOR_WOUND),
        TraceLeft(trace=blood),
        ActivityStarted(activity=_activity(ActivityKind.FIGHT, HERE, (pid, "chr:龚光杰"), T0, T0 + 1, blood.id)),
        TraceLeft(trace=litter),
        ActivityStarted(activity=_activity(ActivityKind.ROUT, HERE, (DISCIPLES.id,), T0, T0 + ROUT_TICKS, litter.id)),
        FactTokenSpawned(token=news),  # 7 · 交手当刻
        TimePassed(ticks=1),  # 8 · tick 33：交手已结束，血迹与狼藉犹在
        RumorSpread(token_id=news.id, location_ids=(CITY, "loc:无量玉洞")),
        Moved(from_location_id=HERE, to_location_id=CITY, exit_label="南下", motivation="去大理城找段正淳"),
        TimePassed(ticks=4),  # 11 · tick 37：大理城已听说
        Moved(from_location_id=CITY, to_location_id=WUXI, exit_label="东去"),
        TimePassed(ticks=4),  # 13 · tick 41：无锡城还没听说
        RumorSpread(token_id=news.id, location_ids=(WUXI,)),  # 14 · 传到了
        Moved(from_location_id=WUXI, to_location_id=CITY, exit_label="西归"),
        Moved(from_location_id=CITY, to_location_id=HERE, exit_label="北上"),
        TimePassed(ticks=8),  # 17 · tick 49：回到旧地
        TimePassed(ticks=31),  # 18 · tick 80：狼藉恰好散尽、溃散恰好了结
        ItemDecayed(item_id="itm:干粮", material=Material.FOOD),
        ItemPilfered(item_id="itm:玉佩", from_holder=HERE, to_holder="chr:龚光杰"),  # 20
        TimePassed(ticks=48),  # 21 · tick 128：血迹恰好散尽
    ]


def _activities(snap: LocalSnapshot) -> dict[str, ActivityState]:
    return {a.kind.value: a.state for a in snap.activities}


def _traces(snap: LocalSnapshot) -> dict[str, int]:
    return {t.description: t.remaining for t in snap.traces}


def _swarms(snap: LocalSnapshot) -> dict[str, str]:
    return {s.name: s.current_state for s in snap.swarms}


def _rumors(snap: LocalSnapshot) -> set[str]:
    return {r.text for r in snap.rumors}


ONGOING, ENDED = ActivityState.ONGOING, ActivityState.ENDED
ROUT = ActivityKind.ROUT.value


async def test_heartbeat_leaves_the_past_where_it_happened(graph: Graph) -> None:
    await graph.seed(HEART, reset=True)
    p = pid()
    story = envelopes(p, heart_journey(p))

    async def at(version: int) -> LocalSnapshot:
        await graph.project(p, story[:version])
        return await graph.local_snapshot(p)

    snap = await at(1)
    assert snap.tick == T0 and snap.activities == () and snap.traces == () and snap.rumors == ()
    assert _swarms(snap) == {"东宗弟子": "围观比剑", "砍柴山民": "挑柴歇脚"}  # 只有此地的人群
    snap = await at(7)  # 交手当刻
    assert _activities(snap) == {"交手": ONGOING, ROUT: ONGOING}
    fight = next(a for a in snap.activities if a.kind is ActivityKind.FIGHT)
    assert fight.participants == (p, "chr:龚光杰") and fight.started_tick == T0
    assert _traces(snap) == {BLOOD.description: 96, LITTER.description: 48}
    assert _swarms(snap) == {"东宗弟子": ROUT, "砍柴山民": "挑柴歇脚"}  # 惊惧阈值低的散了，高的没散
    (rumor,) = snap.rumors
    assert rumor.text == NEWS and rumor.origin_id == HERE and rumor.born_tick == T0
    assert rumor.subject_ids == tuple(sorted((p, "chr:龚光杰")))
    assert snap.labels["swm:东宗弟子"] == "东宗弟子" and snap.labels[p] == "阿星"  # 人群与参与者都有名字
    assert await graph.labels(["swm:赶集百姓", "swm:无此群"]) == {"swm:赶集百姓": "赶集百姓"}

    snap = await at(9)  # 一刻之后：交手已结束，痕迹各少一刻
    assert snap.tick == T0 + 1 and _activities(snap) == {"交手": ENDED, ROUT: ONGOING}
    assert _traces(snap) == {BLOOD.description: 95, LITTER.description: 47}
    snap = await at(11)  # 大理城：此地无事，人群照常；消息已传到
    assert snap.location.id == CITY and snap.activities == () and snap.traces == ()
    assert _swarms(snap) == {"赶集百姓": "赶集"} and _rumors(snap) == {NEWS}
    assert _rumors(await at(13)) == set()  # 无锡城：消息还没传到，在场之人无从知道
    assert _rumors(await at(14)) == {NEWS}

    snap = await at(17)  # 回到无量山：往事与痕迹都还在原处
    assert snap.tick == T0 + 17 and _activities(snap) == {"交手": ENDED, ROUT: ONGOING}
    assert _traces(snap) == {BLOOD.description: 79, LITTER.description: 31}
    assert _swarms(snap)["东宗弟子"] == ROUT
    snap = await at(18)  # 半日之后：狼藉散尽，溃散随之了结，东宗弟子回来了
    assert snap.tick == T0 + ROUT_TICKS and _activities(snap) == {"交手": ENDED}
    assert _traces(snap) == {BLOOD.description: 48}
    assert _swarms(snap) == {"东宗弟子": "围观比剑", "砍柴山民": "挑柴歇脚"}
    snap = await at(20)  # 干粮朽坏、玉佩被龚光杰顺手拿走
    assert snap.item("itm:干粮") is None
    jade = snap.item("itm:玉佩")
    assert jade is not None and jade.holder_id == "chr:龚光杰" and jade.ownership is Ownership.HELD
    snap = await at(21)  # 血迹散尽，交手的往事随之了结；消息只增不减
    assert snap.tick == T0 + 96 and snap.activities == () and snap.traces == () and _rumors(snap) == {NEWS}
    truth = Player.replay(e.event for e in story)
    assert truth is not None and truth.tick == snap.tick and truth.motivation == ""  # 此行所为只进聚合，回来时已无所为

    other = pid()  # 平行世界：同一处地方，没有这些往事
    await graph.project(other, envelopes(other, [PlayerSpawned(player_id=other, name="阿星", location_id=HERE)]))
    pristine = await graph.local_snapshot(other)
    assert pristine.activities == () and pristine.rumors == () and _swarms(pristine)["东宗弟子"] == "围观比剑"
    assert pristine.item("itm:干粮") is not None and pristine.item("itm:玉佩").holder_id == HERE  # type: ignore[union-attr]


async def test_forget_and_replay_rebuilds_the_heartbeat(graph: Graph) -> None:
    await graph.seed(HEART, reset=True)
    p = pid()
    history = envelopes(p, heart_journey(p))[:20]
    await graph.project(p, history)
    before = await graph.local_snapshot(p)
    assert before.activities and before.traces and before.rumors
    await graph.forget(p)
    await graph.project(p, history[:1])  # 抹去后只重放到落脚：活动、痕迹、消息、朽坏与易手一并抹去
    fresh = await graph.local_snapshot(p)
    assert (fresh.tick, fresh.activities, fresh.traces, fresh.rumors) == (T0, (), (), ())
    assert fresh.item("itm:干粮") is not None and fresh.item("itm:玉佩").holder_id == HERE  # type: ignore[union-attr]
    await graph.forget(p)
    await graph.project(p, history)
    assert await graph.local_snapshot(p) == before


EXTRA = 2  # 每样多出两个：最旧的两个被请走


def crowded_journey(pid: str) -> tuple[list[DomainEvent], list[Activity], list[EnvironmentalTrace], list[FactToken]]:
    """各样超出上限两个；同 id 的活动再起即覆盖；消息再生即重置传到之处；被挤掉的消息再传也无事发生。"""
    acts = [_activity(ActivityKind.FIGHT, HERE, (pid, "chr:龚光杰"), T0 + i, T0 + i + 1)
            for i in range(ACTIVITIES_MAX + EXTRA)]
    traces = [_trace(HERE, Mark(f"石上第{i}道剑痕", 96), T0 + i) for i in range(TRACES_MAX + EXTRA)]
    tokens = [_token(f"第{i}桩传闻", (pid,), T0 + i) for i in range(TOKENS_MAX + EXTRA)]
    events: list[DomainEvent] = [
        PlayerSpawned(player_id=pid, name="阿星", location_id=HERE),
        *(ActivityStarted(activity=a) for a in acts),
        *(TraceLeft(trace=t) for t in traces),
        *(FactTokenSpawned(token=t) for t in tokens),
        ActivityStarted(activity=acts[-1].model_copy(update={"participants": (pid, "chr:左子穆")})),  # 同 id 再起：覆盖，不添一个
        RumorSpread(token_id=tokens[-1].id, location_ids=(CITY,)),
        RumorSpread(token_id=tokens[-2].id, location_ids=(CITY, CITY)),
        RumorSpread(token_id=tokens[0].id, location_ids=(CITY,)),  # 已被挤掉：无事发生
        FactTokenSpawned(token=tokens[-1]),  # 再生：传到之处重置为发源地
        Moved(from_location_id=HERE, to_location_id=CITY, exit_label="南下"),
    ]
    return events, acts, traces, tokens


async def test_heartbeat_overlays_keep_only_the_newest(graph: Graph) -> None:
    await graph.seed(HEART, reset=True)
    p = pid()
    events, acts, traces, tokens = crowded_journey(p)
    story = envelopes(p, events)
    await graph.project(p, story[:-1])
    snap = await graph.local_snapshot(p)
    assert {a.id for a in snap.activities} == {a.id for a in acts[EXTRA:]}
    assert {t.id for t in snap.traces} == {t.id for t in traces[EXTRA:]}
    assert {r.id for r in snap.rumors} == {t.id for t in tokens[EXTRA:]}
    newest = next(a for a in snap.activities if a.id == acts[-1].id)
    assert newest.participants == (p, "chr:左子穆")  # 同 id 再起即覆盖
    await graph.project(p, story)
    assert {r.id for r in (await graph.local_snapshot(p)).rumors} == {tokens[-2].id}  # 只有没被重置的那一枚到了大理城


def test_swarms_compile_to_parameterized_cypher() -> None:
    """人群是正典节点：在地点之后入图、LOCATED_IN 落到地点上，名字只走参数；覆盖节点各有 (world, id) 约束与 world 索引。"""
    statements = compile_blueprint(HEART)
    queries = [s.query for s in statements]
    place = next(i for i, q in enumerate(queries) if "MERGE (n:Location" in q)
    node = next(i for i, q in enumerate(queries) if "MERGE (n:Swarm" in q)
    edge = next(i for i, q in enumerate(queries) if "(a:Swarm" in q)
    assert place < node < edge and "[r:LOCATED_IN]" in queries[edge] and "(b:Location" in queries[edge]
    swarms = {r["id"]: r["props"] for r in statements[node].params["rows"]}
    assert swarms["swm:东宗弟子"] == {"name": "东宗弟子", "size": 30, "panic_threshold": 3, "routine": "围观比剑",
                                      "faction": "无量剑东宗", "sources": ["chunk:3"]}
    assert {(r["a"], r["b"]) for r in statements[edge].params["rows"]} == {
        ("swm:东宗弟子", HERE), ("swm:砍柴山民", HERE), ("swm:赶集百姓", CITY),
    }
    assert not any(name in q for q in queries for name in ("东宗弟子", "围观比剑"))
    assert any("FOR (n:Swarm) REQUIRE n.id IS UNIQUE" in q for q in queries)
    for label in ("Activity", "Trace", "Rumor"):
        assert f"FOR (n:{label}) REQUIRE (n.world, n.id) IS UNIQUE" in " ".join(queries)
        assert f"FOR (n:{label}) ON (n.world)" in " ".join(queries)
        assert label in OVERLAY_LABELS
    assert "Swarm" in CANON_LABELS and KIND_LABELS[EntityKind.SWARM] == "Swarm"
    assert "东宗弟子" in render_script(statements)


def _clockwork() -> list[tuple[PlayerIntent, int, list[DomainEvent]]]:
    """经世界时钟走的一程：交手（留痕、东宗弟子溃散、消息每刻两处）→ 去大理城 → 调息二十回跨过两个黎明（风化与顺手牵羊）→ 回无量山。"""
    blow: list[DomainEvent] = [SkillExecuted(skill_id=None, target_id="chr:龚光杰", outcome=CombatOutcome.MINOR_WOUND),
                               HealthChanged(delta=-18, cause="与龚光杰交手", source_id="chr:龚光杰")]
    rest: list[DomainEvent] = [HealthChanged(delta=+5, cause="调息", source="rest")]
    return [
        (PlayerIntent(action_type=ActionType.ATTACK, target_entity="龚光杰"), 1, blow),
        (PlayerIntent(action_type=ActionType.MOVE, target_entity="南下"), 4,
         [Moved(from_location_id=HERE, to_location_id=CITY, exit_label="南下", motivation="去大理城求医")]),
        *((PlayerIntent(action_type=ActionType.REST), 8, rest) for _ in range(20)),
        (PlayerIntent(action_type=ActionType.MOVE, target_entity="北上"), 4,
         [Moved(from_location_id=CITY, to_location_id=HERE, exit_label="北上")]),
    ]


async def test_world_clock_beats_project_like_the_aggregate(graph: Graph) -> None:
    """心跳事件由 WorldClock 按领域纯函数算出（出招时的快照取自内存参照）：每个版本上图谱快照与参照逐字段相等。"""
    await graph.seed(HEART, reset=True)
    reference = InMemoryWorldGraph()
    await reference.seed(HEART)
    clock = WorldClock(Atlas.of(HEART))
    p = pid()
    events: list[DomainEvent] = [PlayerSpawned(player_id=p, name="阿星", location_id=HERE)]
    await graph.project(p, envelopes(p, events))
    await reference.project(p, envelopes(p, events))
    for intent, cost, decided in _clockwork():
        state = Player.replay(events)
        assert state is not None
        beats = clock.advance(Command(intent=intent, time_cost=cost), await reference.local_snapshot(p), state, decided)
        start = len(events) + 1
        events += [*decided, *beats]
        story = envelopes(p, events)
        for upto in range(start, len(story) + 1):
            await graph.project(p, story[:upto])
            await reference.project(p, story[:upto])
            assert await graph.local_snapshot(p) == await reference.local_snapshot(p), f"第 {upto} 版快照分叉"
    kinds = {type(e).__name__ for e in events}
    assert {"TraceLeft", "ActivityStarted", "FactTokenSpawned", "RumorSpread", "TimePassed"} <= kinds
    assert kinds & {"ItemDecayed", "ItemPilfered"}  # 黎明的生态确实走到了
    routs = [e.activity for e in events if isinstance(e, ActivityStarted) and e.activity.kind is ActivityKind.ROUT]
    assert [a.participants for a in routs] == [(DISCIPLES.id,)]  # 只有惊惧阈值低于烈度的那一群散了
    truth = Player.replay(events)
    snap = await graph.local_snapshot(p)
    assert truth is not None and snap.tick == truth.tick >= 2 * 96  # 跨过两个黎明
    assert snap.activities == () and snap.traces == () and _rumors(snap) == {NEWS}
    assert _swarms(snap)["东宗弟子"] == "围观比剑"
    food = snap.item("itm:干粮")  # 两个黎明之后：不是朽了，就是被人顺手拿走了
    assert food is None or food.holder_id.startswith("chr:")


@pytest.mark.neo4j
async def test_memory_and_neo4j_agree_on_the_heartbeat() -> None:
    neo = await _neo4j()
    memory = InMemoryWorldGraph()
    try:
        await neo.seed(HEART, reset=True)
        await memory.seed(HEART)
        p = pid()
        crowded, *_ = crowded_journey(p)
        for story in (envelopes(p, heart_journey(p)), envelopes(p, crowded)):
            await neo.forget(p)
            await memory.forget(p)
            for upto in range(1, len(story) + 1):
                await neo.project(p, story[:upto])
                await memory.project(p, story[:upto])
                assert await neo.local_snapshot(p) == await memory.local_snapshot(p), f"第 {upto} 版快照分叉"

        async def count(label: str) -> int:
            records, _, _ = await neo._driver.execute_query(f"MATCH (n:{label} {{world: $pid}}) RETURN count(n) AS n", pid=p)
            return int(records[0]["n"])

        assert (await count("Activity"), await count("Trace"), await count("Rumor")) == (
            ACTIVITIES_MAX, TRACES_MAX, TOKENS_MAX,
        )
        await neo.forget(p)
        assert (await count("Activity"), await count("Trace"), await count("Rumor")) == (0, 0, 0)
        assert await neo.labels(["swm:东宗弟子", p]) == {"swm:东宗弟子": "东宗弟子"}  # 玩家节点随 forget 抹去
    finally:
        await neo.close()


@pytest.mark.neo4j
async def test_stale_canon_reports_swarms() -> None:
    neo = await _neo4j()
    try:
        await neo.seed(HEART, reset=True)
        assert await neo.stale_canon(HEART) == []
        await neo.seed(WORLD)  # 换回没有人群的蓝图却不 reset：人群也是旧纪元的残留
        assert set(await neo.stale_canon(WORLD)) == {"itm:干粮", "swm:东宗弟子", "swm:砍柴山民", "swm:赶集百姓"}
        await neo.seed(WORLD, reset=True)
        assert await neo.stale_canon(WORLD) == []
    finally:
        await neo.close()


