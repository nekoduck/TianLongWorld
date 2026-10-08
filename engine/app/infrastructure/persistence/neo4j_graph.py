"""
[INPUT]: 依赖 neo4j 的 AsyncGraphDatabase / AsyncDriver / AsyncManagedTransaction，依赖 domain/ports 的 WorldReader / WorldProjector / WorldSeeder，
         依赖 domain/events 的领域事件，依赖 domain/models 的 Attitude / CharacterStatus / EntityKind / Prerequisites / kind_of / WorldBlueprint，依赖 domain/snapshot 的视图，
         依赖 infrastructure/cypher 的 compile_blueprint / CANON_LABELS，依赖 app.errors 的 ProjectionError
[OUTPUT]: 对外提供 Neo4jWorldGraph（connect / close + 图谱三端口 + stale_canon 旧纪元残留检查）
[POS]: persistence 的生产图谱快照。正典 = 播种写入的节点与硬性边，永不被事件改写；
       平行世界 = 以玩家为锚的覆盖层：(:Player) 节点（name / alive / version 检查点）、LOCATED_IN（所在）、KNOWS_SKILL（所学）、
       SUBDUED（制住之人）、(:Character)-[:REGARDS {attitude}]->(:Player)（人情）、(:Item)-[:HELD_BY {world}]->(持有者)（易手之物）。
       物品此刻的持有者 = 本世界的 HELD_BY，否则正典的 canon_holder——覆盖层可整体抹去并从事件流重放重建。
       每种事件一个投影函数（开闭）；投影在单个写事务内推进检查点，版本不超过检查点的事件被跳过（幂等，可安全重试）
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Sequence
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase, AsyncManagedTransaction

from app.domain.events import (
    CombatOutcome,
    DomainEvent,
    EventEnvelope,
    ItemTransferred,
    Moved,
    PlayerDied,
    PlayerSpawned,
    RelationChanged,
    SkillExecuted,
    SkillLearned,
)
from app.domain.models import Attitude, CharacterStatus, EntityKind, Prerequisites, WorldBlueprint, kind_of
from app.domain.ports import WorldProjector, WorldReader, WorldSeeder
from app.domain.snapshot import BondView, CharacterView, ExitView, ItemView, LocalSnapshot, LocationView, SkillView
from app.errors import ProjectionError
from app.infrastructure.cypher import CANON_LABELS, compile_blueprint

logger = logging.getLogger(__name__)

_LABEL = {
    EntityKind.LOCATION: "Location",
    EntityKind.CHARACTER: "Character",
    EntityKind.MARTIAL_ART: "MartialArt",
    EntityKind.ITEM: "Item",
    EntityKind.PLAYER: "Player",
}

type _Tx = AsyncManagedTransaction
type _Projector = Callable[[_Tx, str, Any], Awaitable[None]]


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
    await tx.run("MERGE (p:Player {id: $pid}) SET p.name = $name, p.alive = true, p.death_cause = null",
                 pid=pid, name=e.name)
    await tx.run(_RELOCATE, pid=pid, loc=e.location_id)


async def _moved(tx: _Tx, pid: str, e: Moved) -> None:
    await tx.run(_RELOCATE, pid=pid, loc=e.to_location_id)


async def _transferred(tx: _Tx, pid: str, e: ItemTransferred) -> None:
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


async def _learned(tx: _Tx, pid: str, e: SkillLearned) -> None:
    await tx.run("MATCH (p:Player {id: $pid}) MATCH (a:MartialArt {id: $art}) MERGE (p)-[:KNOWS_SKILL]->(a)",
                 pid=pid, art=e.skill_id)


async def _executed(tx: _Tx, pid: str, e: SkillExecuted) -> None:
    if e.outcome is CombatOutcome.PREVAILED:
        await tx.run("MATCH (p:Player {id: $pid}) MATCH (c:Character {id: $cid}) MERGE (p)-[:SUBDUED]->(c)",
                     pid=pid, cid=e.target_id)


async def _regarded(tx: _Tx, pid: str, e: RelationChanged) -> None:
    await tx.run(
        "MATCH (p:Player {id: $pid}) MATCH (c:Character {id: $cid}) "
        "MERGE (c)-[r:REGARDS]->(p) SET r.attitude = $attitude",
        pid=pid, cid=e.character_id, attitude=e.attitude.value,
    )


async def _died(tx: _Tx, pid: str, e: PlayerDied) -> None:
    await tx.run("MATCH (p:Player {id: $pid}) SET p.alive = false, p.death_cause = $cause", pid=pid, cause=e.cause)


async def _nothing(tx: _Tx, pid: str, e: DomainEvent) -> None:
    """Conversed / ActionFailed：只是历史，不改变图谱。"""


_PROJECTORS: dict[str, _Projector] = {
    "PlayerSpawned": _spawned,
    "Moved": _moved,
    "ItemTransferred": _transferred,
    "SkillLearned": _learned,
    "SkillExecuted": _executed,
    "RelationChanged": _regarded,
    "PlayerDied": _died,
    "Conversed": _nothing,
    "ActionFailed": _nothing,
}


# ============================================================
#  查询：局部真理快照
# ============================================================
_Q_PLAYER = """
MATCH (p:Player {id: $pid})-[:LOCATED_IN]->(l:Location)
RETURN p.name AS name, p.alive AS alive, coalesce(p.version, 0) AS version,
       l {.id, .name, .region, .description} AS location,
       COLLECT { MATCH (p)-[:KNOWS_SKILL]->(a:MartialArt) RETURN a.id } AS skills,
       COLLECT { MATCH (p)-[:SUBDUED]->(c:Character) RETURN c.id } AS subdued,
       COLLECT { MATCH (l)-[e:CONNECTS_TO]->(d:Location) RETURN {label: e.label, to_id: d.id, to_name: d.name} } AS exits
"""

_Q_CHARACTERS = """
MATCH (c:Character)-[:LOCATED_IN]->(:Location {id: $loc})
WHERE c.status = $alive
OPTIONAL MATCH (c)-[r:REGARDS]->(:Player {id: $pid})
RETURN c {.id, .name, .aliases, .faction, .tier, .disposition, .description} AS c,
       r.attitude AS attitude,
       COLLECT { MATCH (c)-[:KNOWS_SKILL]->(a:MartialArt) RETURN a.id } AS skills,
       COLLECT { MATCH (c)-[h:HAS_RELATION]-(o:Character) RETURN {other_id: o.id, kind: h.kind} } AS bonds
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
OPTIONAL MATCH (i)-[:BELONGS_TO]->(o:Character)
RETURN i {.id, .name, .aliases, .kind, .description} AS item, h.id AS holder_id, o.id AS owner_id
"""

_ART = "a {.id, .name, .aliases, .tier, .kind, .faction, .description, .prerequisites} AS a"
_Q_SKILLS = f"""
MATCH (a:MartialArt) WHERE a.id IN $ids RETURN {_ART}
UNION
MATCH (a:MartialArt)-[:REQUIRES]->(i:Item) WHERE i.id IN $inventory RETURN {_ART}
"""

_Q_LABELS = "\nUNION ALL\n".join(
    f"MATCH (n:{label}) WHERE n.id IN ${kind.value} RETURN n.id AS id, n.name AS name" for kind, label in _LABEL.items()
)


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
            labels = " OR ".join(f"n:{label}" for label in (*CANON_LABELS, "Player"))
            await self._driver.execute_query(f"MATCH (n) WHERE {labels} DETACH DELETE n", database_=self._db)
        # 约束是模式变更，不能与数据写入同处一个事务：逐条自动提交
        for statement in compile_blueprint(blueprint):
            await self._driver.execute_query(statement.query, statement.params, database_=self._db)
        if stale := await self.stale_canon(blueprint):
            logger.warning("图谱里有 %d 个正典节点不在本蓝图中（旧纪元残留，如 %s）：MERGE 只增不删，换蓝图请加 --reset", len(stale), stale[:5])

    async def stale_canon(self, blueprint: WorldBlueprint) -> list[str]:
        """不属于这份蓝图的正典节点 id——换蓝图却没 reset 时，新旧两版正典会悄悄混成一个世界。"""
        ids = [e.id for e in blueprint.entities()]
        records, _, _ = await self._driver.execute_query(
            "MATCH (n) WHERE (n:Location OR n:Character OR n:MartialArt OR n:Item) AND NOT n.id IN $ids "
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
        row = await (await tx.run("OPTIONAL MATCH (p:Player {id: $pid}) RETURN coalesce(p.version, 0) AS v",
                                  pid=player_id)).single()
        start = version = int(row["v"]) if row else 0
        for envelope in envelopes:
            if envelope.version <= version:
                continue
            if envelope.version != version + 1:
                raise ProjectionError(f"{player_id} 的投影缺口：检查点 {version}，收到第 {envelope.version} 版")
            await _PROJECTORS[envelope.event.type](tx, player_id, envelope.event)
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
            "MATCH ()-[r:HELD_BY {world: $pid}]->() DELETE r", pid=player_id, database_=self._db
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
    buckets: dict[str, list[str]] = {kind.value: [] for kind in _LABEL}
    for any_id in ids:
        prefix = any_id.split(":", 1)[0]
        if prefix in buckets:
            buckets[prefix].append(any_id)
    result = await tx.run(_Q_LABELS, buckets)
    return {r["id"]: r["name"] async for r in result}


async def _snapshot_tx(tx: _Tx, player_id: str) -> LocalSnapshot:
    head = await (await tx.run(_Q_PLAYER, pid=player_id)).single()
    if head is None:
        raise ProjectionError(f"图谱中没有 {player_id} 的覆盖层")
    loc = head["location"]
    subdued = set(head["subdued"])

    characters = []
    async for r in await tx.run(_Q_CHARACTERS, loc=loc["id"], pid=player_id, alive=CharacterStatus.ALIVE.value):
        c = r["c"]
        characters.append(CharacterView(
            id=c["id"], name=c["name"], aliases=tuple(c["aliases"] or ()), faction=c["faction"] or "",
            tier=c["tier"], disposition=c["disposition"], description=c["description"] or "",
            subdued=c["id"] in subdued, attitude=r["attitude"] or Attitude.NEUTRAL,
            skill_ids=r["skills"], bonds=[BondView(**b) for b in r["bonds"]],
        ))

    items = [
        ItemView(
            id=r["item"]["id"], name=r["item"]["name"], aliases=tuple(r["item"]["aliases"] or ()),
            kind=r["item"]["kind"] or "", description=r["item"]["description"] or "",
            holder_id=r["holder_id"], owner_id=r["owner_id"],
        )
        async for r in await tx.run(_Q_ITEMS, loc=loc["id"], pid=player_id, chars=[c.id for c in characters])
    ]

    inventory = [i.id for i in items if i.holder_id == player_id]
    wanted = sorted({*head["skills"], *(s for c in characters for s in c.skill_ids)})
    skills = [
        SkillView(
            id=a["id"], name=a["name"], aliases=tuple(a["aliases"] or ()), tier=a["tier"], kind=a["kind"] or "",
            faction=a["faction"] or "", description=a["description"] or "",
            prerequisites=Prerequisites.model_validate_json(a["prerequisites"]),
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
        player_skills=head["skills"],
    )
    return snapshot.model_copy(update={"labels": await _labels_tx(tx, sorted(snapshot.referenced_ids()))})
