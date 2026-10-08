"""
[INPUT]: 依赖 domain/models 的 WorldBlueprint 及节点类型，依赖 json 的 dumps
[OUTPUT]: 对外提供 CypherStatement（参数化语句）、compile_blueprint()（蓝图 → 按依赖排序的批量写图语句）、
          render_script()（同一批语句渲染为可交给 cypher-shell 的 .cypher 脚本）、cypher_literal()（值 → Cypher 字面量）
[POS]: infrastructure 的确定性编译器：原著解析管道的终点、Neo4j 播种器的输入。抽取出的名字一律走 $rows 参数，
       不拼进查询文本——原著里的引号与反斜杠伤不到图谱；脚本渲染只是把同一批参数序列化为字面量，二者同源（DRY）。
       硬性边：CONNECTS_TO（地点↔地点）、LOCATED_IN（人物/物品→地点）、HAS_RELATION（人物↔人物）、KNOWS_SKILL（人物→武学）、
       BELONGS_TO（物品→物主、人物/武学→门派），另有 REQUIRES / CONFLICTS_WITH 把武学的获取要求（典籍 as item、地点 as place）
       与修炼要求（根基 as skill、相冲）展开为可遍历的拓扑。人物节点的 name 恒等于 true_name（本名主键），称号另存 titles；
       物品的 LOCATED_IN / BELONGS_TO 边带 provenance：自愈代理推断的安放与原著明写的安放在图里一眼分得清；下落不明的物品没有这两条边
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from app.domain.models import WorldBlueprint

BATCH_ROWS = 500
CANON_LABELS = ("Location", "Character", "MartialArt", "Item", "Faction")


@dataclass(frozen=True, slots=True)
class CypherStatement:
    query: str
    params: dict[str, Any]


# ============================================================
#  语句模板 —— 标签与边类型是代码里的常量，只有数据走参数
# ============================================================
CONSTRAINTS = [
    *(f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
      for label in ("Location", "Character", "MartialArt", "Item", "Player")),
    "CREATE CONSTRAINT faction_name IF NOT EXISTS FOR (n:Faction) REQUIRE n.name IS UNIQUE",
    "CREATE INDEX item_canon_holder IF NOT EXISTS FOR (n:Item) ON (n.canon_holder)",
    "CREATE INDEX held_by_world IF NOT EXISTS FOR ()-[r:HELD_BY]-() ON (r.world)",
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


def compile_blueprint(bp: WorldBlueprint) -> list[CypherStatement]:
    """先约束，再节点，最后边——边的 MATCH 依赖节点已存在。全部 MERGE：重复播种幂等。"""
    statements = [CypherStatement(q, {}) for q in CONSTRAINTS]

    def emit(query: str, rows: Sequence[dict[str, Any]]) -> None:
        for start in range(0, len(rows), BATCH_ROWS):
            statements.append(CypherStatement(query, {"rows": list(rows[start : start + BATCH_ROWS])}))

    # ---- 节点 ----
    emit(_NODE.format(label="Location"), [
        {"id": x.id, "props": {"name": x.name, "aliases": list(x.aliases), "region": x.region,
                               "description": x.description}}
        for x in bp.locations
    ])
    emit(_NODE.format(label="Character"), [
        {"id": x.id, "props": {"name": x.true_name, "true_name": x.true_name, "titles": list(x.titles),
                               "aliases": list(x.aliases), "faction": x.faction,
                               "status": x.status.value, "tier": x.tier.value, "disposition": x.disposition.value,
                               "description": x.description}}
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
                               "provenance": x.provenance.value}}
        for x in bp.items
    ])
    factions = sorted({c.faction for c in bp.characters if c.faction} | {m.faction for m in bp.martial_arts if m.faction})
    emit(_FACTION, [{"name": name} for name in factions])

    # ---- 硬性边 ----
    emit(_edge("Location", "CONNECTS_TO", "Location"), [
        {"a": loc.id, "b": target, "props": {"label": label}}
        for loc in bp.locations for label, target in loc.exits.items()
    ])
    emit(_edge("Character", "LOCATED_IN", "Location"), [
        {"a": c.id, "b": c.location_id, "props": {}} for c in bp.characters if c.location_id
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
        {"a": r.source_id, "b": r.target_id, "props": {"kind": r.kind.value, "note": r.note}} for r in bp.relations
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
