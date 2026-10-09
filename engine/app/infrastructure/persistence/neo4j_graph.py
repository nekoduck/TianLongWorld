"""
[INPUT]: 依赖 neo4j 的 AsyncGraphDatabase / AsyncDriver / AsyncManagedTransaction，依赖 domain/ports 的 WorldReader / WorldProjector / WorldSeeder，
         依赖 domain/events 的领域事件，依赖 domain/combat 的 CombatOutcome，依赖 domain/progression 的 MAX_HP，
         依赖 domain/clocks 的 NarrativeClock，依赖 domain/aggregates 的 EMERGED_MAX，
         依赖 domain/ambient 的 Activity / EnvironmentalTrace / ActivityKind 与 ACTIVITIES_MAX / TRACES_MAX / TOKENS_MAX，依赖 domain/commands 的 SPAWN_TICK，
         依赖 domain/models 的 Attitude / CharacterStatus / Era / ItemUse / Acquisition / Practice / kind_of / WorldBlueprint，依赖 domain/snapshot 的视图，
         依赖 infrastructure/cypher 的 compile_blueprint / CANON_LABELS / OVERLAY_LABELS / KIND_LABELS，依赖 app.errors 的 ProjectionError
[OUTPUT]: 对外提供 Neo4jWorldGraph（connect / close + 图谱三端口 + stale_canon 旧纪元残留检查）
[POS]: persistence 的生产图谱快照。正典 = 播种写入的节点与硬性边，永不被事件改写；
       平行世界 = 以玩家为锚的覆盖层：(:Player) 节点（name / alive / version 检查点 / aptitude 悟性 / hp 气血）、LOCATED_IN（所在）、
       KNOWS_SKILL {proficiency}（所学及熟练度之和——SkillPracticed 在边上做加法，与 evolve 的 reduce 同构；加法不幂等，
       故检查点在 Player 写锁下读取；旧引擎留下的无熟练度边按上抛口径读作 LEGACY_MASTERY_POINTS）、
       SUBDUED（制住之人）、(:Character)-[:REGARDS {attitude}]->(:Player)（人情）、(:Item)-[:HELD_BY {world}]->(持有者)（易手之物）、
       (:Item)-[:CONSUMED {world}]->(:Player)（用掉之物：HELD_BY 原样留着，快照据此滤掉——只删 HELD_BY 会让它回到正典持有者手里）、
       (:Player)-[:LEARNED {world}]->(:Fact)（得知的见闻）。
       物品此刻的持有者 = 本世界的 HELD_BY，否则正典的 canon_holder——覆盖层可整体抹去并从事件流重放重建。
       P1 视图：在场口径排除 arrives_with（后来才到场者 P1 不进任何场景）；羁绊带 era 与 lead（startNode 即上首）；
       出口的 hostile_ahead 是去处在场者里有没有对本世界玩家 REGARDS 敌视且未被 SUBDUED 的人；人设读 persona JSON 的外显部分；
       见闻 = 经 KNOWS_FACT 取知情人在场者 ∪ 经 LEARNED 取已知且 ABOUT / UNLOCKS 指向此地、在场者或可见之物者（known 标明已知），
       ABOUT / UNLOCKS 还原主体与解锁；labels 把 fact:<slug> 映射为见闻正文。
       语义物理引擎：(:Clock {id, world, name, kind, progress, maximum, consequence})-[:ON]->(挂处：Character / Location / Item / Player)
       由 ClockStarted 写入、ClockAdvanced 钳位加减、ClockCollapsed / ClockCleared 删除；(:Emerged {id, world, text, seq, subject_ids})-[:ABOUT]->(主体)
       由 FactEmerged 写入（seq = 信封版本，重提刷新），每个世界只留 seq 最新的 EMERGED_MAX 条；RenownChanged 只进聚合。
       快照召回挂在此地、在场者、可见之物、玩家自己身上的时钟，与主体 ABOUT 此地、在场者或可见之物的微观事实；forget 连同它们一起抹去。
       世界心跳：Player.tick（PlayerSpawned 时 SPAWN_TICK，旧节点读作 SPAWN_TICK）由 TimePassed 累加，随即先删到期的 (:Trace)、
       再删已结束且痕迹不在了的 (:Activity)（与 ambient.elapse 同口径、同次序）；(:Activity {world, id, kind, participants, started, ends, trace})-[:AT]->(:Location)、
       (:Trace {world, id, description, born, decay})-[:AT]->(:Location)、(:Rumor {world, id, text, subject_ids, origin, born, speed, radius})-[:REACHED]->(传到之处)
       按 (world, id) MERGE 覆盖，超上限按（起讫 / 出生刻, id）降序留前 N 个；RumorSpread 只补 REACHED 边（消息不在即无事）；
       ItemDecayed 与 ItemConsumed 同一投影（CONSUMED 边），ItemPilfered 与 ItemTransferred 同一投影（HELD_BY 易手）；Moved.motivation 只进聚合。
       快照另取此地的活动（state 按 tick 现算）、此地 remaining ≥ 1 的痕迹、正典 (:Swarm)-[:LOCATED_IN]->(此地) 的人群
       （routed = 本世界有一个参与者含它、尚未结束的溃散逃离活动）与 REACHED 此地的消息；forget 一并删 (:Activity|Trace|Rumor {world})。
       每种事件一个投影函数（开闭）；投影在单个写事务内推进检查点，版本不超过检查点的事件被跳过（幂等，可安全重试）
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncManagedTransaction

from app.domain.aggregates import EMERGED_MAX
from app.domain.ambient import ACTIVITIES_MAX, TOKENS_MAX, TRACES_MAX, Activity, ActivityKind, EnvironmentalTrace
from app.domain.clocks import NarrativeClock
from app.domain.combat import CombatOutcome
from app.domain.commands import SPAWN_TICK
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
    PlayerDied,
    PlayerSpawned,
    RelationChanged,
    RumorSpread,
    SkillExecuted,
    SkillPracticed,
    TimePassed,
    TraceLeft,
)
from app.domain.models import Acquisition, Attitude, CharacterStatus, Era, ItemUse, Practice, WorldBlueprint, kind_of
from app.domain.ports import WorldProjector, WorldReader, WorldSeeder
from app.domain.progression import MAX_HP
from app.domain.snapshot import (
    ActivityView,
    BondView,
    CharacterView,
    EmergedView,
    ExitView,
    FactView,
    ItemView,
    LocalSnapshot,
    LocationView,
    PersonaView,
    RumorView,
    SkillView,
    SwarmView,
    TraceView,
)
from app.errors import ProjectionError
from app.infrastructure.cypher import CANON_LABELS, KIND_LABELS, OVERLAY_LABELS, compile_blueprint

logger = logging.getLogger(__name__)

_LABEL = KIND_LABELS

type _Tx = AsyncManagedTransaction
type _Projector = Callable[[_Tx, str, Any], Awaitable[None]]
type _VersionedProjector = Callable[[_Tx, str, Any, int], Awaitable[None]]


# ============================================================
#  投影：事件 → 覆盖层的 Cypher
# ============================================================
_RELOCATE = """
MATCH (p:Player {id: $pid})
OPTIONAL MATCH (p)-[old:LOCATED_IN]->()
DELETE old
WITH DISTINCT p
MATCH (l:Location {id: $loc})
MERGE (p)-[:LOCATED_IN]->(l)
"""


async def _spawned(tx: _Tx, pid: str, e: PlayerSpawned) -> None:
    await tx.run(
        "MERGE (p:Player {id: $pid}) "
        "SET p.name = $name, p.alive = true, p.death_cause = null, p.aptitude = $aptitude, p.hp = $hp, p.tick = $tick",
        pid=pid, name=e.name, aptitude=e.aptitude, hp=MAX_HP, tick=SPAWN_TICK,
    )
    await tx.run(_RELOCATE, pid=pid, loc=e.location_id)


async def _moved(tx: _Tx, pid: str, e: Moved) -> None:
    """此行所为（motivation）只进聚合：它是短期记忆的线头，不是图上的事实。"""
    await tx.run(_RELOCATE, pid=pid, loc=e.to_location_id)


async def _transferred(tx: _Tx, pid: str, e: ItemTransferred | ItemPilfered) -> None:
    """易手：玩家经手（ItemTransferred）与此地之人顺手拿走（ItemPilfered）是同一种覆盖——本世界的 HELD_BY 改指新持有者。"""
    holder_label = _LABEL[kind_of(e.to_holder)]  # 标签取自代码常量表，只有数据走参数
    await tx.run(
        f"""
        MATCH (i:Item {{id: $item}})
        OPTIONAL MATCH (i)-[old:HELD_BY {{world: $pid}}]->()
        DELETE old
        WITH DISTINCT i
        MATCH (h:{holder_label} {{id: $holder}})
        CREATE (i)-[:HELD_BY {{world: $pid}}]->(h)
        """,
        item=e.item_id, pid=pid, holder=e.to_holder,
    )


async def _practiced(tx: _Tx, pid: str, e: SkillPracticed) -> None:
    """熟练度在边上做加法。旧引擎投影的 KNOWS_SKILL 没有 proficiency：按上抛器的折算读作旧账的"学会"，与聚合根同口径。"""
    await tx.run(
        "MATCH (p:Player {id: $pid}) MATCH (a:MartialArt {id: $art}) "
        "MERGE (p)-[k:KNOWS_SKILL]->(a) "
        "ON CREATE SET k.proficiency = $gained "
        "ON MATCH SET k.proficiency = coalesce(k.proficiency, $legacy) + $gained",
        pid=pid, art=e.skill_id, gained=e.proficiency_gained, legacy=LEGACY_MASTERY_POINTS,
    )


async def _health(tx: _Tx, pid: str, e: HealthChanged) -> None:
    await tx.run(
        "MATCH (p:Player {id: $pid}) "
        "WITH p, coalesce(p.hp, $max) + $delta AS hp "
        "SET p.hp = CASE WHEN hp < 0 THEN 0 WHEN hp > $max THEN $max ELSE hp END",
        pid=pid, delta=e.delta, max=MAX_HP,
    )


async def _executed(tx: _Tx, pid: str, e: SkillExecuted) -> None:
    if e.outcome is CombatOutcome.SUCCESS:
        await tx.run("MATCH (p:Player {id: $pid}) MATCH (c:Character {id: $cid}) MERGE (p)-[:SUBDUED]->(c)",
                     pid=pid, cid=e.target_id)


async def _regarded(tx: _Tx, pid: str, e: RelationChanged) -> None:
    await tx.run(
        "MATCH (p:Player {id: $pid}) MATCH (c:Character {id: $cid}) "
        "MERGE (c)-[r:REGARDS]->(p) SET r.attitude = $attitude",
        pid=pid, cid=e.character_id, attitude=e.attitude.value,
    )


async def _consumed(tx: _Tx, pid: str, e: ItemConsumed | ItemDecayed) -> None:
    """
    用掉（ItemConsumed）与露天朽坏（ItemDecayed）都记成本世界的 CONSUMED 边——从此不在任何地方（与 evolve 折进 consumed 同口径）；
    HELD_BY 原样留着：只删 HELD_BY 会让它回到正典持有者手里。
    """
    await tx.run(
        "MATCH (i:Item {id: $item}) MATCH (p:Player {id: $pid}) MERGE (i)-[:CONSUMED {world: $pid}]->(p)",
        item=e.item_id, pid=pid,
    )


async def _learned(tx: _Tx, pid: str, e: FactLearned) -> None:
    """得知的见闻记成本世界的 LEARNED 边：线人走了，主体或 unlock 目标在场时它照样进快照（known=True）。"""
    await tx.run(
        "MATCH (p:Player {id: $pid}) MATCH (f:Fact {id: $fact}) MERGE (p)-[:LEARNED {world: $pid}]->(f)",
        pid=pid, fact=e.fact_id,
    )


async def _died(tx: _Tx, pid: str, e: PlayerDied) -> None:
    await tx.run("MATCH (p:Player {id: $pid}) SET p.alive = false, p.death_cause = $cause", pid=pid, cause=e.cause)


# ---------------- 语义物理引擎：叙事时钟与微观事实（本世界的覆盖节点，world = 玩家 id） ----------------
async def _clock_started(tx: _Tx, pid: str, e: ClockStarted) -> None:
    """时钟成节点挂到实体上：同一世界同 id 即同一只（重挂即覆盖，与 clocks.started 同口径），ON 边随之改挂。"""
    c = e.clock
    anchor = _LABEL[kind_of(c.anchor_id)]  # 标签取自代码常量表，只有数据走参数
    await tx.run(
        f"""
        MERGE (k:Clock {{world: $pid, id: $id}})
        SET k.name = $name, k.kind = $kind, k.progress = $progress, k.maximum = $maximum, k.consequence = $consequence
        WITH k
        OPTIONAL MATCH (k)-[old:ON]->()
        DELETE old
        WITH DISTINCT k
        MATCH (a:{anchor} {{id: $anchor}})
        MERGE (k)-[:ON]->(a)
        """,
        pid=pid, id=c.id, name=c.name, kind=c.kind.value, progress=c.progress, maximum=c.maximum,
        consequence=c.consequence, anchor=c.anchor_id,
    )


async def _clock_advanced(tx: _Tx, pid: str, e: ClockAdvanced) -> None:
    """推进或回退，钳在 [0, maximum − 1]（与 clocks.advanced 同口径）：满格由 ClockCollapsed 明写，投影从不替它坍缩。"""
    await tx.run(
        "MATCH (k:Clock {world: $pid, id: $id}) "
        "WITH k, k.progress + $steps AS p "
        "SET k.progress = CASE WHEN p < 0 THEN 0 WHEN p > k.maximum - 1 THEN k.maximum - 1 ELSE p END",
        pid=pid, id=e.clock_id, steps=e.steps,
    )


async def _clock_retired(tx: _Tx, pid: str, e: ClockCollapsed | ClockCleared) -> None:
    """坍缩或销毁：时钟退场，节点连边一并删去。"""
    await tx.run("MATCH (k:Clock {world: $pid, id: $id}) DETACH DELETE k", pid=pid, id=e.clock_id)


async def _emerged(tx: _Tx, pid: str, e: FactEmerged, version: int) -> None:
    """
    微观事实成节点、ABOUT 它点了名的实体；seq 记信封版本，重提同一条即刷新 seq 与主体。
    每个世界只留 seq 最新的 EMERGED_MAX 条——与聚合根「新者在前、重提提到最前、至多 EMERGED_MAX」同口径。
    主体另存 subject_ids 属性：快照按它原样还原，ABOUT 边供图上遍历与场景过滤。
    """
    await tx.run(
        "MERGE (f:Emerged {world: $pid, id: $id}) SET f.text = $text, f.seq = $seq, f.subject_ids = $subjects "
        "WITH f OPTIONAL MATCH (f)-[old:ABOUT]->() DELETE old",
        pid=pid, id=e.fact_id, text=e.text, seq=version, subjects=list(e.subject_ids),
    )
    by_label: dict[str, list[str]] = {}
    for subject in e.subject_ids:
        by_label.setdefault(_LABEL[kind_of(subject)], []).append(subject)
    for label, ids in by_label.items():
        await tx.run(
            f"MATCH (f:Emerged {{world: $pid, id: $id}}) UNWIND $ids AS s MATCH (n:{label} {{id: s}}) MERGE (f)-[:ABOUT]->(n)",
            pid=pid, id=e.fact_id, ids=ids,
        )
    await tx.run(
        "MATCH (f:Emerged {world: $pid}) WITH f ORDER BY f.seq DESC SKIP $keep DETACH DELETE f",
        pid=pid, keep=EMERGED_MAX,
    )


# ---------------- 世界心跳：时间、活动、痕迹、消息（本世界的覆盖节点，world = 玩家 id） ----------------
async def _time_passed(tx: _Tx, pid: str, e: TimePassed) -> None:
    """
    时间走了 ticks 刻：tick 累加，随即先删到期的痕迹、再删已结束且痕迹已不在的活动——与 ambient.elapse 同口径、同次序
    （消散不另写事件：它是时间的纯函数）。
    """
    row = await (await tx.run(
        "MATCH (p:Player {id: $pid}) SET p.tick = coalesce(p.tick, $spawn) + $ticks RETURN p.tick AS tick",
        pid=pid, spawn=SPAWN_TICK, ticks=e.ticks,
    )).single()
    if row is None:
        return
    tick = int(row["tick"])
    await tx.run("MATCH (t:Trace {world: $pid}) WHERE t.born + t.decay <= $tick DETACH DELETE t", pid=pid, tick=tick)
    await tx.run(
        "MATCH (a:Activity {world: $pid}) WHERE a.ends <= $tick "
        "AND (a.trace IS NULL OR NOT EXISTS { MATCH (t:Trace {world: $pid}) WHERE t.id = a.trace }) "
        "DETACH DELETE a",
        pid=pid, tick=tick,
    )


async def _placed(tx: _Tx, label: str, pid: str, node_id: str, location_id: str) -> None:
    """覆盖节点 (:Activity|Trace {world, id}) 落在一处地方：AT 边随之改指（同 id 再起即覆盖）。"""
    await tx.run(
        f"""
        MATCH (n:{label} {{world: $pid, id: $id}})
        OPTIONAL MATCH (n)-[old:AT]->()
        DELETE old
        WITH DISTINCT n
        MATCH (l:Location {{id: $loc}})
        MERGE (n)-[:AT]->(l)
        """,
        pid=pid, id=node_id, loc=location_id,
    )


async def _keep_newest(tx: _Tx, label: str, key: str, pid: str, keep: int) -> None:
    """每个世界只留按（key, id）降序的前 keep 个——与 ambient.with_* 的「超上限请走最旧的」同口径。"""
    await tx.run(
        f"MATCH (n:{label} {{world: $pid}}) WITH n ORDER BY n.{key} DESC, n.id DESC SKIP $keep DETACH DELETE n",
        pid=pid, keep=keep,
    )


async def _activity_started(tx: _Tx, pid: str, e: ActivityStarted) -> None:
    a = e.activity
    await tx.run(
        "MERGE (n:Activity {world: $pid, id: $id}) "
        "SET n.kind = $kind, n.participants = $participants, n.started = $started, n.ends = $ends, n.trace = $trace",
        pid=pid, id=a.id, kind=a.kind.value, participants=list(a.participants), started=a.started_tick,
        ends=a.ends_tick, trace=a.trace_id,
    )
    await _placed(tx, "Activity", pid, a.id, a.location_id)
    await _keep_newest(tx, "Activity", "started", pid, ACTIVITIES_MAX)


async def _trace_left(tx: _Tx, pid: str, e: TraceLeft) -> None:
    t = e.trace
    await tx.run(
        "MERGE (n:Trace {world: $pid, id: $id}) SET n.description = $description, n.born = $born, n.decay = $decay",
        pid=pid, id=t.id, description=t.description, born=t.born_tick, decay=t.decay_ticks,
    )
    await _placed(tx, "Trace", pid, t.id, t.location_id)
    await _keep_newest(tx, "Trace", "born", pid, TRACES_MAX)


async def _token_spawned(tx: _Tx, pid: str, e: FactTokenSpawned) -> None:
    """消息成节点；同 id 再生即覆盖（传到之处一并重置为这枚消息自带的 reached，与 with_token 同口径）。"""
    t = e.token
    await tx.run(
        "MERGE (r:Rumor {world: $pid, id: $id}) "
        "SET r.text = $text, r.subject_ids = $subjects, r.origin = $origin, r.born = $born, r.speed = $speed, "
        "r.radius = $radius "
        "WITH r OPTIONAL MATCH (r)-[old:REACHED]->() DELETE old",
        pid=pid, id=t.id, text=t.text, subjects=list(t.subject_ids), origin=t.origin_id, born=t.born_tick,
        speed=t.speed, radius=t.radius,
    )
    await _reach(tx, pid, t.id, t.reached)
    await _keep_newest(tx, "Rumor", "born", pid, TOKENS_MAX)


async def _reach(tx: _Tx, pid: str, token_id: str, location_ids: Sequence[str]) -> None:
    await tx.run(
        "MATCH (r:Rumor {world: $pid, id: $id}) UNWIND $places AS place "
        "MATCH (l:Location {id: place}) MERGE (r)-[:REACHED]->(l)",
        pid=pid, id=token_id, places=list(location_ids),
    )


async def _rumor_spread(tx: _Tx, pid: str, e: RumorSpread) -> None:
    """消息又传到几处：补 REACHED 边；消息已被挤掉或从未有过即无事发生（与 ambient.reached 同口径）。"""
    await _reach(tx, pid, e.token_id, e.location_ids)


async def _nothing(tx: _Tx, pid: str, e: DomainEvent) -> None:
    """Conversed / ActionFailed / Parleyed / Maneuvered：只是历史或只进聚合（心事线索）；RenownChanged 的名望只进聚合与状态栏——都不改变图谱的快照。"""


_PROJECTORS: dict[str, _Projector] = {
    "PlayerSpawned": _spawned,
    "Moved": _moved,
    "ItemTransferred": _transferred,
    "SkillPracticed": _practiced,
    "SkillExecuted": _executed,
    "HealthChanged": _health,
    "RelationChanged": _regarded,
    "PlayerDied": _died,
    "Conversed": _nothing,
    "ActionFailed": _nothing,
    "Parleyed": _nothing,
    "FactLearned": _learned,
    "ItemConsumed": _consumed,
    "Maneuvered": _nothing,
    "ClockStarted": _clock_started,
    "ClockAdvanced": _clock_advanced,
    "ClockCollapsed": _clock_retired,
    "ClockCleared": _clock_retired,
    "RenownChanged": _nothing,
    "TimePassed": _time_passed,
    "ActivityStarted": _activity_started,
    "TraceLeft": _trace_left,
    "FactTokenSpawned": _token_spawned,
    "RumorSpread": _rumor_spread,
    "ItemDecayed": _consumed,
    "ItemPilfered": _transferred,
}
_VERSIONED: dict[str, _VersionedProjector] = {  # 需要信封版本的投影（按新旧裁剪的微观事实）
    "FactEmerged": _emerged,
}


# ============================================================
#  查询：局部真理快照
# ============================================================
_Q_PLAYER = """
MATCH (p:Player {id: $pid})-[:LOCATED_IN]->(l:Location)
RETURN p.name AS name, p.alive AS alive, coalesce(p.version, 0) AS version,
       coalesce(p.aptitude, 1.0) AS aptitude, coalesce(p.hp, $max_hp) AS hp, coalesce(p.tick, $spawn) AS tick,
       l {.id, .name, .region, .description} AS location,
       COLLECT { MATCH (p)-[k:KNOWS_SKILL]->(a:MartialArt) RETURN {id: a.id, points: coalesce(k.proficiency, $legacy)} } AS practice,
       COLLECT { MATCH (p)-[:SUBDUED]->(c:Character) RETURN c.id } AS subdued,
       COLLECT {
           MATCH (l)-[e:CONNECTS_TO]->(d:Location)
           RETURN {label: e.label, to_id: d.id, to_name: d.name, hostile_ahead: EXISTS {
               MATCH (c:Character)-[:LOCATED_IN]->(d)
               WHERE c.status = $alive AND c.arrives_with IS NULL
                 AND EXISTS { (c)-[:REGARDS {attitude: $hostile}]->(p) } AND NOT EXISTS { (p)-[:SUBDUED]->(c) }
           }}
       } AS exits
"""

_Q_CHARACTERS = """
MATCH (c:Character)-[:LOCATED_IN]->(:Location {id: $loc})
WHERE c.status = $alive AND c.arrives_with IS NULL
OPTIONAL MATCH (c)-[r:REGARDS]->(:Player {id: $pid})
RETURN c {.id, .name, .titles, .aliases, .faction, .tier, .disposition, .description, .persona} AS c,
       r.attitude AS attitude,
       COLLECT { MATCH (c)-[:KNOWS_SKILL]->(a:MartialArt) RETURN a.id } AS skills,
       COLLECT {
           MATCH (c)-[h:HAS_RELATION]-(o:Character)
           RETURN {other_id: o.id, kind: h.kind, era: coalesce(h.era, $opening), lead: startNode(h) = c}
       } AS bonds
"""

_Q_ITEMS = """
CALL () {
    MATCH (h:Location {id: $loc}) RETURN h
    UNION
    MATCH (h:Character) WHERE h.id IN $chars RETURN h
    UNION
    MATCH (h:Player {id: $pid}) RETURN h
}
CALL (h) {
    MATCH (i:Item)-[:HELD_BY {world: $pid}]->(h) RETURN i
    UNION
    MATCH (i:Item {canon_holder: h.id})
    WHERE NOT EXISTS { (i)-[:HELD_BY {world: $pid}]->() }
    RETURN i
}
WITH h, i WHERE i.arrives_with IS NULL AND NOT EXISTS { (i)-[:CONSUMED {world: $pid}]->() }
OPTIONAL MATCH (i)-[:BELONGS_TO]->(o:Character)
RETURN i {.id, .name, .aliases, .kind, .description, .portable, .hazard, .use} AS item,
       h.id AS holder_id, o.id AS owner_id
"""

_Q_FACTS = """
CALL () {
    MATCH (k:Character)-[:KNOWS_FACT]->(f:Fact) WHERE k.id IN $chars RETURN f
    UNION
    MATCH (:Player {id: $pid})-[:LEARNED {world: $pid}]->(f:Fact)
    WHERE EXISTS { MATCH (f)-[:ABOUT|UNLOCKS]->(a) WHERE a.id IN $scene }
    RETURN f
}
WITH DISTINCT f
OPTIONAL MATCH (f)-[u:UNLOCKS]->(t)
RETURN f.id AS id, f.text AS text,
       COLLECT { MATCH (f)-[:ABOUT]->(s) RETURN s.id } AS subject_ids,
       COLLECT { MATCH (w:Character)-[:KNOWS_FACT]->(f) RETURN w.id } AS knower_ids,
       CASE WHEN u IS NULL THEN null ELSE {kind: u.kind, target_id: t.id} END AS unlock,
       EXISTS { MATCH (:Player {id: $pid})-[:LEARNED {world: $pid}]->(f) } AS known
"""

_Q_CLOCKS = """
MATCH (k:Clock {world: $pid})-[:ON]->(a) WHERE a.id IN $anchors
RETURN k {.id, .name, .kind, .progress, .maximum, .consequence, anchor_id: a.id} AS k
"""

_Q_EMERGED = """
MATCH (f:Emerged {world: $pid}) WHERE EXISTS { MATCH (f)-[:ABOUT]->(s) WHERE s.id IN $scene }
RETURN f.id AS id, f.text AS text, f.subject_ids AS subject_ids
"""

_Q_ACTIVITIES = """
MATCH (a:Activity {world: $pid})-[:AT]->(:Location {id: $loc})
RETURN a {.id, .kind, .participants, .started, .ends, .trace} AS a
"""

_Q_TRACES = """
MATCH (t:Trace {world: $pid})-[:AT]->(:Location {id: $loc}) WHERE t.born + t.decay - $tick >= 1
RETURN t {.id, .description, .born, .decay} AS t
"""

_Q_SWARMS = """
MATCH (s:Swarm)-[:LOCATED_IN]->(:Location {id: $loc})
RETURN s {.id, .name, .size, .panic_threshold, .routine} AS s,
       EXISTS {
           MATCH (a:Activity {world: $pid, kind: $rout}) WHERE s.id IN a.participants AND a.ends > $tick
       } AS routed
"""

_Q_RUMORS = """
MATCH (r:Rumor {world: $pid})-[:REACHED]->(:Location {id: $loc})
RETURN r.id AS id, r.text AS text, r.subject_ids AS subject_ids, r.origin AS origin_id, r.born AS born_tick
"""

_ART = "a {.id, .name, .aliases, .tier, .kind, .faction, .description, .acquisition, .practice} AS a"
_Q_SKILLS = f"""
MATCH (a:MartialArt) WHERE a.id IN $ids RETURN {_ART}
UNION
MATCH (a:MartialArt)-[:REQUIRES {{as: 'item'}}]->(i:Item) WHERE i.id IN $inventory RETURN {_ART}
"""

_Q_CHECKPOINT = """
OPTIONAL MATCH (p:Player {id: $pid})
FOREACH (_ IN CASE WHEN p IS NULL THEN [] ELSE [1] END | SET p.version = coalesce(p.version, 0))
RETURN coalesce(p.version, 0) AS v
"""

_FACT = "fact"  # 见闻的 id 前缀：它不是实体种类，名字是正文
_Q_LABELS = "\nUNION ALL\n".join([
    *(f"MATCH (n:{label}) WHERE n.id IN ${kind.value} RETURN n.id AS id, n.name AS name" for kind, label in _LABEL.items()),
    f"MATCH (n:Fact) WHERE n.id IN ${_FACT} RETURN n.id AS id, n.text AS name",
])


class Neo4jWorldGraph(WorldReader, WorldProjector, WorldSeeder):
    def __init__(self, driver: AsyncDriver, *, database: str = "neo4j") -> None:
        self._driver = driver
        self._db = database

    @classmethod
    async def connect(cls, uri: str, user: str, password: str, *, database: str = "neo4j") -> "Neo4jWorldGraph":
        driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
        await driver.verify_connectivity()
        return cls(driver, database=database)

    async def close(self) -> None:
        await self._driver.close()

    # ---------------- 播种 ----------------
    async def seed(self, blueprint: WorldBlueprint, *, reset: bool = False) -> None:
        if reset:
            labels = " OR ".join(f"n:{label}" for label in (*CANON_LABELS, *OVERLAY_LABELS))
            await self._driver.execute_query(f"MATCH (n) WHERE {labels} DETACH DELETE n", database_=self._db)
        # 约束是模式变更，不能与数据写入同处一个事务：逐条自动提交
        for statement in compile_blueprint(blueprint):
            await self._driver.execute_query(statement.query, statement.params, database_=self._db)
        if stale := await self.stale_canon(blueprint):
            logger.warning("图谱里有 %d 个正典节点不在本蓝图中（旧纪元残留，如 %s）：MERGE 只增不删，换蓝图请加 --reset", len(stale), stale[:5])

    async def stale_canon(self, blueprint: WorldBlueprint) -> list[str]:
        """不属于这份蓝图的正典节点 id（含见闻与人群）——换蓝图却没 reset 时，新旧两版正典会悄悄混成一个世界。"""
        ids = [*(e.id for e in blueprint.entities()), *(f.id for f in blueprint.facts), *(s.id for s in blueprint.swarms)]
        records, _, _ = await self._driver.execute_query(
            "MATCH (n) WHERE (n:Location OR n:Character OR n:MartialArt OR n:Item OR n:Fact OR n:Swarm) AND NOT n.id IN $ids "
            "RETURN n.id AS id ORDER BY id",
            ids=ids, database_=self._db,
        )
        return [r["id"] for r in records]

    async def is_seeded(self) -> bool:
        records, _, _ = await self._driver.execute_query(
            "MATCH (l:Location) RETURN count(l) > 0 AS seeded", database_=self._db
        )
        return bool(records[0]["seeded"])

    # ---------------- 投影 ----------------
    async def project(self, player_id: str, envelopes: Sequence[EventEnvelope]) -> int:
        async with self._driver.session(database=self._db) as session:
            version: int = await session.execute_write(self._project_tx, player_id, list(envelopes))
        return version

    @staticmethod
    async def _project_tx(tx: _Tx, player_id: str, envelopes: list[EventEnvelope]) -> int:
        # 先取 Player 节点的写锁再读检查点：熟练度与气血的投影是加法而非幂等的覆盖，同一世界的两次投影
        # （本进程的 publish 与另一连接的 heal）若都读到旧检查点，就会把同一批事件加两遍。
        # SET 的右侧读取自身属性时，Cypher 先加写锁再读（直接依赖），后到的事务因此读到已推进的检查点
        row = await (await tx.run(_Q_CHECKPOINT, pid=player_id)).single()
        start = version = int(row["v"]) if row else 0
        for envelope in envelopes:
            if envelope.version <= version:
                continue
            if envelope.version != version + 1:
                raise ProjectionError(f"{player_id} 的投影缺口：检查点 {version}，收到第 {envelope.version} 版")
            kind = envelope.event.type
            if kind in _VERSIONED:
                await _VERSIONED[kind](tx, player_id, envelope.event, envelope.version)
            else:
                await _PROJECTORS[kind](tx, player_id, envelope.event)
            version = envelope.version
        if version != start:
            await tx.run("MATCH (p:Player {id: $pid}) SET p.version = $v", pid=player_id, v=version)
        return version

    async def checkpoint(self, player_id: str) -> int:
        records, _, _ = await self._driver.execute_query(
            "MATCH (p:Player {id: $pid}) RETURN coalesce(p.version, 0) AS v", pid=player_id, database_=self._db
        )
        return int(records[0]["v"]) if records else 0

    async def forget(self, player_id: str) -> None:
        await self._driver.execute_query(
            "MATCH ()-[r:HELD_BY|CONSUMED|LEARNED {world: $pid}]->() DELETE r", pid=player_id, database_=self._db
        )
        await self._driver.execute_query(
            "MATCH (n:Clock|Emerged|Activity|Trace|Rumor {world: $pid}) DETACH DELETE n", pid=player_id, database_=self._db
        )
        await self._driver.execute_query(
            "MATCH (p:Player {id: $pid}) DETACH DELETE p", pid=player_id, database_=self._db
        )

    # ---------------- 查询 ----------------
    async def spawn_points(self) -> tuple[str, ...]:
        records, _, _ = await self._driver.execute_query(
            "MATCH (l:Location) WHERE EXISTS { (l)-[:CONNECTS_TO]->() } RETURN l.id AS id ORDER BY id",
            database_=self._db,
        )
        return tuple(r["id"] for r in records)

    async def labels(self, ids: Iterable[str]) -> dict[str, str]:
        async with self._driver.session(database=self._db) as session:
            result: dict[str, str] = await session.execute_read(_labels_tx, list(ids))
        return result

    async def local_snapshot(self, player_id: str) -> LocalSnapshot:
        async with self._driver.session(database=self._db) as session:
            snapshot: LocalSnapshot = await session.execute_read(_snapshot_tx, player_id)
        return snapshot


async def _column(result: Any, key: str) -> AsyncIterator[Any]:
    async for record in result:
        yield record[key]


async def _labels_tx(tx: _Tx, ids: list[str]) -> dict[str, str]:
    buckets: dict[str, list[str]] = {prefix: [] for prefix in (*(kind.value for kind in _LABEL), _FACT)}
    for any_id in ids:
        prefix = any_id.split(":", 1)[0]
        if prefix in buckets:
            buckets[prefix].append(any_id)
    result = await tx.run(_Q_LABELS, buckets)
    return {r["id"]: r["name"] async for r in result}


async def _snapshot_tx(tx: _Tx, player_id: str) -> LocalSnapshot:
    head = await (await tx.run(
        _Q_PLAYER, pid=player_id, max_hp=MAX_HP, legacy=LEGACY_MASTERY_POINTS, spawn=SPAWN_TICK,
        alive=CharacterStatus.ALIVE.value, hostile=Attitude.HOSTILE.value,
    )).single()
    if head is None:
        raise ProjectionError(f"图谱中没有 {player_id} 的覆盖层")
    loc = head["location"]
    tick = int(head["tick"])
    subdued = set(head["subdued"])

    characters = []
    async for r in await tx.run(
        _Q_CHARACTERS, loc=loc["id"], pid=player_id, alive=CharacterStatus.ALIVE.value, opening=Era.OPENING.value
    ):
        c = r["c"]
        characters.append(CharacterView(
            id=c["id"], name=c["name"], titles=tuple(c["titles"] or ()), aliases=tuple(c["aliases"] or ()),
            faction=c["faction"] or "",
            tier=c["tier"], disposition=c["disposition"], description=c["description"] or "",
            subdued=c["id"] in subdued, attitude=r["attitude"] or Attitude.NEUTRAL,
            skill_ids=r["skills"], bonds=[BondView(**b) for b in r["bonds"]],
            persona=PersonaView.model_validate_json(c["persona"]) if c["persona"] else None,
        ))
    present = [c.id for c in characters]

    items = [
        ItemView(
            id=r["item"]["id"], name=r["item"]["name"], aliases=tuple(r["item"]["aliases"] or ()),
            kind=r["item"]["kind"] or "", description=r["item"]["description"] or "",
            holder_id=r["holder_id"], owner_id=r["owner_id"],
            portable=r["item"]["portable"] is not False, hazard=r["item"]["hazard"],
            use=ItemUse.model_validate_json(r["item"]["use"]) if r["item"]["use"] else None,
        )
        async for r in await tx.run(_Q_ITEMS, loc=loc["id"], pid=player_id, chars=present)
    ]
    scene = [loc["id"], *present, *(i.id for i in items)]  # 此地、在场者、看得见的物（地上 / 人手 / 行囊）
    facts = [FactView(**r.data()) async for r in await tx.run(_Q_FACTS, pid=player_id, chars=present, scene=scene)]
    clocks = [  # 挂在此地、在场者、可见之物或玩家自己身上的
        NarrativeClock.model_validate(k)
        async for k in _column(await tx.run(_Q_CLOCKS, pid=player_id, anchors=[*scene, player_id]), "k")
    ]
    emerged = [EmergedView(**r.data()) async for r in await tx.run(_Q_EMERGED, pid=player_id, scene=scene)]
    # 世界心跳：图上存的是领域对象的属性，进行与否、还剩几刻经同一个领域对象按此刻的 tick 现算（与内存实现同口径）
    activities = [
        _activity_view(Activity(
            id=a["id"], kind=a["kind"], participants=tuple(a["participants"]), location_id=loc["id"],
            started_tick=a["started"], ends_tick=a["ends"], trace_id=a["trace"],
        ), tick)
        async for a in _column(await tx.run(_Q_ACTIVITIES, pid=player_id, loc=loc["id"]), "a")
    ]
    traces = [
        TraceView(id=t["id"], description=t["description"], remaining=EnvironmentalTrace(
            id=t["id"], location_id=loc["id"], description=t["description"], born_tick=t["born"], decay_ticks=t["decay"],
        ).remaining(tick))
        async for t in _column(await tx.run(_Q_TRACES, pid=player_id, loc=loc["id"], tick=tick), "t")
    ]
    swarms = [
        SwarmView(**r["s"], routed=bool(r["routed"]))
        async for r in await tx.run(_Q_SWARMS, pid=player_id, loc=loc["id"], tick=tick, rout=ActivityKind.ROUT.value)
    ]
    rumors = [RumorView(**r.data()) async for r in await tx.run(_Q_RUMORS, pid=player_id, loc=loc["id"])]

    inventory = [i.id for i in items if i.holder_id == player_id]
    practice = {row["id"]: int(row["points"] or 0) for row in head["practice"]}
    wanted = sorted({*practice, *(s for c in characters for s in c.skill_ids)})
    skills = [
        SkillView(
            id=a["id"], name=a["name"], aliases=tuple(a["aliases"] or ()), tier=a["tier"], kind=a["kind"] or "",
            faction=a["faction"] or "", description=a["description"] or "",
            acquisition=Acquisition.model_validate_json(a["acquisition"]),
            practice=Practice.model_validate_json(a["practice"]),
        )
        async for a in _column(await tx.run(_Q_SKILLS, ids=wanted, inventory=inventory), "a")
    ]

    snapshot = LocalSnapshot(
        player_id=player_id,
        player_name=head["name"],
        alive=bool(head["alive"]),
        version=int(head["version"]),
        location=LocationView(id=loc["id"], name=loc["name"], region=loc["region"] or "",
                              description=loc["description"] or ""),
        exits=[ExitView(**e) for e in head["exits"]],
        characters=characters,
        items=items,
        skills=skills,
        player_practice=practice,
        player_aptitude=float(head["aptitude"]),
        player_hp=int(head["hp"]),
        facts=facts,
        clocks=clocks,
        emerged=emerged,
        tick=tick,
        activities=activities,
        traces=traces,
        swarms=swarms,
        rumors=rumors,
    )
    return snapshot.model_copy(update={"labels": await _labels_tx(tx, sorted(snapshot.referenced_ids()))})


def _activity_view(a: Activity, tick: int) -> ActivityView:
    return ActivityView(id=a.id, kind=a.kind, participants=a.participants, state=a.state(tick), started_tick=a.started_tick)
