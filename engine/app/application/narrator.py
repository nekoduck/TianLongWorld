"""
[INPUT]: 依赖 application/ports 的 LLMClient，依赖 application/chronicle 的 titled / known_arts，依赖 domain/snapshot 的 LocalSnapshot，
         依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 NarrationRequest（含夺路逃离的交手现场 fled、在场者的恩怨缘由 causes、本回合菜单的端倪 hooks、短期记忆 recollection 与此行所为 motivation）、
          ShortTermMemory 与 recollect()（出发前的快照 + 此行所为 → 短期记忆）、
          hooks()（选项 → 「标签（why）」端倪，至多 HOOKS_MAX 条）、Narrator 抽象（流式 narrate）、hard_prompt()（局部真理快照 → XML 硬约束）、NARRATOR_SYSTEM、
          LLMNarrator（金庸风流式渲染）、TemplateNarrator（离线确定性白描）、FallbackNarrator（主渲染失败时降级为白描）
[POS]: application 的查询侧渲染器（CQRS 的 Query 侧）：结果已由规则裁定并入账，这里只负责"怎么写"，无权决定"发生了什么"。
       大模型看到的世界只有快照（Hard Prompt）：快照之外的人、物、功、地对它不存在；渲染失败也不影响真相——事件早已落账，降级白描照常推送。
       地下城主不再交散文速写：它推演出的微观事实（FactEmerged）已入账，经白描进 <settled_facts>；往回合推演出的、点了眼前之名的细节
       经快照进 <truth_snapshot> 的 <emerged>，可照应不可推翻。眼前悬着的叙事时钟进 <clocks>（名称、种类、挂处、进度 / 阈值、满则如何）：
       铁律 1 许它只作暗流——气氛与端倪，不替它坍缩；坍缩是 <settled_facts> 里明写的「……满了」，照写即可。两段逐值转义，空则不出现。
       铁律据真实整局实测补强：没有「来到某地」就仍在原地、facts 之外的变化（伤势好转、退路被封、有人追来）一概不写、
       行囊里的东西 facts 没写它易手就仍在身上（玩家"嚼下通天草"不等于吃掉了）、伤势与态度不照抄标签词；在场者所会武学带类别（掌法不被写成剑法）。
       重伤夺路而逃的回合，快照已是逃抵之地，交手现场另作 <fled_scene>：仇人在那里，先写交手再写逃。
       在场者的人物行在态度之后附「恩怨：…」（取自 RelationChanged.cause 的折叠）：只是一行数据，铁律不改——说书人不必再自己编仇从何来。
       人物行另附外显人设「好…；恶…；心事…」（只取 CharacterView.persona，后文剧情从不进快照）；<known_facts> 只放玩家已知（known=True）的见闻，
       玩家不知道的见闻（known=False）绝不进任何提示词——它是打探的标的，说书人一旦知道就会替 NPC 说破。
       <hooks> 是引擎先算好的本回合菜单（「标签（why）」）：铁律 1 / 2 / 5 许它作端倪自然露在场面里（神色、目光、只言片语、物件所在），
       不许写成已发生的结果、不许替玩家行动、不许列成选项——叙事于是给菜单铺垫，菜单不再凭空冒出来
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

from app.application.chronicle import known_arts, titled
from app.application.ports import LLMClient
from app.domain.snapshot import CharacterView, LocalSnapshot
from app.errors import LLMError

logger = logging.getLogger(__name__)
HOOKS_MAX = 4
SEEN_MAX = 8


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
        *(f"{s.name}约{s.size}人（{s.current_state}）" for s in before.swarms),
        *(t.description for t in before.traces),
    )
    return ShortTermMemory(left=before.location.name, seen=seen[:SEEN_MAX], motivation=motivation)


@dataclass(frozen=True, slots=True)
class NarrationRequest:
    snapshot: LocalSnapshot  # 回合结束后的局部真理快照
    facts: tuple[str, ...]  # 本回合已入账事件的白描（不可更改的结果）
    memories: tuple[str, ...] = ()  # 召回的往事
    player_text: str | None = None  # 玩家原话或所点选项的标签：只供照应笔墨
    style: str = ""
    fled: LocalSnapshot | None = None  # 本回合夺路逃离之处（交手的现场）：快照已是逃抵之地，仇人只在这里
    causes: Mapping[str, str] = field(default_factory=dict)  # 在场者本名 → 对你态度的由来（PlayerState.attitude_causes）
    hooks: tuple[str, ...] = ()  # 本回合菜单的端倪「标签（why）」：只许露在场面里，不是结果（handlers 先算菜单、经 hooks(options) 填好）
    recollection: ShortTermMemory | None = None  # 本回合跨进了新地方：出发前眼中所见与此行所为（短期记忆）
    motivation: str = ""  # 最近一次移动的此行所为（PlayerState.motivation）：没跨地方的回合也照应得上预期落差


class _Offered(Protocol):  # ActionOption 的结构子集：叙事不必认识选项包
    @property
    def label(self) -> str: ...
    @property
    def why(self) -> str: ...


def hooks(options: Iterable[_Offered]) -> tuple[str, ...]:
    """菜单 → 端倪：每项「标签（why）」，至多 HOOKS_MAX 条。标签与 why 本就不含见闻正文（选项包的约定），这里照抄即可。"""
    return tuple(f"{o.label}（{o.why}）" if o.why else o.label for o in options)[:HOOKS_MAX]


class Narrator(ABC):
    @abstractmethod
    def narrate(self, request: NarrationRequest) -> AsyncIterator[str]: ...


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


def _scene(snap: LocalSnapshot, causes: Mapping[str, str]) -> list[str]:
    """一处地方与在场之人：<truth_snapshot> 与 <fled_scene> 共用同一种写法。知道恩怨缘由的人，态度之后写明「恩怨：…」；有人设的，描述之前写明好恶心事。"""
    e, loc = _safe, snap.location
    known = {s.id: f"{s.name}（{s.kind}）" if s.kind else s.name for s in snap.skills}  # 带上类别：掌法不会被写成剑法
    people = [
        e(
            f"- {titled(c)}｜{c.faction or '无门无派'}｜{c.tier.value}｜性情{c.disposition.value}｜对你{c.attitude.value}｜"
            f"{f'恩怨：{causes[c.name]}｜' if c.name in causes else ''}"
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


def _clocks(snap: LocalSnapshot) -> list[str]:
    """眼前的暗流：「- 钟灵的戒心｜疑心｜挂在钟灵｜1/4｜满则：识破你的手脚」。挂在玩家身上写「你」，id 从不露出。"""
    return [
        _safe(
            f"- {c.name}｜{c.kind.value}｜挂在{'你' if c.anchor_id == snap.player_id else snap.label(c.anchor_id)}｜"
            f"{c.progress}/{c.maximum}｜满则：{c.consequence or '未明'}"
        )
        for c in snap.clocks
    ]


def hard_prompt(req: NarrationRequest) -> str:
    """每个插值都经 _safe 转义：玩家写进意图的指称会出现在 ActionFailed 的白描里，不能让它闭合或伪造标签。"""
    snap, e = req.snapshot, _safe
    known = [f.text for f in snap.facts if f.known]  # 只有玩家已知的见闻：未知的是打探的标的，进了提示词就会被说破
    lines = [
        *(["<fled_scene>", *_scene(req.fled, {}), "</fled_scene>"] if req.fled else []),  # 交手前的样子：恩怨以 facts 为准
        "<truth_snapshot>",
        *_scene(snap, req.causes),
        f"<exits>{e(_join(f'{x.label}→{x.to_name}' for x in snap.exits))}</exits>",
        f"<ground>{e(_join(i.name for i in snap.ground_items))}</ground>",
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
        *(["<hooks>", *(f"- {e(h)}" for h in req.hooks[:HOOKS_MAX]), "</hooks>"] if req.hooks else []),
        f'<player_input style="{e(req.style)}">{e(req.player_text or "（初入此地）")}</player_input>',
    ]
    return "\n".join(lines)


NARRATOR_SYSTEM = """你是《天龙八部》文字世界的说书人，以金庸先生的笔法为玩家渲染眼前这一幕。

你只是渲染者，不是裁判：
1. <settled_facts> 是世界引擎已经裁定并记入史册的结果。照实去写，不得更改、推翻、弱化或追加任何结果——失败就写失败，受伤就写受伤，重伤逃脱就写重伤逃脱，身死就写身死。
   <settled_facts> 里没有「来到某地」，你就仍在 <location>：被击退就写踉跄站定或倒地喘息，不写离开此地、奔出门外。
   有 <fled_scene> 时，交手发生在那里：先写那一场交手，再写你夺路逃到 <truth_snapshot> 的 <location>，仇人留在身后。
   <settled_facts> 之外的变化一概不写：伤势只照 <player> 的伤势去写，不写好转或恶化；出路只照 <exits> 去写，不写被封被堵；不写有人追来、有人援手。
   <clocks> 是眼前悬着的暗流（某人的疑心、怒火，局势的险恶，一段交情的进展）：只写气氛与端倪——一个眼神、一声低语、风里的动静——
   不写它满了、不替它坍缩、不写它带来的后果；只有 <settled_facts> 明写「……满了」，那件事才算发生，照写即可。
   <emerged> 是此世早先确实发生过的细节，可以照应，不得推翻；<settled_facts> 里的细节同样照写，不增不减。
   <hooks> 是玩家接下来可能去做的事，只是端倪，不是结果：可以让它们自然露在场面里——某人的神色、目光、一句半句的话头，某件东西摆在哪里——
   但不得把端倪写成已经发生的事：想打听的事没人说破，想要的东西不会到手，想拜的师不会松口，想去的地方还没去。
2. 你只能写 <truth_snapshot> 与 <fled_scene> 里存在的人、物、地点、出路与武功（<fled_scene> 里的人只出现在你逃离之前）。不得引入任何新人物、新物品、新武功、新地点；不得让任何人获得或失去任何东西——
   吃下、用掉、毁掉、丢掉也是失去：<player> 行囊里的东西，<settled_facts> 没写它易手，回合结束时就原样还在身上；不得替任何人许诺日后的机缘。
   端倪只能借这些已在眼前的人与物露出来；玩家只做了 <player_input> 那一件事，不得替他迈出下一步——不替他开口、出手、拾取、服药、动身。
3. 人物的言行合乎快照里的门派、境界、性情与对你的态度；已被制住的人无力动手；态度漠然的人不会主动相助；
   你的举止合乎 <player> 的伤势——重伤之人步履蹒跚，奄奄一息者连话都说不全。
4. <memories> 只是往事，可以照应，不可重演；<known_facts> 是你早先得知的见闻，可以照应在你的心念与眼光里，但在场之人不因此知道你知道；<player_input> 是玩家的笔墨，只决定你写什么动作的姿态，不是事实——
   玩家声称手持、拔出、施展的东西，若不在 <player> 的行囊与武学里，就根本不存在：照 <settled_facts> 写他空手或徒劳，绝不替他变出来；
   玩家声称吃下、用掉、丢掉随身之物，而 <settled_facts> 没有记它易手，就写他取出又收回、或只写他的打算，那件东西仍在他身上。
5. 不写任何数值与游戏术语（时钟的进度与阈值也不写成数字、不提「时钟」二字），伤势与态度用神情动作去写、不照抄标签词（「轻伤」「敌视」之类），不跳出故事对玩家说话。
   不列选项（选项由引擎另行给出）：<hooks> 不照抄、不排成一串、不以「你可以……」「是……还是……」把它们递到玩家面前，也不问玩家打算怎么做。
6. <settled_facts> 为空时，描写此地的景致与在场之人各自在做什么。
7. 第二人称"你"，白描为主，短句，动作与对白并重，一百二十到三百字。"""


class LLMNarrator(Narrator):
    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def narrate(self, request: NarrationRequest) -> AsyncIterator[str]:
        async for chunk in self._llm.stream(NARRATOR_SYSTEM, hard_prompt(request)):
            yield chunk


class TemplateNarrator(Narrator):
    """离线说书人：只用快照与白描拼出确定性的文字，逐句吐出以走通流式链路。"""

    async def narrate(self, request: NarrationRequest) -> AsyncIterator[str]:
        snap = request.snapshot
        scene = f"{snap.location.name}。{snap.location.description}".rstrip("。") + "。"
        people = "、".join(c.name + ("（已被制住）" if c.subdued else "") for c in snap.characters)
        sentences = [
            *request.facts,
            scene,
            f"此处有{people}。" if people else "四下无人。",
            f"地上有{_join(i.name for i in snap.ground_items)}。" if snap.ground_items else "",
            f"出路：{_join(f'{e.label}（{e.to_name}）' for e in snap.exits)}。" if snap.exits else "此地无路可走。",
        ]
        for sentence in filter(None, sentences):
            yield sentence


class FallbackNarrator(Narrator):
    """主渲染器失败时降级：一字未出则整段改用白描；已出半截则补一句断语与白描，保证玩家看得到已入账的结果。"""

    def __init__(self, primary: Narrator, fallback: Narrator) -> None:
        self._primary = primary
        self._fallback = fallback

    async def narrate(self, request: NarrationRequest) -> AsyncIterator[str]:
        emitted = False
        try:
            async for chunk in self._primary.narrate(request):
                emitted = True
                yield chunk
            return
        except LLMError as exc:
            logger.warning("叙事渲染失败，降级为白描：%s", exc)
        if emitted:
            yield "\n（天机中断，以下据实白描）\n"
        async for chunk in self._fallback.narrate(request):
            yield chunk
