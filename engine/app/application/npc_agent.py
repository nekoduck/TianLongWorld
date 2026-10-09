"""
[INPUT]: 依赖 application/ports 的 LLMClient / JsonSchema，依赖 application/briefs/agenda 的 Brief / agenda_brief / encounter_brief / skirmish_brief，
         依赖 application/resolution_agent 的 GM_SYSTEMS（撞见沿用地下城主结果已定之事的系统提示）与 _read（推演的宽容解析）、_by_name（枚举认中文也认英文名），
         依赖 domain/npc 的 planning_due / free / destinations / admit / AgendaProposal / skirmish_stakes / settle_skirmish / meet / SkirmishStakes，
         依赖 domain/agenda 的 Encounter / EncounterKind / SkirmishOutcome，依赖 domain/heartbeat 的 Atlas，
         依赖 domain/resolution 的 Envelope / ResolutionOutput / envelope_of / settle / _fact（微观事实的筛子，与闸门同一个），
         依赖 domain/rules 的 retreat（撞见里危机坍缩被迫脱身的去处），依赖 domain/approach 的 Route，
         依赖 domain/aggregates 的 PlayerState / evolve，依赖 domain/events 的 DomainEvent / Moved，依赖 domain/lore 的 Persona，
         依赖 domain/models 的 CharacterRelation / Era，依赖 domain/snapshot 的 LocalSnapshot，依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 分层 NPC 生态（H-Agent）的神经层与编排——
          宏观层：AgendaPlanner 抽象 plan(brief, schema) -> list[AgendaProposal]、LLMAgendaPlanner(llm, *, budget, attempts=2)（结构化输出 + 宽容解析；
                  LLMError / 超时 / 不合契约 / 意外一律 []，绝不抛错）、NullPlanner（不调大模型：一条议程也不立，idle——NpcDirector 不为它记空转的 AgendaPlanned）、AGENDA_SYSTEM；
          裁决层：EncounterJudge 抽象 meet(env, brief) -> ResolutionOutput | None 与 skirmish(stakes, brief) -> (SkirmishOutcome | None, fact | None)、
                  LLMEncounterJudge(llm, *, budget, canon_names, attempts=2)（撞见复用地下城主的系统提示 / 解析 / 预算，另加 MEET_NOTE；狭路相逢用 SKIRMISH_SYSTEM，
                  outcome 只认可裁结局、fact 过微观事实的筛子与原著名录预筛）、CanonicalJudge（都返回空：领域取确定性裁决）、MEET_SYSTEM / SKIRMISH_SYSTEM；
          编排：NpcDirector(planner, judge, atlas, personas, *, relations)——settle_encounters(state, snap, *, dm_quota=1)（按次序坍缩待裁决的中断，
                  前 dm_quota 次请判官、其余确定性；撞见 = FIXED 物理边界里的推演经 resolution.settle 过闸 → npc.meet，狭路相逢 = skirmish_stakes → 判官 → settle_skirmish）、
                  plan(state, events)（planning_due 才规划：简报 → planner → npc.admit；无人可领议程时不调大模型，planner 抛错也照记 AgendaPlanned）
[POS]: application 的 H-Agent：大模型只在刀刃上发言——宏观层一日至多一轮议程（江湖震动另算、冷却两个时辰），裁决层每回合至多一场请判官；
       微观层的寻路与行军全在 domain/npc.march（world_clock 每回合调用），这里一个大模型也不调。
       判官与议程都只是提议：议程过 npc.admit（名字全等落地、去处三跳以内、意图一行中文），撞见的推演过 resolution.settle（结果已定：只许动时钟、事实、名望），
       狭路相逢的结局过 settle_skirmish（区间外取确定性裁决）。大模型失灵（欠费、断网、限流、保险丝熔断、胡言、超时）一律退回确定性裁决并记 warning——
       它在命令侧玩家锁里同步等，宁可少一分推演，不可拖垮回合。简报只给局部认知：NPC 只知道传到他所在之处的消息，玩家身在何处不进议程简报（被动沙盒）
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Iterable, Mapping, Sequence
from functools import reduce
from typing import Any

from app.application.briefs.agenda import Brief, agenda_brief, encounter_brief, skirmish_brief
from app.application.ports import JsonSchema, LLMClient
from app.application.resolution_agent import GM_SYSTEMS, _by_name, _read
from app.domain import npc
from app.domain.agenda import AGENDA_CHARS, Encounter, EncounterKind, SkirmishOutcome
from app.domain.aggregates import PlayerState, evolve
from app.domain.approach import Route
from app.domain.events import DomainEvent, Moved
from app.domain.heartbeat import Atlas
from app.domain.lore import Persona
from app.domain.models import CharacterRelation, Era
from app.domain.npc import AgendaProposal, SkirmishStakes
from app.domain.resolution import Envelope, ResolutionOutput, _fact, envelope_of, settle
from app.domain.rules import retreat
from app.domain.snapshot import LocalSnapshot
from app.errors import LLMError

__all__ = [
    "AGENDA_SYSTEM",
    "MEET_SYSTEM",
    "SKIRMISH_SYSTEM",
    "AgendaPlanner",
    "CanonicalJudge",
    "EncounterJudge",
    "LLMAgendaPlanner",
    "LLMEncounterJudge",
    "NpcDirector",
    "NullPlanner",
]

logger = logging.getLogger(__name__)

# ============================================================
#  系统提示 —— 法则不点任何具体的人（具体的例子模型会照抄），占位一律写〈〉：照抄进来的过不了闸门
# ============================================================
AGENDA_SYSTEM = (
    "你是《天龙八部》文字世界的江湖推演者，为几位核心人物定下接下来的行止（宏观议程）。简报写明时辰、缘由与每位人物的绝对事实："
    "本名称号、门派、境界、性情、此刻所在、执念（心事）与好恶、对玩家的态度、此地情报、开篇的仇敌与羁绊、眼下的议程、可去之处与路程。\n"
    "法则：\n"
    "1. 议程只出于此人自己：他的执念、好恶、性情与恩怨，以及「此地情报」——传到他所在之处的消息。别处发生而消息未到的事，他一无所知，不可据以行动。\n"
    "2. 被动沙盒：不为制造剧情凭空安排相遇与巧合，不为撞上玩家而出门（他并不知道玩家身在何处）。"
    "大多数人大多数时候守在原处过自己的日子——没有出于执念或情报的充分理由，就写「留守」或干脆不写此人。\n"
    "3. npc 写本名；target 只能取此人「可去之处」里的地名，或「留守」（让眼下的议程作罢、不出门）。眼下的议程仍要继续的，照写原去处。\n"
    f"4. intent 写战略意图：一行中文、2~{AGENDA_CHARS} 字，写去做什么（「去〈地名〉〈做什么〉」），不写结果，不写英文、数字与任何标记。\n"
    "5. priority：1 寻常、2 要紧、3 志在必得。每人至多一条，一轮至多四人出门。\n"
    "6. 不得引入简报之外的人物、地点与往事，也不写后来的剧情。\n"
    '只输出符合 schema 的 JSON 对象：{"agendas": [{"npc": …, "target": …, "intent": …, "priority": …}]}，没有人要动就给空数组。'
)
_MEET_NOTE = (
    "撞见：这一回合要推演的不是玩家的举动，而是一场撞见——一位带着自己议程的人物与玩家在此不期而遇。"
    "简报里没有 <intent> 与 <player_input>：<encounter> 写明来者是谁、为何而来（他自己的议程）、对你的态度与恩怨。"
    "相遇本身已经发生，规则照常结算；你只推演它在暗流里的余波——来者对你起没起疑心、戒心、敌意或交情（新建或推进挂在他身上的时钟）、"
    "留一两条细节（一个神色、一句话头）、折损一点名望。来意只是他自己的事：对你漠然、与你素无瓜葛的人多半擦肩而过，"
    "那就什么也别动（deltas 与 clock_mutations 留空，new_facts 至多一条）。不替玩家行动，不写玩家说了什么；局部认知照闸门法则的末条——来者只知道传到此地的消息与亲眼所见。"
)
MEET_SYSTEM = f"{GM_SYSTEMS[Route.FIXED]}\n\n{_MEET_NOTE}"
SKIRMISH_SYSTEM = (
    "你是《天龙八部》文字世界的地下城主，裁决两位有开篇仇怨的人物狭路相逢。世界引擎已在 <physics> 圈出可裁结局与确定性裁决。\n"
    "推理（reasoning，一两句）：双方的境界（带伤折一档）、性情（仁厚者手下留情，中庸者打发了事，狠辣者往死里打）、各自的执念与来意、此地情势，谁压过谁。\n"
    "法则：\n"
    "1. outcome 只能取 <physics> 的可裁结局之一；区间外的一律作废，按确定性裁决结算。来者是走进这处地方的那一位，在此者是先到的那一位。\n"
    "2. fact 写一条这场相逢冒出来的细节（一个神色、一处痕迹、一句狠话）：一行中文，至多四十字，不写英文、数字与任何标记；"
    "人名地名只用简报里出现的；不得夹带状态变化（死了、毙命、杀了、夺下、到手、交给、学会、传授、离开了、来到、逃到、制住之类）——"
    "胜负、伤势与去留由结局写进账。没有值得写的就写空串。\n"
    "3. 局部认知：两人只知道 <rumors> 里传到此地的消息与自己本来的见闻；不得引入简报之外的人物、物品、武功与往事，也不写后来的剧情。\n"
    "只输出符合 schema 的 JSON 对象，不要解释。"
)


# ============================================================
#  共用 —— 预算、截取 JSON、原著名录预筛
# ============================================================
async def _within[T](work: Awaitable[T], budget: float, what: str) -> T | None:
    """时间预算管整场（重采样不另领一份）；LLMError、超时、任何意外一律 None 并记 warning——绝不抛错。"""
    try:
        return await asyncio.wait_for(work, budget)
    except LLMError as exc:
        logger.warning("%s失灵（%s），改由规则：%s", what, "可重试" if exc.retryable else "不可重试", exc)
    except TimeoutError:
        logger.warning("%s %.1f 秒内未答完，改由规则", what, budget)
    except Exception:
        logger.exception("%s出错，改由规则", what)
    return None


def _json(raw: str) -> Any:
    """截取回复里的 JSON（容忍围栏与前后闲话）：先出现的括号是哪种就先按哪种截（顶层是数组时，里面的对象不该被单独截走）。解不出即抛 ValueError。"""
    pairs = sorted((("{", "}"), ("[", "]")), key=lambda p: raw.find(p[0]) if p[0] in raw else len(raw))
    for opening, closing in pairs:
        start, end = raw.find(opening), raw.rfind(closing)
        if 0 <= start < end:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError("回复里没有可解析的 JSON")


def _stray(text: str, here: Sequence[str], canon: frozenset[str]) -> str | None:
    """事实点了原著里有、却不在此景的名字：返回那个名字。先剔掉此景的名字再查（长名先剔）。"""
    rest = text
    for name in here:
        rest = rest.replace(name, "　")
    return next((n for n in canon if n in rest), None)


# ============================================================
#  宏观层 —— 议程
# ============================================================
class AgendaPlanner(ABC):
    @abstractmethod
    async def plan(self, brief: str, schema: JsonSchema) -> list[AgendaProposal]:
        """为核心 NPC 提议议程。实现不得抛错：失灵时返回 []（领域照记一轮 AgendaPlanned，谁也不出门）。"""

    @property
    def idle(self) -> bool:
        """从不提议的规划者：NpcDirector 不为它记 AgendaPlanned——离线时事件流里不留一日一条的空转。"""
        return False


class NullPlanner(AgendaPlanner):
    """不调大模型的规划者：离线、mock、NPC_AGENDA 关掉时都是它——世界照样有时辰，人物各守原处，账上也不记规划的空转。"""

    async def plan(self, brief: str, schema: JsonSchema) -> list[AgendaProposal]:
        return []

    @property
    def idle(self) -> bool:
        return True


def _proposals(raw: str) -> list[AgendaProposal]:
    """宽容解析：容忍围栏、顶层直接是数组、多余字段、意图首尾的标点；不合契约的单条丢弃。整份解不出即抛 ValueError。"""
    data = _json(raw)
    items = data.get("agendas") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("回复里没有 agendas 数组")
    out: list[AgendaProposal] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        fixed = dict(item)
        if isinstance(fixed.get("intent"), str):
            fixed["intent"] = fixed["intent"].strip().strip("「」“”\"'").rstrip("。！；，、.!;,")
        try:
            out.append(AgendaProposal.model_validate(fixed))
        except ValueError as exc:
            logger.warning("一条议程提议不合契约，丢弃：%s", exc)
    return out


class LLMAgendaPlanner(AgendaPlanner):
    """宏观层的大模型：结构化输出 agendas 数组；不合契约重采样一次，仍不行或失灵即 []。只在规划时机（一日至多一轮、江湖震动另算）被请。"""

    def __init__(self, llm: LLMClient, *, budget: float = 10.0, attempts: int = 2) -> None:
        self._llm = llm
        self._budget = budget
        self._attempts = attempts

    async def plan(self, brief: str, schema: JsonSchema) -> list[AgendaProposal]:
        return await _within(self._consult(brief, schema), self._budget, "议程推演") or []

    async def _consult(self, brief: str, schema: JsonSchema) -> list[AgendaProposal]:
        for attempt in range(1, self._attempts + 1):
            raw = await self._llm.complete(AGENDA_SYSTEM, brief, schema)
            try:
                return _proposals(raw)
            except (ValueError, TypeError) as exc:
                logger.warning("议程推演第 %d 次不合契约：%s", attempt, exc)
        logger.warning("议程推演 %d 次皆不合契约，这一轮谁也不出门", self._attempts)
        return []


# ============================================================
#  裁决层 —— 判官
# ============================================================
class EncounterJudge(ABC):
    @abstractmethod
    async def meet(self, env: Envelope, brief: Brief) -> ResolutionOutput | None:
        """撞见：在结果已定的物理边界（FIXED）里推演余波。None 即交给规则（只记一条 EncounterResolved）。不得抛错。"""

    @abstractmethod
    async def skirmish(self, stakes: SkirmishStakes, brief: Brief) -> tuple[SkirmishOutcome | None, str | None]:
        """狭路相逢：在可裁区间里挑结局、可附一条细节。(None, None) 即取确定性裁决。不得抛错。"""


class CanonicalJudge(EncounterJudge):
    """不调大模型的判官：离线、mock、判官失灵时都是它——领域照样给出确定的结局。"""

    async def meet(self, env: Envelope, brief: Brief) -> ResolutionOutput | None:
        return None

    async def skirmish(self, stakes: SkirmishStakes, brief: Brief) -> tuple[SkirmishOutcome | None, str | None]:
        return None, None


class LLMEncounterJudge(EncounterJudge):
    """
    裁决层的大模型。撞见复用地下城主（结果已定之事的系统提示 + MEET_NOTE、同一个宽容解析、同一份预算口径），事实预筛照原著名录；
    狭路相逢只挑结局与一条细节：outcome 认中文也认英文名、不在区间里即作废（细节随之作废，免得与确定性裁决各说各话）。
    """

    def __init__(
        self, llm: LLMClient, *, budget: float = 8.0, canon_names: frozenset[str] = frozenset(), attempts: int = 2
    ) -> None:
        self._llm = llm
        self._budget = budget
        self._attempts = attempts
        self._canon = frozenset(n for n in canon_names if len(n) >= 2)  # 单字名不作数

    async def meet(self, env: Envelope, brief: Brief) -> ResolutionOutput | None:
        output = await _within(self._meet(brief), self._budget, "撞见的判官")
        if output is None or not output.new_facts or not self._canon:
            return output
        kept = []
        for text in output.new_facts:
            if (name := _stray(text, brief.names, self._canon)) is None:
                kept.append(text)
            else:
                logger.warning("撞见的事实点了此景之外的「%s」，丢弃：%s", name, text)
        return output if len(kept) == len(output.new_facts) else output.model_copy(update={"new_facts": tuple(kept)})

    async def _meet(self, brief: Brief) -> ResolutionOutput | None:
        for attempt in range(1, self._attempts + 1):
            raw = await self._llm.complete(MEET_SYSTEM, brief.text, brief.schema)
            try:
                return _read(raw)
            except (ValueError, TypeError) as exc:
                logger.warning("撞见的判官第 %d 次推演不合契约：%s", attempt, exc)
        return None

    async def skirmish(self, stakes: SkirmishStakes, brief: Brief) -> tuple[SkirmishOutcome | None, str | None]:
        return await _within(self._skirmish(stakes, brief), self._budget, "狭路相逢的判官") or (None, None)

    async def _skirmish(self, stakes: SkirmishStakes, brief: Brief) -> tuple[SkirmishOutcome | None, str | None]:
        for attempt in range(1, self._attempts + 1):
            raw = await self._llm.complete(SKIRMISH_SYSTEM, brief.text, brief.schema)
            try:
                data = _json(raw)
                if not isinstance(data, dict):
                    raise ValueError("回复不是 JSON 对象")
                outcome = SkirmishOutcome(_by_name(SkirmishOutcome, data.get("outcome", "")))
            except (ValueError, TypeError) as exc:
                logger.warning("狭路相逢的判官第 %d 次不合契约：%s", attempt, exc)
                continue
            if outcome not in stakes.admissible:
                logger.warning("狭路相逢的判官挑了区间外的「%s」，改取确定性裁决", outcome.value)
                return None, None
            return outcome, self._fact(data.get("fact"), brief)
        return None, None

    def _fact(self, raw: Any, brief: Brief) -> str | None:
        text = str(raw or "").strip()
        if not text:
            return None
        notes: list[str] = []
        kept = _fact(text, notes)
        if kept is None:
            logger.warning("狭路相逢的细节不合格，丢弃：%s", "；".join(notes))
            return None
        if (name := _stray(kept, brief.names, self._canon)) is not None:
            logger.warning("狭路相逢的细节点了此景之外的「%s」，丢弃：%s", name, kept)
            return None
        return kept


# ============================================================
#  编排 —— NpcDirector
# ============================================================
class NpcDirector:
    """
    H-Agent 的应用层门面，由 TurnPipeline 在命令侧玩家锁里、世界心跳入账之后调用：
    先 settle_encounters 坍缩待裁决的中断（每回合至多 dm_quota 场请判官），折叠后再 plan（只在规划时机）。
    两者返回的都是尚未入账的事件，由调用方追加；它们只编排不裁判——闸门全在 domain/npc 与 domain/resolution。
    """

    def __init__(
        self,
        planner: AgendaPlanner,
        judge: EncounterJudge,
        atlas: Atlas,
        personas: Mapping[str, Persona] | Iterable[Persona] = (),
        *,
        relations: Iterable[CharacterRelation] = (),
    ) -> None:
        self._planner = planner
        self._judge = judge
        self._atlas = atlas
        self._personas: dict[str, Persona] = (
            dict(personas) if isinstance(personas, Mapping) else {p.character_id: p for p in personas}
        )
        self._relations = tuple(r for r in relations if r.era is Era.OPENING)  # 只认开篇的恩怨

    # ---------------- 裁决层 ----------------
    async def settle_encounters(
        self, state: PlayerState, snap: LocalSnapshot, *, dm_quota: int = 1
    ) -> list[DomainEvent]:
        """
        按次序坍缩 state.encounters：前 dm_quota 次请判官，其余确定性。每一场的事件随即折叠进状态，下一场看到的是坍缩之后的世界
        （狭路相逢的落败者带伤、退了一跳）；快照是这一批之前的那份——撞见须来者在快照里、玩家仍在此地，否则确定性收尾。死者没有心跳。
        """
        if not state.alive:
            return []
        out: list[DomainEvent] = []
        quota = dm_quota
        for encounter in state.encounters:
            if encounter.kind is EncounterKind.MEET_PLAYER:
                events, asked = await self._meet(encounter, state, snap, quota > 0)
            else:
                events, asked = await self._skirmish(encounter, state, quota > 0)
            quota -= asked
            out += events
            state = reduce(evolve, events, state)
        return out

    async def _meet(
        self, encounter: Encounter, state: PlayerState, snap: LocalSnapshot, ask: bool
    ) -> tuple[list[DomainEvent], bool]:
        here = encounter.location_id == state.location_id == snap.location.id
        if not (ask and here and snap.character(encounter.npc_id) is not None):
            return npc.meet(encounter, [], state), False
        env = envelope_of(None, state, snap, target_id=encounter.npc_id)
        brief = encounter_brief(encounter, env, snap, state, state.agendas.get(encounter.npc_id), self._atlas)
        output = await self._ask(self._judge.meet(env, brief), "撞见的判官")
        if output is None:
            return npc.meet(encounter, [], state), True
        settlement = settle(env, output, state, snap)
        settled: list[DomainEvent] = list(settlement.events)
        if settlement.flee and (way := retreat(state, snap)) is not None:  # 危机坍缩挂在此地或你身上：被迫脱身
            settled.append(Moved(from_location_id=state.location_id, to_location_id=way.to_id, exit_label=way.label, fleeing=True))
        for note in settlement.notes:
            logger.info("撞见过闸：%s", note)
        return npc.meet(encounter, settled, state, by="地下城主"), True

    async def _skirmish(self, encounter: Encounter, state: PlayerState, ask: bool) -> tuple[list[DomainEvent], bool]:
        if encounter.npc_id not in self._atlas.characters or encounter.other_id not in self._atlas.characters:
            return npc.meet(encounter, [], state), False  # 当事人已不在世上：草草收场，只让来者驻足
        stakes = npc.skirmish_stakes(encounter, state, self._atlas)
        if not ask:
            return npc.settle_skirmish(stakes, None, None, state, self._atlas), False
        brief = skirmish_brief(stakes, state, self._atlas, self._personas, self._relations)
        verdict = await self._ask(self._judge.skirmish(stakes, brief), "狭路相逢的判官")
        proposed, fact = verdict if verdict is not None else (None, None)
        by = "地下城主" if proposed is not None else "规则"
        return npc.settle_skirmish(stakes, proposed, fact, state, self._atlas, by=by), True

    @staticmethod
    async def _ask[T](work: Awaitable[T], what: str) -> T | None:
        """判官与规划者按契约不抛错；替身或新实现抛了也只当失灵——中断照样坍缩，回合照样落账。"""
        try:
            return await work
        except Exception:
            logger.exception("%s抛错，改由规则", what)
            return None

    # ---------------- 宏观层 ----------------
    async def plan(self, state: PlayerState, events: Iterable[DomainEvent] = ()) -> list[DomainEvent]:
        """
        规划者 idle（NullPlanner）或 planning_due 为 None 即 []；否则为能领议程（free）且有处可去的核心 NPC 写简报 → planner → npc.admit。
        没有一位能领议程就不调大模型；planner 失灵或抛错，照样记一条 AgendaPlanned（下一轮按它算冷却）。
        """
        if not state.alive or self._planner.idle:  # 不立议程的世界（离线 / NPC_AGENDA 关）不记空转的 AgendaPlanned
            return []
        cause = npc.planning_due(state, self._atlas, events)
        if cause is None:
            return []
        movers = [c for c in self._atlas.core if npc.free(state, self._atlas, c) and npc.destinations(state, self._atlas, c)]
        proposals: list[AgendaProposal] = []
        if movers:
            brief = agenda_brief(state, self._atlas, movers, self._personas, self._relations, cause)
            proposals = await self._ask(self._planner.plan(brief.text, brief.schema), "议程推演") or []
        admitted = npc.admit(proposals, state, self._atlas, cause)
        logger.info("议程（%s）：提议 %d 条，放行 %d 条", cause, len(proposals), len(admitted) - 1)
        return admitted

