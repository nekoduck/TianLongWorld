"""
[INPUT]: 依赖 pydantic v2 的 BaseModel，依赖 domain/models 的 Tier / Disposition / Attitude / RelationKind / Prerequisites
[OUTPUT]: 对外提供 局部真理快照 LocalSnapshot（集合字段构造即按固定键排序、referenced_ids 列出名称表须覆盖的 id）及其视图 LocationView / ExitView / CharacterView / BondView / ItemView / SkillView
[POS]: domain 的读模型（CQRS 查询侧）：图谱投影在"玩家此刻所在之处"的一个切片。
       裁决规则只凭它判定物理事实（出口、在场者、物品所在），叙事大模型只凭它落笔（Hard Prompt），选项生成器只遍历它的合法边；
       快照之外的世界对这一回合不存在——这是杜绝幻觉的边界
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from app.domain.models import Attitude, Disposition, Prerequisites, RelationKind, Tier


def _get(x: Any, name: str) -> Any:
    return x[name] if isinstance(x, dict) else getattr(x, name)


def _canonical(data: Any, order: dict[str, Callable[[Any], Any]]) -> Any:
    """集合字段恒按固定键排序：同一个世界状态，无论出自哪个图谱实现，得到逐字段相等的快照。"""
    if isinstance(data, dict):
        data = dict(data)
        for key, sort_key in order.items():
            if key in data:
                data[key] = tuple(sorted(data[key], key=sort_key))
    return data


class _View(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class _Named(_View):
    id: str
    name: str
    aliases: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


class LocationView(_View):
    id: str
    name: str
    region: str = ""
    description: str = ""


class ExitView(_View):
    label: str
    to_id: str
    to_name: str

    @property
    def names(self) -> tuple[str, ...]:
        return (self.label, self.to_name)


class BondView(_View):
    other_id: str
    kind: RelationKind


class SkillView(_Named):
    tier: Tier
    kind: str = ""
    faction: str = ""
    description: str = ""
    prerequisites: Prerequisites = Prerequisites()


class ItemView(_Named):
    kind: str = ""
    description: str = ""
    holder_id: str  # 本世界中此刻的持有者（loc: 地上 / chr: 某人身上 / ply: 玩家行囊）
    owner_id: str | None = None  # 原著物主（BELONGS_TO），物归原主的依据


class CharacterView(_Named):
    faction: str = ""
    tier: Tier
    disposition: Disposition
    description: str = ""
    subdued: bool = False  # 本世界中已被玩家制住
    attitude: Attitude = Attitude.NEUTRAL  # 本世界中对玩家的态度
    skill_ids: tuple[str, ...] = ()
    bonds: tuple[BondView, ...] = ()  # HAS_RELATION（无向）

    @model_validator(mode="before")
    @classmethod
    def _order(cls, data: Any) -> Any:
        return _canonical(data, {"skill_ids": str, "bonds": lambda b: (_get(b, "other_id"), _get(b, "kind"))})

    def bond_with(self, other_id: str) -> RelationKind | None:
        return next((b.kind for b in self.bonds if b.other_id == other_id), None)


class LocalSnapshot(_View):
    """
    version 是投影检查点：等于玩家事件流的版本时，快照与真相一致。
    labels 覆盖快照里出现的每一个 id（含前置条件引用的远方地点与典籍），渲染文字时无需再查图。
    """

    player_id: str
    player_name: str
    alive: bool
    version: int
    location: LocationView
    exits: tuple[ExitView, ...] = ()
    characters: tuple[CharacterView, ...] = ()  # 在场之人（已故者不在场）
    items: tuple[ItemView, ...] = ()  # 可见之物：地上、在场者身上、玩家行囊
    skills: tuple[SkillView, ...] = ()  # 此情此景可知的武学：玩家所会 ∪ 在场者所会 ∪ 行囊典籍所载
    player_skills: tuple[str, ...] = ()
    labels: dict[str, str] = {}

    @model_validator(mode="before")
    @classmethod
    def _order(cls, data: Any) -> Any:
        def by_id(x: Any) -> Any:
            return _get(x, "id")

        return _canonical(data, {
            "exits": lambda e: (_get(e, "to_id"), _get(e, "label")),
            "characters": by_id, "items": by_id, "skills": by_id, "player_skills": str,
        })

    def referenced_ids(self) -> set[str]:
        """快照里出现的全部 id（含前置条件指向的远方地点与典籍、物主、羁绊另一端）：labels 必须覆盖它们。"""
        ids = {self.location.id, self.player_id, *self.player_skills}
        ids |= {e.to_id for e in self.exits} | {i.id for i in self.items} | {s.id for s in self.skills}
        ids |= {i.owner_id for i in self.items if i.owner_id}
        for c in self.characters:
            ids |= {c.id, *c.skill_ids, *(b.other_id for b in c.bonds)}
        for s in self.skills:
            p = s.prerequisites
            ids |= {*p.skills, *p.items, *p.conflicts, *([p.location_id] if p.location_id else [])}
        return ids

    def character(self, character_id: str) -> CharacterView | None:
        return next((c for c in self.characters if c.id == character_id), None)

    def skill(self, skill_id: str) -> SkillView | None:
        return next((s for s in self.skills if s.id == skill_id), None)

    def item(self, item_id: str) -> ItemView | None:
        return next((i for i in self.items if i.id == item_id), None)

    def items_of(self, holder_id: str) -> tuple[ItemView, ...]:
        return tuple(i for i in self.items if i.holder_id == holder_id)

    @property
    def ground_items(self) -> tuple[ItemView, ...]:
        return self.items_of(self.location.id)

    @property
    def inventory(self) -> tuple[ItemView, ...]:
        return self.items_of(self.player_id)

    @property
    def known_skills(self) -> tuple[SkillView, ...]:
        return tuple(s for s in self.skills if s.id in self.player_skills)

    def label(self, any_id: str | None) -> str:
        if any_id is None:
            return ""
        return self.labels.get(any_id) or any_id.split(":", 1)[-1]
