"""
[INPUT]: 依赖 domain/models 的 WorldBlueprint / EntityKind / kind_of 及节点类型，依赖 domain/lore 的 Persona，
         依赖 domain/geography 的 ways（每条出口的有效方位 / 交通方式 / 耗时），依赖 json 的 dumps
[OUTPUT]: 对外提供 CypherStatement（参数化语句）、compile_blueprint()（蓝图 → 按依赖排序的批量写图语句）、
          render_script()（同一批语句渲染为可交给 cypher-shell 的 .cypher 脚本）、cypher_literal()（值 → Cypher 字面量）、
          CANON_LABELS（正典节点标签，含见闻 Fact 与人群 Swarm）、OVERLAY_LABELS（覆盖节点标签 Player / Clock / Emerged / Activity / Trace / Rumor）、
          KIND_LABELS（id 前缀 → 节点标签，含 swm → Swarm）
[POS]: infrastructure 的确定性编译器：原著解析管道的终点、Neo4j 播种器的输入。抽取出的名字一律走 $rows 参数，
       不拼进查询文本——原著里的引号与反斜杠伤不到图谱；脚本渲染只是把同一批参数序列化为字面量，二者同源（DRY）。
       硬性边：CONNECTS_TO（地点↔地点）、LOCATED_IN（人物/物品/人群→地点）、HAS_RELATION（人物↔人物）、KNOWS_SKILL（人物→武学）、
       BELONGS_TO（物品→物主、人物/武学→门派），另有 REQUIRES / CONFLICTS_WITH 把武学的获取要求（典籍 as item、地点 as place）
       与修炼要求（根基 as skill、相冲）展开为可遍历的拓扑。人物节点的 name 恒等于 true_name（本名主键），称号另存 titles；
       物品的 LOCATED_IN / BELONGS_TO 边带 provenance：自愈代理推断的安放与原著明写的安放在图里一眼分得清；下落不明的物品没有这两条边。
       P1 掌故与物性：HAS_RELATION 带 era（结于何时，方向即上首）；人物节点带 arrives_with 与 persona（外显人设 JSON，出处另存 persona_sources，
       foreshadow 永不入图）；物品节点带 portable / hazard / use（JSON）/ arrives_with；见闻是 (:Fact {id, text, sources}) 节点，
       (知情人)-[:KNOWS_FACT]->(f)、(f)-[:ABOUT]->(主体)、(f)-[:UNLOCKS {kind}]->(所解之边的那一端)——主体与目标按 id 前缀落到各自的标签上。
       人群是 (:Swarm {id, name, size, panic_threshold, routine, faction, sources})-[:LOCATED_IN]->(:Location)：正典只记他们平日在哪、做什么，
       溃散与否是平行世界的事（覆盖层的 Activity）。
       约束里另有覆盖节点 (:Clock) / (:Emerged) / (:Activity) / (:Trace) / (:Rumor) 的 (world, id) 复合唯一与 world 索引：它们由投影写入，不由蓝图产出。
       空间属性图：CONNECTS_TO 按 geography.ways 写 label / direction / travel_method / time_cost（撰写的道路注记优先，否则确定性推出——
       与内存图谱、世界物理的寻路同一个函数），Location 节点带 landmark / renowned（可见性注记：地标远眺可见、名胜天下皆知，缺省 false）；
       覆盖层的行军所在 (:Character)-[:AT {world}]->(:Location) 另建 AT.world 索引（VISITED / HEARD_OF / WOUNDED 都挂在 Player 上，随它一并抹去）
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from app.domain.geography import ways
from app.domain.lore import Persona
from app.domain.models import EntityKind, WorldBlueprint, kind_of

BATCH_ROWS = 500
CANON_LABELS = ("Location", "Character", "MartialArt", "Item", "Faction", "Fact", "Swarm")
OVERLAY_LABELS = ("Player", "Clock", "Emerged", "Activity", "Trace", "Rumor")  # 平行世界的覆盖节点：reset 时与正典一并清空
_WORLD_SCOPED = tuple(x for x in OVERLAY_LABELS if x != "Player")  # 以 (world, id) 为键的覆盖节点（Player 以自身 id 为键）
KIND_LABELS = {
    EntityKind.LOCATION: "Location",
    EntityKind.CHARACTER: "Character",
    EntityKind.MARTIAL_ART: "MartialArt",
    EntityKind.ITEM: "Item",
    EntityKind.PLAYER: "Player",
    EntityKind.SWARM: "Swarm",
}


@dataclass(frozen=True, slots=True)
class CypherStatement:
    query: str
    params: dict[str, Any]


# ============================================================
#  语句模板 —— 标签与边类型是代码里的常量，只有数据走参数
# ============================================================
CONSTRAINTS = [
    *(f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
      for label in ("Location", "Character", "MartialArt", "Item", "Player", "Fact", "Swarm")),
    "CREATE CONSTRAINT faction_name IF NOT EXISTS FOR (n:Faction) REQUIRE n.name IS UNIQUE",
    "CREATE INDEX item_canon_holder IF NOT EXISTS FOR (n:Item) ON (n.canon_holder)",
    "CREATE INDEX held_by_world IF NOT EXISTS FOR ()-[r:HELD_BY]-() ON (r.world)",
    "CREATE INDEX consumed_world IF NOT EXISTS FOR ()-[r:CONSUMED]-() ON (r.world)",
    "CREATE INDEX at_world IF NOT EXISTS FOR ()-[r:AT]-() ON (r.world)",  # 行军所在：(:Character)-[:AT {world}]->(:Location)
    # 覆盖节点（语义物理引擎的时钟与微观事实、世界心跳的活动 / 痕迹 / 消息）：id 只在本世界内唯一
    # （同一挂处同名的时钟、同一刻同一处的同一件事，在每个平行世界里 id 相同）
    *(f"CREATE CONSTRAINT {label.lower()}_world_id IF NOT EXISTS FOR (n:{label}) REQUIRE (n.world, n.id) IS UNIQUE"
      for label in _WORLD_SCOPED),
    *(f"CREATE INDEX {label.lower()}_world IF NOT EXISTS FOR (n:{label}) ON (n.world)" for label in _WORLD_SCOPED),
]

_NODE = "UNWIND $rows AS row MERGE (n:{label} {{id: row.id}}) SET n += row.props"
_FACTION = "UNWIND $rows AS row MERGE (:Faction {name: row.name})"
_EDGE = (
    "UNWIND $rows AS row "
    "MATCH (a:{a} {{id: row.a}}) MATCH (b:{b} {{{key}: row.b}}) "
    "MERGE (a)-[r:{rel}]->(b) SET r += row.props"
)


def _edge(a: str, rel: str, b: str, key: str = "id") -> str:
    return _EDGE.format(a=a, rel=rel, b=b, key=key)


def _json(model: BaseModel | None) -> str | None:
    return None if model is None else json.dumps(model.model_dump(mode="json"), ensure_ascii=False)


def compile_blueprint(bp: WorldBlueprint) -> list[CypherStatement]:
    """先约束，再节点（含见闻与人群），最后边——边的 MATCH 依赖节点已存在。全部 MERGE：重复播种幂等。"""
    statements = [CypherStatement(q, {}) for q in CONSTRAINTS]

    def emit(query: str, rows: Sequence[dict[str, Any]]) -> None:
        for start in range(0, len(rows), BATCH_ROWS):
            statements.append(CypherStatement(query, {"rows": list(rows[start : start + BATCH_ROWS])}))

    # ---- 节点 ----
    sights = {s.location_id: s for s in bp.sights}
    emit(_NODE.format(label="Location"), [
        {"id": x.id, "props": {"name": x.name, "aliases": list(x.aliases), "region": x.region,
                               "description": x.description,
                               "landmark": bool(s and s.landmark), "renowned": bool(s and s.renowned)}}
        for x in bp.locations for s in [sights.get(x.id)]
    ])
    personas = {p.character_id: p for p in bp.personas}
    emit(_NODE.format(label="Character"), [
        {"id": x.id, "props": {"name": x.true_name, "true_name": x.true_name, "titles": list(x.titles),
                               "aliases": list(x.aliases), "faction": x.faction,
                               "status": x.status.value, "tier": x.tier.value, "disposition": x.disposition.value,
                               "description": x.description, "arrives_with": x.arrives_with,
                               **_persona(personas.get(x.id))}}
        for x in bp.characters
    ])
    emit(_NODE.format(label="MartialArt"), [
        {"id": x.id, "props": {"name": x.name, "aliases": list(x.aliases), "faction": x.faction, "kind": x.kind,
                               "tier": x.tier.value, "description": x.description,
                               "acquisition": json.dumps(x.acquisition.model_dump(mode="json"), ensure_ascii=False),
                               "practice": json.dumps(x.practice.model_dump(mode="json"), ensure_ascii=False)}}
        for x in bp.martial_arts
    ])
    emit(_NODE.format(label="Item"), [
        {"id": x.id, "props": {"name": x.name, "aliases": list(x.aliases), "kind": x.kind,
                               "description": x.description, "canon_holder": x.canon_holder,
                               "provenance": x.provenance.value, "portable": x.portable, "hazard": x.hazard,
                               "use": _json(x.use), "arrives_with": x.arrives_with}}
        for x in bp.items
    ])
    emit(_NODE.format(label="Fact"), [
        {"id": f.id, "props": {"text": f.text, "sources": list(f.sources)}} for f in bp.facts
    ])
    emit(_NODE.format(label="Swarm"), [
        {"id": s.id, "props": {"name": s.name, "size": s.size, "panic_threshold": s.panic_threshold,
                               "routine": s.routine, "faction": s.faction, "sources": list(s.sources)}}
        for s in bp.swarms
    ])
    factions = sorted({c.faction for c in bp.characters if c.faction} | {m.faction for m in bp.martial_arts if m.faction})
    emit(_FACTION, [{"name": name} for name in factions])

    # ---- 硬性边 ----
    emit(_edge("Location", "CONNECTS_TO", "Location"), [  # 一对 (from, to) 一条边：方位、交通方式、耗时取有效值
        {"a": w.from_id, "b": w.to_id, "props": {"label": w.label, "direction": w.direction.value,
                                                 "travel_method": w.travel_method.value, "time_cost": w.time_cost}}
        for w in ways(bp).values()
    ])
    emit(_edge("Character", "LOCATED_IN", "Location"), [
        {"a": c.id, "b": c.location_id, "props": {}} for c in bp.characters if c.location_id
    ])
    emit(_edge("Swarm", "LOCATED_IN", "Location"), [
        {"a": s.id, "b": s.location_id, "props": {}} for s in bp.swarms
    ])
    emit(_edge("Item", "LOCATED_IN", "Location"), [
        {"a": i.id, "b": i.location_id, "props": {"provenance": i.provenance.value}} for i in bp.items if i.location_id
    ])
    emit(_edge("Item", "BELONGS_TO", "Character"), [
        {"a": i.id, "b": i.owner_id, "props": {"provenance": i.provenance.value}} for i in bp.items if i.owner_id
    ])
    emit(_edge("Character", "BELONGS_TO", "Faction", key="name"), [
        {"a": c.id, "b": c.faction, "props": {}} for c in bp.characters if c.faction
    ])
    emit(_edge("MartialArt", "BELONGS_TO", "Faction", key="name"), [
        {"a": m.id, "b": m.faction, "props": {}} for m in bp.martial_arts if m.faction
    ])
    emit(_edge("Character", "KNOWS_SKILL", "MartialArt"), [
        {"a": c.id, "b": s, "props": {}} for c in bp.characters for s in c.skills
    ])
    emit(_edge("Character", "HAS_RELATION", "Character"), [
        {"a": r.source_id, "b": r.target_id, "props": {"kind": r.kind.value, "note": r.note, "era": r.era.value}}
        for r in bp.relations
    ])

    # ---- 见闻：谁知道、说的是谁、解开哪条边 ----
    emit(_edge("Character", "KNOWS_FACT", "Fact"), [
        {"a": k, "b": f.id, "props": {}} for f in bp.facts for k in f.knower_ids
    ])
    for kind, label in KIND_LABELS.items():
        emit(_edge("Fact", "ABOUT", label), [
            {"a": f.id, "b": s, "props": {}} for f in bp.facts for s in f.subject_ids if kind_of(s) is kind
        ])
        emit(_edge("Fact", "UNLOCKS", label), [
            {"a": f.id, "b": f.unlock.target_id, "props": {"kind": f.unlock.kind}}
            for f in bp.facts if f.unlock is not None and kind_of(f.unlock.target_id) is kind
        ])

    # ---- 获取要求与修炼要求的拓扑 ----
    emit(_edge("MartialArt", "REQUIRES", "MartialArt"), [
        {"a": m.id, "b": s, "props": {"as": "skill"}} for m in bp.martial_arts for s in m.practice.skills
    ])
    emit(_edge("MartialArt", "REQUIRES", "Item"), [
        {"a": m.id, "b": i, "props": {"as": "item"}} for m in bp.martial_arts for i in m.acquisition.items
    ])
    emit(_edge("MartialArt", "REQUIRES", "Location"), [
        {"a": m.id, "b": m.acquisition.location_id, "props": {"as": "place"}}
        for m in bp.martial_arts if m.acquisition.location_id
    ])
    emit(_edge("MartialArt", "CONFLICTS_WITH", "MartialArt"), [
        {"a": m.id, "b": s, "props": {}} for m in bp.martial_arts for s in m.practice.conflicts
    ])
    return statements


def _persona(persona: Persona | None) -> dict[str, Any]:
    """外显人设作 JSON 属性随人物节点入图（与武学的两道门同一口径）；出处另存，供审阅。"""
    if persona is None:
        return {"persona": None, "persona_sources": None}
    shown = {"likes": list(persona.likes), "dislikes": list(persona.dislikes), "worry": persona.worry}
    return {"persona": json.dumps(shown, ensure_ascii=False), "persona_sources": list(persona.sources)}


# ============================================================
#  脚本渲染
# ============================================================
def cypher_literal(value: Any) -> str:
    """Python 值 → Cypher 字面量。字符串借 JSON 的转义（与 Cypher 字符串转义兼容），映射键是代码常量故可裸写。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return json.dumps(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return "{" + ", ".join(f"`{k}`: {cypher_literal(v)}" for k, v in value.items()) + "}"
    if isinstance(value, Iterable):
        return "[" + ", ".join(cypher_literal(v) for v in value) + "]"
    raise TypeError(f"无法渲染为 Cypher 字面量：{type(value).__name__}")


def _inline(statement: CypherStatement) -> Iterator[str]:
    if "rows" not in statement.params:
        yield statement.query + ";"
        return
    rows = statement.params["rows"]
    if rows:
        yield statement.query.replace("$rows", cypher_literal(rows), 1) + ";"


def render_script(statements: Iterable[CypherStatement]) -> str:
    header = "// TLBB-Engine 原著播种脚本：由 app/infrastructure/cypher.py 自蓝图确定性生成，请勿手改\n"
    return header + "\n".join(line for s in statements for line in _inline(s)) + "\n"
