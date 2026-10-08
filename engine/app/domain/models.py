"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / Field / model_validator，依赖 enum 的 StrEnum
[OUTPUT]: 对外提供 本体枚举 EntityKind / Tier / Disposition / CharacterStatus / Attitude / RelationKind / Transmission、
          entity_id() / kind_of() 标识工具、图谱节点 Location / Character / MartialArt / Item、前置条件 Prerequisites、
          关系边 CharacterRelation、原著蓝图 WorldBlueprint（引用完整性 + 前置无环 + 物品唯一归属的最后闸门）
[POS]: domain 的世界本体：原著解析管道的产物形状、Neo4j 图谱的节点与边的来源、裁决规则读取的事实；
       这里只有"世界是什么"，没有"世界此刻怎样"——后者属于事件流（events.py）与聚合根（aggregates.py）
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Iterable
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

NAME_CHARS = 24
DESC_CHARS = 200


# ============================================================
#  标识 —— 实体 id 自带种类前缀，持有者引用无需额外的类型字段
# ============================================================
class EntityKind(StrEnum):
    LOCATION = "loc"
    CHARACTER = "chr"
    MARTIAL_ART = "art"
    ITEM = "itm"
    PLAYER = "ply"


def entity_id(kind: EntityKind, name: str) -> str:
    """原著实体以"种类:正名"为 id：可读、确定、重复抽取同一人物得到同一 id。"""
    return f"{kind}:{name}"


def kind_of(any_id: str) -> EntityKind:
    return EntityKind(any_id.split(":", 1)[0])


# ============================================================
#  语义枚举 —— 只比高下、不做加减：境界是有序等级，不是数值属性
# ============================================================
class Tier(StrEnum):
    NONE = "不入流"
    THIRD = "三流"
    SECOND = "二流"
    FIRST = "一流"
    PEERLESS = "绝顶"

    @property
    def rank(self) -> int:
        return _TIER_RANK[self]


_TIER_RANK = {tier: rank for rank, tier in enumerate(Tier)}


class Disposition(StrEnum):
    """性情：决定以强凌弱时是留手还是下死手。"""

    MERCIFUL = "仁厚"
    NEUTRAL = "中庸"
    RUTHLESS = "狠辣"


class CharacterStatus(StrEnum):
    ALIVE = "健在"
    DECEASED = "已故"


class Attitude(StrEnum):
    """NPC 对玩家的态度。初见一律漠然；只有图谱推导出的事件能改变它。"""

    HOSTILE = "敌视"
    NEUTRAL = "漠然"
    FRIENDLY = "友善"


class RelationKind(StrEnum):
    KIN = "亲族"
    MENTOR = "师徒"
    FELLOW = "同门"
    SWORN = "结义"
    SERVES = "主仆"
    LOVER = "情侣"
    ENEMY = "仇敌"

    @property
    def is_bond(self) -> bool:
        """休戚与共的关系：一方受辱，另一方记恨。"""
        return self is not RelationKind.ENEMY


class Transmission(StrEnum):
    TEACHER = "师传"  # 须有通晓此功、且对你友善的人当面传授
    SELF = "自悟"  # 凭 items 中的典籍自行参悟，无需师父


# ============================================================
#  图谱节点
# ============================================================
class _Entity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str = Field(min_length=1, max_length=NAME_CHARS)
    aliases: tuple[str, ...] = ()
    description: str = Field(default="", max_length=DESC_CHARS)

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


class Location(_Entity):
    region: str = Field(default="", max_length=NAME_CHARS)
    exits: dict[str, str] = Field(default_factory=dict)  # 出入口映射：出口标签 → 目标地点 id（CONNECTS_TO 的来源）


class Character(_Entity):
    faction: str = Field(default="", max_length=NAME_CHARS)  # 阵营 / 门派（BELONGS_TO → Faction）
    status: CharacterStatus = CharacterStatus.ALIVE
    tier: Tier = Tier.NONE
    disposition: Disposition = Disposition.NEUTRAL
    location_id: str | None = None  # 开篇所在（LOCATED_IN）；None 表示不在任何场景中
    skills: tuple[str, ...] = ()  # 所会武学（KNOWS_SKILL）


class Prerequisites(BaseModel):
    """
    修习一门武学的前置依赖字典。宁严勿宽：每一条都是硬性条件，裁决规则逐条核验，缺一不可。
    sealed 表示原著中的前置无法在本体里落地（引用了不存在的典籍或地点、或前置成环）——宁可失传，不可滥传。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    skills: tuple[str, ...] = ()  # 须先通晓的武学（REQUIRES → MartialArt）
    items: tuple[str, ...] = ()  # 须随身携带之物：秘籍、图谱、信物（REQUIRES → Item）
    location_id: str | None = None  # 须身处此地方可修习（REQUIRES → Location）
    min_tier: Tier = Tier.NONE  # 修习者须已达到的境界
    conflicts: tuple[str, ...] = ()  # 身负其一即不可修习的相冲武学（CONFLICTS_WITH）
    transmission: Transmission = Transmission.TEACHER
    sealed: bool = False

    @model_validator(mode="after")
    def _self_study_needs_text(self) -> Self:
        if self.transmission is Transmission.SELF and not self.items and not self.sealed:
            raise ValueError("自悟的武学必须写明所凭典籍（items）")
        return self


class MartialArt(_Entity):
    faction: str = Field(default="", max_length=NAME_CHARS)
    kind: str = Field(default="", max_length=NAME_CHARS)  # 内功 / 掌法 / 指法 / 剑法 / 身法……只供叙事
    tier: Tier = Tier.THIRD
    prerequisites: Prerequisites = Prerequisites()


class Item(_Entity):
    """
    唯一性归属：一件物品至多一位物主（BELONGS_TO），且恰有一个物理所在。
    location_id 为空时由物主随身携带；不为空时静置于该地（物主可以不在身边——失物、藏宝）。
    """

    kind: str = Field(default="", max_length=NAME_CHARS)  # 兵器 / 秘籍 / 信物 / 丹药……只供叙事
    owner_id: str | None = None
    location_id: str | None = None

    @model_validator(mode="after")
    def _must_exist_somewhere(self) -> Self:
        if self.owner_id is None and self.location_id is None:
            raise ValueError(f"物品 {self.id} 既无物主也无所在，不存在于世界之中")
        return self

    @property
    def canon_holder(self) -> str:
        """开篇时此物的物理持有者：静置之地优先，否则是随身携带的物主。"""
        return self.location_id or self.owner_id  # type: ignore[return-value]  # 校验器保证二者其一非空


class CharacterRelation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    target_id: str
    kind: RelationKind
    note: str = Field(default="", max_length=DESC_CHARS)


# ============================================================
#  原著蓝图 —— 世界播种的中间表示：抽取（非确定）与写图（确定）之间的唯一契约
# ============================================================
class WorldBlueprint(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    locations: tuple[Location, ...] = ()
    characters: tuple[Character, ...] = ()
    martial_arts: tuple[MartialArt, ...] = ()
    items: tuple[Item, ...] = ()
    relations: tuple[CharacterRelation, ...] = ()

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        errors = _integrity_errors(self)
        if errors:
            raise ValueError("原著蓝图不自洽：\n" + "\n".join(errors))
        return self

    def entities(self) -> Iterable[_Entity]:
        yield from self.locations
        yield from self.characters
        yield from self.martial_arts
        yield from self.items


def _integrity_errors(bp: WorldBlueprint) -> list[str]:
    ids: dict[EntityKind, set[str]] = {kind: set() for kind in EntityKind}
    errors: list[str] = []
    for entity in bp.entities():
        kind = kind_of(entity.id)
        if entity.id in ids[kind]:
            errors.append(f"重复的实体 id：{entity.id}")
        ids[kind].add(entity.id)

    def need(ref: str | None, kind: EntityKind, where: str) -> None:
        if ref is not None and ref not in ids[kind]:
            errors.append(f"{where} 引用了不存在的{kind.name}：{ref}")

    for loc in bp.locations:
        for label, target in loc.exits.items():
            need(target, EntityKind.LOCATION, f"{loc.id} 的出口「{label}」")
    for ch in bp.characters:
        need(ch.location_id, EntityKind.LOCATION, ch.id)
        for skill in ch.skills:
            need(skill, EntityKind.MARTIAL_ART, ch.id)
    for art in bp.martial_arts:
        p = art.prerequisites
        for skill in (*p.skills, *p.conflicts):
            need(skill, EntityKind.MARTIAL_ART, art.id)
        for text in p.items:
            need(text, EntityKind.ITEM, art.id)
        need(p.location_id, EntityKind.LOCATION, art.id)
    for item in bp.items:
        need(item.owner_id, EntityKind.CHARACTER, item.id)
        need(item.location_id, EntityKind.LOCATION, item.id)
    for rel in bp.relations:
        need(rel.source_id, EntityKind.CHARACTER, "关系边")
        need(rel.target_id, EntityKind.CHARACTER, "关系边")

    cycle = prerequisite_cycle({art.id: art.prerequisites.skills for art in bp.martial_arts})
    if cycle:
        errors.append(f"武学前置成环：{' → '.join(cycle)}")
    return errors


def prerequisite_cycle(graph: dict[str, tuple[str, ...]]) -> list[str]:
    """前置依赖图里的任意一个环（深度优先三色标记）；无环返回空表。环中的武学永远学不成，必须在蓝图层面拒收。"""
    white, grey, black = 0, 1, 2
    color = dict.fromkeys(graph, white)
    stack: list[str] = []

    def visit(node: str) -> list[str]:
        color[node] = grey
        stack.append(node)
        for nxt in graph.get(node, ()):
            if color.get(nxt, black) == grey:
                return [*stack[stack.index(nxt) :], nxt]
            if color.get(nxt) == white and (found := visit(nxt)):
                return found
        stack.pop()
        color[node] = black
        return []

    for node in graph:
        if color[node] == white and (found := visit(node)):
            return found
    return []
