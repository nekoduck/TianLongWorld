"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / Field / model_validator，依赖 enum 的 StrEnum，依赖 domain/lore 的 Persona / Fact / lore_integrity_errors
[OUTPUT]: 对外提供 本体枚举 EntityKind / Tier / Disposition / CharacterStatus / Attitude（五档人情阶梯，rank / step）/ Era（关系结于何时）/
          RelationKind / Transmission / Provenance、entity_id() / kind_of() 标识工具、
          图谱节点 Location / Character（true_name 本名为主键 + titles 称号 + aliases 别名 + foreshadow 后文剧情 + arrives_with 后来才到场）/
          MartialArt / Item（portable 可携、hazard 险性、use 用法 ItemUse、arrives_with）、Remedy 功效、
          武学的获取要求 Acquisition 与修炼要求 Practice、关系边 CharacterRelation（带 era）、
          原著蓝图 WorldBlueprint（含 personas / facts 掌故；引用完整性 + 根基无环 + 关系边无自环且一对人物至多一条 + 掌故闸门的最后一道关）
[POS]: domain 的世界本体：原著解析管道的产物形状、Neo4j 图谱的节点与边的来源、裁决规则读取的事实；
       这里只有"世界是什么"，没有"世界此刻怎样"——后者属于事件流（events.py）与聚合根（aggregates.py）。
       语义本体对齐：人物的主键是本名而不是江湖上最响的那个称呼（段延庆不叫「恶贯满盈」）；武学把"门径从何而来"（获取）
       与"根基够不够"（修炼）分开，入门与精进各守各的门；物品可以下落不明（孤儿），由播种期的自愈代理据常识安放并标明来历。
       人情是有序阶梯，只比 rank：戒备不是仇人，友善还不是心腹；关系边记下结于何时（era），只有开篇的羁绊牵动 T=0 的人情；
       foreshadow 只供离线审阅，绝不进任何提示词；带 arrives_with 的人与物 T=0 不在任何场景
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Iterable
from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.lore import Fact, Persona, lore_integrity_errors

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
    """
    NPC 对玩家的态度：一架五档的人情阶梯（敌视 −2 … 信赖 +2）。初见一律漠然；只有图谱推导出的事件能改变它。
    高下只比 rank、不比值：「戒备」不是仇人（调息、修习、脱身席只认敌视），「友善」还不是心腹（二流以上的武学只传信赖之人）。
    原有三值的字面不动——账本里的旧 RelationChanged 照读。
    """

    HOSTILE = "敌视"
    WARY = "戒备"
    NEUTRAL = "漠然"
    FRIENDLY = "友善"
    TRUSTED = "信赖"

    @property
    def rank(self) -> int:
        return _ATTITUDE_RANK[self]

    def step(self, delta: int) -> "Attitude":
        """沿阶梯移动 delta 档，钳在 [敌视, 信赖] 之内。"""
        return _ATTITUDE_BY_RANK[max(-2, min(2, self.rank + delta))]


_ATTITUDE_RANK = {attitude: rank for rank, attitude in enumerate(Attitude, start=-2)}
_ATTITUDE_BY_RANK = {rank: attitude for attitude, rank in _ATTITUDE_RANK.items()}


class Era(StrEnum):
    """一条关系结于何时：开篇（T=0 已有）、将至（开篇之后旋即结下）、后文（远在后头）。只有开篇的羁绊牵动 T=0 的人情。"""

    OPENING = "开篇"
    IMMINENT = "将至"
    LATER = "后文"


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


class Provenance(StrEnum):
    """一条事实的来历：原著明写，还是自愈代理据常识补全。推断可审阅、可推翻，永远与原著分得清。"""

    CANON = "原著"
    INFERRED = "推断"


# ============================================================
#  图谱节点
# ============================================================
class _Entity(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    aliases: tuple[str, ...] = ()
    description: str = Field(default="", max_length=DESC_CHARS)


class _Named(_Entity):
    """地点、武学、物品：一个名字就是正名。"""

    name: str = Field(min_length=1, max_length=NAME_CHARS)

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


class Location(_Named):
    region: str = Field(default="", max_length=NAME_CHARS)
    exits: dict[str, str] = Field(default_factory=dict)  # 出入口映射：出口标签 → 目标地点 id（CONNECTS_TO 的来源）


class Character(_Entity):
    """
    人物的三种名字各司其职：true_name 是本名（姓名或法号），也是主键——id 恒为 chr:{true_name}；
    titles 是江湖称号与绰号（「恶贯满盈」「南海鳄神」）；aliases 是化名、旧称、封号（「延庆太子」）。
    三者都能被玩家用来指称此人，但只有本名决定"他是谁"：称呼再响，也不能篡位成主键。
    """

    true_name: str = Field(min_length=1, max_length=NAME_CHARS)
    titles: tuple[str, ...] = ()
    faction: str = Field(default="", max_length=NAME_CHARS)  # 阵营 / 门派（BELONGS_TO → Faction）
    status: CharacterStatus = CharacterStatus.ALIVE
    tier: Tier = Tier.NONE
    disposition: Disposition = Disposition.NEUTRAL
    location_id: str | None = None  # T=0 时所在（LOCATED_IN）；None 表示不在任何场景中
    skills: tuple[str, ...] = ()  # T=0 时已身负的武学（KNOWS_SKILL）
    foreshadow: str = Field(default="", max_length=DESC_CHARS)  # 从描述里拆出的后文剧情：只供离线审阅，绝不进任何提示词
    arrives_with: str | None = None  # 后来才到场（预兆 id，P2 由世界事件带上场）：非空者 T=0 不在任何场景

    @property
    def name(self) -> str:
        """显示名即本名：与地点、武学、物品一样有 name，调用方无需区分实体种类（里氏替换）。"""
        return self.true_name

    @property
    def names(self) -> tuple[str, ...]:
        return (self.true_name, *self.titles, *self.aliases)


# ============================================================
#  武学的两道门 —— 获取要求管"门径从何而来"，修炼要求管"根基够不够"
# ============================================================
class _Requirement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Acquisition(_Requirement):
    """
    获取要求：得其门径（入门）的条件。宁严勿宽，逐条核验、缺一不可。
    sealed 表示原著中的门径无法在本体里落地（引用了不存在的典籍或地点、或根基成环）——宁可失传，不可滥传。
    """

    transmission: Transmission = Transmission.TEACHER
    items: tuple[str, ...] = ()  # 自悟所凭、或入门须持之物：秘籍、图谱、信物（REQUIRES {as: item} → Item）
    location_id: str | None = None  # 须身处此地方得门径：典籍所藏、传功之所（REQUIRES {as: place} → Location）
    sealed: bool = False

    @model_validator(mode="after")
    def _self_study_needs_text(self) -> Self:
        if self.transmission is Transmission.SELF and not self.items and not self.sealed:
            raise ValueError("自悟的武学必须写明所凭典籍（items）")
        return self


class Practice(_Requirement):
    """修炼要求：入门那一刻与此后每一次精进都须满足的根基。"""

    skills: tuple[str, ...] = ()  # 须先练出根基的武学（REQUIRES {as: skill} → MartialArt）；火候门槛由 progression 定
    min_tier: Tier = Tier.NONE  # 修炼者须已达到的境界
    conflicts: tuple[str, ...] = ()  # 身负其一即不可修炼的相冲武学（CONFLICTS_WITH）


class MartialArt(_Named):
    faction: str = Field(default="", max_length=NAME_CHARS)
    kind: str = Field(default="", max_length=NAME_CHARS)  # 内功 / 掌法 / 指法 / 剑法 / 身法……只供叙事
    tier: Tier = Tier.THIRD  # 此功练到融会贯通时的境界上限；火候不到则打折扣（progression.effective_tier）
    acquisition: Acquisition = Acquisition()
    practice: Practice = Practice()


Remedy = Literal["疗伤", "解毒"]  # 用法的功效：疗伤回气血，解毒（P2 起有中毒之状）


class ItemUse(BaseModel):
    """随身之物的用法：服药、敷药。potency 是药力的档次（1~3），折算多少气血由规则定。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    effect: Remedy
    potency: int = Field(default=1, ge=1, le=3)


class Item(_Named):
    """
    唯一性归属：一件物品至多一位物主（BELONGS_TO），至多一个物理所在（LOCATED_IN）。
    location_id 为空时由物主随身携带；不为空时静置于该地（物主可以不在身边——失物、藏宝）。
    两者皆空即下落不明（孤儿）：原著提到它、却没写它在哪——它存在于本体，却不在任何场景中，
    直到自愈代理（infrastructure/graph_linter.py）据常识为它安放一处，并把 provenance 记为推断。
    """

    kind: str = Field(default="", max_length=NAME_CHARS)  # 兵器 / 秘籍 / 信物 / 丹药……只供叙事
    owner_id: str | None = None
    location_id: str | None = None
    provenance: Provenance = Provenance.CANON  # 物主与所在的来历
    portable: bool = True  # 不可携带（崖壁、玉璧、活毒物、蒲团）：拾取一律驳回
    hazard: str | None = Field(default=None, max_length=NAME_CHARS)  # 有毒等险性：取到手即受伤
    use: ItemUse | None = None  # 可服可敷之物的用法；None 即不能"使用"
    arrives_with: str | None = None  # 后来才出现（预兆 id）：非空者 T=0 不在任何场景

    @property
    def lost(self) -> bool:
        return self.owner_id is None and self.location_id is None

    @property
    def canon_holder(self) -> str | None:
        """T=0 时此物的物理持有者：静置之地优先，否则是随身携带的物主；下落不明者为 None。"""
        return self.location_id or self.owner_id


class CharacterRelation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str
    target_id: str
    kind: RelationKind
    note: str = Field(default="", max_length=DESC_CHARS)
    era: Era = Era.OPENING  # 审计之前一律视为开篇；审计把后文才结下的关系标出来


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
    personas: tuple[Persona, ...] = ()  # 外显人设（lore.py）
    facts: tuple[Fact, ...] = ()  # 可打探入账的见闻（lore.py）

    @model_validator(mode="after")
    def _integrity(self) -> Self:
        errors = [*_integrity_errors(self), *lore_integrity_errors(self)]
        if errors:
            raise ValueError("原著蓝图不自洽：\n" + "\n".join(errors))
        return self

    def entities(self) -> Iterable[Location | Character | MartialArt | Item]:
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
    for ch in bp.characters:
        if ch.id != entity_id(EntityKind.CHARACTER, ch.true_name):
            errors.append(f"人物主键须是本名：{ch.id} ≠ chr:{ch.true_name}")

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
        for skill in (*art.practice.skills, *art.practice.conflicts):
            need(skill, EntityKind.MARTIAL_ART, art.id)
        for text in art.acquisition.items:
            need(text, EntityKind.ITEM, art.id)
        need(art.acquisition.location_id, EntityKind.LOCATION, art.id)
    for item in bp.items:
        need(item.owner_id, EntityKind.CHARACTER, item.id)
        need(item.location_id, EntityKind.LOCATION, item.id)
    pairs: set[frozenset[str]] = set()
    for rel in bp.relations:
        need(rel.source_id, EntityKind.CHARACTER, "关系边")
        need(rel.target_id, EntityKind.CHARACTER, "关系边")
        pair = frozenset((rel.source_id, rel.target_id))
        if len(pair) == 1:
            errors.append(f"关系边自环：{rel.source_id}")
        elif pair in pairs:
            errors.append(f"同一对人物至多一条关系边：{rel.source_id} — {rel.target_id}")
        pairs.add(pair)

    cycle = prerequisite_cycle({art.id: art.practice.skills for art in bp.martial_arts})
    if cycle:
        errors.append(f"武学根基成环：{' → '.join(cycle)}")
    return errors


def prerequisite_cycle(graph: dict[str, tuple[str, ...]]) -> list[str]:
    """根基依赖图里的任意一个环（深度优先三色标记）；无环返回空表。环中的武学永远学不成，必须在蓝图层面拒收。"""
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
