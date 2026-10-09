"""
[INPUT]: 依赖 pydantic v2 的 BaseModel，依赖 domain/models 的 Tier / Disposition / Attitude / Era / RelationKind / Acquisition / Practice / ItemUse，
         依赖 domain/lore 的 FactUnlock，依赖 domain/progression 的 MAX_HP / Mastery / Vitality / mastery_of / vitality
[OUTPUT]: 对外提供 局部真理快照 LocalSnapshot（集合字段构造即按固定键排序、referenced_ids 列出名称表须覆盖的 id、玩家的熟练度 / 悟性 / 气血
          及现算的 mastery / vitality、facts 知情人在场的见闻）及其视图 LocationView / ExitView（hostile_ahead 去处有仇人）/
          CharacterView（含称号、persona 外显人设）/ BondView（era 结于何时、lead 本人是关系的上首）/ ItemView（portable / hazard / use）/
          SkillView（获取要求 + 修炼要求）/ PersonaView / FactView
[POS]: domain 的读模型（CQRS 查询侧）：图谱投影在"玩家此刻所在之处"的一个切片。
       裁决规则只凭它判定物理事实（出口、在场者、物品所在），叙事大模型只凭它落笔（Hard Prompt），选项生成器只遍历它的合法边；
       快照之外的世界对这一回合不存在——这是杜绝幻觉的边界。
       P1 的视图字段（era / lead / hostile_ahead / portable / hazard / use / persona / facts）都有缺省值：图谱实现不填也照样构造，
       阶段 B 两套图谱同时填上；人设只给外显部分，后文剧情（foreshadow）永远不进快照
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from app.domain.lore import FactUnlock
from app.domain.models import Acquisition, Attitude, Disposition, Era, ItemUse, Practice, RelationKind, Tier
from app.domain.progression import MAX_HP, Mastery, Vitality, mastery_of, vitality


def _get(x: Any, name: str, default: Any = None) -> Any:
    """视图或原始字典的字段；缺省的字段（图谱实现没填的 P1 新字段）取视图的缺省值，排序键因此与实现无关。"""
    return x.get(name, default) if isinstance(x, dict) else getattr(x, name, default)


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
    hostile_ahead: bool = False  # 去处此刻站着对玩家敌视的在场者

    @property
    def names(self) -> tuple[str, ...]:
        return (self.label, self.to_name)


class BondView(_View):
    other_id: str
    kind: RelationKind
    era: Era = Era.OPENING  # 这段关系结于何时：只有开篇的羁绊牵动 T=0 的人情
    lead: bool = False  # 本人是关系的上首（source 一方：师父、兄长）


class SkillView(_Named):
    tier: Tier
    kind: str = ""
    faction: str = ""
    description: str = ""
    acquisition: Acquisition = Acquisition()
    practice: Practice = Practice()


class ItemView(_Named):
    kind: str = ""
    description: str = ""
    holder_id: str  # 本世界中此刻的持有者（loc: 地上 / chr: 某人身上 / ply: 玩家行囊）
    owner_id: str | None = None  # 原著物主（BELONGS_TO），物归原主的依据
    portable: bool = True  # 不可携带之物拾取一律驳回
    hazard: str | None = None  # 有毒等险性：取到手即受伤
    use: ItemUse | None = None  # 可服可敷之物的用法


class PersonaView(_View):
    """外显人设：玩家看得出的好恶与心事。"""

    likes: tuple[str, ...] = ()
    dislikes: tuple[str, ...] = ()
    worry: str = ""


class FactView(_View):
    """知情人之一在场的见闻：可经交涉、打探入账。"""

    id: str
    text: str
    subject_ids: tuple[str, ...] = ()
    knower_ids: tuple[str, ...] = ()
    unlock: FactUnlock | None = None

    @model_validator(mode="before")
    @classmethod
    def _order(cls, data: Any) -> Any:
        return _canonical(data, {"subject_ids": str, "knower_ids": str})


class CharacterView(_Named):
    """name 是本名；titles 是江湖称号——玩家喊「恶贯满盈」也能落到段延庆身上。"""

    titles: tuple[str, ...] = ()
    faction: str = ""
    tier: Tier
    disposition: Disposition
    description: str = ""
    subdued: bool = False  # 本世界中已被玩家制住
    attitude: Attitude = Attitude.NEUTRAL  # 本世界中对玩家的态度
    skill_ids: tuple[str, ...] = ()
    bonds: tuple[BondView, ...] = ()  # HAS_RELATION（无向；lead 标明本人是否上首）
    persona: PersonaView | None = None

    @model_validator(mode="before")
    @classmethod
    def _order(cls, data: Any) -> Any:
        return _canonical(data, {
            "skill_ids": str,
            "bonds": lambda b: (
                _get(b, "other_id"), _get(b, "kind"), bool(_get(b, "lead", False)), str(_get(b, "era", Era.OPENING))
            ),
        })

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.titles, *self.aliases)

    def bond_with(self, other_id: str) -> RelationKind | None:
        return next((b.kind for b in self.bonds if b.other_id == other_id), None)


class LocalSnapshot(_View):
    """
    version 是投影检查点：等于玩家事件流的版本时，快照与真相一致。
    labels 覆盖快照里出现的每一个 id（含获取 / 修炼要求引用的远方地点与典籍），渲染文字时无需再查图。
    玩家的渐进式状态只投影原始数值（熟练度之和、悟性、气血），火候与伤势与聚合根经同一套 progression 现算——两边不可能各说各话。
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
    player_practice: dict[str, int] = {}  # 武学 → 熟练度之和
    player_aptitude: float = 1.0
    player_hp: int = MAX_HP
    facts: tuple[FactView, ...] = ()  # 知情人之一在场的见闻
    labels: dict[str, str] = {}

    @model_validator(mode="before")
    @classmethod
    def _order(cls, data: Any) -> Any:
        def by_id(x: Any) -> Any:
            return _get(x, "id")

        return _canonical(data, {
            "exits": lambda e: (_get(e, "to_id"), _get(e, "label")),
            "characters": by_id, "items": by_id, "skills": by_id, "facts": by_id,
        })

    @property
    def player_skills(self) -> tuple[str, ...]:
        """玩家已入门的武学，按 id 排序。"""
        return tuple(sorted(skill for skill, points in self.player_practice.items() if points > 0))

    def mastery(self, skill_id: str) -> Mastery | None:
        return mastery_of(self.player_practice.get(skill_id, 0), self.player_aptitude)

    @property
    def vitality(self) -> Vitality:
        return vitality(self.player_hp)

    def referenced_ids(self) -> set[str]:
        """快照里出现的全部 id（含前置条件指向的远方地点与典籍、物主、羁绊另一端）：labels 必须覆盖它们。"""
        ids = {self.location.id, self.player_id, *self.player_skills}
        ids |= {e.to_id for e in self.exits} | {i.id for i in self.items} | {s.id for s in self.skills}
        ids |= {i.owner_id for i in self.items if i.owner_id}
        for c in self.characters:
            ids |= {c.id, *c.skill_ids, *(b.other_id for b in c.bonds)}
        for s in self.skills:
            a, p = s.acquisition, s.practice
            ids |= {*p.skills, *a.items, *p.conflicts, *([a.location_id] if a.location_id else [])}
        for f in self.facts:  # 见闻本身也要有名：labels 把 fact:<slug> 映射为见闻正文，白描与记忆才不会露出 slug
            ids |= {f.id, *f.subject_ids, *f.knower_ids, *([f.unlock.target_id] if f.unlock else [])}
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
