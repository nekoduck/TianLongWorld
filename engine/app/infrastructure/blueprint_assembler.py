"""
[INPUT]: 依赖 domain/models 的节点类型、entity_id / EntityKind、prerequisite_cycle、NAME_CHARS / DESC_CHARS 与 WorldBlueprint；
         Raw* 抽取记录仅作类型标注（运行期不导入，避免与 knowledge_extractor 成环）
[OUTPUT]: 对外提供 BlueprintAssembler（名称级抽取记录 → 引用完整的 WorldBlueprint）、AssemblyReport（丢弃 / 封存 / 失败的明细）、
          GENERIC_PEOPLE / GENERIC_PLACES / GENERIC_ARTS 泛称词表（人物与地点另有"描述不是名字"的模式判据）
[POS]: infrastructure 的确定性组装器（World Seeding 的后半程）：大模型读书，这里定案。
       实体消歧：两条记录的正名互见（正名=正名 或 正名=别名）才合并，别名撞别名不合并；称谓与泛称（爹爹、夫人、院子、卧室）
       既不能当正名也不能当别名——真实原著里「妈妈」曾把刀白凤与甘宝宝捏成一个人、「卧室」曾把剑湖宫与万劫谷连成一片；
       正名按各块记录投票（同票取先出现者），免得某一回只以「爹爹」称呼的人从此就叫「爹爹」；
       尾缀并入：「玄悲禅师」「一阳指法」在词干恰是另一组正名时并入它，带尾缀的称呼降为别名；
       时间切片：标量状态"首次登场即开篇"（按原著先后取第一次写明的值；看不出的 None 不占位，否则"未知"会冒充"最弱"），列表取并集；
       引用落地：一切名称引用都必须解析到本体实体，解析不了的出口、关系、人物武学直接丢弃，物品无处安放即丢弃；
       宁严勿宽：武学的前置引用了本体中不存在的武学 / 典籍 / 地点，或前置成环，一律封存（sealed）——宁可失传，不可滥传；
       拓扑补全：上级地点与其处所互通（「剑湖宫」入「剑湖宫·练武厅」），道路双向，A 通 B 而 B 不通 A 时补一条「往A」的回程
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.domain.models import (
    DESC_CHARS,
    NAME_CHARS,
    Character,
    CharacterRelation,
    CharacterStatus,
    Disposition,
    EntityKind,
    Item,
    Location,
    MartialArt,
    Prerequisites,
    Tier,
    Transmission,
    WorldBlueprint,
    entity_id,
    prerequisite_cycle,
)

if TYPE_CHECKING:
    from app.infrastructure.knowledge_extractor import ChunkExtraction


@dataclass
class AssemblyReport:
    dropped: list[str] = field(default_factory=list)
    sealed: list[str] = field(default_factory=list)
    failed_chunks: list[str] = field(default_factory=list)

    def render(self) -> str:
        sections = (("丢弃", self.dropped), ("封存", self.sealed), ("抽取失败", self.failed_chunks))
        return "\n".join(f"[{title}] {line}" for title, lines in sections for line in lines) or "（无异常）"


# ============================================================
#  泛称词表 —— 抽取提示词已禁止，这里是纵深防御：泛称当名字，就会把不同的人、不同的地方合成一个
# ============================================================
GENERIC_PEOPLE = frozenset(
    ["爹爹", "爹", "爸爸", "妈妈", "娘", "娘亲", "父亲", "母亲", "孩儿", "儿子", "女儿", "老大", "老二", "老三", "老四", "大哥", "二哥", "三弟", "四弟", "大姊", "姊姊", "妹子", "妹妹", "师父", "师傅", "师兄", "师弟", "师姊", "师妹", "师叔", "师伯", "夫人", "太太", "老爷", "公子", "少爷", "姑娘", "小姐", "丫头", "帮主", "谷主", "掌门", "宫主", "庄主", "皇上", "皇帝", "王爷", "先生", "前辈", "大师", "和尚", "道姑", "老者", "老人", "少年", "少女", "汉子", "大汉", "书生", "女子", "男子", "婆婆", "那人", "此人",
     "丫鬟", "丫环", "小婢", "太监", "皇后", "小儿", "妇人", "小沙弥", "店主人", "伯父", "伯母", "王妃", "老头", "老头儿"]
)
GENERIC_PLACES = frozenset(
    ["院子", "院中", "卧室", "厢房", "内堂", "堂中", "大厅", "花厅", "厅中", "书房", "石室", "山洞", "洞穴", "洞中", "树林", "林中", "山坡", "山坡上", "山溪", "山下", "山后", "山腰", "山顶", "峰下", "崖下", "谷中", "屋中", "屋顶", "门外", "房中", "江边", "江畔", "路上", "大路", "小路", "河边", "湖边"]
)
GENERIC_ARTS = frozenset(["轻功", "内功", "外功", "剑法", "刀法", "掌法", "拳法", "指法", "腿法", "身法", "暗器", "点穴", "擒拿", "武功", "功夫", "抓法"])

# 描述不是名字：「段誉的爹爹」「凶霸霸的大汉」「那少女」「段誉之母」「白须老者」——它们要么与真人重复，要么根本无名
_DESCRIBED_PERSON = re.compile(
    r"的|姓|那人$|^(?:那|这|一个|某)|之(?:父|母|妻|夫|子|女|兄|弟|姊|妹|师)$"
    r"|.(?:老者|老汉|汉子|女子|女郎|少女|少年|小婢|丫鬟|丫环|弟子|卫士|老板|主人|妇人|老头)$"
)
_DESCRIBED_PLACE = re.compile(r"的|一处|^(?:对面|左边|右边|东边|西边|南边|北边|前面|后面|远处)")


# 尊号后缀：「玄悲禅师」与「玄悲」是同一个人——词干是另一条记录的正名时并入它
_HONORIFIC = re.compile(r"(?:禅师|大师|道长|道人|先生|师兄|师弟|师姊|师妹|师哥|师叔|师伯|前辈)$")


def _person_generic(name: str) -> bool:
    return len(name) < 2 or name in GENERIC_PEOPLE or bool(_DESCRIBED_PERSON.search(name))


def _place_generic(name: str) -> bool:
    return name in GENERIC_PLACES or bool(_DESCRIBED_PLACE.search(name))


_DESCRIBED_ART = re.compile(r"的|^(?:独门|本门|家传)|一派武功$")


def _art_generic(name: str) -> bool:
    return name in GENERIC_ARTS or bool(_DESCRIBED_ART.search(name))


def _never(name: str) -> bool:
    return False


# ============================================================
#  实体消歧 —— 同类记录按正名互见合并，保持原著先后
# ============================================================
@dataclass
class _Group:
    records: list[Any] = field(default_factory=list)
    primary: set[str] = field(default_factory=set)  # 成员记录的正名
    every: set[str] = field(default_factory=set)  # 正名 ∪ 别名

    @property
    def name(self) -> str:
        """正名投票：各块记录里最常作 name 的那个，同票取先出现者（Counter 保序）。"""
        return Counter(_clean([r.name])[0] for r in self.records).most_common(1)[0][0]

    @property
    def aliases(self) -> tuple[str, ...]:
        """别名 = 其余成员记录的正名 ∪ 全部别名：「萧峰」并入「乔峰」之后仍是它的一个称呼。"""
        names = _union([[r.name for r in self.records], *(r.aliases for r in self.records)])
        return tuple(n for n in names if n != self.name)

    def absorb(self, other: _Group) -> None:
        self.records.extend(other.records)
        self.primary |= other.primary
        self.every |= other.every


def _clean(names: Iterable[str]) -> list[str]:
    return [n.strip()[:NAME_CHARS] for n in names if n and n.strip()]


def _group(records: Iterable[Any], generic: Callable[[str], bool], report: AssemblyReport) -> list[_Group]:
    groups: list[_Group] = []
    for record in records:
        name = _clean([record.name])
        if not name:
            continue
        if generic(name[0]):
            report.dropped.append(f"「{name[0]}」是泛称或描述，不成实体")
            continue
        record.aliases = [a for a in _clean(record.aliases) if not generic(a)]
        aliases = [a for a in record.aliases if a != name[0]]
        hits = [
            g for g in groups
            if name[0] in g.every or any(len(a) >= 2 and a in g.primary for a in aliases)
        ]
        target = hits[0] if hits else _Group()
        for extra in hits[1:]:
            target.absorb(extra)
            groups.remove(extra)
        if not hits:
            groups.append(target)
        target.records.append(record)
        target.primary.add(name[0])
        target.every |= {name[0], *aliases}
    suffix = _HONORIFIC if generic is _person_generic else _ART_SUFFIX if generic is _art_generic else None
    return _fold_suffixes(groups, suffix) if suffix else groups


_ART_SUFFIX = re.compile(r"(?<=.)(?:剑法|法)$")  # 「一阳指法」并入「一阳指」


def _fold_suffixes(groups: list[_Group], suffix: re.Pattern[str]) -> list[_Group]:
    """
    「玄悲禅师」并入「玄悲」、「一阳指法」并入「一阳指」：只在词干本身是另一组的正名时才并——
    「章虚道人」「降龙十八掌」的词干不是任何实体，它们就是全名。
    """
    by_name = {n: g for g in groups for n in g.primary}
    kept: list[_Group] = []
    for g in groups:
        stems = {suffix.sub("", n) for n in g.primary} - g.primary
        host = next((by_name[s] for s in stems if s in by_name and by_name[s] is not g), None)
        if host is None:
            kept.append(g)
            continue
        for record in g.records:  # 带后缀的称呼降为别名，正名改记词干，跨块投票时与裸名同票
            record.aliases = [*record.aliases, record.name]
            record.name = suffix.sub("", record.name)
        host.absorb(g)
        host.every |= {r.name for r in g.records}
        for n in g.primary:
            by_name[n] = host
    return kept


def _first(values: Iterable[Any], default: Any = None) -> Any:
    return next((v for v in values if v not in (None, "", [])), default)


def _union(lists: Iterable[Iterable[str]]) -> list[str]:
    return list(dict.fromkeys(x for xs in lists for x in _clean(xs)))


def _land(names: Iterable[str], idx: _Index, *, owner: str, misses: list[str]) -> tuple[str, ...]:
    """名称引用落地为 id（去重、去自指）；落不了地的名字记进 misses，由调用方决定丢弃还是封存。"""
    landed: list[str] = []
    for name in names:
        if (rid := idx.get(name)) is None:
            misses.append(name)
        elif rid != owner and rid not in landed:
            landed.append(rid)
    return tuple(landed)


class _Index:
    """名称 → id。正名优先；别名只在同类中无歧义时才收录；都不中时退而求唯一的包含匹配（「剑湖宫外」落到「剑湖宫」），多义不猜。"""

    def __init__(self, kind: EntityKind, groups: Sequence[_Group]) -> None:
        self.ids: dict[str, str] = {}
        claims: dict[str, set[str]] = {}
        for g in groups:
            gid = entity_id(kind, g.name)
            for n in g.primary:
                self.ids.setdefault(n, gid)
            for n in g.every - g.primary:
                claims.setdefault(n, set()).add(gid)
        for alias, owners in claims.items():
            if len(owners) == 1 and alias not in self.ids:
                self.ids[alias] = next(iter(owners))

    def get(self, name: str | None) -> str | None:
        if not name or not (wanted := name.strip()):
            return None
        if wanted in self.ids:
            return self.ids[wanted]
        if len(wanted) < 2:
            return None
        hits = {gid for known, gid in self.ids.items() if len(known) >= 2 and (known in wanted or wanted in known)}
        return hits.pop() if len(hits) == 1 else None


# ============================================================
#  组装
# ============================================================
class BlueprintAssembler:
    def assemble(self, extractions: Sequence[ChunkExtraction]) -> tuple[WorldBlueprint, AssemblyReport]:
        report = AssemblyReport()
        loc_g = _group((r for e in extractions for r in e.locations), _place_generic, report)
        chr_g = _group((r for e in extractions for r in e.characters), _person_generic, report)
        art_g = _group((r for e in extractions for r in e.martial_arts), _art_generic, report)
        itm_g = _group((r for e in extractions for r in e.items), _never, report)
        loc_i = _Index(EntityKind.LOCATION, loc_g)
        chr_i = _Index(EntityKind.CHARACTER, chr_g)
        art_i = _Index(EntityKind.MARTIAL_ART, art_g)

        locations = self._locations(loc_g, loc_i, report)
        characters = [self._character(g, loc_i, art_i, report) for g in chr_g]
        # 物品可能因无处安放而不存在：先定案物品，再只为落地的物品建索引——否则武学前置会指向一件被丢弃的秘籍
        landed = [(g, i) for g in itm_g if (i := self._item(g, chr_i, loc_i, report)) is not None]
        items = [i for _, i in landed]
        itm_i = _Index(EntityKind.ITEM, [g for g, _ in landed])
        arts = self._martial_arts(art_g, art_i, itm_i, loc_i, report)
        relations = self._relations(extractions, chr_i, report)
        blueprint = WorldBlueprint(
            locations=tuple(locations),
            characters=tuple(characters),
            martial_arts=tuple(arts),
            items=tuple(items),
            relations=tuple(relations),
        )
        return blueprint, report

    # ---- 地点：出口落地 + 道路双向 ----
    @staticmethod
    def _locations(groups: Sequence[_Group], idx: _Index, report: AssemblyReport) -> list[Location]:
        exits: dict[str, dict[str, str]] = {}
        names = {entity_id(EntityKind.LOCATION, g.name): g.name for g in groups}
        for g in groups:  # 上级地点通往其处所：「剑湖宫」入「剑湖宫·练武厅」，回程由下面的道路双向补齐
            here = entity_id(EntityKind.LOCATION, g.name)
            parent_name = _first(r.parent for r in g.records)
            parent = idx.get(parent_name)
            if parent == here:  # 包含匹配会把「镇南王府」落到「镇南王府·书房」自己身上：那不是上级
                parent = None
            if parent_name and parent is None:
                report.dropped.append(f"{g.name} 的上级地点「{parent_name}」不在本体之中")
            elif parent and here not in exits.setdefault(parent, {}).values():
                exits[parent][f"入{g.name.split('·')[-1]}"[:NAME_CHARS]] = here
        for g in groups:
            here = entity_id(EntityKind.LOCATION, g.name)
            table = exits.setdefault(here, {})
            for raw in (x for r in g.records for x in r.exits):
                target = idx.get(raw.destination)
                if target is None:
                    report.dropped.append(f"{g.name} 的出口「{raw.label}」通往未知地点「{raw.destination}」")
                    continue
                if target == here or target in table.values():
                    continue
                label = (raw.label.strip() or f"往{names[target]}")[:NAME_CHARS]
                table[label if label not in table else f"往{names[target]}"] = target
        for here, table in list(exits.items()):  # 道路双向
            for target in list(table.values()):
                back = exits.setdefault(target, {})
                if here not in back.values():
                    label = f"往{names[here]}"[:NAME_CHARS]
                    back[label if label not in back else f"回{names[here]}"[:NAME_CHARS]] = here
        return [
            Location(
                id=entity_id(EntityKind.LOCATION, g.name),
                name=g.name,
                aliases=g.aliases,
                region=str(_first(r.region for r in g.records) or "")[:NAME_CHARS],
                description=str(_first(r.description for r in g.records) or "")[:DESC_CHARS],
                exits=exits.get(entity_id(EntityKind.LOCATION, g.name), {}),
            )
            for g in groups
        ]

    # ---- 人物：首次登场即开篇状态 ----
    @staticmethod
    def _character(g: _Group, loc_i: _Index, art_i: _Index, report: AssemblyReport) -> Character:
        where = _first(r.location for r in g.records)
        location_id = loc_i.get(where)
        if where and location_id is None:
            report.dropped.append(f"{g.name} 所在的未知地点「{where}」（此人不入任何场景）")
        skills: list[str] = []
        for name in _union(r.skills for r in g.records):
            if (sid := art_i.get(name)) is None:
                report.dropped.append(f"{g.name} 所会的未知武学「{name}」")
            elif sid not in skills:
                skills.append(sid)
        return Character(
            id=entity_id(EntityKind.CHARACTER, g.name),
            name=g.name,
            aliases=g.aliases,
            faction=str(_first(r.faction for r in g.records) or "")[:NAME_CHARS],
            status=_first((r.status for r in g.records), CharacterStatus.ALIVE),
            tier=_first((r.tier for r in g.records), Tier.NONE),  # 全书都看不出武功：多半是书生婢女之流
            disposition=_first((r.disposition for r in g.records), Disposition.NEUTRAL),
            location_id=location_id,
            skills=tuple(skills),
            description=str(_first(r.description for r in g.records) or "")[:DESC_CHARS],
        )

    # ---- 武学：前置落地，落不了地即封存；成环即封存并断环 ----
    @staticmethod
    def _martial_arts(
        groups: Sequence[_Group], art_i: _Index, itm_i: _Index, loc_i: _Index, report: AssemblyReport
    ) -> list[MartialArt]:
        prereqs: dict[str, Prerequisites] = {}
        for g in groups:
            aid = entity_id(EntityKind.MARTIAL_ART, g.name)
            raw = [r.prerequisites for r in g.records]
            unresolved: list[str] = []
            skills = _land(_union(p.skills for p in raw), art_i, owner=aid, misses=unresolved)
            items = _land(_union(p.items for p in raw), itm_i, owner=aid, misses=unresolved)
            place_name = _first(p.location for p in raw)
            place = loc_i.get(place_name)
            if place_name and place is None:
                unresolved.append(place_name)
            conflicts = _land(_union(p.conflicts for p in raw), art_i, owner=aid, misses=[])  # 相冲之功不在本体即无从相冲，丢弃无害
            # 前置是武学的静态属性而非随时间变化的状态：境界门槛取最严的一条；任何一段写明可凭典籍自悟才算自悟
            min_tier = max((p.min_tier for p in raw), key=lambda t: t.rank)
            self_study = any(p.transmission is Transmission.SELF for p in raw)
            transmission = Transmission.SELF if self_study else Transmission.TEACHER
            if transmission is Transmission.SELF and not items:
                report.dropped.append(f"{g.name} 写作自悟却未载明典籍，改为须师传")
                transmission = Transmission.TEACHER
            sealed = bool(unresolved)
            if sealed:
                report.sealed.append(f"{g.name}：前置「{'、'.join(unresolved)}」不在本体之中")
            prereqs[aid] = Prerequisites(
                skills=skills, items=items, location_id=place, min_tier=min_tier,
                conflicts=conflicts, transmission=transmission, sealed=sealed,
            )
        while cycle := prerequisite_cycle({k: v.skills for k, v in prereqs.items()}):
            report.sealed.append(f"前置成环：{' → '.join(cycle)}")
            for aid in set(cycle):
                prereqs[aid] = prereqs[aid].model_copy(update={"skills": (), "sealed": True})
        return [
            MartialArt(
                id=entity_id(EntityKind.MARTIAL_ART, g.name),
                name=g.name,
                aliases=g.aliases,
                faction=str(_first(r.faction for r in g.records) or "")[:NAME_CHARS],
                kind=str(_first(r.kind for r in g.records) or "")[:NAME_CHARS],
                tier=_first((r.tier for r in g.records), Tier.THIRD),
                description=str(_first(r.description for r in g.records) or "")[:DESC_CHARS],
                prerequisites=prereqs[entity_id(EntityKind.MARTIAL_ART, g.name)],
            )
            for g in groups
        ]

    # ---- 物品：唯一归属，无处安放即不存在 ----
    @staticmethod
    def _item(g: _Group, chr_i: _Index, loc_i: _Index, report: AssemblyReport) -> Item | None:
        owner = chr_i.get(_first(r.owner for r in g.records))
        where = loc_i.get(_first(r.location for r in g.records))
        if owner is None and where is None:
            report.dropped.append(f"物品「{g.name}」既无可落地的物主也无可落地的所在")
            return None
        return Item(
            id=entity_id(EntityKind.ITEM, g.name),
            name=g.name,
            aliases=g.aliases,
            kind=str(_first(r.kind for r in g.records) or "")[:NAME_CHARS],
            description=str(_first(r.description for r in g.records) or "")[:DESC_CHARS],
            owner_id=owner,
            location_id=where,
        )

    # ---- 关系：两端都须是本体人物；去重、去自环 ----
    @staticmethod
    def _relations(
        extractions: Sequence[ChunkExtraction], chr_i: _Index, report: AssemblyReport
    ) -> list[CharacterRelation]:
        seen: set[tuple[str, str]] = set()
        relations: list[CharacterRelation] = []
        for raw in (r for e in extractions for r in e.relations):
            a, b = chr_i.get(raw.source), chr_i.get(raw.target)
            if raw.kind is None:
                report.dropped.append(f"关系「{raw.source} — {raw.target}」的类别无法识别")
                continue
            if a is None or b is None:
                report.dropped.append(f"关系「{raw.source} —{raw.kind.value}— {raw.target}」有一端不在本体之中")
                continue
            pair = (min(a, b), max(a, b))
            if a == b or pair in seen:
                continue
            seen.add(pair)
            relations.append(CharacterRelation(source_id=a, target_id=b, kind=raw.kind, note=raw.note[:DESC_CHARS]))
        return relations
