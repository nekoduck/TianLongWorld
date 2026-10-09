"""
[INPUT]: 依赖 app.infrastructure.persistence 的 InMemoryWorldGraph / Neo4jWorldGraph，依赖 app.infrastructure.cypher 的 compile_blueprint / render_script / CANON_LABELS / OVERLAY_LABELS / KIND_LABELS，
         依赖 app.domain 的 events / models / lore（Fact / FactUnlock / SwarmNode）/ intent / outcomes / snapshot / clocks / resolution（fact_id）/ aggregates（EMERGED_MAX）/
         ambient（活动、痕迹、消息与上限）/ commands（SPAWN_TICK / Command）/ heartbeat（Atlas / BLOOD / LITTER / ROUT_TICKS / Mark），
         依赖 app.application.world_clock 的 WorldClock，依赖 tests/world 的 WORLD，依赖 tests/conftest 的 NEO4J_* 环境变量
[OUTPUT]: 图谱契约测试：同一组用例在内存实现与真实 Neo4j（设置 TLBB_TEST_NEO4J_URI 时）上共跑，另有"双实现快照逐字段相等"的同构证明
[POS]: tests 的投影正确性：正典只读、覆盖层随事件演化（熟练度在 KNOWS_SKILL 上做加法、气血钳位）、投影幂等可重试、
       抹去覆盖层后从事件流重放得到同一个世界；下落不明的物品不进任何快照，人物的称号随快照下发；
       P1 视图（自带的 LORE 蓝图 = WORLD + 后来才到场者 + 三种物性 + 将至的师徒 + 人设 + 五条见闻）：羁绊的 era / lead、外显人设、
       知情人在场的见闻及其正文作 label、物品的 portable / hazard / use、arrives_with 的人与物不进任何场景、出口的 hostile_ahead
       （已故、未到、被制住者不算）、USE 一回合（ItemConsumed）后物品不回地上也不回正典持有者、抹去重放连同用掉的记录一起重建、
       掌故编译成参数化 Cypher 且 foreshadow 一字不入图，以及双实现在 P1 旅程每个版本与每处正典切片上逐字段相等；
       已知的见闻（INFORMED 蓝图 = LORE + 三位线人的三件事）：线人不在而主体或 unlock 目标在场（地上、行囊、在场者）时照样进快照
       且 known=True、没打听过的平行世界看不到、抹去重放一并清掉 LEARNED；越过闸门的重复主体 / 知情人去重保序，两实现同口径；
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
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

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
    LEGACY_MASTERY_POINTS,
    ActivityStarted,
    ClockAdvanced,
    ClockCleared,
    ClockCollapsed,
    ClockStarted,
    DomainEvent,
    EventEnvelope,
    FactEmerged,
    FactLearned,
    FactTokenSpawned,
    HealthChanged,
    ItemConsumed,
    ItemDecayed,
    ItemPilfered,
    ItemTransferred,
    Moved,
    Parleyed,
    PlayerDied,
    PlayerSpawned,
    RelationChanged,
    RumorSpread,
    SkillExecuted,
    SkillPracticed,
    TimePassed,
    TraceLeft,
)
from app.domain.heartbeat import BLOOD, LITTER, ROUT_TICKS, Atlas, Mark
from app.domain.intent import ActionType, Aim, Approach, PlayerIntent
from app.domain.lore import Fact, FactUnlock, SwarmNode
from app.domain.models import (
    Acquisition,
    Attitude,
    EntityKind,
    Era,
    Item,
    ItemUse,
    MartialArt,
    Material,
    Ownership,
    RelationKind,
    Tier,
    Transmission,
    WorldBlueprint,
)
from app.domain.outcomes import SocialOutcome
from app.domain.progression import MAX_HP, Mastery
from app.domain.resolution import fact_id
from app.domain.snapshot import BondView, LocalSnapshot, PersonaView
from app.errors import ProjectionError
from app.infrastructure.cypher import CANON_LABELS, KIND_LABELS, OVERLAY_LABELS, compile_blueprint, render_script
from app.infrastructure.persistence.memory_graph import InMemoryWorldGraph
from app.infrastructure.persistence.neo4j_graph import Neo4jWorldGraph
from tests.conftest import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER
from tests.world import WORLD

type Graph = InMemoryWorldGraph | Neo4jWorldGraph


async def _neo4j() -> Neo4jWorldGraph:
    if not NEO4J_URI:
        pytest.skip("未设置 TLBB_TEST_NEO4J_URI")
    graph = await Neo4jWorldGraph.connect(NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD)
    await graph.seed(WORLD, reset=True)
    return graph


@pytest.fixture(params=["memory", pytest.param("neo4j", marks=pytest.mark.neo4j)])
async def graph(request: pytest.FixtureRequest) -> AsyncIterator[Graph]:
    if request.param == "memory":
        g = InMemoryWorldGraph()
        await g.seed(WORLD)
        yield g
        return
    neo = await _neo4j()
    yield neo
    await neo.close()


def envelopes(pid: str, events: Sequence[DomainEvent], start: int = 1) -> list[EventEnvelope]:
    return [
        EventEnvelope(stream_id=pid, version=i, event_id=uuid4(), recorded_at=datetime.now(UTC), event=e)  # type: ignore[arg-type]
        for i, e in enumerate(events, start=start)
    ]


def journey(pid: str) -> list[DomainEvent]:
    """一段走遍覆盖层的旅程：位置、物品易手（地上 / 人手 / 行囊）、熟练度累加、气血涨落（含越界钳位）、制住、人情、生死。"""
    return [
        PlayerSpawned(player_id=pid, name="阿星", location_id="loc:无量山", aptitude=1.1),
        ItemTransferred(item_id="itm:玉佩", from_holder="loc:无量山", to_holder=pid),
        Moved(from_location_id="loc:无量山", to_location_id="loc:无量玉洞", exit_label="崖下"),
        ItemTransferred(item_id="itm:北冥神功卷轴", from_holder="loc:无量玉洞", to_holder=pid),
        SkillPracticed(skill_id="art:北冥神功", proficiency_gained=10, source_id="itm:北冥神功卷轴"),
        SkillPracticed(skill_id="art:北冥神功", proficiency_gained=10, source_id="itm:北冥神功卷轴"),
        Moved(from_location_id="loc:无量玉洞", to_location_id="loc:无量山", exit_label="攀上"),
        HealthChanged(delta=-58, cause="与龚光杰交手", source_id="chr:龚光杰"),
        HealthChanged(delta=+80, cause="调息疗伤"),
        HealthChanged(delta=-7, cause="与左子穆交手", source_id="chr:左子穆"),
        SkillExecuted(skill_id="art:北冥神功", target_id="chr:左子穆", outcome=CombatOutcome.SUCCESS),
        RelationChanged(character_id="chr:左子穆", attitude=Attitude.HOSTILE, cause="遭你出手相攻"),
        RelationChanged(character_id="chr:辛双清", attitude=Attitude.FRIENDLY, cause="敌人之敌"),
        ItemTransferred(item_id="itm:无量剑", from_holder="chr:左子穆", to_holder=pid),
        ItemTransferred(item_id="itm:北冥神功卷轴", from_holder=pid, to_holder="loc:无量山"),
    ]


def pid() -> str:
    return f"ply:{uuid4().hex[:12]}"


# ============================================================
#  契约
# ============================================================
async def test_canon_queries(graph: Graph) -> None:
    assert await graph.is_seeded()
    assert await graph.spawn_points() == ("loc:大理城", "loc:无量山", "loc:无量玉洞", "loc:无锡城")
    assert await graph.labels(["loc:无量山", "chr:乔峰", "art:一阳指", "itm:玉佩", "ghost:无"]) == {
        "loc:无量山": "无量山", "chr:乔峰": "乔峰", "art:一阳指": "一阳指", "itm:玉佩": "玉佩",
    }


async def test_snapshot_after_spawn_shows_only_the_local_slice(graph: Graph) -> None:
    p = pid()
    assert await graph.project(p, envelopes(p, journey(p)[:1])) == 1
    snap = await graph.local_snapshot(p)
    assert snap.version == 1 and snap.location.name == "无量山" and snap.alive
    assert [(e.label, e.to_name) for e in snap.exits] == [("南下", "大理城"), ("崖下", "无量玉洞")]
    assert [c.name for c in snap.characters] == ["南海鳄神", "左子穆", "辛双清", "龚光杰"]
    assert {i.id: i.holder_id for i in snap.items} == {"itm:无量剑": "chr:左子穆", "itm:玉佩": "loc:无量山"}
    assert snap.item("itm:玉佩").owner_id == "chr:段正淳"  # type: ignore[union-attr]
    assert {s.id for s in snap.skills} == {"art:无量剑法"}  # 在场者所会
    assert snap.character("chr:左子穆").bond_with("chr:辛双清") is not None  # type: ignore[union-attr]
    assert snap.labels["chr:段正淳"] == "段正淳" and snap.labels[p] == "阿星"  # 物主与玩家自己也有名字
    assert snap.player_practice == {} and snap.player_hp == MAX_HP and snap.player_aptitude == 1.1


async def test_titles_travel_with_the_snapshot(graph: Graph) -> None:
    p = pid()
    await graph.project(p, envelopes(p, [PlayerSpawned(player_id=p, name="阿星", location_id="loc:大理城")]))
    yanqing = (await graph.local_snapshot(p)).character("chr:段延庆")
    assert yanqing is not None and yanqing.name == "段延庆" and yanqing.titles == ("恶贯满盈",)
    assert yanqing.names == ("段延庆", "恶贯满盈", "延庆太子")


async def test_lost_items_exist_in_canon_but_in_no_scene(graph: Graph) -> None:
    scroll = Item(id="itm:谱诀", name="谱诀", kind="秘籍")  # 下落不明的孤儿
    art = MartialArt(id="art:谱中功", name="谱中功",
                     acquisition=Acquisition(items=("itm:谱诀",), transmission=Transmission.SELF))
    orphaned = WORLD.model_copy(update={"items": (*WORLD.items, scroll), "martial_arts": (*WORLD.martial_arts, art)})
    await graph.seed(orphaned, reset=True)
    for where in ("loc:无量山", "loc:大理城", "loc:无量玉洞", "loc:无锡城"):
        p = pid()
        await graph.project(p, envelopes(p, [PlayerSpawned(player_id=p, name="阿星", location_id=where)]))
        snap = await graph.local_snapshot(p)
        assert snap.item("itm:谱诀") is None and snap.skill("art:谱中功") is None
    assert await graph.labels(["itm:谱诀"]) == {"itm:谱诀": "谱诀"}


async def test_overlay_follows_the_journey(graph: Graph) -> None:
    p = pid()
    await graph.project(p, envelopes(p, journey(p)))
    snap = await graph.local_snapshot(p)
    assert snap.version == 15 and snap.location.id == "loc:无量山"
    assert snap.player_skills == ("art:北冥神功",) and snap.player_practice == {"art:北冥神功": 20}
    assert snap.mastery("art:北冥神功") is Mastery.MINOR and snap.player_aptitude == 1.1
    assert snap.player_hp == MAX_HP - 7  # 先伤后养，养不过上限
    assert {i.id for i in snap.inventory} == {"itm:玉佩", "itm:无量剑"}
    assert snap.item("itm:北冥神功卷轴").holder_id == "loc:无量山"  # type: ignore[union-attr]  # 被带上崖、放在了地上
    zuo, xin = snap.character("chr:左子穆"), snap.character("chr:辛双清")
    assert zuo is not None and zuo.subdued and zuo.attitude is Attitude.HOSTILE
    assert xin is not None and not xin.subdued and xin.attitude is Attitude.FRIENDLY
    assert "art:凌波微步" not in {s.id for s in snap.skills}  # 卷轴在地上不在行囊：只有在场者与玩家所会
    other = pid()
    await graph.project(other, envelopes(other, journey(other)[:1]))
    pristine = await graph.local_snapshot(other)
    assert pristine.item("itm:玉佩").holder_id == "loc:无量山"  # type: ignore[union-attr]  # 平行世界互不干扰
    assert not pristine.character("chr:左子穆").subdued  # type: ignore[union-attr]


async def test_projection_is_idempotent_and_refuses_gaps(graph: Graph) -> None:
    p = pid()
    history = envelopes(p, journey(p))
    assert await graph.project(p, history[:4]) == 4
    assert await graph.project(p, history) == 15  # 前四条被跳过
    assert await graph.project(p, history) == 15
    assert await graph.checkpoint(p) == 15
    q = pid()
    await graph.project(q, envelopes(q, journey(q)[:1]))
    with pytest.raises(ProjectionError, match="缺口"):
        await graph.project(q, envelopes(q, journey(q)[2:4], start=3))


async def test_forget_and_replay_rebuilds_the_same_world(graph: Graph) -> None:
    p = pid()
    history = envelopes(p, journey(p))
    await graph.project(p, history)
    before = await graph.local_snapshot(p)
    await graph.forget(p)
    assert await graph.checkpoint(p) == 0
    with pytest.raises(ProjectionError):
        await graph.local_snapshot(p)
    await graph.project(p, history)
    assert await graph.local_snapshot(p) == before


async def test_death_is_projected(graph: Graph) -> None:
    p = pid()
    await graph.project(p, envelopes(p, [*journey(p)[:1], PlayerDied(cause="冒犯", killer_id="chr:南海鳄神")]))
    assert not (await graph.local_snapshot(p)).alive


@pytest.mark.neo4j
async def test_reseeding_without_reset_reports_stale_canon() -> None:
    neo = await _neo4j()
    try:
        smaller = WORLD.model_copy(update={"items": WORLD.items[:-1]})  # 新蓝图少了打狗棒
        await neo.seed(smaller)
        assert await neo.stale_canon(smaller) == ["itm:打狗棒"]  # MERGE 只增不删：旧纪元的节点还在
        await neo.seed(smaller, reset=True)
        assert await neo.stale_canon(smaller) == []
        await neo.seed(LORE)
        await neo.seed(smaller)  # 见闻也是正典节点：换回没有见闻的蓝图同样报出残留
        assert {"fact:东西宗相争", "chr:容子矩"} <= set(await neo.stale_canon(smaller))
        await neo.seed(smaller, reset=True)
        assert await neo.stale_canon(smaller) == []
    finally:
        await neo.close()


# ============================================================
#  同构证明：同一段事件流，两个实现给出逐字段相等的快照
# ============================================================
@pytest.mark.neo4j
async def test_memory_and_neo4j_snapshots_are_identical() -> None:
    neo = await _neo4j()
    memory = InMemoryWorldGraph()
    await memory.seed(WORLD)
    try:
        p = pid()
        story = envelopes(p, journey(p))
        for upto in range(1, len(story) + 1):
            await neo.project(p, story[:upto])
            await memory.project(p, story[:upto])
            assert await neo.local_snapshot(p) == await memory.local_snapshot(p), f"第 {upto} 版快照分叉"
    finally:
        await neo.close()


# ============================================================
#  加法投影的两道防线：并发投影不重复相加，旧引擎的边按上抛口径读
# ============================================================
@pytest.mark.neo4j
async def test_concurrent_projections_never_add_twice() -> None:
    """publish 与另一连接的 heal 同时追平同一批事件：检查点在写锁下读取，熟练度与气血只加一遍。"""
    neo = await _neo4j()
    try:
        for _ in range(5):
            p = pid()
            story = envelopes(p, journey(p))
            await neo.project(p, story[:5])
            await asyncio.gather(neo.project(p, story), neo.project(p, story))
            snap = await neo.local_snapshot(p)
            truth = Player.replay(e.event for e in story)
            assert truth is not None and snap.player_practice == dict(truth.practice) and snap.player_hp == truth.hp
    finally:
        await neo.close()


@pytest.mark.neo4j
async def test_overlays_left_by_the_old_engine_read_like_the_upcast() -> None:
    """旧引擎把"学会"投成一条没有熟练度的 KNOWS_SKILL：不重放也要与上抛后的聚合根同口径（融会贯通的熟练度）。"""
    neo = await _neo4j()
    try:
        p = pid()
        await neo.project(p, envelopes(p, journey(p)[:1]))
        await neo._driver.execute_query(  # 旧版 _learned 的原样写法
            "MATCH (pl:Player {id: $pid}) MATCH (a:MartialArt {id: 'art:北冥神功'}) MERGE (pl)-[:KNOWS_SKILL]->(a) "
            "SET pl.version = 2", pid=p,
        )
        assert (await neo.local_snapshot(p)).player_practice == {"art:北冥神功": LEGACY_MASTERY_POINTS}
        more = SkillPracticed(skill_id="art:北冥神功", proficiency_gained=10)
        await neo.project(p, envelopes(p, [more], start=3))
        assert (await neo.local_snapshot(p)).player_practice == {"art:北冥神功": LEGACY_MASTERY_POINTS + 10}
    finally:
        await neo.close()


# ============================================================
#  P1 视图：关系的 era / lead、外显人设、见闻、出口的仇人、物性、后来才到场者、用掉之物
# ============================================================
LORE = WorldBlueprint.model_validate({
    **WORLD.model_dump(),
    "characters": [
        *WORLD.model_dump()["characters"],
        {"id": "chr:容子矩", "true_name": "容子矩", "foreshadow": "后文惨死于丁春秋掌下", "faction": "无量剑东宗", "tier": Tier.THIRD,
         "location_id": "loc:无量山", "skills": ["art:无量剑法"], "arrives_with": "portent:比剑"},
    ],
    "items": [
        *WORLD.model_dump()["items"],
        {"id": "itm:金创药", "name": "金创药", "kind": "丹药", "location_id": "loc:无量山",
         "use": {"effect": "疗伤", "potency": 2}},
        {"id": "itm:断肠草", "name": "断肠草", "kind": "毒物", "location_id": "loc:无量山", "hazard": "剧毒"},
        {"id": "itm:无量玉璧", "name": "无量玉璧", "location_id": "loc:无量玉洞", "portable": False},
        {"id": "itm:毒信笺", "name": "毒信笺", "owner_id": "chr:南海鳄神", "arrives_with": "portent:传书"},
    ],
    "relations": [
        *WORLD.model_dump()["relations"],
        {"source_id": "chr:段誉", "target_id": "chr:南海鳄神", "kind": RelationKind.MENTOR, "era": Era.IMMINENT},
    ],
    "personas": [
        {"character_id": "chr:左子穆", "likes": ["本门剑法"], "dislikes": ["西宗"], "worry": "剑湖宫比剑",
         "sources": ["chunk:3"]},
    ],
    "facts": [
        {"id": "fact:东西宗相争", "text": "左子穆与辛双清东西宗相争多年", "subject_ids": ["chr:辛双清", "chr:左子穆"],
         "knower_ids": ["chr:龚光杰", "chr:辛双清"], "unlock": {"kind": "LEVERAGE", "target_id": "chr:辛双清"},
         "sources": ["ev:3"]},
        {"id": "fact:左子穆心事", "text": "左子穆最放不下剑湖宫比剑", "subject_ids": ["chr:左子穆"],
         "knower_ids": ["chr:左子穆"], "unlock": {"kind": "MOTIVE", "target_id": "chr:左子穆"}, "sources": ["chunk:3"]},
        {"id": "fact:断肠草有毒", "text": "崖边断肠草碰不得", "subject_ids": ["itm:断肠草"],
         "knower_ids": ["chr:辛双清"], "unlock": {"kind": "HAZARD", "target_id": "itm:断肠草"}, "sources": ["chunk:4"]},
        {"id": "fact:乔峰降龙", "text": "乔峰身负降龙十八掌", "subject_ids": ["chr:乔峰"], "knower_ids": ["chr:乔峰"],
         "unlock": {"kind": "TEACHING", "target_id": "art:降龙十八掌"}, "sources": ["chunk:9"]},
        {"id": "fact:容子矩来意", "text": "容子矩为比剑而来", "subject_ids": ["chr:容子矩"], "knower_ids": ["chr:容子矩"],
         "sources": ["chunk:5"]},
    ],
})


def lore_journey(pid: str) -> list[DomainEvent]:
    """走遍 P1 视图的旅程：仇人在去处（含已故者、后来才到场者的敌意不算）、制住后不再挡路、打听见闻、交涉、服药。"""
    return [
        PlayerSpawned(player_id=pid, name="阿星", location_id="loc:大理城"),
        RelationChanged(character_id="chr:南海鳄神", attitude=Attitude.HOSTILE, cause="遭你冒犯"),
        RelationChanged(character_id="chr:汪剑通", attitude=Attitude.HOSTILE, cause="旧怨"),  # 已故者挡不了路
        RelationChanged(character_id="chr:容子矩", attitude=Attitude.HOSTILE, cause="旧怨"),  # 还没到场
        Moved(from_location_id="loc:大理城", to_location_id="loc:无量山", exit_label="北上"),
        SkillExecuted(skill_id=None, target_id="chr:南海鳄神", outcome=CombatOutcome.SUCCESS),
        Moved(from_location_id="loc:无量山", to_location_id="loc:大理城", exit_label="南下"),
        Moved(from_location_id="loc:大理城", to_location_id="loc:无量山", exit_label="北上"),
        FactLearned(fact_id="fact:东西宗相争", source_id="chr:龚光杰"),
        Parleyed(npc_id="chr:左子穆", aim=Aim.LEARN, approach=Approach.WORDS, outcome=SocialOutcome.SOFTENED),
        ItemTransferred(item_id="itm:金创药", from_holder="loc:无量山", to_holder=pid),
        HealthChanged(delta=-40, cause="与龚光杰交手", source_id="chr:龚光杰"),
        ItemConsumed(item_id="itm:金创药", effect="疗伤"),
        HealthChanged(delta=+20, cause="敷金创药", source_id="itm:金创药", source="item"),
        RelationChanged(character_id="chr:辛双清", attitude=Attitude.HOSTILE, cause="遭你出手相攻"),
        Moved(from_location_id="loc:无量山", to_location_id="loc:无量玉洞", exit_label="崖下"),
    ]


def test_lore_compiles_to_parameterized_cypher() -> None:
    """见闻成节点、三种边按主体种类落标签；关系带 era；人设只存外显部分；后文剧情（foreshadow）一个字也不入图。"""
    statements = compile_blueprint(LORE)
    first_edge = next(i for i, s in enumerate(statements) if "MERGE (a)-[" in s.query)
    assert any("MERGE (n:Fact" in s.query for s in statements[:first_edge])
    edges = {(s.query.split("[r:")[1].split("]")[0], s.query.split("MATCH (b:")[1].split(" ")[0])
             for s in statements[first_edge:]}
    assert {("KNOWS_FACT", "Fact"), ("ABOUT", "Character"), ("ABOUT", "Item"), ("UNLOCKS", "Character"),
            ("UNLOCKS", "MartialArt"), ("UNLOCKS", "Item")} <= edges

    def rows(fragment: str) -> list[dict[str, Any]]:
        return [r for s in statements if fragment in s.query for r in s.params.get("rows", [])]

    assert {r["props"]["era"] for r in rows("[r:HAS_RELATION]")} == {"开篇", "将至"}
    people = {r["id"]: r["props"] for r in rows("MERGE (n:Character")}
    assert json.loads(people["chr:左子穆"]["persona"]) == {"likes": ["本门剑法"], "dislikes": ["西宗"], "worry": "剑湖宫比剑"}
    assert people["chr:容子矩"]["arrives_with"] == "portent:比剑" and "foreshadow" not in people["chr:容子矩"]
    items = {r["id"]: r["props"] for r in rows("MERGE (n:Item")}
    assert json.loads(items["itm:金创药"]["use"]) == {"effect": "疗伤", "potency": 2}
    assert items["itm:无量玉璧"]["portable"] is False and items["itm:断肠草"]["hazard"] == "剧毒"
    assert "丁春秋" not in render_script(statements)


async def _lore(graph: Graph) -> str:
    await graph.seed(LORE, reset=True)
    return pid()


async def test_bonds_carry_era_and_lead(graph: Graph) -> None:
    p = await _lore(graph)
    await graph.project(p, envelopes(p, [PlayerSpawned(player_id=p, name="阿星", location_id="loc:大理城")]))
    yu = (await graph.local_snapshot(p)).character("chr:段誉")
    assert yu is not None and set(yu.bonds) == {
        BondView(other_id="chr:段正淳", kind=RelationKind.KIN, era=Era.OPENING, lead=True),
        BondView(other_id="chr:南海鳄神", kind=RelationKind.MENTOR, era=Era.IMMINENT, lead=True),
    }
    zheng = (await graph.local_snapshot(p)).character("chr:段正淳")
    assert zheng is not None and zheng.bonds == (BondView(other_id="chr:段誉", kind=RelationKind.KIN, lead=False),)


async def test_persona_facts_and_item_traits_in_the_scene(graph: Graph) -> None:
    p = await _lore(graph)
    await graph.project(p, envelopes(p, [PlayerSpawned(player_id=p, name="阿星", location_id="loc:无量山")]))
    snap = await graph.local_snapshot(p)
    zuo = snap.character("chr:左子穆")
    assert zuo is not None and zuo.persona == PersonaView(likes=("本门剑法",), dislikes=("西宗",), worry="剑湖宫比剑")
    assert snap.character("chr:龚光杰").persona is None  # type: ignore[union-attr]
    assert {f.id for f in snap.facts} == {"fact:东西宗相争", "fact:左子穆心事", "fact:断肠草有毒"}  # 知情人之一在场
    rivalry = next(f for f in snap.facts if f.id == "fact:东西宗相争")
    assert rivalry.subject_ids == ("chr:左子穆", "chr:辛双清") and rivalry.knower_ids == ("chr:辛双清", "chr:龚光杰")
    assert rivalry.unlock == FactUnlock(kind="LEVERAGE", target_id="chr:辛双清")
    assert snap.labels["fact:东西宗相争"] == "左子穆与辛双清东西宗相争多年"  # 见闻的名字是正文，不露 slug
    assert await graph.labels(["fact:乔峰降龙", "fact:无此事"]) == {"fact:乔峰降龙": "乔峰身负降龙十八掌"}
    salve, herb = snap.item("itm:金创药"), snap.item("itm:断肠草")
    assert salve is not None and salve.use == ItemUse(effect="疗伤", potency=2) and salve.portable and salve.hazard is None
    assert herb is not None and herb.hazard == "剧毒" and herb.use is None
    # 后来才到场的人与物不进任何场景：容子矩、他的见闻、南海鳄神身上的毒信笺
    assert snap.character("chr:容子矩") is None and snap.item("itm:毒信笺") is None
    assert "fact:容子矩来意" not in {f.id for f in snap.facts}
    q = pid()
    await graph.project(q, envelopes(q, [PlayerSpawned(player_id=q, name="阿星", location_id="loc:无量玉洞")]))
    cave = await graph.local_snapshot(q)
    wall = cave.item("itm:无量玉璧")
    assert wall is not None and not wall.portable and cave.facts == ()


async def test_hostile_ahead_and_consumed_items_follow_the_overlay(graph: Graph) -> None:
    p = await _lore(graph)
    story = envelopes(p, lore_journey(p))

    async def at(version: int) -> LocalSnapshot:
        await graph.project(p, story[:version])
        return await graph.local_snapshot(p)

    def ahead(snap: LocalSnapshot) -> dict[str, bool]:
        return {e.to_id: e.hostile_ahead for e in snap.exits}

    assert ahead(await at(1)) == {"loc:无量山": False, "loc:无锡城": False}
    assert ahead(await at(4)) == {"loc:无量山": True, "loc:无锡城": False}  # 已故的汪剑通、未到的容子矩都不算
    assert ahead(await at(7)) == {"loc:无量山": False, "loc:无锡城": False}  # 南海鳄神已被制住
    snap = await at(12)
    assert {i.id for i in snap.inventory} == {"itm:金创药"} and snap.player_hp == MAX_HP - 40
    snap = await at(14)
    assert snap.item("itm:金创药") is None and snap.inventory == ()  # 用掉了：不回地上、不回正典持有者
    assert snap.player_hp == MAX_HP - 20
    snap = await at(16)
    assert ahead(snap) == {"loc:无量山": True}  # 辛双清翻脸：回去的路上有仇人
    assert await graph.labels([p]) == {p: "阿星"}


async def test_forget_clears_consumed_items(graph: Graph) -> None:
    p = await _lore(graph)
    history = envelopes(p, lore_journey(p)[:14])
    await graph.project(p, history)
    before = await graph.local_snapshot(p)
    await graph.forget(p)
    await graph.project(p, history[:11])  # 抹去后只重放到拾起金创药：用掉的记录也一并抹去了
    assert {i.id for i in (await graph.local_snapshot(p)).inventory} == {"itm:金创药"}
    await graph.forget(p)
    await graph.project(p, history)
    assert await graph.local_snapshot(p) == before


@pytest.mark.neo4j
async def test_memory_and_neo4j_agree_on_the_p1_views() -> None:
    neo = await _neo4j()
    memory = InMemoryWorldGraph()
    try:
        await neo.seed(LORE, reset=True)
        await memory.seed(LORE)
        p = pid()
        story = envelopes(p, lore_journey(p))
        for upto in range(1, len(story) + 1):
            await neo.project(p, story[:upto])
            await memory.project(p, story[:upto])
            assert await neo.local_snapshot(p) == await memory.local_snapshot(p), f"第 {upto} 版快照分叉"
        for where in ("loc:无量山", "loc:大理城", "loc:无量玉洞", "loc:无锡城"):  # 每一处的正典切片
            q = pid()
            spawn = envelopes(q, [PlayerSpawned(player_id=q, name="阿星", location_id=where)])
            await neo.project(q, spawn)
            await memory.project(q, spawn)
            assert await neo.local_snapshot(q) == await memory.local_snapshot(q), where
        ids = ["fact:东西宗相争", "fact:容子矩来意", "chr:容子矩", "itm:毒信笺", p]
        assert await neo.labels(ids) == await memory.labels(ids)
    finally:
        await neo.close()


# ============================================================
#  已知的见闻：线人不在，主体或 unlock 目标在场时照样进快照（known=True）；重复 id 与 MERGE 同口径
# ============================================================
INFORMED = WorldBlueprint.model_validate({
    **LORE.model_dump(),
    "characters": [
        *LORE.model_dump()["characters"],
        {"id": "chr:东宗信使", "true_name": "东宗信使", "faction": "无量剑东宗", "tier": Tier.THIRD, "location_id": "loc:大理城"},
    ],
    "items": [
        *LORE.model_dump()["items"],
        {"id": "itm:蛇毒酒", "name": "蛇毒酒", "kind": "毒物", "location_id": "loc:无锡城", "hazard": "蛇毒"},
    ],
    "facts": [
        *LORE.model_dump()["facts"],
        {"id": "fact:玉佩来历", "text": "这块玉佩是段家信物", "subject_ids": ["itm:玉佩"], "knower_ids": ["chr:段正淳"],
         "sources": ["chunk:2"]},  # 主体是物：玉佩在地上或在行囊里即在场
        {"id": "fact:无锡毒酒", "text": "段誉听说无锡城里有一坛蛇毒酒", "subject_ids": ["chr:段誉"], "knower_ids": ["chr:段誉"],
         "unlock": {"kind": "HAZARD", "target_id": "itm:蛇毒酒"}, "sources": ["chunk:2"]},  # 只有 unlock 目标会在场
        {"id": "fact:左子穆好名", "text": "左子穆把剑湖宫比剑看得极重", "subject_ids": ["chr:左子穆"], "knower_ids": ["chr:东宗信使"],
         "unlock": {"kind": "MOTIVE", "target_id": "chr:左子穆"}, "sources": ["chunk:3"]},  # 心事：把柄在本人面前用
    ],
})


def informed_journey(pid: str) -> list[DomainEvent]:
    """在大理城从三位线人处得知三件事，再去线人都不在的地方：主体或 unlock 目标在场的那几件随身带着。"""
    return [
        PlayerSpawned(player_id=pid, name="阿星", location_id="loc:大理城"),
        FactLearned(fact_id="fact:玉佩来历", source_id="chr:段正淳"),
        FactLearned(fact_id="fact:无锡毒酒", source_id="chr:段誉"),
        FactLearned(fact_id="fact:左子穆好名", source_id="chr:东宗信使"),
        Moved(from_location_id="loc:大理城", to_location_id="loc:无量山", exit_label="北上"),
        ItemTransferred(item_id="itm:玉佩", from_holder="loc:无量山", to_holder=pid),
        Moved(from_location_id="loc:无量山", to_location_id="loc:大理城", exit_label="南下"),
        Moved(from_location_id="loc:大理城", to_location_id="loc:无锡城", exit_label="东去"),
    ]


def _facts(snap: LocalSnapshot) -> dict[str, bool]:
    return {f.id: f.known for f in snap.facts}


async def test_learned_facts_follow_their_subjects_not_the_informant(graph: Graph) -> None:
    await graph.seed(INFORMED, reset=True)
    p = pid()
    story = envelopes(p, informed_journey(p))

    async def at(version: int) -> LocalSnapshot:
        await graph.project(p, story[:version])
        return await graph.local_snapshot(p)

    told = {"fact:玉佩来历", "fact:无锡毒酒", "fact:左子穆好名"}
    assert _facts(await at(1)) == dict.fromkeys(told, False)  # 线人都在场：可打探，尚未知
    assert _facts(await at(4)) == dict.fromkeys(told, True)
    assert _facts(await at(5)) == {  # 无量山：三位线人都不在
        "fact:东西宗相争": False, "fact:左子穆心事": False, "fact:断肠草有毒": False,
        "fact:玉佩来历": True,  # 主体玉佩躺在地上
        "fact:左子穆好名": True,  # 主体兼心事的主人左子穆在场：借势的筹码
    }
    snap = await at(8)
    assert _facts(snap) == {
        "fact:乔峰降龙": False,  # 知情人乔峰在场
        "fact:玉佩来历": True,  # 玉佩在行囊里
        "fact:无锡毒酒": True,  # 主体段誉不在，unlock 目标蛇毒酒在地上
    }
    assert snap.labels["fact:无锡毒酒"] == "段誉听说无锡城里有一坛蛇毒酒" and snap.labels["itm:蛇毒酒"] == "蛇毒酒"
    q = pid()  # 平行世界：没打听过的人在无锡城只看得到知情人在场的那一件
    await graph.project(q, envelopes(q, [PlayerSpawned(player_id=q, name="阿星", location_id="loc:无锡城")]))
    assert _facts(await graph.local_snapshot(q)) == {"fact:乔峰降龙": False}


async def test_forget_clears_learned_facts(graph: Graph) -> None:
    await graph.seed(INFORMED, reset=True)
    p = pid()
    history = envelopes(p, informed_journey(p))
    await graph.project(p, history)
    before = await graph.local_snapshot(p)
    await graph.forget(p)
    await graph.project(p, history[:1])  # 抹去后只重放到落脚：得知的记录一并抹去
    assert _facts(await graph.local_snapshot(p)) == {"fact:玉佩来历": False, "fact:无锡毒酒": False, "fact:左子穆好名": False}
    await graph.forget(p)
    await graph.project(p, history)
    assert await graph.local_snapshot(p) == before


async def test_duplicate_fact_ids_collapse_like_merge(graph: Graph) -> None:
    """闸门拒收重复的主体 / 知情人；越过闸门的旧蓝图也不许两套实现分叉：内存去重保序，与 Neo4j 的 MERGE 同口径。"""
    dup = Fact.model_construct(
        id="fact:重复", text="左子穆与辛双清不和", subject_ids=("chr:左子穆", "chr:辛双清", "chr:左子穆"),
        knower_ids=("chr:龚光杰", "chr:龚光杰"), unlock=None, sources=("ev:1",),
    )
    await graph.seed(WorldBlueprint.model_construct(**{**dict(WORLD), "facts": (dup,)}), reset=True)
    p = pid()
    await graph.project(p, envelopes(p, [PlayerSpawned(player_id=p, name="阿星", location_id="loc:无量山")]))
    (fact,) = (await graph.local_snapshot(p)).facts
    assert fact.subject_ids == ("chr:左子穆", "chr:辛双清") and fact.knower_ids == ("chr:龚光杰",)


@pytest.mark.neo4j
async def test_memory_and_neo4j_agree_on_learned_facts() -> None:
    neo = await _neo4j()
    memory = InMemoryWorldGraph()
    try:
        await neo.seed(INFORMED, reset=True)
        await memory.seed(INFORMED)
        p = pid()
        story = envelopes(p, informed_journey(p))
        for upto in range(1, len(story) + 1):
            await neo.project(p, story[:upto])
            await memory.project(p, story[:upto])
            assert await neo.local_snapshot(p) == await memory.local_snapshot(p), f"第 {upto} 版快照分叉"
    finally:
        await neo.close()


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
