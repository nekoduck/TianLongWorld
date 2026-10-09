"""
[INPUT]: 依赖 application/ports 的 LLMClient，依赖 application/chronicle 的 titled / known_arts，依赖 application/navigation 的 duration_label（出路的耗时与导航同一种说法），
         依赖 domain/snapshot 的 LocalSnapshot / ExitView 及 ActivityView / TraceView / SwarmView，依赖 domain/approach 的 TacticalAxis，依赖 domain/intent 的 PlayerIntent，
         依赖 domain/stakes 的 Risk，依赖 domain/commands 的 TICKS_PER_SHICHEN，依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 NarrationRequest（含夺路逃离的交手现场 fled、在场者的恩怨缘由 causes、短期记忆 recollection 与此行所为 motivation、
          可供性目录 menu（按次序编号 m1…）与在场者的来意 errands）、Afforded（目录一招的结构子集）、MenuPick / MenuPicks（说书人交出的 <menu>，未过闸）、
          ShortTermMemory 与 recollect()（出发前的快照 + 此行所为 → 短期记忆）、affordance()（目录一行）、way()（出路一行：方位｜去处｜交通方式｜路程，地下城主的简报共用）、
          read_menu()（<menu> 尾巴 → MenuPicks，宽容解析）、Narrator 抽象（流式 narrate：正文 str 若干，LLMNarrator 末尾另有一个 MenuPicks）、
          hard_prompt()（局部真理快照 → XML 硬约束）、NARRATOR_SYSTEM、MENU_MARKERS / HEADING_MAX / PICKS_MAX、
          世界心跳的此地之物的写法 when / cast / headcount / lingering / activity / trace / crowd / chronological（地下城主的简报共用），
          LLMNarrator（金庸风流式渲染 + 同一次调用交出菜单）、TemplateNarrator（离线确定性白描，带一句时辰）、FallbackNarrator（主渲染失败时降级为白描，透传主渲染器的 MenuPicks）
[POS]: application 的查询侧渲染器（CQRS 的 Query 侧）：结果已由规则裁定并入账，这里只负责"怎么写"，无权决定"发生了什么"。
       大模型看到的世界只有快照（Hard Prompt）：快照之外的人、物、功、地对它不存在；渲染失败也不影响真相——事件早已落账，降级白描照常推送。
       地下城主不交散文速写：它推演出的微观事实（FactEmerged）已入账，经白描进 <settled_facts>；往回合推演出的、点了眼前之名的细节
       经快照进 <truth_snapshot> 的 <emerged>，可照应不可推翻。眼前悬着的叙事时钟进 <clocks>（名称、种类、挂处、进度 / 阈值、满则如何）：
       铁律 1 许它只作暗流——气氛与端倪，不替它坍缩；坍缩是 <settled_facts> 里明写的「……满了」，照写即可。两段逐值转义，空则不出现。
       铁律据真实整局实测补强：没有「来到某地」就仍在原地、facts 之外的变化（伤势好转、退路被封、有人追来）一概不写、
       行囊里的东西 facts 没写它易手就仍在身上（玩家"嚼下通天草"不等于吃掉了）、伤势与态度不照抄标签词；在场者所会武学带类别（掌法不被写成剑法）。
       重伤夺路而逃的回合，快照已是逃抵之地，交手现场另作 <fled_scene>：仇人在那里，先写交手再写逃。
       在场者的人物行在态度之后附「恩怨：…」（取自 RelationChanged.cause 的折叠）与「来意：…」（带议程的核心 NPC 此行的打算，铁律 3 只许作神色举止的端倪），
       另附外显人设「好…；恶…；心事…」（只取 CharacterView.persona，后文剧情从不进快照）；<known_facts> 只放玩家已知（known=True）的见闻，
       玩家不知道的见闻（known=False）绝不进任何提示词——它是打探的标的，说书人一旦知道就会替 NPC 说破。
       意图风味封装：<affordances> 是引擎先算好的可供性目录（「m1｜激化｜徒手向龚光杰出手｜对象：龚光杰｜缘由：…｜风险：凶险」，逐值转义，指令与意图从不进提示词）——
       说书人在同一次调用里写完正文，另起一行交 <menu>[{"pick":"m1","flavor":"……"}…]</menu>：挑 3~4 招、尽量覆盖不同的战术维度、各配一句 ≤20 字的武侠风味；
       正文只给挑中的几招铺垫端倪（铁律 1 / 2 / 5：端倪不是结果、不替玩家行动、正文里不列选项）。LLMNarrator 流式只吐正文：遇 <menu、``` 或裸 JSON 数组「[{」即截断，
       可能是标记前缀的尾巴与其前的空白先扣住、跨块也不漏一个字；紧贴标记之前的小标题行（短、无句末标点：「菜单：」）一并截掉——行首至多扣住 HEADING_MAX 字；流尽后 read_menu 宽容解析（围栏、缺 </menu>、多余字段、编号写成数字、半截数组逐个对象捞），
       解析不了即空 MenuPicks——挑什么、文案过不过闸由 options/menu.compose 决定，执行的永远是目录里那一招的 underlying_command。
       探索迷雾：出路一律写「方位｜去处｜交通方式｜路程」，未知的去处只写「未知区域」，出口标签（常带地名）从不进提示词；铁律 2 不许替未知区域取名。
       世界心跳（局部认知）：<truth_snapshot> 恒有 <time>（时辰与昼夜，铁律 6 要夜里写得出夜色），另有此地的 <crowds>（名｜约数｜此刻在做什么或溃散逃离）、
       <activities>（「交手｜你、龚光杰｜已结束」，按先后）、<traces>（「地上点点血迹｜还剩约十二个时辰」）、<rumors>（传到此地的消息正文），
       空则不出现、逐值转义、act: / trc: / swm: / tok: id 从不露出；铁律 1 许已结束的事与痕迹只作往事形迹、铁律 3 让溃散的人群不在原处做原来的事，
       并钉死在场之人只知道 <rumors>、亲眼所见与自己本来的见闻——全局事件流从不进提示词，<memories> 与短期记忆是玩家自己记得的，NPC 并不知道。
       跨进新地方那一回合另起 <short_term_memory>（<left> 刚离开之地、<seen> 出发前眼中所见、<motivation> 此行所为），否则此行所为非空时单给 <motivation>：
       铁律 4 让说书人把此行所为与眼前所见对照，写出预期落差（期待落空、意外撞见、物是人非），不替玩家改主意、不替玩家行动
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.application.chronicle import known_arts, titled
from app.application.navigation import duration_label
from app.application.ports import LLMClient
from app.domain.approach import TacticalAxis
from app.domain.commands import TICKS_PER_SHICHEN
from app.domain.intent import PlayerIntent
from app.domain.snapshot import ActivityView, CharacterView, ExitView, LocalSnapshot, SwarmView, TraceView
from app.domain.stakes import Risk
from app.errors import LLMError

logger = logging.getLogger(__name__)
SEEN_MAX = 8
PICKS_MAX = 8  # <menu> 至多读这么多项（过闸后只留 3~4 席）：载荷有界，胡写一长串也不拖累
MENU_MARKERS = ("<menu", "```", "[{")  # 正文到此为止：菜单的开标签、说书人自作主张包上的围栏、或连标签也没写的裸 JSON 数组（「[」与「{」之间可隔空白）
HEADING_MAX = 16  # 紧贴在标记之前、这么短又没有句末标点的一行，是说书人自添的小标题（「菜单：」「**可选之招**」），不进正文


@dataclass(frozen=True, slots=True)
class ShortTermMemory:
    """
    跨进新地方那一回合的短期记忆：上一回合眼中所见（刚离开之地的人、人群、痕迹）与此行所为。
    说书人据此写出预期落差（期待落空、意外撞见）；这些是玩家记得的事，此地的人并不知道。
    """

    left: str  # 刚离开之地
    seen: tuple[str, ...] = ()  # 离开前一刻眼中所见，每项一行，至多 SEEN_MAX 行
    motivation: str = ""  # 此行所为（Moved.motivation）


def recollect(before: LocalSnapshot, motivation: str) -> ShortTermMemory:
    """出发前的快照 → 短期记忆：在场之人（称呼、对你的态度、是否被制住）、人群（人数、此刻在做什么）、尚未消散的痕迹。"""
    seen = (
        *(f"{titled(c)}（对你{c.attitude.value}{'，已被你制住' if c.subdued else ''}）" for c in before.characters),
        *(f"{s.name}{headcount(s.size)}（{s.current_state}）" for s in before.swarms),  # 约数与 <crowds> 同一种写法
        *(t.description for t in before.traces),
    )
    return ShortTermMemory(left=before.location.name, seen=seen[:SEEN_MAX], motivation=motivation)


@dataclass(frozen=True, slots=True)
class MenuPick:
    """说书人从可供性目录里挑中的一招：key 是目录的编号（m1、m2……），flavor 是它为这一招配的武侠风味文案（未过闸）。"""

    key: str
    flavor: str


@dataclass(frozen=True, slots=True)
class MenuPicks:
    """叙事流的最后一项：说书人在正文之后交出的 <menu>（解析后、未过闸）。叙事流里至多一个，离线与降级的说书人从不产出。"""

    picks: tuple[MenuPick, ...] = ()


class Afforded(Protocol):
    """可供性目录里的一招（options 的 ActionOption 的结构子集）：叙事不必认识选项包，只读朴素标签、战术轴、缘由、风险与对象。"""

    @property
    def label(self) -> str: ...
    @property
    def tactical_axis(self) -> TacticalAxis: ...
    @property
    def why(self) -> str: ...
    @property
    def risk(self) -> Risk | None: ...
    @property
    def intent(self) -> PlayerIntent: ...


@dataclass(frozen=True, slots=True)
class NarrationRequest:
    snapshot: LocalSnapshot  # 回合结束后的局部真理快照
    facts: tuple[str, ...]  # 本回合已入账事件的白描（不可更改的结果）
    memories: tuple[str, ...] = ()  # 召回的往事
    player_text: str | None = None  # 玩家原话或所点选项的文案：只供照应笔墨
    style: str = ""
    fled: LocalSnapshot | None = None  # 本回合夺路逃离之处（交手的现场）：快照已是逃抵之地，仇人只在这里
    causes: Mapping[str, str] = field(default_factory=dict)  # 在场者本名 → 对你态度的由来（PlayerState.attitude_causes）
    recollection: ShortTermMemory | None = None  # 本回合跨进了新地方：出发前眼中所见与此行所为（短期记忆）
    motivation: str = ""  # 最近一次移动的此行所为（PlayerState.motivation）：没跨地方的回合也照应得上预期落差
    menu: tuple[Afforded, ...] = ()  # 可供性目录（options.catalogue 的 ActionOption，按次序编号 m1、m2……）：说书人只许从中挑 3~4 招配上风味
    errands: Mapping[str, str] = field(default_factory=dict)  # 在场者本名 → 他此行的议程意图（离了家、带议程的核心 NPC）


class Narrator(ABC):
    @abstractmethod
    def narrate(self, request: NarrationRequest) -> AsyncIterator[str | MenuPicks]:
        """流式渲染：若干段正文（str）；会配菜单的说书人在流尽后另交一个 MenuPicks（至多一个，且是最后一项）。"""


# ============================================================
#  Hard Prompt —— 每一行都来自图谱快照或事件白描；玩家的原话被转义并标明只是笔墨
# ============================================================
def _safe(text: str) -> str:
    return text.replace("<", "＜").replace(">", "＞")


def _join(parts: Iterable[str]) -> str:
    return "、".join(parts) or "无"


def _persona(c: CharacterView) -> str:
    """外显人设：「好：…；恶：…；心事：…｜」，缺哪段省哪段，全无则空。只读快照里的 persona，后文剧情（foreshadow）从不进快照。"""
    p = c.persona
    if p is None:
        return ""
    parts = [f"好：{'、'.join(p.likes)}" if p.likes else "", f"恶：{'、'.join(p.dislikes)}" if p.dislikes else "",
             f"心事：{p.worry}" if p.worry else ""]
    shown = "；".join(filter(None, parts))
    return f"{shown}｜" if shown else ""


def _scene(snap: LocalSnapshot, causes: Mapping[str, str], errands: Mapping[str, str]) -> list[str]:
    """
    一处地方与在场之人：<truth_snapshot> 与 <fled_scene> 共用同一种写法。知道恩怨缘由的人，态度之后写明「恩怨：…」；
    带议程而来的人再写「来意：…」（只作神色举止的端倪）；有人设的，描述之前写明好恶心事。
    """
    e, loc = _safe, snap.location
    known = {s.id: f"{s.name}（{s.kind}）" if s.kind else s.name for s in snap.skills}  # 带上类别：掌法不会被写成剑法
    people = [
        e(
            f"- {titled(c)}｜{c.faction or '无门无派'}｜{c.tier.value}｜性情{c.disposition.value}｜对你{c.attitude.value}｜"
            f"{f'恩怨：{causes[c.name]}｜' if c.name in causes else ''}{f'来意：{errands[c.name]}｜' if c.name in errands else ''}"
            f"{'已被你制住' if c.subdued else '行动自如'}｜身负：{_join(known.get(s, snap.label(s)) for s in c.skill_ids)}｜"
            f"随身：{_join(i.name for i in snap.items_of(c.id))}｜{_persona(c)}{c.description}"
        )
        for c in snap.characters
    ]
    return [
        f'<location name="{e(loc.name)}" region="{e(loc.region)}">{e(loc.description)}</location>',
        "<people>",
        *(people or ["（此处空无一人）"]),
        "</people>",
    ]


def way(x: ExitView) -> str:
    """
    一条出路：「南｜大理城｜步行｜约半个时辰」，未知的去处写「未知区域」。出口标签（引擎的把手，常带地名）从不写进来——
    说书人与地下城主看到的出路同一种写法，耗时与导航按钮上的同一种说法（navigation.duration_label）。
    """
    return f"{x.direction.value}｜{x.shown_name}｜{x.travel_method.value}｜{duration_label(x.time_cost)}"


def affordance(key: str, option: Afforded) -> str:
    """可供性目录的一行：「m1｜激化｜徒手向龚光杰出手｜对象：龚光杰｜缘由：仇怨未了｜风险：凶险」，缺哪段省哪段；指令与意图的其余字段从不写进来。"""
    parts = [key, option.tactical_axis.label, option.label]
    if target := option.intent.target_entity:
        parts.append(f"对象：{target}")
    if option.why:
        parts.append(f"缘由：{option.why}")
    if option.risk is not None:
        parts.append(f"风险：{option.risk.value}")
    return "｜".join(parts)


def _clocks(snap: LocalSnapshot) -> list[str]:
    """眼前的暗流：「- 钟灵的戒心｜疑心｜挂在钟灵｜1/4｜满则：识破你的手脚」。挂在玩家身上写「你」，id 从不露出。"""
    return [
        _safe(
            f"- {c.name}｜{c.kind.value}｜挂在{'你' if c.anchor_id == snap.player_id else snap.label(c.anchor_id)}｜"
            f"{c.progress}/{c.maximum}｜满则：{c.consequence or '未明'}"
        )
        for c in snap.clocks
    ]


# ============================================================
#  世界心跳的此地之物 —— 时辰、此地的事、痕迹、人群：说书人与地下城主的简报共用同一种写法，act: / trc: / swm: / tok: id 从不露出
# ============================================================
_DIGITS = "零一二三四五六七八九"


def _numeral(n: int) -> str:
    """一千以内的中文数字：五、十、三十、一百一十、二百零五。"""
    hundreds, rest = divmod(n, 100)
    tens, ones = divmod(rest, 10)
    head = f"{_DIGITS[hundreds]}百" if hundreds else ""
    if not rest:
        return head or _DIGITS[0]
    if not tens:
        return f"{head}{'零' if head else ''}{_DIGITS[ones]}"
    return f"{head}{'' if tens == 1 and not head else _DIGITS[tens]}十{_DIGITS[ones] if ones else ''}"


def when(snap: LocalSnapshot) -> str:
    """此刻的时辰与昼夜：「第一日·辰正｜白昼」「第二日·子初三刻｜黑夜」。"""
    return f"{snap.time_label}｜{'白昼' if snap.daylight else '黑夜'}"


def cast(snap: LocalSnapshot, ids: Iterable[str]) -> str:
    """一件事的参与者：玩家写「你」，人与人群取名（labels 覆盖 chr: / swm:），id 从不露出。"""
    return "、".join("你" if i == snap.player_id else snap.label(i) for i in ids)


def headcount(size: int) -> str:
    """人群的约数：十人以下照实，否则取整到十——「约三十人」「约一百五十人」。"""
    return f"约{_numeral(size if size < 10 else (size + 5) // 10 * 10)}人"


def lingering(remaining: int) -> str:
    """痕迹还剩多久：不足一个时辰按刻，否则按时辰（四舍五入）——「还剩约三刻」「还剩约十二个时辰」。"""
    if remaining < TICKS_PER_SHICHEN:
        return f"还剩约{_numeral(remaining)}刻"
    return f"还剩约{_numeral((remaining + TICKS_PER_SHICHEN // 2) // TICKS_PER_SHICHEN)}个时辰"


def activity(snap: LocalSnapshot, a: ActivityView) -> str:
    """此地的一件事：「交手｜你、龚光杰｜已结束」。"""
    return f"{a.kind.value}｜{cast(snap, a.participants)}｜{a.state.value}"


def trace(t: TraceView) -> str:
    """此地的一道痕迹：「地上点点血迹｜还剩约十二个时辰」。"""
    return f"{t.description}｜{lingering(t.remaining)}"


def crowd(s: SwarmView) -> str:
    """此地的一群人：「无量剑东宗弟子｜约三十人｜围观比剑」，受惊溃散时末段是「溃散逃离」。"""
    return f"{s.name}｜{headcount(s.size)}｜{s.current_state}"


def chronological(snap: LocalSnapshot) -> tuple[ActivityView, ...]:
    """此地的事按发生先后排（同刻按 id）：往事在前，眼下的在后。"""
    return tuple(sorted(snap.activities, key=lambda a: (a.started_tick, a.id)))


def _listed(tag: str, rows: Iterable[str]) -> list[str]:
    """一段列表：逐值转义，空则整段不出现。"""
    body = [f"- {_safe(r)}" for r in rows]
    return [f"<{tag}>", *body, f"</{tag}>"] if body else []


def _recollection(req: NarrationRequest) -> list[str]:
    """
    短期记忆：跨进新地方那一回合给 <short_term_memory>（刚离开之地、出发前眼中所见、此行所为，缺哪段省哪段）；
    否则此行所为非空时单给 <motivation>。都是玩家自己记得的事，此地的人并不知道。
    """
    e = _safe
    if (r := req.recollection) is not None:
        return [
            "<short_term_memory>",
            f"<left>{e(r.left)}</left>",
            *_listed("seen", r.seen[:SEEN_MAX]),
            *([f"<motivation>{e(r.motivation)}</motivation>"] if r.motivation else []),
            "</short_term_memory>",
        ]
    return [f"<motivation>{e(req.motivation)}</motivation>"] if req.motivation else []


def hard_prompt(req: NarrationRequest) -> str:
    """每个插值都经 _safe 转义：玩家写进意图的指称会出现在 ActionFailed 的白描里，不能让它闭合或伪造标签。"""
    snap, e = req.snapshot, _safe
    known = [f.text for f in snap.facts if f.known]  # 只有玩家已知的见闻：未知的是打探的标的，进了提示词就会被说破
    lines = [
        *(["<fled_scene>", *_scene(req.fled, {}, {}), "</fled_scene>"] if req.fled else []),  # 交手前的样子：恩怨以 facts 为准
        "<truth_snapshot>",
        f"<time>{e(when(snap))}</time>",
        *_scene(snap, req.causes, req.errands),
        *_listed("crowds", map(crowd, snap.swarms)),
        *(_listed("exits", map(way, snap.exits)) or ["<exits>无</exits>"]),  # 迷雾：未知的去处只写「未知区域」，标签从不进来
        f"<ground>{e(_join(i.name for i in snap.ground_items))}</ground>",
        *_listed("activities", (activity(snap, a) for a in chronological(snap))),  # 已结束者是往事形迹，不是正在发生
        *_listed("traces", map(trace, snap.traces)),
        *_listed("rumors", (r.text for r in snap.rumors)),  # 在场之人知道的玩家所作所为，只有这些（与亲眼所见）
        (
            f'<player name="{e(snap.player_name)}" alive="{str(snap.alive).lower()}">'
            f"伤势：{e(snap.vitality.value)}；武学：{e(_join(known_arts(snap)))}；"
            f"行囊：{e(_join(i.name for i in snap.inventory))}</player>"
        ),
        *(["<clocks>", *_clocks(snap), "</clocks>"] if snap.clocks else []),
        *(["<emerged>", *(f"- {e(m.text)}" for m in snap.emerged), "</emerged>"] if snap.emerged else []),
        "</truth_snapshot>",
        *(["<known_facts>", *(f"- {e(t)}" for t in known), "</known_facts>"] if known else []),
        "<settled_facts>",
        *(f"{n}. {e(fact)}" for n, fact in enumerate(req.facts, start=1)),
        "</settled_facts>",
        "<memories>",
        *(f"- {e(m)}" for m in req.memories),
        "</memories>",
        *_recollection(req),
        *_listed("affordances", (affordance(f"m{n}", o) for n, o in enumerate(req.menu, start=1))),
        f'<player_input style="{e(req.style)}">{e(req.player_text or "（初入此地）")}</player_input>',
        *([_MENU_REMINDER] if req.menu else []),
    ]
    return "\n".join(lines)


_MENU_REMINDER = "<reminder>正文写完，另起一行交 <menu>，内为 JSON 数组（从可供性目录挑三到四招），以 </menu> 收尾，此后不再写字（第 9 条）。</reminder>"


NARRATOR_SYSTEM = """你是《天龙八部》文字世界的说书人，以金庸先生的笔法为玩家渲染眼前这一幕，并为玩家接下来可出的招配上一句武侠风味。

你只是渲染者，不是裁判：
1. <settled_facts> 是世界引擎已经裁定并记入史册的结果。照实去写，不得更改、推翻、弱化或追加任何结果——失败就写失败，受伤就写受伤，重伤逃脱就写重伤逃脱，身死就写身死。
   <settled_facts> 里没有「来到某地」，你就仍在 <location>：被击退就写踉跄站定或倒地喘息，不写离开此地、奔出门外。
   有 <fled_scene> 时，交手发生在那里：先写那一场交手，再写你夺路逃到 <truth_snapshot> 的 <location>，仇人留在身后。
   <settled_facts> 之外的变化一概不写：伤势只照 <player> 的伤势去写，不写好转或恶化；出路只照 <exits> 去写，不写被封被堵；不写有人追来、有人援手。
   <clocks> 是眼前悬着的暗流（某人的疑心、怒火，局势的险恶，一段交情的进展）：只写气氛与端倪——一个眼神、一声低语、风里的动静——
   不写它满了、不替它坍缩、不写它带来的后果；只有 <settled_facts> 明写「……满了」，那件事才算发生，照写即可。
   <emerged> 是此世早先确实发生过的细节，可以照应，不得推翻；<settled_facts> 里的细节同样照写，不增不减。
   <affordances> 是玩家此刻可出的招，只是可能，不是结果：正文可以给你挑进 <menu> 的那几招铺垫端倪——某人的神色、目光、一句半句的话头，某件东西摆在哪里——
   但不得把端倪写成已经发生的事：想打听的事没人说破，想要的东西不会到手，想拜的师不会松口，想去的地方还没去。
   <activities> 里已结束的事与 <traces> 的痕迹是此地的往事形迹：可以照应（地上的血迹、凌乱的脚印、人群散去后的狼藉），不可写成正在发生；
   事中之人此刻在不在，只看 <people>。
2. 你只能写 <truth_snapshot> 与 <fled_scene> 里存在的人、物、地点、出路与武功（<fled_scene> 里的人只出现在你逃离之前）。不得引入任何新人物、新物品、新武功、新地点；不得让任何人获得或失去任何东西——
   吃下、用掉、毁掉、丢掉也是失去：<player> 行囊里的东西，<settled_facts> 没写它易手，回合结束时就原样还在身上；不得替任何人许诺日后的机缘。
   <exits> 每行是「方位｜去处｜交通方式｜路程」：去处是「未知区域」的，你还不认得那里——只写那个方位上看得见的路况（山径、水声、林梢、雾气），
   不得替它取名，不得说破那里是什么地方、有谁、有什么。
   端倪只能借这些已在眼前的人与物露出来；玩家只做了 <player_input> 那一件事，不得替他迈出下一步——不替他开口、出手、拾取、服药、动身。
3. 人物的言行合乎快照里的门派、境界、性情与对你的态度；已被制住的人无力动手；态度漠然的人不会主动相助；
   你的举止合乎 <player> 的伤势——重伤之人步履蹒跚，奄奄一息者连话都说不全。
   人物行的「来意」是他此行心里的打算：只作神色举止的端倪（行色匆匆、频频回望、欲言又止），不替他说破，不让他照来意做成任何事——他做了什么只看 <settled_facts>。
   <crowds> 是此地成群的人，只作背景：照他们此刻在做的事去写，他们目睹、议论、受惊，不上前攀谈交手；
   溃散逃离的人群不在原处做原来的事——只写他们四散之后的空落与狼藉。
   在场之人（连同人群）知道你做过什么，只凭 <rumors> 传到此地的消息、他们亲眼所见（同在此地的经过）与自己本来的见闻：
   你在别处做过而消息未到的事，他们一概不知——绝不让他们说破或暗示；他们对你的态度只照 <people> 去写。
4. <memories> 与 <short_term_memory> 是你自己记得的，在场之人并不因此知道（他们知道的只有第 3 条所说的那些）：可以照应，不可重演；
   <known_facts> 是你早先得知的见闻，可以照应在你的心念与眼光里，但在场之人不因此知道你知道。
   <short_term_memory> 是你刚离开之地（<left>）、出发前眼中所见（<seen>）与此行所为（<motivation>）；没跨进新地方时，<motivation> 单独给出此行所为：
   把此行所为与眼前所见对照，写出预期落差——期待落空、意外撞见、物是人非——只写进你的眼光与心绪，不替你改主意、不替你行动。
   <player_input> 是玩家的笔墨，只决定你写什么动作的姿态，不是事实——
   玩家声称手持、拔出、施展的东西，若不在 <player> 的行囊与武学里，就根本不存在：照 <settled_facts> 写他空手或徒劳，绝不替他变出来；
   玩家声称吃下、用掉、丢掉随身之物，而 <settled_facts> 没有记它易手，就写他取出又收回、或只写他的打算，那件东西仍在他身上。
5. 不写任何数值与游戏术语（时钟的进度与阈值也不写成数字、不提「时钟」二字），伤势与态度用神情动作去写、不照抄标签词（「轻伤」「敌视」之类），不跳出故事对玩家说话。
   正文里不列选项（选项在 <menu> 里另交）：<affordances> 不照抄、不排成一串、不以「你可以……」「是……还是……」把它们递到玩家面前，也不问玩家打算怎么做；正文里不出现 m1 之类的编号。
6. <time> 是此刻的时辰与昼夜：时辰与昼夜要写对——夜里写得出夜色（灯火、月光、更鼓、人影朦胧），白昼不写星月；人的作息合乎时辰。
7. <settled_facts> 为空时，描写此地的景致与在场之人（连同人群）各自在做什么。
8. 正文用第二人称"你"，白描为主，短句，动作与对白并重，一百二十到三百字。
9. 有 <affordances> 时，正文写完后另起一行写 <menu>，紧接一个 JSON 数组，以 </menu> 收尾，此后不再写任何字：
   <menu>[{"pick": "m2", "flavor": "……"}, {"pick": "m5", "flavor": "……"}, {"pick": "m1", "flavor": "……"}]</menu>
   挑三到四招：pick 只能取 <affordances> 的编号、不重复，尽量覆盖不同的战术维度（激化、诡道、化解、旁观），优先挑正文里铺垫过端倪的招。
   flavor 是给这一招配的一句武侠风味，至多二十字：只写这一招的姿态与意图（怎么出手、怎么开口、怎么下手），不写结果（得手、制住、如愿、重伤之类）、不写数值，
   不用英文、数字与符号，不点 <affordances> 与 <truth_snapshot> 之外的人物、物件、武功与地名。
   没有 <affordances> 就只写正文，不写 <menu>。"""


# ============================================================
#  <menu> —— 正文之后的菜单：流式里扣住标记（防跨块），流尽后宽容解析
# ============================================================
_MENU_TAG = re.compile(r"</?\s*menu\s*>", re.IGNORECASE)
_FENCE = re.compile(r"```[A-Za-z]*")
_OBJECT = re.compile(r"\{[^{}]*\}")
_PICK_KEYS = ("pick", "key", "id")
_FLAVOR_KEYS = ("flavor", "flavor_text", "text")


_MARK = re.compile(r"<menu|```|\[\s*\{", re.IGNORECASE)  # MENU_MARKERS 的匹配式
_MARK_PREFIX = re.compile(r"(?:<(?:m(?:e(?:n)?)?)?|`{1,2}|\[\s*)$", re.IGNORECASE)  # 可能长成标记的尾巴
_CLOSERS = "。！？…」』”’）"


def _heading_at(text: str, end: int) -> int | None:
    """text[:end] 的最后一行若像小标题（换行之后、不长于 HEADING_MAX、没有句末标点），返回那行之前换行符的位置；否则 None。"""
    nl = text.rfind("\n", 0, end)
    line = text[nl + 1 : end].strip()
    if nl < 0 or not line or len(line) > HEADING_MAX or any(c in line for c in _CLOSERS):
        return None
    return nl


def _pending(text: str) -> int:
    """
    正文里从哪一处起须先扣住，下一块来了再定吐不吐：可能是标记前缀的尾巴（「<me」「``」「[」），
    以及换行之后还短、没有句末标点的那一行（标记若紧随其后，它就是小标题）——连同其前的空白。
    """
    m = _MARK_PREFIX.search(text)
    cut = len((text[: m.start()] if m else text).rstrip())
    if (nl := _heading_at(text, cut)) is not None:
        cut = len(text[:nl].rstrip())
    return cut


class _MenuSplitter:
    """把说书人的流切成正文与 <menu> 尾巴：正文照吐，一见标记即截断，标记之后的一切只进尾巴；紧贴标记之前的小标题一并截掉。"""

    def __init__(self) -> None:
        self._held = ""
        self.tail: str | None = None

    def feed(self, chunk: str) -> str:
        if self.tail is not None:
            self.tail += chunk
            return ""
        text = self._held + chunk
        if found := _MARK.search(text):
            at = found.start()
            self.tail, self._held = text[at:], ""
            body = text[:at].rstrip()
            nl = _heading_at(body, len(body))
            return (body if nl is None else body[:nl]).rstrip()
        cut = _pending(text)
        self._held = text[cut:]
        return text[:cut]

    def close(self) -> str:
        """流尽：没见到标记，扣住的尾巴原是正文（行末的空白一并吐出）；见到了则已在 tail 里。"""
        rest, self._held = self._held, ""
        return rest if self.tail is None else ""


def _key(raw: Any) -> str | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return f"m{raw}"  # 编号写成数字：3 即 m3
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def read_menu(tail: str) -> MenuPicks:
    """
    <menu> 尾巴 → MenuPicks（未过闸）。宽容：去掉 <menu> / </menu> 与围栏、缺 </menu> 照读、多余字段忽略、
    pick 写成数字即「m数字」；整段数组读不了就逐个对象地捞（半截的数组也捞得回前几项）；什么也捞不到即空。至多 PICKS_MAX 项。
    """
    body = _FENCE.sub(" ", _MENU_TAG.sub(" ", tail))
    items: list[Any] = []
    start, end = body.find("["), body.rfind("]")
    if 0 <= start < end:
        try:
            parsed = json.loads(body[start : end + 1])
            items = parsed if isinstance(parsed, list) else []
        except ValueError:
            items = []
    if not items:
        for match in _OBJECT.finditer(body):
            try:
                items.append(json.loads(match.group()))
            except ValueError:
                continue
    picks = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        key = _key(next((item[k] for k in _PICK_KEYS if k in item), None))
        flavor = next((item[k] for k in _FLAVOR_KEYS if k in item), None)
        if key is not None and isinstance(flavor, str):
            picks.append(MenuPick(key=key, flavor=flavor.strip()))
    return MenuPicks(tuple(picks[:PICKS_MAX]))


class LLMNarrator(Narrator):
    """金庸风的流式说书人：同一次调用里先写正文、再交 <menu>。正文流式吐出，<menu> 之后的字一个也不进正文；流尽后恒交一个 MenuPicks（读不出即空）。"""

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def narrate(self, request: NarrationRequest) -> AsyncIterator[str | MenuPicks]:
        splitter = _MenuSplitter()
        async for chunk in self._llm.stream(NARRATOR_SYSTEM, hard_prompt(request)):
            if text := splitter.feed(chunk):
                yield text
        if rest := splitter.close():
            yield rest
        picks = read_menu(splitter.tail) if splitter.tail is not None else MenuPicks()
        if splitter.tail is not None and not picks.picks:
            logger.warning("说书人的 <menu> 读不出任何一招，改用退路菜单")
        yield picks


class TemplateNarrator(Narrator):
    """离线说书人：只用快照与白描拼出确定性的文字，逐句吐出以走通流式链路；不配菜单（下发退路菜单的朴素标签）。"""

    async def narrate(self, request: NarrationRequest) -> AsyncIterator[str]:
        snap = request.snapshot
        scene = f"{snap.location.name}。{snap.location.description}".rstrip("。") + "。"
        people = "、".join(c.name + ("（已被制住）" if c.subdued else "") for c in snap.characters)
        sentences = [
            *request.facts,
            scene,
            f"时值{snap.time_label}，{'天光正亮' if snap.daylight else '夜色深沉'}。",
            f"此处有{people}。" if people else "四下无人。",
            f"地上有{_join(i.name for i in snap.ground_items)}。" if snap.ground_items else "",
            # 迷雾：只写方位与去处（未知即「未知区域」），出口标签不露
            f"出路：{_join(f'{e.direction.value}（{e.shown_name}）' for e in snap.exits)}。" if snap.exits else "此地无路可走。",
        ]
        for sentence in filter(None, sentences):
            yield sentence


class FallbackNarrator(Narrator):
    """
    主渲染器失败时降级：一字未出则整段改用白描；已出半截则补一句断语与白描，保证玩家看得到已入账的结果。
    主渲染器交出的 MenuPicks 原样透传；降级了就没有 MenuPicks——调用方用退路菜单。
    """

    def __init__(self, primary: Narrator, fallback: Narrator) -> None:
        self._primary = primary
        self._fallback = fallback

    async def narrate(self, request: NarrationRequest) -> AsyncIterator[str | MenuPicks]:
        emitted = False
        try:
            async for chunk in self._primary.narrate(request):
                emitted = emitted or isinstance(chunk, str)
                yield chunk
            return
        except LLMError as exc:
            logger.warning("叙事渲染失败，降级为白描：%s", exc)
        if emitted:
            yield "\n（天机中断，以下据实白描）\n"
        async for chunk in self._fallback.narrate(request):
            yield chunk
