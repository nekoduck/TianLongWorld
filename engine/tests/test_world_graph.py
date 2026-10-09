"""
[INPUT]: 依赖 app.infrastructure.persistence 的 InMemoryWorldGraph / Neo4jWorldGraph，依赖 app.infrastructure.cypher 的 compile_blueprint / render_script / CANON_LABELS / OVERLAY_LABELS / KIND_LABELS，
         依赖 app.domain 的 events / models / lore（Fact / FactUnlock）/ intent / outcomes / snapshot / progression，
         依赖 tests/conftest 的 graph 双实现夹具 / Graph / envelopes / pid 与 NEO4J_* 环境变量，依赖 tests/world 的 WORLD
[OUTPUT]: 图谱契约测试：同一组用例在内存实现与真实 Neo4j（设置 TLBB_TEST_NEO4J_URI 时）上共跑，另有"双实现快照逐字段相等"的同构证明；
          LORE 蓝图与 journey 旅程供 tests/test_world_graph_spatial 复用
[POS]: tests 的投影正确性：正典只读、覆盖层随事件演化（熟练度在 KNOWS_SKILL 上做加法、气血钳位）、投影幂等可重试、
       抹去覆盖层后从事件流重放得到同一个世界；下落不明的物品不进任何快照，人物的称号随快照下发；
       P1 视图（自带的 LORE 蓝图 = WORLD + 后来才到场者 + 三种物性 + 将至的师徒 + 人设 + 五条见闻）：羁绊的 era / lead、外显人设、
       知情人在场的见闻及其正文作 label、物品的 portable / hazard / use、arrives_with 的人与物不进任何场景、出口的 hostile_ahead
       （已故、未到、被制住者不算）、USE 一回合（ItemConsumed）后物品不回地上也不回正典持有者、抹去重放连同用掉的记录一起重建、
       掌故编译成参数化 Cypher 且 foreshadow 一字不入图，以及双实现在 P1 旅程每个版本与每处正典切片上逐字段相等；
       已知的见闻（INFORMED 蓝图 = LORE + 三位线人的三件事）：线人不在而主体或 unlock 目标在场（地上、行囊、在场者）时照样进快照
       且 known=True、没打听过的平行世界看不到、抹去重放一并清掉 LEARNED；越过闸门的重复主体 / 知情人去重保序，两实现同口径。
       语义物理引擎与世界心跳的覆盖层在 tests/test_world_graph_heartbeat，空间属性图与分层 NPC 生态在 tests/test_world_graph_spatial
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import asyncio
import json
from typing import Any

import pytest

from app.domain.aggregates import Player
from app.domain.combat import CombatOutcome
from app.domain.events import (
    LEGACY_MASTERY_POINTS,
    DomainEvent,
    FactLearned,
    HealthChanged,
    ItemConsumed,
    ItemTransferred,
    Moved,
    Parleyed,
    PlayerDied,
    PlayerSpawned,
    RelationChanged,
    SkillExecuted,
    SkillPracticed,
)
from app.domain.intent import Aim, Approach
from app.domain.lore import Fact, FactUnlock
from app.domain.models import (
    Acquisition,
    Attitude,
    Era,
    Item,
    ItemUse,
    MartialArt,
    RelationKind,
    Tier,
    Transmission,
    WorldBlueprint,
)
from app.domain.outcomes import SocialOutcome
from app.domain.progression import MAX_HP, Mastery
from app.domain.snapshot import BondView, LocalSnapshot, PersonaView
from app.errors import ProjectionError
from app.infrastructure.cypher import compile_blueprint, render_script
from app.infrastructure.persistence.memory_graph import InMemoryWorldGraph
from tests.conftest import Graph, _neo4j, envelopes, pid
from tests.world import WORLD


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


