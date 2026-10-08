"""
[INPUT]: 依赖 app.infrastructure.persistence 的 InMemoryWorldGraph / Neo4jWorldGraph，依赖 app.domain.events，依赖 tests/world 的 WORLD，
         依赖 tests/conftest 的 NEO4J_* 环境变量
[OUTPUT]: 图谱契约测试：同一组用例在内存实现与真实 Neo4j（设置 TLBB_TEST_NEO4J_URI 时）上共跑，另有"双实现快照逐字段相等"的同构证明
[POS]: tests 的投影正确性：正典只读、覆盖层随事件演化（熟练度在 KNOWS_SKILL 上做加法、气血钳位）、投影幂等可重试、
       抹去覆盖层后从事件流重放得到同一个世界；下落不明的物品不进任何快照，人物的称号随快照下发
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.combat import CombatOutcome
from app.domain.events import (
    DomainEvent,
    EventEnvelope,
    HealthChanged,
    ItemTransferred,
    Moved,
    PlayerDied,
    PlayerSpawned,
    RelationChanged,
    SkillExecuted,
    SkillPracticed,
)
from app.domain.models import Acquisition, Attitude, Item, MartialArt, Transmission
from app.domain.progression import MAX_HP, Mastery
from app.errors import ProjectionError
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
