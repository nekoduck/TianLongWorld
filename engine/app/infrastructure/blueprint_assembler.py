"""
[INPUT]: 依赖 domain/models 的节点类型、entity_id / EntityKind、prerequisite_cycle、NAME_CHARS / DESC_CHARS 与 WorldBlueprint；
         Raw* 抽取记录仅作类型标注（运行期不导入，避免与 knowledge_extractor 成环）
[OUTPUT]: 对外提供 BlueprintAssembler（名称级抽取记录 → 引用完整的 WorldBlueprint）、AssemblyReport（丢弃 / 封存 / 失败的明细）
[POS]: infrastructure 的确定性组装器（World Seeding 的后半程）：大模型读书，这里定案。
       实体消歧：两条记录的正名互见（正名=正名 或 正名=别名）才合并，别名撞别名不合并——"大师""公子"这类泛称不会把两个人捏成一个；
       时间切片：标量状态"首次登场即开篇"（按原著先后取第一次出现的值），列表取并集；
       引用落地：一切名称引用都必须解析到本体实体，解析不了的出口、关系、人物武学直接丢弃，物品无处安放即丢弃；
       宁严勿宽：武学的前置引用了本体中不存在的武学 / 典籍 / 地点，或前置成环，一律封存（sealed）——宁可失传，不可滥传；
       拓扑补全：道路双向，A 通 B 而 B 不通 A 时补一条「往A」的回程
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.domain.models import (
    DESC_CHARS,
    NAME_CHARS,
    Character,
    CharacterRelation,
    EntityKind,
    Item,
    Location,
    MartialArt,
    Prerequisites,
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
#  实体消歧 —— 同类记录按正名互见合并，保持原著先后
# ============================================================
@dataclass
class _Group:
    records: list[Any] = field(default_factory=list)
    primary: set[str] = field(default_factory=set)  # 成员记录的正名
    every: set[str] = field(default_factory=set)  # 正名 ∪ 别名

    @property
    def name(self) -> str:
        return str(self.records[0].name)

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


def _group(records: Iterable[Any]) -> list[_Group]:
    groups: list[_Group] = []
    for record in records:
        name = _clean([record.name])
        if not name:
            continue
        aliases = [a for a in _clean(record.aliases) if a != name[0]]
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
    return groups


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
    """名称 → id。正名优先；别名只在同类中无歧义时才收录。"""

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
        return self.ids.get(name.strip()) if name else None


# ============================================================
#  组装
# ============================================================
class BlueprintAssembler:
    def assemble(self, extractions: Sequence[ChunkExtraction]) -> tuple[WorldBlueprint, AssemblyReport]:
        report = AssemblyReport()
        loc_g = _group(r for e in extractions for r in e.locations)
        chr_g = _group(r for e in extractions for r in e.characters)
        art_g = _group(r for e in extractions for r in e.martial_arts)
        itm_g = _group(r for e in extractions for r in e.items)
        loc_i = _Index(EntityKind.LOCATION, loc_g)
        chr_i = _Index(EntityKind.CHARACTER, chr_g)
        art_i = _Index(EntityKind.MARTIAL_ART, art_g)
        itm_i = _Index(EntityKind.ITEM, itm_g)

        locations = self._locations(loc_g, loc_i, report)
        characters = [self._character(g, loc_i, art_i, report) for g in chr_g]
        arts = self._martial_arts(art_g, art_i, itm_i, loc_i, report)
        items = [i for g in itm_g if (i := self._item(g, chr_i, loc_i, report)) is not None]
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
        first = g.records[0]
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
            status=first.status,
            tier=first.tier,
            disposition=first.disposition,
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
            transmission = raw[0].transmission
            if transmission is Transmission.SELF and not items:
                report.dropped.append(f"{g.name} 写作自悟却未载明典籍，改为须师传")
                transmission = Transmission.TEACHER
            sealed = bool(unresolved)
            if sealed:
                report.sealed.append(f"{g.name}：前置「{'、'.join(unresolved)}」不在本体之中")
            prereqs[aid] = Prerequisites(
                skills=skills, items=items, location_id=place, min_tier=raw[0].min_tier,
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
                tier=g.records[0].tier,
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
