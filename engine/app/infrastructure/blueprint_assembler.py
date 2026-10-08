"""
[INPUT]: 依赖 domain/models 的节点类型、entity_id / EntityKind、prerequisite_cycle、NAME_CHARS / DESC_CHARS 与 WorldBlueprint；
         Raw* 抽取记录仅作类型标注（运行期不导入，避免与 knowledge_extractor 成环）
[OUTPUT]: 对外提供 BlueprintAssembler（名称级抽取记录 → 引用完整的 WorldBlueprint）、AssemblyReport（丢弃 / 封存 / 孤儿 / 时间线 / 自愈 / 失败的明细）、
          CanonEventKind（抽取契约里 T=0 之后的三类状态变化：组装器认得的证据种类）、
          GENERIC_PEOPLE / GENERIC_PLACES / GENERIC_ARTS 泛称词表（人物与地点另有"描述不是名字"的模式判据）、
          entrance_label()（上级地点通往其处所的出口标签「入练武厅」，命名参考凭它认上级）
[POS]: infrastructure 的确定性组装器（World Seeding 的后半程）：大模型读书，这里定案。
       实体消歧：两条记录的正名互见（正名=某组任一称呼，或本记录的称号 / 别名=某组某条记录的正名）才合并，别名撞别名不合并；
       称谓与泛称（爹爹、夫人、院子、卧室）既不能当正名也不能当别名——真实原著里「妈妈」曾把刀白凤与甘宝宝捏成一个人；
       三名分立：本名只在知道本名的记录里投票（只以称号出场的记录写的是称号，不能篡位成主键——段延庆不叫「恶贯满盈」），
       整组都只知称号时才退回全体投票；称号归 titles，化名旧称归 aliases；称号可以多人共用（「姑苏慕容」），
       只知称号的记录只在唯一一位已知本名者认领它时才归入、两人都认领就多义不猜，尚无本名的称号组也只能被认领一次——称号永远不是撮合两人的桥；
       尾缀并入：「玄悲禅师」「一阳指法」在词干恰是另一组正名时并入它，带尾缀的称呼降为别名；
       时间切片：标量状态"首次登场即开篇"（按原著先后取第一次写明的值；看不出的 None 不占位），列表取并集；
       时间线隔离：抽取契约的 events 是 T=0 之后的状态变化，它们是证据而不是状态——只用来否决被时间线污染的开篇状态
       （后文习得的武学剔出开篇武学、后文才得到的物品不认他作开篇物主——主张早于"得到"即失而复得、照认——、后文身故者开篇健在），
       本身不进蓝图；否决会抹掉原著状态，所以事件只认全名落地，不做包含匹配；
       境界通篇看不出时由图谱证据定下限：身负武学或身在门派者至少三流，两样都无从考证的才算不入流；
       引用落地：一切名称引用都必须解析到本体实体，解析不了的出口、关系、人物武学直接丢弃；
       物品无处安放即丢弃——唯独被某门武学获取要求引用的，以下落不明的孤儿留在本体里，等自愈代理（graph_linter）据常识安放；
       宁严勿宽：武学引用了根本不存在的武学 / 典籍 / 地点，或根基成环，一律封存（sealed）——宁可失传，不可滥传；
       拓扑补全：上级地点与其处所互通（「剑湖宫」入「剑湖宫·练武厅」），道路双向，A 通 B 而 B 不通 A 时补一条「往A」的回程
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.domain.models import (
    DESC_CHARS,
    NAME_CHARS,
    Acquisition,
    Character,
    CharacterRelation,
    CharacterStatus,
    Disposition,
    EntityKind,
    Item,
    Location,
    MartialArt,
    Practice,
    Tier,
    Transmission,
    WorldBlueprint,
    entity_id,
    prerequisite_cycle,
)

if TYPE_CHECKING:
    from app.infrastructure.knowledge_extractor import ChunkExtraction


# T=0 之后改变状态的三类事：每一类恰好否决一种开篇状态——组装器只认得这三种证据。
# 枚举也是抽取契约的一部分（knowledge_extractor 的 RawCanonEvent.kind），定义在消费它的这一侧，免得运行期成环
class CanonEventKind(StrEnum):
    LEARNED = "习得武学"  # 否决：此人开篇即会此功
    OBTAINED = "得到物品"  # 否决：此人是此物的开篇物主
    DIED = "身故"  # 否决：此人开篇已故


@dataclass
class AssemblyReport:
    dropped: list[str] = field(default_factory=list)
    sealed: list[str] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)  # 被武学引用却下落不明的物品，待自愈
    timeline: list[str] = field(default_factory=list)  # 被后文事件否决的开篇状态
    healed: list[str] = field(default_factory=list)  # 自愈代理的安放与未决（由播种操作面在组装之后填入）
    failed_chunks: list[str] = field(default_factory=list)

    def render(self) -> str:
        sections = (
            ("丢弃", self.dropped), ("封存", self.sealed), ("孤儿", self.orphans),
            ("时间线", self.timeline), ("自愈", self.healed), ("抽取失败", self.failed_chunks),
        )
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
def _is_title(record: Any) -> bool:
    """这条记录的 name 写的是称号（本段不知其本名）：只有人物记录有此标记，旧版缓存一律视为本名。"""
    return bool(getattr(record, "name_is_title", False))


def _titles_of(record: Any) -> list[str]:
    return list(getattr(record, "titles", ()))


@dataclass
class _Group:
    records: list[Any] = field(default_factory=list)
    primary: set[str] = field(default_factory=set)  # 知道本名的成员记录的 name
    titled: set[str] = field(default_factory=set)  # 只知称号的成员记录的 name（它是称号，不是正名）
    every: set[str] = field(default_factory=set)  # 正名 ∪ 称号 ∪ 别名

    @property
    def name(self) -> str:
        """
        本名投票：只在知道本名的记录里投——只以称号出场的记录写的是称号，票数再多也不能篡位成主键；
        整组都只知称号时才退回全体投票。同票取先出现者（Counter 保序）。
        """
        voters = [r for r in self.records if not _is_title(r)] or self.records
        return Counter(_clean([r.name])[0] for r in voters).most_common(1)[0][0]

    @property
    def titles(self) -> tuple[str, ...]:
        """称号 = 全组 titles ∪ 只知称号的记录的 name（去掉本名）。"""
        names = _union([[r.name for r in self.records if _is_title(r)], *(_titles_of(r) for r in self.records)])
        return tuple(n for n in names if n != self.name)

    @property
    def aliases(self) -> tuple[str, ...]:
        """别名 = 其余知道本名的记录的 name ∪ 全部别名（去掉本名与称号）：「萧峰」并入「乔峰」之后仍是它的一个称呼。"""
        taken = {self.name, *self.titles}
        names = _union([[r.name for r in self.records if not _is_title(r)], *(r.aliases for r in self.records)])
        return tuple(n for n in names if n not in taken)

    def absorb(self, other: _Group) -> None:
        self.records.extend(other.records)
        self.primary |= other.primary
        self.titled |= other.titled
        self.every |= other.every

    @property
    def anonymous(self) -> bool:
        """整组都只知称号：还没有哪条记录写出本名，它的称号才可以被后来写出本名的记录认领。"""
        return not self.primary


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
        if hasattr(record, "titles"):
            record.titles = [t for t in _clean(record.titles) if not generic(t)]
        others = [a for a in (*record.aliases, *_titles_of(record)) if a != name[0]]
        if _is_title(record):
            # 只知称号的记录：称号可以被多人共用（「大侠」「姑苏慕容」），只有唯一一位已知本名者认领它时才归入；
            # 两人都认领就多义不猜——否则它会成为桥，把两个不同的人捏成一个
            claimants = [g for g in groups if not g.anonymous and name[0] in g.every]
            if len(claimants) > 1:
                report.dropped.append(f"称号「{name[0]}」同时指向{'、'.join(sorted(g.name for g in claimants))}，多义不猜")
                continue
            hits = claimants + [
                g for g in groups if g.anonymous and (name[0] in g.titled or any(a in g.titled for a in others))
            ]
        else:
            # 知道本名的记录：正名互见才合并；称号与别名只能撞上别人的正名，或认领一个尚无本名的称号组
            hits = [
                g for g in groups
                if name[0] in g.every
                or any(len(a) >= 2 and (a in g.primary or (g.anonymous and a in g.titled)) for a in others)
            ]
        target = hits[0] if hits else _Group()
        for extra in hits[1:]:
            target.absorb(extra)
            groups.remove(extra)
        if not hits:
            groups.append(target)
        target.records.append(record)
        (target.titled if _is_title(record) else target.primary).add(name[0])
        target.every |= {name[0], *others}
    suffix = _HONORIFIC if generic is _person_generic else _ART_SUFFIX if generic is _art_generic else None
    return _fold_suffixes(groups, suffix) if suffix else groups


_ART_SUFFIX = re.compile(r"(?<=.)(?:剑法|法)$")  # 「一阳指法」并入「一阳指」


def _fold_suffixes(groups: list[_Group], suffix: re.Pattern[str]) -> list[_Group]:
    """
    「玄悲禅师」并入「玄悲」、「一阳指法」并入「一阳指」：只在词干本身是另一组的正名时才并——
    「章虚道人」「降龙十八掌」的词干不是任何实体，它们就是全名。折叠只作用于正名，称号与别名原样保留。
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


def _label(any_id: str) -> str:
    """实体 id 的显示名：id 恒为「种类:正名」。"""
    return any_id.split(":", 1)[1]


def _because(note: str) -> str:
    return f"：{note}" if note else ""


class _Index:
    """名称 → id。正名优先；别名只在同类中无歧义时才收录；都不中时退而求唯一的包含匹配（「剑湖宫外」落到「剑湖宫」），多义不猜。"""

    def __init__(self, kind: EntityKind, groups: Sequence[_Group]) -> None:
        self.ids: dict[str, str] = {}
        claims: dict[str, set[str]] = {}
        for g in groups:
            gid = entity_id(kind, g.name)
            for n in g.primary or g.titled:  # 尚无本名的称号组，以称号为正名
                self.ids.setdefault(n, gid)
            for n in g.every - g.primary:
                claims.setdefault(n, set()).add(gid)
        for alias, owners in claims.items():
            if len(owners) == 1 and alias not in self.ids:
                self.ids[alias] = next(iter(owners))

    def exact(self, name: str | None) -> str | None:
        """只认全名（正名与无歧义的别名 / 称号），不做包含匹配：给会否决原著状态的证据用。"""
        return self.ids.get(name.strip()) if name else None

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
#  时间线 —— T=0 之后的状态变化是证据，不是状态：只用来否决被时间线污染的开篇状态
# ============================================================
@dataclass
class _Timeline:
    learned: dict[tuple[str, str], str] = field(default_factory=dict)  # (人物, 武学) → 事件转述
    obtained: dict[tuple[str, str], tuple[int, str]] = field(default_factory=dict)  # (物品, 人物) → (最早发生的块, 事件转述)
    died: dict[str, str] = field(default_factory=dict)  # 人物 → 事件转述

    @classmethod
    def read(
        cls, extractions: Sequence[ChunkExtraction], chr_i: _Index, art_i: _Index, itm_i: _Index,
        report: AssemblyReport,
    ) -> _Timeline:
        """
        全书事件按原著先后落地：主角落到人物，宾语落到武学 / 物品；落不了地的事件证明不了任何事，丢弃。
        否决会抹掉原著状态，所以落地只认全名（正名、称号、无歧义的别名），不做包含匹配——
        「段正淳之子」被组装器当描述丢掉，它的事件也不能借包含匹配去否决段正淳。
        """
        timeline = cls()
        for chunk, raw in ((i, ev) for i, e in enumerate(extractions) for ev in e.events):
            if raw.kind is None:
                report.dropped.append(f"事件「{raw.subject} — {raw.object or ''}」的种类无法识别")
                continue
            what = f"{raw.subject}{raw.kind.value}{raw.object or ''}"
            if (who := chr_i.exact(raw.subject)) is None:
                report.dropped.append(f"事件「{what}」的人物不在本体之中")
                continue
            note = raw.note.strip()
            if raw.kind is CanonEventKind.DIED:
                timeline.died.setdefault(who, note)
                continue
            learned = raw.kind is CanonEventKind.LEARNED
            if (thing := (art_i if learned else itm_i).exact(raw.object)) is None:
                report.dropped.append(f"事件「{what}」的{'武学' if learned else '物品'}不在本体之中")
            elif learned:
                timeline.learned.setdefault((who, thing), note)
            else:
                timeline.obtained.setdefault((thing, who), (chunk, note))
        return timeline


# ============================================================
#  组装
# ============================================================
def entrance_label(place: str) -> str:
    """上级地点通往其处所的那条出口的标签（「入练武厅」）：组装器据此连边，命名参考据此认出谁是上级——原文出口「入谷」「入内」不算。"""
    return f"入{place.split('·')[-1]}"[:NAME_CHARS]


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
        itm_i = _Index(EntityKind.ITEM, itm_g)  # 全部物品都可被引用：无处安放者是否留下，要看有没有武学需要它
        timeline = _Timeline.read(extractions, chr_i, art_i, itm_i, report)

        locations = self._locations(loc_g, loc_i, report)
        characters = [self._character(g, loc_i, art_i, timeline, report) for g in chr_g]
        chunk_of = {id(r): i for i, e in enumerate(extractions) for r in e.items}  # 物品记录 → 所在块，判断主张与事件的先后
        placed = {
            entity_id(EntityKind.ITEM, g.name): self._place(g, chr_i, loc_i, timeline, chunk_of, report) for g in itm_g
        }
        arts = self._martial_arts(art_g, art_i, itm_i, loc_i, report)
        items = self._items(itm_g, placed, arts, report)
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
                exits[parent][entrance_label(g.name)] = here
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

    # ---- 人物：首次登场即开篇状态，后文事件否决被时间线污染的部分 ----
    @staticmethod
    def _character(
        g: _Group, loc_i: _Index, art_i: _Index, timeline: _Timeline, report: AssemblyReport
    ) -> Character:
        cid = entity_id(EntityKind.CHARACTER, g.name)
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
        for sid in [s for s in skills if (cid, s) in timeline.learned]:
            report.timeline.append(f"{g.name} 开篇时尚未习得「{_label(sid)}」（后文习得{_because(timeline.learned[cid, sid])}）")
            skills.remove(sid)
        status = _first((r.status for r in g.records), CharacterStatus.ALIVE)
        if cid in timeline.died and status is CharacterStatus.DECEASED:
            report.timeline.append(f"{g.name} 开篇时健在（后文身故{_because(timeline.died[cid])}）")
            status = CharacterStatus.ALIVE
        return Character(
            id=cid,
            true_name=g.name,
            titles=g.titles,
            aliases=g.aliases,
            faction=str(_first(r.faction for r in g.records) or "")[:NAME_CHARS],
            status=status,
            # 境界看不出时由图谱证据定下限：身负武学或身在门派者至少三流；两样都无从考证的才算不入流（未知 ≠ 最弱）
            tier=_first((r.tier for r in g.records), Tier.THIRD if skills or _first(r.faction for r in g.records) else Tier.NONE),
            disposition=_first((r.disposition for r in g.records), Disposition.NEUTRAL),
            location_id=location_id,
            skills=tuple(skills),
            description=str(_first(r.description for r in g.records) or "")[:DESC_CHARS],
        )

    # ---- 武学：两道门落地，落不了地即封存；根基成环即封存并断环 ----
    @staticmethod
    def _martial_arts(
        groups: Sequence[_Group], art_i: _Index, itm_i: _Index, loc_i: _Index, report: AssemblyReport
    ) -> list[MartialArt]:
        gates: dict[str, tuple[Acquisition, Practice]] = {}
        for g in groups:
            aid = entity_id(EntityKind.MARTIAL_ART, g.name)
            acq = [r.acquisition for r in g.records]
            prac = [r.practice for r in g.records]
            unresolved: list[str] = []
            skills = _land(_union(p.skills for p in prac), art_i, owner=aid, misses=unresolved)
            items = _land(_union(a.items for a in acq), itm_i, owner=aid, misses=unresolved)
            place_name = _first(a.location for a in acq)
            place = loc_i.get(place_name)
            if place_name and place is None:
                unresolved.append(place_name)
            conflicts = _land(_union(p.conflicts for p in prac), art_i, owner=aid, misses=[])  # 相冲之功不在本体即无从相冲，丢弃无害
            # 两道门是武学的静态属性而非随时间变化的状态：境界门槛取最严的一条；任何一段写明可凭典籍自悟才算自悟
            min_tier = max((p.min_tier for p in prac), key=lambda t: t.rank)
            self_study = any(a.transmission is Transmission.SELF for a in acq)
            transmission = Transmission.SELF if self_study else Transmission.TEACHER
            if transmission is Transmission.SELF and not items:
                report.dropped.append(f"{g.name} 写作自悟却未载明典籍，改为须师传")
                transmission = Transmission.TEACHER
            sealed = bool(unresolved)
            if sealed:
                report.sealed.append(f"{g.name}：获取 / 修炼要求「{'、'.join(unresolved)}」不在本体之中")
            gates[aid] = (
                Acquisition(items=items, location_id=place, transmission=transmission, sealed=sealed),
                Practice(skills=skills, min_tier=min_tier, conflicts=conflicts),
            )
        while cycle := prerequisite_cycle({k: v[1].skills for k, v in gates.items()}):
            report.sealed.append(f"根基成环：{' → '.join(cycle)}")
            for aid in set(cycle):
                acq_gate, practice = gates[aid]
                gates[aid] = (acq_gate.model_copy(update={"sealed": True}), practice.model_copy(update={"skills": ()}))
        return [
            MartialArt(
                id=entity_id(EntityKind.MARTIAL_ART, g.name),
                name=g.name,
                aliases=g.aliases,
                faction=str(_first(r.faction for r in g.records) or "")[:NAME_CHARS],
                kind=str(_first(r.kind for r in g.records) or "")[:NAME_CHARS],
                tier=_first((r.tier for r in g.records), Tier.THIRD),
                description=str(_first(r.description for r in g.records) or "")[:DESC_CHARS],
                acquisition=gates[entity_id(EntityKind.MARTIAL_ART, g.name)][0],
                practice=gates[entity_id(EntityKind.MARTIAL_ART, g.name)][1],
            )
            for g in groups
        ]

    # ---- 物品：唯一归属；后文才得到者不作开篇物主 ----
    @staticmethod
    def _place(
        g: _Group, chr_i: _Index, loc_i: _Index, timeline: _Timeline, chunk_of: dict[int, int], report: AssemblyReport
    ) -> tuple[str | None, str | None]:
        """
        （物主, 所在）：物主按原著先后取第一个写明的候选。某人"得到此物"的事件发生在他作物主的那条主张之时或之前，
        那条主张就是被时间线污染的后文状态，跳过、取下一个，没有就无主；主张早于事件（开篇本就是他的，后来失而复得）则照认。
        """
        iid = entity_id(EntityKind.ITEM, g.name)
        owner: str | None = None
        vetoed: list[str] = []
        for record in (r for r in g.records if r.owner not in (None, "")):
            landed = chr_i.get(record.owner)
            event = timeline.obtained.get((iid, landed)) if landed is not None else None
            if landed is not None and event is not None and chunk_of.get(id(record), 0) >= event[0]:
                if landed not in vetoed:
                    vetoed.append(landed)
                continue
            owner = landed
            break
        for who in vetoed:
            report.timeline.append(f"「{g.name}」开篇时不归{_label(who)}所有（后文才得到{_because(timeline.obtained[iid, who][1])}）")
        return owner, loc_i.get(_first(r.location for r in g.records))

    @staticmethod
    def _items(
        groups: Sequence[_Group], placed: dict[str, tuple[str | None, str | None]], arts: Sequence[MartialArt],
        report: AssemblyReport,
    ) -> list[Item]:
        """
        无处安放的物品默认不存在；唯独被某门武学获取要求引用的留作孤儿（下落不明）——丢掉它，那门武学只能封存，
        留下它，自愈代理还能据常识为它找个去处。
        """
        needed: dict[str, list[str]] = {}
        for art in arts:
            for iid in art.acquisition.items:
                needed.setdefault(iid, []).append(art.name)
        items: list[Item] = []
        for g in groups:
            iid = entity_id(EntityKind.ITEM, g.name)
            owner, where = placed[iid]
            if owner is None and where is None:
                if iid not in needed:
                    report.dropped.append(f"物品「{g.name}」既无可落地的物主也无可落地的所在")
                    continue
                report.orphans.append(f"「{g.name}」下落不明，为「{'、'.join(needed[iid])}」所需——待自愈")
            items.append(Item(
                id=iid,
                name=g.name,
                aliases=g.aliases,
                kind=str(_first(r.kind for r in g.records) or "")[:NAME_CHARS],
                description=str(_first(r.description for r in g.records) or "")[:DESC_CHARS],
                owner_id=owner,
                location_id=where,
            ))
        return items

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
