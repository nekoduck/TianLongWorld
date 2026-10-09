"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / Field；WorldBlueprint 仅作类型标注（models 运行期导入本模块，反向只在 TYPE_CHECKING 里，不成环）
[OUTPUT]: 对外提供 Persona（外显人设：好恶与心事，每条 ≤16 字、带出处）、FactUnlock（一条见闻解开的那条边：TEACHING / LEVERAGE / HAZARD / MOTIVE）、
          Fact（可经交涉、打探入账的见闻：≤40 字、主体、知情人、出处）、lore_integrity_errors(bp)（人设与见闻的蓝图闸门）
[POS]: domain 的「掌故」本体：原著蓝图里除了人、地、功、物与关系之外，玩家能察觉的脾性与能打听到的事。
       它们属于蓝图（离线由子代理撰写、经闸门入库、provenance 永远是推断），不是运行期的新端口；
       人设只收对玩家可见的外显部分（后文剧情另在 Character.foreshadow，不进任何提示词）；
       见闻的 unlock 必须落在蓝图已有的一条边上——它只是让玩家"知道"那条边，从不凭空造一条边。
       字数、出处格式由字段约束守住；"不得照抄原文 ≥16 字"需要原著全文，由离线 ingest 闸门守（阶段 B）
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from app.domain.models import Character, Item, WorldBlueprint

PERSONA_CHARS = 16
FACT_CHARS = 40

Trait = Annotated[str, Field(min_length=1, max_length=PERSONA_CHARS)]
Source = Annotated[str, Field(pattern=r"^(ev|chunk):\S+$")]  # "ev:<块号>"（抽取记录里的事件）或 "chunk:<块号>"（原文块）
UnlockKind = Literal["TEACHING", "LEVERAGE", "HAZARD", "MOTIVE"]


class _Lore(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Persona(_Lore):
    """外显人设：玩家看得出的好恶与心事。只给外显部分——后文的命运、未示人的秘密都不在这里。"""

    character_id: str
    likes: tuple[Trait, ...] = ()
    dislikes: tuple[Trait, ...] = ()
    worry: str = Field(default="", max_length=PERSONA_CHARS)  # 心事
    sources: tuple[Source, ...] = Field(min_length=1)


class FactUnlock(_Lore):
    """
    知道了这件事，能解开什么：
      TEACHING 某人身负某功（KNOWS_SKILL：主体之一会 target 这门武学）；
      LEVERAGE 借势（关系边：target 与某主体之间有 HAS_RELATION）；
      HAZARD 险物（target 是一件带 hazard 的物品）；
      MOTIVE 动机（target 有人设，见闻说中了他的心事或好恶）。
    """

    kind: UnlockKind
    target_id: str


class Fact(_Lore):
    """见闻：可经交涉、打探入账（FactLearned）的一件事。知情人之一在场，它才进局部快照。"""

    id: str = Field(pattern=r"^fact:\S+$")
    text: str = Field(min_length=1, max_length=FACT_CHARS)
    subject_ids: tuple[str, ...] = Field(min_length=1)
    knower_ids: tuple[str, ...] = Field(min_length=1)
    unlock: FactUnlock | None = None
    sources: tuple[Source, ...] = Field(min_length=1)


# ============================================================
#  闸门 —— 悬空引用、unlock 须落在一条边上、知情人须与主体有涉
# ============================================================
def lore_integrity_errors(bp: WorldBlueprint) -> list[str]:
    chars = {c.id: c for c in bp.characters}
    items = {i.id: i for i in bp.items}
    arts = {a.id for a in bp.martial_arts}
    known = {*chars, *items, *arts, *(loc.id for loc in bp.locations)}
    ties: dict[str, set[str]] = {}
    for rel in bp.relations:
        ties.setdefault(rel.source_id, set()).add(rel.target_id)
        ties.setdefault(rel.target_id, set()).add(rel.source_id)

    def anchors(subject: str) -> set[str]:
        """主体背后的"本人"：人物即其自身，物品是物主与开篇同处一地的人，武学是身负此功者，地点是开篇身在其中者。"""
        if subject in chars:
            return {subject}
        if subject in items:
            item = items[subject]
            near = {c.id for c in chars.values() if item.location_id and c.location_id == item.location_id}
            return near | ({item.owner_id} if item.owner_id else set())
        return {c.id for c in chars.values() if subject in c.skills or c.location_id == subject}

    errors: list[str] = []
    personas: set[str] = set()
    for persona in bp.personas:
        if persona.character_id not in chars:
            errors.append(f"人设引用了不存在的人物：{persona.character_id}")
        if persona.character_id in personas:
            errors.append(f"重复的人设：{persona.character_id}")
        personas.add(persona.character_id)

    facts: set[str] = set()
    for fact in bp.facts:
        where = f"见闻 {fact.id}"
        if fact.id in facts:
            errors.append(f"重复的见闻 id：{fact.id}")
        facts.add(fact.id)
        errors += [f"{where} 的主体不存在：{s}" for s in fact.subject_ids if s not in known]
        people = {a for s in fact.subject_ids for a in anchors(s)}
        for knower in fact.knower_ids:
            if knower not in chars:
                errors.append(f"{where} 的知情人不是蓝图里的人物：{knower}")
            elif not (knower in fact.subject_ids or knower in people or any(
                knower in ties.get(p, ()) or (chars[p].faction and chars[p].faction == chars[knower].faction)
                for p in people
            )):
                errors.append(f"{where} 的知情人 {knower} 与主体无涉（须是主体本人、同门或有关系边）")
        if fact.unlock is not None and not _lands(fact, fact.unlock, chars, items, arts, ties, personas):
            errors.append(f"{where} 的 unlock {fact.unlock.kind}→{fact.unlock.target_id} 落不到蓝图的边上")
    return errors


def _lands(
    fact: Fact, unlock: FactUnlock, chars: dict[str, Character], items: dict[str, Item], arts: set[str],
    ties: dict[str, set[str]], personas: set[str],
) -> bool:
    target = unlock.target_id
    match unlock.kind:
        case "TEACHING":  # KNOWS_SKILL：主体之一身负这门武学
            return target in arts and any(s in chars and target in chars[s].skills for s in fact.subject_ids)
        case "LEVERAGE":  # 关系边：target 与另一位主体之间有 HAS_RELATION
            return target in chars and any(s != target and s in ties.get(target, ()) for s in fact.subject_ids)
        case "HAZARD":  # 有毒之物
            return target in items and items[target].hazard is not None
        case "MOTIVE":  # 人设
            return target in personas
    return False
