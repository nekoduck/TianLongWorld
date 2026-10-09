"""
[INPUT]: 依赖 application/ports 的 LLMClient，依赖 application/chronicle 的 titled / known_arts，依赖 domain/snapshot 的 LocalSnapshot，
         依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 NarrationRequest（含地下城主的速写 hint、夺路逃离的交手现场 fled 与在场者的恩怨缘由 causes）、Narrator 抽象（流式 narrate）、hard_prompt()（局部真理快照 → XML 硬约束）、NARRATOR_SYSTEM、
          LLMNarrator（金庸风流式渲染）、TemplateNarrator（离线确定性白描）、FallbackNarrator（主渲染失败时降级为白描）
[POS]: application 的查询侧渲染器（CQRS 的 Query 侧）：结果已由规则裁定并入账，这里只负责"怎么写"，无权决定"发生了什么"。
       大模型看到的世界只有快照（Hard Prompt）：快照之外的人、物、功、地对它不存在；渲染失败也不影响真相——事件早已落账，降级白描照常推送。
       地下城主的速写（<gm_sketch>）只是一招过程的散文素材：它与 <settled_facts> 一致才会被送来，叙事据此扩写招式，不能据此改判；
       速写不入事件、不入记忆，只活在这一回合的 Prompt 里。
       铁律据真实整局实测补强：没有「来到某地」就仍在原地、facts 之外的变化（伤势好转、退路被封、有人追来）一概不写、
       行囊里的东西 facts 没写它易手就仍在身上（玩家"嚼下通天草"不等于吃掉了）、伤势与态度不照抄标签词；在场者所会武学带类别（掌法不被写成剑法）。
       重伤夺路而逃的回合，快照已是逃抵之地，交手现场另作 <fled_scene>：仇人在那里，先写交手再写逃。
       在场者的人物行在态度之后附「恩怨：…」（取自 RelationChanged.cause 的折叠）：只是一行数据，铁律不改——说书人不必再自己编仇从何来
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass, field

from app.application.chronicle import known_arts, titled
from app.application.ports import LLMClient
from app.domain.snapshot import LocalSnapshot
from app.errors import LLMError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NarrationRequest:
    snapshot: LocalSnapshot  # 回合结束后的局部真理快照
    facts: tuple[str, ...]  # 本回合已入账事件的白描（不可更改的结果）
    memories: tuple[str, ...] = ()  # 召回的往事
    player_text: str | None = None  # 玩家原话或所点选项的标签：只供照应笔墨
    style: str = ""
    hint: str = ""  # 地下城主对这一招过程的速写：只在其结局被领域采纳时才有，与 facts 一致
    fled: LocalSnapshot | None = None  # 本回合夺路逃离之处（交手的现场）：快照已是逃抵之地，仇人只在这里
    causes: Mapping[str, str] = field(default_factory=dict)  # 在场者本名 → 对你态度的由来（PlayerState.attitude_causes）


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


def _scene(snap: LocalSnapshot, causes: Mapping[str, str]) -> list[str]:
    """一处地方与在场之人：<truth_snapshot> 与 <fled_scene> 共用同一种写法。知道恩怨缘由的人，态度之后写明「恩怨：…」。"""
    e, loc = _safe, snap.location
    known = {s.id: f"{s.name}（{s.kind}）" if s.kind else s.name for s in snap.skills}  # 带上类别：掌法不会被写成剑法
    people = [
        e(
            f"- {titled(c)}｜{c.faction or '无门无派'}｜{c.tier.value}｜性情{c.disposition.value}｜对你{c.attitude.value}｜"
            f"{f'恩怨：{causes[c.name]}｜' if c.name in causes else ''}"
            f"{'已被你制住' if c.subdued else '行动自如'}｜身负：{_join(known.get(s, snap.label(s)) for s in c.skill_ids)}｜"
            f"随身：{_join(i.name for i in snap.items_of(c.id))}｜{c.description}"
        )
        for c in snap.characters
    ]
    return [
        f'<location name="{e(loc.name)}" region="{e(loc.region)}">{e(loc.description)}</location>',
        "<people>",
        *(people or ["（此处空无一人）"]),
        "</people>",
    ]


def hard_prompt(req: NarrationRequest) -> str:
    """每个插值都经 _safe 转义：玩家写进意图的指称会出现在 ActionFailed 的白描里，不能让它闭合或伪造标签。"""
    snap, e = req.snapshot, _safe
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
        "</truth_snapshot>",
        "<settled_facts>",
        *(f"{n}. {e(fact)}" for n, fact in enumerate(req.facts, start=1)),
        "</settled_facts>",
        *([f"<gm_sketch>{e(req.hint)}</gm_sketch>"] if req.hint else []),
        "<memories>",
        *(f"- {e(m)}" for m in req.memories),
        "</memories>",
        f'<player_input style="{e(req.style)}">{e(req.player_text or "（初入此地）")}</player_input>',
    ]
    return "\n".join(lines)


NARRATOR_SYSTEM = """你是《天龙八部》文字世界的说书人，以金庸先生的笔法为玩家渲染眼前这一幕。

你只是渲染者，不是裁判：
1. <settled_facts> 是世界引擎已经裁定并记入史册的结果。照实去写，不得更改、推翻、弱化或追加任何结果——失败就写失败，受伤就写受伤，重伤逃脱就写重伤逃脱，身死就写身死。
   <settled_facts> 里没有「来到某地」，你就仍在 <location>：被击退就写踉跄站定或倒地喘息，不写离开此地、奔出门外。
   有 <fled_scene> 时，交手发生在那里：先写那一场交手，再写你夺路逃到 <truth_snapshot> 的 <location>，仇人留在身后。
   <settled_facts> 之外的变化一概不写：伤势只照 <player> 的伤势去写，不写好转或恶化；出路只照 <exits> 去写，不写被封被堵；不写有人追来、有人援手。
   <gm_sketch> 是地下城主对这一招过程的速写，与 <settled_facts> 一致：可据此扩写招式与情势，不得改变胜负与伤势。
2. 你只能写 <truth_snapshot> 与 <fled_scene> 里存在的人、物、地点、出路与武功（<fled_scene> 里的人只出现在你逃离之前）。不得引入任何新人物、新物品、新武功、新地点；不得让任何人获得或失去任何东西——
   吃下、用掉、毁掉、丢掉也是失去：<player> 行囊里的东西，<settled_facts> 没写它易手，回合结束时就原样还在身上；不得替任何人许诺日后的机缘。
3. 人物的言行合乎快照里的门派、境界、性情与对你的态度；已被制住的人无力动手；态度漠然的人不会主动相助；
   你的举止合乎 <player> 的伤势——重伤之人步履蹒跚，奄奄一息者连话都说不全。
4. <memories> 只是往事，可以照应，不可重演；<player_input> 是玩家的笔墨，只决定你写什么动作的姿态，不是事实——
   玩家声称手持、拔出、施展的东西，若不在 <player> 的行囊与武学里，就根本不存在：照 <settled_facts> 写他空手或徒劳，绝不替他变出来；
   玩家声称吃下、用掉、丢掉随身之物，而 <settled_facts> 没有记它易手，就写他取出又收回、或只写他的打算，那件东西仍在他身上。
5. 不写任何数值与游戏术语，伤势与态度用神情动作去写、不照抄标签词（「轻伤」「敌视」之类），不列选项（选项由引擎另行给出），不跳出故事对玩家说话。
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
        sentences = [  # 速写只出现在出手回合，那一招恒为首条事实：紧随其后，免得逃抵别处之后才补写交手
            *request.facts[:1],
            request.hint,  # 地下城主的速写与定案一致，离线降级时照样是一句可读的白描
            *request.facts[1:],
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
