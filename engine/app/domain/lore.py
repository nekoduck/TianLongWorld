"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / Field；WorldBlueprint 仅作类型标注，Era 在闸门函数体内调用时才取（models 运行期导入本模块，反向只在 TYPE_CHECKING 与函数体里，不成环）
[OUTPUT]: 对外提供 Persona（外显人设：好恶与心事，每条 ≤16 字、带出处）、FactUnlock（一条见闻解开的那条边：TEACHING / LEVERAGE / HAZARD / MOTIVE）、
          Fact（可经交涉、打探入账的见闻：≤40 字、主体、知情人、出处）、lore_integrity_errors(bp)（人设与见闻的蓝图闸门：
          悬空引用、重复的人设与见闻、主体或知情人有重复、知情人与主体无涉、unlock 落不到边上；关系边只认开篇的，后来才到场的物品不作主体、不作险物目标；
          人群须落在蓝图的地点上、id 即 swm:{名称}、门派须是蓝图里有人的门派）、
          SwarmNode（人群：一处地方成群出现、没有名姓的人——人数、惊惧阈值、平日在做什么、出处）、
          世界本份的排异词汇——Trigger（摩擦的触发：撰写的十种 生面孔 / 擅入 / 持械 / 血污 / 恃强 / 喧哗 / 花言 / 窥探 / 动武 / 偷采 与图谱推出的七种
          失物 / 信物 / 旧怨 / 闻讯 / 寻踪 / 清场 / 波及；.authored / .deed）、Stance（NPC 的反应：盘问 / 喝止 / 敌意，.rank 由轻到重）、
          Aversion（人设的排异区：trigger、stance、basis 须是此人某条 dislikes 或 worry 的原文、擅入须带地点、出处）与 Persona.aversions（至多 AVERSIONS_MAX 条）
[POS]: domain 的「掌故」本体：原著蓝图里除了人、地、功、物与关系之外，玩家能察觉的脾性与能打听到的事。
       它们属于蓝图（离线由子代理撰写、经闸门入库、provenance 永远是推断），不是运行期的新端口；
       人设只收对玩家可见的外显部分（后文剧情另在 Character.foreshadow，不进任何提示词）；
       见闻的 unlock 必须落在蓝图已有的一条边上——它只是让玩家"知道"那条边，从不凭空造一条边。
       字数、出处格式由字段约束守住；"不得照抄原文 ≥16 字"需要原著全文，由离线 ingest 闸门守（阶段 B）。
       人群（SwarmNode）同属掌故：原著写了他们在场，却没给名姓——他们不能攀谈、不能交手，只目睹、传话、受惊溃散（世界心跳）。
       排异区（Aversion）是人设的一部分：「多疑的人看见生面孔」「外人擅入禁地」——它只把人设里已有的一条好恶落成可检验的触发（basis 必须是原文），
       由子代理撰写、经 friction 闸门入库；图谱推出的触发（失物认主、亲故认出信物、仇人相见、闻讯认人、按议程签名寻人、局势清场与波及）不需撰写，
       由 domain/friction 按图谱现算。反应只有三档，落成悬在那人身上的时钟（盘问 → 疑心、喝止 / 敌意 → 敌意）
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from app.domain.models import Character, Item, WorldBlueprint

PERSONA_CHARS = 16
FACT_CHARS = 40
SWARM_CHARS = 12
AVERSIONS_MAX = 4  # 一个人的排异区至多四条：人设里最扎眼的几条好恶

Trait = Annotated[str, Field(min_length=1, max_length=PERSONA_CHARS)]
Source = Annotated[str, Field(pattern=r"^(ev|chunk):\S+$")]  # "ev:<块号>"（抽取记录里的事件）或 "chunk:<块号>"（原文块）
UnlockKind = Literal["TEACHING", "LEVERAGE", "HAZARD", "MOTIVE"]


class _Lore(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Trigger(StrEnum):
    """
    世界本份的摩擦触发。前十种由子代理照人设撰写（排异区），后七种由图谱推出、不需撰写：
      在场即触发：生面孔（他从没见过你）、擅入（你身在他的禁地）、持械（你手里有兵刃）、血污（你一身是血）；
      当着他的面做了什么：恃强（欺凌弱者）、喧哗（威逼喝问）、花言（言辞讨好他）、窥探（打探他或他的人）、动武（任何交手）、偷采（在他的地头拾取东西）；
      图谱推出：失物（他认出你身上是他丢的东西）、信物（他认出你身上是他亲故之物）、旧怨（敌视你的人认出了你）、闻讯（传到此地的消息里那人的模样就是你）、
      寻踪（他的议程签名对上了你）、清场（局势推进到你所在之处）、波及（别人的恶斗溅到你身上）。
    """

    STRANGER = "生面孔"
    TRESPASS = "擅入"
    ARMED = "持械"
    BLOODIED = "血污"
    BULLY = "恃强"
    CLAMOR = "喧哗"
    FLATTERY = "花言"
    PRYING = "窥探"
    BRAWL = "动武"
    POACH = "偷采"
    OWN_ITEM = "失物"
    TOKEN = "信物"
    GRUDGE = "旧怨"
    RUMOR = "闻讯"
    HUNT = "寻踪"
    SWEEP = "清场"
    SPILL = "波及"

    @property
    def authored(self) -> bool:
        """可以写进排异区的触发（其余由图谱推出）。"""
        return self in _AUTHORED

    @property
    def deed(self) -> bool:
        """当着他的面做了什么才触发（其余在场即触发）。"""
        return self in _DEEDS


_AUTHORED = frozenset({
    Trigger.STRANGER, Trigger.TRESPASS, Trigger.ARMED, Trigger.BLOODIED, Trigger.BULLY,
    Trigger.CLAMOR, Trigger.FLATTERY, Trigger.PRYING, Trigger.BRAWL, Trigger.POACH,
})
_DEEDS = frozenset({Trigger.BULLY, Trigger.CLAMOR, Trigger.FLATTERY, Trigger.PRYING, Trigger.BRAWL, Trigger.POACH})
PLACED = frozenset({Trigger.TRESPASS, Trigger.POACH})  # 可带地点：擅入必带，偷采不带即任何地方


class Stance(StrEnum):
    """NPC 的反应，由轻到重：盘问（问你的来历）/ 喝止（令你住手或离开）/ 敌意（起了杀心）。"""

    INTERROGATE = "盘问"
    WARN = "喝止"
    HOSTILE = "敌意"

    @property
    def rank(self) -> int:
        return (Stance.INTERROGATE, Stance.WARN, Stance.HOSTILE).index(self) + 1


class Aversion(_Lore):
    """
    排异区：人设里一条好恶落成的可检验触发。basis 必须是此人人设某条 dislikes 或 worry 的原文——它不发明新脾气，
    只说明那条脾气在什么情形下会被触犯、触犯了是盘问、喝止还是起杀心。擅入必带地点（他的禁地），偷采可带地点（他的地头）。
    """

    trigger: Trigger
    stance: Stance
    basis: str = Field(min_length=1, max_length=PERSONA_CHARS)
    location_id: str | None = None
    sources: tuple[Source, ...] = Field(min_length=1)


class Persona(_Lore):
    """外显人设：玩家看得出的好恶与心事。只给外显部分——后文的命运、未示人的秘密都不在这里。"""

    character_id: str
    likes: tuple[Trait, ...] = ()
    dislikes: tuple[Trait, ...] = ()
    worry: str = Field(default="", max_length=PERSONA_CHARS)  # 心事
    sources: tuple[Source, ...] = Field(min_length=1)
    aversions: tuple[Aversion, ...] = Field(default=(), max_length=AVERSIONS_MAX)  # 排异区（friction 闸门入库）


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


class SwarmNode(_Lore):
    """
    人群：一处地方成群出现、没有名姓的人（东宗弟子、观礼宾客）。他们不是人物——不能攀谈、不能交手、不进人情——却是这处地方的一部分：
    他们目睹，有人群在场的消息走得快；一举的烈度高过他们的惊惧阈值，人群即溃散逃离（domain/heartbeat）。
    current_state 不在这里：平日在做什么（routine）是正典，溃散与否是平行世界的事，由快照现算（SwarmView）。
    """

    id: str = Field(pattern=r"^swm:.+")
    name: str = Field(min_length=1, max_length=SWARM_CHARS)
    location_id: str
    size: int = Field(ge=3, le=500)  # 约莫多少人：只供叙事与烈度的感受，不做加减
    panic_threshold: int = Field(ge=1, le=10)  # 惊惧阈值：一举的烈度（0~10）高过它，人群即溃散逃离
    routine: str = Field(min_length=1, max_length=SWARM_CHARS)  # 平日此刻在做什么：「围观比剑」
    faction: str = Field(default="", max_length=24)
    sources: tuple[Source, ...] = Field(min_length=1)


# ============================================================
#  闸门 —— 悬空引用、unlock 须落在一条边上、知情人须与主体有涉
# ============================================================
def lore_integrity_errors(bp: WorldBlueprint) -> list[str]:
    from app.domain.models import Era  # models 运行期导入本模块：反向只能在调用时取

    chars = {c.id: c for c in bp.characters}
    items = {i.id: i for i in bp.items}
    arts = {a.id for a in bp.martial_arts}
    known = {*chars, *items, *arts, *(loc.id for loc in bp.locations)}
    ties: dict[str, set[str]] = {}
    for rel in bp.relations:
        if rel.era is not Era.OPENING:  # 将至、后文才结下的关系在 T=0 还不存在：既不让人知情，也不作把柄
            continue
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
    places = {loc.id for loc in bp.locations}
    for persona in bp.personas:
        if persona.character_id not in chars:
            errors.append(f"人设引用了不存在的人物：{persona.character_id}")
        if persona.character_id in personas:
            errors.append(f"重复的人设：{persona.character_id}")
        personas.add(persona.character_id)
        errors += _aversion_errors(persona, places)

    facts: set[str] = set()
    for fact in bp.facts:
        where = f"见闻 {fact.id}"
        if fact.id in facts:
            errors.append(f"重复的见闻 id：{fact.id}")
        facts.add(fact.id)
        for field, ids in (("主体", fact.subject_ids), ("知情人", fact.knower_ids)):
            if len(set(ids)) != len(ids):  # 两套图谱对重复的处理不同（内存照留、Neo4j 合并）：从源头拒收，快照才同构
                errors.append(f"{where} 的{field}有重复：{'、'.join(sorted({i for i in ids if ids.count(i) > 1}))}")
        errors += [f"{where} 的主体不存在：{s}" for s in fact.subject_ids if s not in known]
        errors += [f"{where} 的主体 {s} 后来才到场（{items[s].arrives_with}）" for s in fact.subject_ids
                   if s in items and items[s].arrives_with]
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

    factions = {c.faction for c in chars.values() if c.faction}
    swarms: set[str] = set()
    for swarm in bp.swarms:
        where = f"人群 {swarm.id}"
        if swarm.id in swarms:
            errors.append(f"重复的人群 id：{swarm.id}")
        swarms.add(swarm.id)
        if swarm.id != f"swm:{swarm.name}":
            errors.append(f"{where} 的 id 须是 swm:{swarm.name}")
        if swarm.location_id not in places:
            errors.append(f"{where} 落在不存在的地点：{swarm.location_id}")
        if swarm.faction and swarm.faction not in factions:
            errors.append(f"{where} 的门派「{swarm.faction}」在蓝图里没有一个人")
    return errors


def _aversion_errors(persona: Persona, places: set[str]) -> list[str]:
    """排异区的闸门：只收可撰写的触发、basis 须是人设原文、擅入必带地点而其余（偷采除外）不带、地点须在蓝图里、同一触发同一地点不重复。"""
    where = f"人设 {persona.character_id} 的排异区"
    grounds = {*persona.dislikes, *([persona.worry] if persona.worry else [])}
    errors: list[str] = []
    seen: set[tuple[Trigger, str | None]] = set()
    for a in persona.aversions:
        if not a.trigger.authored:
            errors.append(f"{where}：「{a.trigger}」由图谱推出，不可撰写")
        if a.basis not in grounds:
            errors.append(f"{where}：basis「{a.basis}」不是此人人设里的好恶或心事原文")
        if a.trigger is Trigger.TRESPASS and a.location_id is None:
            errors.append(f"{where}：擅入须写明是哪处禁地")
        if a.location_id is not None and a.trigger not in PLACED:
            errors.append(f"{where}：「{a.trigger}」不带地点")
        if a.location_id is not None and a.location_id not in places:
            errors.append(f"{where}：地点不存在：{a.location_id}")
        if (a.trigger, a.location_id) in seen:
            errors.append(f"{where}：重复的触发「{a.trigger}」")
        seen.add((a.trigger, a.location_id))
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
        case "HAZARD":  # 有毒之物，且 T=0 就在世上（后来才到场之物属于 P2 的世界事件）
            return target in items and items[target].hazard is not None and not items[target].arrives_with
        case "MOTIVE":  # 人设
            return target in personas
    return False
