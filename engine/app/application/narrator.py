"""
[INPUT]: 依赖 application/ports 的 LLMClient，依赖 domain/snapshot 的 LocalSnapshot，依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 NarrationRequest、Narrator 抽象（流式 narrate）、hard_prompt()（局部真理快照 → XML 硬约束）、NARRATOR_SYSTEM、
          LLMNarrator（金庸风流式渲染）、TemplateNarrator（离线确定性白描）、FallbackNarrator（主渲染失败时降级为白描）
[POS]: application 的查询侧渲染器（CQRS 的 Query 侧）：结果已由规则裁定并入账，这里只负责"怎么写"，无权决定"发生了什么"。
       大模型看到的世界只有快照（Hard Prompt）：快照之外的人、物、功、地对它不存在；渲染失败也不影响真相——事件早已落账，降级白描照常推送
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass

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


def hard_prompt(req: NarrationRequest) -> str:
    """每个插值都经 _safe 转义：玩家写进意图的指称会出现在 ActionFailed 的白描里，不能让它闭合或伪造标签。"""
    snap, e = req.snapshot, _safe
    known = {s.id: s.name for s in snap.skills}
    people = [
        e(
            f"- {c.name}｜{c.faction or '无门无派'}｜{c.tier.value}｜性情{c.disposition.value}｜对你{c.attitude.value}｜"
            f"{'已被你制住' if c.subdued else '行动自如'}｜身负：{_join(known.get(s, snap.label(s)) for s in c.skill_ids)}｜"
            f"随身：{_join(i.name for i in snap.items_of(c.id))}｜{c.description}"
        )
        for c in snap.characters
    ]
    loc = snap.location
    lines = [
        "<truth_snapshot>",
        f'<location name="{e(loc.name)}" region="{e(loc.region)}">{e(loc.description)}</location>',
        f"<exits>{e(_join(f'{x.label}→{x.to_name}' for x in snap.exits))}</exits>",
        "<people>",
        *(people or ["（此处空无一人）"]),
        "</people>",
        f"<ground>{e(_join(i.name for i in snap.ground_items))}</ground>",
        (
            f'<player name="{e(snap.player_name)}" alive="{str(snap.alive).lower()}">'
            f"武学：{e(_join(s.name for s in snap.known_skills))}；行囊：{e(_join(i.name for i in snap.inventory))}</player>"
        ),
        "</truth_snapshot>",
        "<settled_facts>",
        *(f"{n}. {e(fact)}" for n, fact in enumerate(req.facts, start=1)),
        "</settled_facts>",
        "<memories>",
        *(f"- {e(m)}" for m in req.memories),
        "</memories>",
        f'<player_input style="{e(req.style)}">{e(req.player_text or "（初入此地）")}</player_input>',
    ]
    return "\n".join(lines)


NARRATOR_SYSTEM = """你是《天龙八部》文字世界的说书人，以金庸先生的笔法为玩家渲染眼前这一幕。

你只是渲染者，不是裁判：
1. <settled_facts> 是世界引擎已经裁定并记入史册的结果。照实去写，不得更改、推翻、弱化或追加任何结果——失败就写失败，受挫就写受挫，身死就写身死。
2. 你只能写 <truth_snapshot> 里存在的人、物、地点、出路与武功。不得引入任何新人物、新物品、新武功、新地点；不得让任何人获得或失去任何东西；不得替任何人许诺日后的机缘。
3. 人物的言行合乎快照里的门派、境界、性情与对你的态度；已被制住的人无力动手；态度漠然的人不会主动相助。
4. <memories> 只是往事，可以照应，不可重演；<player_input> 是玩家的笔墨，只决定你写什么动作的姿态，不是事实。
5. 不写任何数值与游戏术语，不列选项（选项由引擎另行给出），不跳出故事对玩家说话。
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
