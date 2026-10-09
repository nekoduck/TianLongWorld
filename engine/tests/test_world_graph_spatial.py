"""
[INPUT]: 依赖 app.infrastructure.persistence 的 InMemoryWorldGraph / Neo4jWorldGraph，依赖 app.infrastructure.cypher 的 compile_blueprint / render_script，
         依赖 app.domain 的 events / geography（UNKNOWN_PLACE / ways）/ agenda（NpcAgenda / Encounter / AgendaEnd）/ npc（AgendaProposal / admit / march / meet）/ heartbeat（Atlas / residents_at），
         依赖 tests/conftest 的 graph 双实现夹具 / Graph / envelopes / pid 与 NEO4J_*，依赖 tests/test_world_graph 的 LORE
[OUTPUT]: 空间属性图与分层 NPC 生态的图谱契约测试（GEO 蓝图 = LORE + 大理城↔无量山撰写过的骑马大道 + 无量山地标、无锡城名胜 + 辛双清的执念）：
          CONNECTS_TO 的方位 / 交通方式 / 耗时逐条等于 geography.ways（撰写的优先、其余推出）、地点带 landmark / renowned、AT.world 索引、名字只走参数；
          出路的认知随旅程演化（远眺、名胜、亲历、未知即「未知区域」且 names 只剩标签、问路得知），平行世界没来过即未知；
          NPC 行军改此世所在（在场者、随身之物、所知的见闻、去处的 hostile_ahead 都跟着他走，回家即复原）、带伤恰在 until 那一刻痊愈、
          议程与中断只进聚合（快照除版本外逐字相等）、平行世界不受影响；抹去重放一并重建；admit 放行议程、march 按骑马大道二十四刻寻路、
          撞见玩家、meet 收尾、抵达了结——每个版本图谱与内存参照逐字段相等且在场者等于 residents_at；双实现在这段旅程与每处正典切片上逐字段相等，
          Neo4j 的 AT / VISITED / HEARD_OF / WOUNDED 恰为 1 / 2 / 1 / 1 条、forget 后不留
[POS]: tests 的空间覆盖层投影正确性：(:Player)-[:VISITED|HEARD_OF {world}]、(:Character)-[:AT|WOUNDED {world}] 两套实现逐字段同构
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import pytest

from app.domain.agenda import AgendaEnd, Encounter, EncounterKind, NpcAgenda, encounter_id
from app.domain.aggregates import Player, PlayerState
from app.domain.commands import SPAWN_TICK
from app.domain.events import (
    AgendaConcluded,
    AgendaIssued,
    AgendaPlanned,
    DomainEvent,
    EncounterBegan,
    EncounterResolved,
    Moved,
    NpcMoved,
    NpcWounded,
    PlacesLearned,
    PlayerSpawned,
    RelationChanged,
    TimePassed,
)
from app.domain.geography import UNKNOWN_PLACE, ways
from app.domain.heartbeat import Atlas, residents_at
from app.domain.models import Attitude, WorldBlueprint
from app.domain.npc import AgendaProposal, admit, march, meet
from app.domain.snapshot import LocalSnapshot
from app.infrastructure.cypher import compile_blueprint, render_script
from app.infrastructure.persistence.memory_graph import InMemoryWorldGraph
from tests.conftest import Graph, _neo4j, envelopes, pid
from tests.test_world_graph import LORE

HERE, CITY, WUXI = "loc:无量山", "loc:大理城", "loc:无锡城"
T0 = SPAWN_TICK


# ============================================================
#  空间属性图与探索迷雾、分层 NPC 生态：出路带方位 / 交通方式 / 耗时 / 认知；NPC 此世所在、带伤；议程与中断只进聚合
# ============================================================
CAVE = "loc:无量玉洞"
ZUO, GONG = "chr:左子穆", "chr:龚光杰"
GEO = WorldBlueprint.model_validate({  # LORE + 大理城与无量山之间撰写过的骑马大道 + 无量山是地标、无锡城是名胜 + 辛双清的执念
    **LORE.model_dump(),
    "personas": [
        *LORE.model_dump()["personas"],
        {"character_id": "chr:辛双清", "worry": "西宗声势日衰", "sources": ["chunk:3"]},
    ],
    "passages": [
        {"from_id": CITY, "to_id": HERE, "direction": "北", "travel_method": "骑马", "time_cost": 24,
         "basis": "大理城北上无量山须骑马大半日", "sources": ["chunk:1"]},
        {"from_id": HERE, "to_id": CITY, "direction": "南", "travel_method": "骑马", "time_cost": 24,
         "basis": "下山南归大理城须骑马大半日", "sources": ["chunk:1"]},
    ],
    "sights": [
        {"location_id": HERE, "landmark": True, "sources": ["chunk:1"]},
        {"location_id": WUXI, "renowned": True, "sources": ["chunk:9"]},
    ],
})


def geo_journey(pid: str) -> list[DomainEvent]:
    """
    投胎大理城（无量山远眺可见、无锡城是名胜）→ 北上无量山（大理城亲历、崖下未知）→ 向左子穆问路（崖下问路得知）→
    左子穆领了下山的议程、龚光杰带伤一日、左子穆翻脸 → 左子穆骑马下山到大理城（无量山不再有他、他的剑与心事随他走，南下的路上有了仇人）→
    一日过去龚光杰伤愈 → 南下大理城与左子穆撞见（议程与中断只进聚合）→ 左子穆回山（大理城不再有他，北上的路上有了仇人）。
    """
    meeting = Encounter(id=encounter_id(EncounterKind.MEET_PLAYER, ZUO, pid, CITY, T0 + 100),
                        kind=EncounterKind.MEET_PLAYER, npc_id=ZUO, other_id=pid, location_id=CITY, tick=T0 + 100)
    return [
        PlayerSpawned(player_id=pid, name="阿星", location_id=CITY),  # 1
        Moved(from_location_id=CITY, to_location_id=HERE, exit_label="北上"),  # 2
        PlacesLearned(location_ids=(CAVE,), source_id=ZUO),  # 3
        AgendaPlanned(tick=T0, cause="初临江湖"),
        AgendaIssued(agenda=NpcAgenda(npc_id=ZUO, target_id=CITY, intent="下山访友", issued_tick=T0)),  # 5
        NpcWounded(npc_id=GONG, until_tick=T0 + 96, cause="无量山一战"),  # 6
        RelationChanged(character_id=ZUO, attitude=Attitude.HOSTILE, cause="遭你冒犯"),  # 7
        NpcMoved(npc_id=ZUO, from_location_id=HERE, to_location_id=CITY, tick=T0 + 24, witnessed="离开"),  # 8
        TimePassed(ticks=96),  # 9 · tick 128：龚光杰的伤恰好到期
        Moved(from_location_id=HERE, to_location_id=CITY, exit_label="南下"),  # 10
        EncounterBegan(encounter=meeting),
        EncounterResolved(encounter_id=meeting.id, kind=meeting.kind, location_id=CITY, npc_ids=(ZUO,),
                          resume_tick=T0 + 104, witnessed=True),
        AgendaConcluded(npc_id=ZUO, how=AgendaEnd.ARRIVED, tick=T0 + 24),  # 13
        NpcMoved(npc_id=ZUO, from_location_id=CITY, to_location_id=HERE, tick=T0 + 128, witnessed="离开"),  # 14
    ]


def _ways(snap: LocalSnapshot) -> dict[str, tuple[str, str, str, int, str]]:
    return {e.to_id: (e.direction.value, e.shown_name, e.travel_method.value, e.time_cost, e.discovery.value)
            for e in snap.exits}


def test_geography_compiles_onto_connects_to() -> None:
    """CONNECTS_TO 的方位 / 交通方式 / 耗时就是 geography.ways（撰写的优先、其余推出），地点带地标与名胜；名字只走参数。"""
    statements = compile_blueprint(GEO)
    roads = {(r["a"], r["b"]): r["props"] for s in statements if "[r:CONNECTS_TO]" in s.query for r in s.params["rows"]}
    assert roads == {key: {"label": w.label, "direction": w.direction.value, "travel_method": w.travel_method.value,
                           "time_cost": w.time_cost} for key, w in ways(GEO).items()}
    assert roads[(CITY, HERE)] == {"label": "北上", "direction": "北", "travel_method": "骑马", "time_cost": 24}  # 撰写的
    assert roads[(HERE, CAVE)] == {"label": "崖下", "direction": "下", "travel_method": "步行", "time_cost": 4}  # 推出的
    assert roads[(CAVE, HERE)]["travel_method"] == "攀援" and roads[(CITY, WUXI)]["direction"] == "东"
    places = {r["id"]: r["props"] for s in statements if "MERGE (n:Location" in s.query for r in s.params["rows"]}
    assert {k: (v["landmark"], v["renowned"]) for k, v in places.items()} == {
        HERE: (True, False), WUXI: (False, True), CITY: (False, False), CAVE: (False, False),
    }
    queries = " ".join(s.query for s in statements)
    assert "FOR ()-[r:AT]-() ON (r.world)" in queries and not any(x in queries for x in ("骑马", "北上"))
    assert "骑马" in render_script(statements)


async def test_exits_carry_the_way_and_the_fog(graph: Graph) -> None:
    await graph.seed(GEO, reset=True)
    p = pid()
    story = envelopes(p, geo_journey(p))

    async def at(version: int) -> LocalSnapshot:
        await graph.project(p, story[:version])
        return await graph.local_snapshot(p)

    snap = await at(1)  # 大理城：无量山远远望得见，无锡城天下皆知
    assert _ways(snap) == {HERE: ("北", "无量山", "骑马", 24, "远眺"), WUXI: ("东", "无锡城", "步行", 4, "名胜")}
    snap = await at(2)  # 无量山：来路亲历，崖下的去处未知——名字不露，只剩标签与方位把手
    assert _ways(snap) == {CITY: ("南", "大理城", "骑马", 24, "亲历"), CAVE: ("下", UNKNOWN_PLACE, "步行", 4, "未知")}
    cave = next(e for e in snap.exits if e.to_id == CAVE)
    assert not cave.known and cave.names == ("崖下",) and snap.exit_names(cave) == ("崖下", "下")
    snap = await at(3)  # 问过路：崖下是无量玉洞
    assert _ways(snap)[CAVE] == ("下", "无量玉洞", "步行", 4, "问路")
    assert snap.exit_names(next(e for e in snap.exits if e.to_id == CAVE)) == ("崖下", "无量玉洞", "下")
    for e in snap.exits:  # 与 Cypher 编译、Atlas 寻路同一个函数
        w = ways(GEO)[(HERE, e.to_id)]
        assert (e.label, e.direction, e.travel_method, e.time_cost) == (w.label, w.direction, w.travel_method, w.time_cost)
    snap = await at(len(story))  # 回到大理城：无量山已亲历，无锡城仍是名胜
    assert _ways(snap) == {HERE: ("北", "无量山", "骑马", 24, "亲历"), WUXI: ("东", "无锡城", "步行", 4, "名胜")}
    other = pid()  # 平行世界：没来过、没问过路
    await graph.project(other, envelopes(other, [PlayerSpawned(player_id=other, name="阿星", location_id=HERE)]))
    assert _ways(await graph.local_snapshot(other)) == {
        CITY: ("南", UNKNOWN_PLACE, "骑马", 24, "未知"),
        CAVE: ("下", UNKNOWN_PLACE, "步行", 4, "未知"),
    }


async def test_npcs_walk_and_bleed_in_this_world_only(graph: Graph) -> None:
    """行军改的是此世所在：在场者、随身之物、所知的见闻、去处的仇人都跟着他走；带伤到期即愈；议程与中断不进快照。"""
    await graph.seed(GEO, reset=True)
    p = pid()
    story = envelopes(p, geo_journey(p))

    async def at(version: int) -> LocalSnapshot:
        await graph.project(p, story[:version])
        return await graph.local_snapshot(p)

    def people(snap: LocalSnapshot) -> dict[str, bool]:
        return {c.id: c.wounded for c in snap.characters}

    asked = await at(3)
    snap = await at(5)  # 无量山：左子穆领了议程还没动身
    assert snap == asked.model_copy(update={"version": 5})  # 议程只进聚合
    assert people(snap) == {"chr:南海鳄神": False, ZUO: False, "chr:辛双清": False, GONG: False}
    assert snap.item("itm:无量剑") is not None and "fact:左子穆心事" in {f.id for f in snap.facts}
    snap = await at(7)
    assert people(snap)[GONG] and not people(snap)[ZUO]  # 龚光杰带伤一日
    assert {e.to_id: e.hostile_ahead for e in snap.exits} == {CITY: False, CAVE: False}  # 翻脸的左子穆还在眼前
    snap = await at(8)  # 左子穆下山：无量山不再有他，他的剑与心事随他走，南下的路上有了仇人
    assert ZUO not in people(snap) and snap.item("itm:无量剑") is None
    assert "fact:左子穆心事" not in {f.id for f in snap.facts} and "fact:东西宗相争" in {f.id for f in snap.facts}
    assert {e.to_id: e.hostile_ahead for e in snap.exits} == {CITY: True, CAVE: False}
    assert not people(await at(9))[GONG]  # 伤到 tick 128 为止：恰在那一刻已愈
    snap = await at(10)  # 南下大理城：左子穆在此，剑在他手里，心事可打探
    zuo = snap.character(ZUO)
    assert zuo is not None and zuo.attitude is Attitude.HOSTILE and not zuo.wounded
    assert snap.item("itm:无量剑").holder_id == ZUO  # type: ignore[union-attr]
    assert "fact:左子穆心事" in {f.id for f in snap.facts}
    assert await at(13) == snap.model_copy(update={"version": 13})  # 撞见与议程了结只进聚合
    snap = await at(14)  # 左子穆回山：大理城不再有他，北上的路上有了仇人
    assert ZUO not in people(snap) and snap.item("itm:无量剑") is None
    assert {e.to_id: e.hostile_ahead for e in snap.exits} == {HERE: True, WUXI: False}
    truth = Player.replay(e.event for e in story)
    assert truth is not None and dict(truth.npc_at) == {ZUO: HERE} and truth.agendas == {} and truth.encounters == ()
    other = pid()  # 平行世界：左子穆从没下过山，龚光杰也没受伤
    await graph.project(other, envelopes(other, [PlayerSpawned(player_id=other, name="阿星", location_id=HERE)]))
    pristine = await graph.local_snapshot(other)
    assert people(pristine) == {"chr:南海鳄神": False, ZUO: False, "chr:辛双清": False, GONG: False}


async def test_forget_and_replay_rebuilds_the_spatial_overlay(graph: Graph) -> None:
    await graph.seed(GEO, reset=True)
    p = pid()
    history = envelopes(p, geo_journey(p))[:9]
    await graph.project(p, history)
    before = await graph.local_snapshot(p)
    await graph.forget(p)
    await graph.project(p, history[:2])  # 抹去后只重放到上山：问路、行军、带伤一并抹去
    fresh = await graph.local_snapshot(p)
    assert ZUO in {c.id for c in fresh.characters} and not any(c.wounded for c in fresh.characters)
    assert _ways(fresh)[CAVE][-1] == "未知"
    await graph.forget(p)
    await graph.project(p, history)
    assert await graph.local_snapshot(p) == before


async def test_marching_npcs_project_like_the_aggregate(graph: Graph) -> None:
    """议程经领域闸门 admit 放行、行军由 march 按道路耗时寻路：左子穆骑马下山撞见玩家；每个版本上图谱快照与内存参照逐字段相等。"""
    await graph.seed(GEO, reset=True)
    reference = InMemoryWorldGraph()
    await reference.seed(GEO)
    atlas = Atlas.of(GEO)
    assert atlas.core == ("chr:左子穆", "chr:辛双清")
    p = pid()
    events: list[DomainEvent] = [PlayerSpawned(player_id=p, name="阿星", location_id=CITY)]

    def state() -> PlayerState:
        truth = Player.replay(events)
        assert truth is not None
        return truth

    events += admit([AgendaProposal(npc="左子穆", target="大理城", intent="下山访友")], state(), atlas, "初临江湖")
    for _ in range(4):
        events.append(TimePassed(ticks=8))
        events += march(state(), atlas)
    (began,) = [e for e in events if isinstance(e, EncounterBegan)]
    events += meet(began.encounter, [], state())
    events += march(state(), atlas)  # 中断了结：已在目标，议程抵达
    story = envelopes(p, events)
    for upto in range(1, len(story) + 1):
        await graph.project(p, story[:upto])
        await reference.project(p, story[:upto])
        assert await graph.local_snapshot(p) == await reference.local_snapshot(p), f"第 {upto} 版快照分叉"
    moved = [e for e in events if isinstance(e, NpcMoved)]
    assert [(e.npc_id, e.from_location_id, e.to_location_id, e.tick, e.witnessed) for e in moved] == [
        (ZUO, HERE, CITY, T0 + 24, "来到"),  # 骑马大道：二十四刻
    ]
    assert began.encounter.kind is EncounterKind.MEET_PLAYER and began.encounter.location_id == CITY
    assert isinstance(events[-1], AgendaConcluded) and events[-1].how is AgendaEnd.ARRIVED
    snap = await graph.local_snapshot(p)
    assert ZUO in {c.id for c in snap.characters} and snap.item("itm:无量剑").holder_id == ZUO  # type: ignore[union-attr]
    assert {c.id for c in snap.characters} == {c.id for c in residents_at(state(), atlas, CITY)}


@pytest.mark.neo4j
async def test_memory_and_neo4j_agree_on_the_march_and_the_fog() -> None:
    neo = await _neo4j()
    memory = InMemoryWorldGraph()
    try:
        await neo.seed(GEO, reset=True)
        await memory.seed(GEO)
        p = pid()
        story = envelopes(p, geo_journey(p))
        for upto in range(1, len(story) + 1):
            await neo.project(p, story[:upto])
            await memory.project(p, story[:upto])
            assert await neo.local_snapshot(p) == await memory.local_snapshot(p), f"第 {upto} 版快照分叉"
        for where in (HERE, CITY, CAVE, WUXI):  # 每一处的正典切片
            q = pid()
            spawn = envelopes(q, [PlayerSpawned(player_id=q, name="阿星", location_id=where)])
            await neo.project(q, spawn)
            await memory.project(q, spawn)
            assert await neo.local_snapshot(q) == await memory.local_snapshot(q), where

        async def count(pattern: str) -> int:
            records, _, _ = await neo._driver.execute_query(f"MATCH {pattern} RETURN count(r) AS n", pid=p)
            return int(records[0]["n"])

        overlay = ("()-[r:AT {world: $pid}]->()", "()-[r:VISITED {world: $pid}]->()",
                   "()-[r:HEARD_OF {world: $pid}]->()", "()-[r:WOUNDED {world: $pid}]->()")
        assert [await count(x) for x in overlay] == [1, 2, 1, 1]
        await neo.forget(p)
        assert [await count(x) for x in overlay] == [0, 0, 0, 0]
    finally:
        await neo.close()
