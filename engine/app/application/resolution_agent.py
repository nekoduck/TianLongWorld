"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / field_validator，依赖 application/ports 的 LLMClient，依赖 application/briefs 的 brief / schema / SECTIONS，
         依赖 domain/stakes 的 AnyStakes / Outcome / Proposal / route_of，依赖 domain/combat 的 CombatOutcome / CombatProposal / HP_BANDS / Stakes，
         依赖 domain/outcomes 的 SocialOutcome / CovertOutcome，依赖 domain/approach 的 Route，依赖 domain/aggregates 的 PlayerState，
         依赖 domain/snapshot 的 LocalSnapshot，依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 Resolution（提议 + 速写 + 出处）、Resolver 抽象（resolve 接收三路赌注之一）、CanonicalResolver（不调大模型，交给领域的确定性裁决）、
          CombatResult / SocialResult / CovertResult（地下城主的结构化裁决：结局认枚举名也认中文，速写收得严）、
          gm_system() 与 GM_SYSTEMS（共用铁律 + 每路一段说明）、GM_SYSTEM（出手一路，即 v3 铁律原文）、
          LLMResolutionAgent（按路线出简报与 schema、区间外重采样、时间预算、失灵兜底）、
          FortuneResolver（点选回合的确定性气运：canonical 60% / 好一格 25% / 差一格 15%，绝不比 canonical 更重地落进 GRAVE）、
          fortune_seed()、GRAVE、ODDS；并转出 combat_brief / verdict_schema / MEANING（旧导入路径照旧可用）
[POS]: application 的模糊裁决引擎（地下城主，Game Master）：插在 [Validate] 与 [Event] 之间，只为胜负未定（contested）的一招发言——
       出手 / 交涉 / 暗中三路同一个口径：领域先由 rules.stakes 依图谱圈出可裁区间，这里按路线把图谱状态打包成简报（application/briefs），
       请大模型在区间里挑一个结局（出手另在该结局的气血带里挑扣减），再写一句速写。它的输出只是提议——
       领域经 stakes.settle_any 钳进区间后才落为事件：越级取胜、非极端找死的毙命、交涉推上信赖、暗中致死，根本不在候选里。
       大模型失灵（欠费、断网、限流、胡言、越界）一律退回兜底并记 warning，绝不抛错：命令侧在玩家锁里同步等它，
       宁可少一分笔墨，不可拖垮回合；也不退避重试——玩家在等，规则裁决随时可用；超出时间预算（缺省 8 秒）同样交给规则。
       出手一路的铁律与简报经 2026-10 真实 Gemini 选型实测定稿为 v3（14 场景 × 3 次 × 7 组候选），共用框架逐字还原它；
       速写超长或夹带 JSON / 英文 / 数字即整句作废（实测吐过元话术）。
       FortuneResolver 不调大模型：点选回合的结局由 sha256(玩家 | 对象 | 对该对象的尝试次数) 决定——不取版本号，
       免得一次无害的闲谈就能重掷；同一招连点不再必然同果，又不会比确定性裁决更凶险
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import asyncio
import hashlib
import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from app.application import briefs
from app.application.briefs import MEANING, SECTIONS, Section, combat_brief, verdict_schema
from app.application.ports import LLMClient
from app.domain.aggregates import PlayerState
from app.domain.approach import Route
from app.domain.combat import HP_BANDS, CombatOutcome, CombatProposal, Stakes
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.snapshot import LocalSnapshot
from app.domain.social import SocialStakes
from app.domain.stakes import AnyStakes, Outcome, Proposal, route_of
from app.errors import LLMError

__all__ = [
    "GM_SYSTEM",
    "GM_SYSTEMS",
    "GRAVE",
    "HINT_CHARS",
    "MEANING",
    "ODDS",
    "CanonicalResolver",
    "CombatResult",
    "CovertResult",
    "FortuneResolver",
    "LLMResolutionAgent",
    "Resolution",
    "Resolver",
    "SocialResult",
    "combat_brief",
    "fortune_seed",
    "gm_system",
    "verdict_schema",
]

logger = logging.getLogger(__name__)

HINT_CHARS = 80  # 提示词要六十字，留三分之一余量；超出说明模型跑偏了，整句作废而不是截半句
# 速写里不该有的东西：JSON / 代码的符号、成串的英文（元话术）、数字——出现即整句作废（结局照收）
_HINT_JUNK = re.compile(r"[{}\[\]<>`＜＞]|[A-Za-z]{3,}|[0-9０-９]")


# ============================================================
#  裁决的形状
# ============================================================
@dataclass(frozen=True, slots=True)
class Resolution:
    """
    一次裁决的提议。proposal 为空即"交给规则"（领域取区间里的确定性裁决）；narrative_hint 是速写，
    只有当领域采纳了这一结局时才传给叙事，且永不入事件、不入记忆；by 标明出处（规则 / 地下城主 / 气运），只供日志与调试。
    """

    proposal: Proposal | CombatProposal | None
    narrative_hint: str = ""
    by: str = "规则"


_RULED = Resolution(None, "", "规则")


class Resolver(ABC):
    @abstractmethod
    async def resolve(self, stakes: AnyStakes, scene: LocalSnapshot, state: PlayerState, said: str | None) -> Resolution:
        """为一次胜负未定之事（出手 / 交涉 / 暗中）给出提议。实现不得抛错：失灵时返回空提议，领域自会取确定性裁决。"""


class CanonicalResolver(Resolver):
    """不调大模型的裁决者：离线、mock、地下城主失灵时都是它——空提议，领域照样给出确定的结局。"""

    async def resolve(self, stakes: AnyStakes, scene: LocalSnapshot, state: PlayerState, said: str | None) -> Resolution:
        return _RULED


def _by_name(members: type[StrEnum], value: Any) -> Any:
    """结局既认枚举名（SEVERE_WOUND，不分大小写）也认中文值（重伤）：名字查不到就原样交给 pydantic 按值校验。"""
    text = str(value).strip()
    return members.__members__.get(text.upper(), text)


class _Verdict(BaseModel):
    """三路裁决共用的速写闸门：收得严——超长或夹带 JSON / 代码 / 英文 / 数字就整句作废（结局照收），叙事自会按事实白描。"""

    model_config = ConfigDict(frozen=True, extra="ignore")

    narrative_hint: str = ""

    @field_validator("narrative_hint", mode="before")
    @classmethod
    def _hint(cls, value: Any) -> str:
        text = " ".join(str(value or "").split())
        return "" if len(text) > HINT_CHARS or _HINT_JUNK.search(text) else text


class CombatResult(_Verdict):
    """
    出手的结构化裁决，宽容地收：多余字段忽略。这里只校验形状——结局在不在可裁区间里由调用方核验，
    扣减出不出界由领域钳位，正负号也由领域按扣减理解。
    """

    outcome_type: CombatOutcome
    hp_change: int

    @field_validator("outcome_type", mode="before")
    @classmethod
    def _outcome(cls, value: Any) -> Any:
        return _by_name(CombatOutcome, value)


class SocialResult(_Verdict):
    """交涉的结构化裁决：只有结局与速写——交涉永不伤人，没有扣减。"""

    outcome: SocialOutcome

    @field_validator("outcome", mode="before")
    @classmethod
    def _outcome(cls, value: Any) -> Any:
        return _by_name(SocialOutcome, value)


class CovertResult(_Verdict):
    """暗中取物的结构化裁决：只有结局与速写——暗中行事永不伤人，没有扣减。"""

    outcome: CovertOutcome

    @field_validator("outcome", mode="before")
    @classmethod
    def _outcome(cls, value: Any) -> Any:
        return _by_name(CovertOutcome, value)


def _read(stakes: AnyStakes, raw: str) -> tuple[Proposal | CombatProposal, str]:
    """截取 JSON、按路线校验形状 → 提议与速写。不合契约即抛 ValueError（含 pydantic 的 ValidationError 与 JSON 解码错误）。"""
    start, end = raw.find("{"), raw.rfind("}")
    data = json.loads(raw[start : end + 1])
    if isinstance(stakes, Stakes):
        fought = CombatResult.model_validate(data)
        return CombatProposal(outcome=fought.outcome_type, hp_change=fought.hp_change), fought.narrative_hint
    model = SocialResult if isinstance(stakes, SocialStakes) else CovertResult
    result = model.model_validate(data)
    return Proposal(outcome=result.outcome), result.narrative_hint


# ============================================================
#  地下城主铁律 —— 共用框架（只裁什么、0 号结局含义、末条只出 JSON）+ 每路一段说明（briefs/ 各自持有）
# ============================================================
_HEAD = "你是《天龙八部》文字世界的地下城主，只裁{scope}。世界引擎已依图谱圈出可裁区间 <admissible>，你在区间里定夺，只输出 JSON。"
_MEANING_RULE = "<admissible> 里每种结局后面写明了它在故事里意味着什么，速写必须写成那个样子。"
_JSON_RULE = "只输出符合 schema 的 JSON 对象，不要解释。"


def gm_system(section: Section) -> str:
    """共用铁律套上一路的说明：0 号恒是结局含义，末条恒是只出 JSON，中间依次编号接上该路的规矩，最后附示例。"""
    rules = (_MEANING_RULE, *section.rules, _JSON_RULE)
    numbered = "\n".join(f"{i}. {rule}" for i, rule in enumerate(rules))
    return f"{_HEAD.format(scope=section.scope)}\n\n裁决铁律：\n{numbered}\n\n{section.example}"


GM_SYSTEMS: dict[Route, str] = {route: gm_system(section) for route, section in SECTIONS.items()}
GM_SYSTEM = GM_SYSTEMS[Route.COMBAT]  # 出手一路：逐字即 v3 铁律


# ============================================================
#  大模型地下城主
# ============================================================
class LLMResolutionAgent(Resolver):
    """
    结果已定（区间里只有一种结局）的一招不花一分钱，直接交给兜底；胜负未定才请大模型，按路线出简报、铁律与 schema。
    输出不合契约或结局越出区间就重采样；任何 LLMError（可重试与否）、任何意外、次数用尽，都交给兜底——绝不抛错。
    """

    def __init__(
        self, llm: LLMClient, *, attempts: int = 2, fallback: Resolver | None = None, budget: float = 8.0
    ) -> None:
        self._llm = llm
        self._attempts = attempts
        self._fallback = fallback or CanonicalResolver()
        # 时间预算（秒）：玩家在锁里等它。超时即交给规则，宁可少一分笔墨——实测 p99 约 8.5s，偶有十余秒的长尾，
        # 而客户端沿用的是为原著抽取设的超时（可长达十分钟）
        self._budget = budget

    async def resolve(self, stakes: AnyStakes, scene: LocalSnapshot, state: PlayerState, said: str | None) -> Resolution:
        if stakes.contested:
            try:
                # 预算管整场裁决而非每一次采样：第一次拖到时限边缘又不合契约，重采样不能再领一份预算
                ruling = await asyncio.wait_for(self._consult(stakes, scene, state, said), self._budget)
                if ruling is not None:
                    return ruling
            except LLMError as exc:
                logger.warning("地下城主失灵（%s），改由规则裁决：%s", "可重试" if exc.retryable else "不可重试", exc)
            except TimeoutError:
                logger.warning("地下城主 %.1f 秒内未裁决，改由规则裁决", self._budget)
            except Exception:  # 地下城主只是锦上添花：任何意外都不能让这一回合失败
                logger.exception("地下城主出错，改由规则裁决")
        return await self._fallback.resolve(stakes, scene, state, said)

    async def _consult(
        self, stakes: AnyStakes, scene: LocalSnapshot, state: PlayerState, said: str | None
    ) -> Resolution | None:
        system = GM_SYSTEMS[route_of(stakes)]
        brief, contract = briefs.brief(stakes, scene, state, said), briefs.schema(stakes)
        for attempt in range(1, self._attempts + 1):
            raw = await self._llm.complete(system, brief, contract)
            try:
                proposal, hint = _read(stakes, raw)
            except ValueError as exc:
                logger.warning("地下城主第 %d 次裁决不合契约：%s", attempt, exc)
                continue
            if proposal.outcome in stakes.admissible:
                return Resolution(proposal, hint, "地下城主")
            logger.warning("地下城主第 %d 次裁决越出可裁区间：%s ∉ %s", attempt, proposal.outcome.name,
                           "、".join(o.name for o in stakes.admissible))
        logger.warning("地下城主 %d 次裁决皆不可用，改由规则裁决", self._attempts)
        return None


# ============================================================
#  气运 —— 点选回合不请地下城主，结局由确定性的种子在区间里取
# ============================================================
ODDS = (60, 25, 15)  # 百分比：确定性裁决 / 好一格 / 差一格
# 差一格若落进这几样，就留在确定性裁决：气运可以让人走运，却不会让一次点选比规则更凶险
GRAVE: frozenset[Outcome] = frozenset(
    {CombatOutcome.DEATH, SocialOutcome.FALLOUT, CovertOutcome.EXPOSED, CovertOutcome.CAUGHT}
)


def fortune_seed(stakes: AnyStakes, state: PlayerState) -> bytes:
    """种子 = 玩家 | 对象 | 对该对象出过几次有赌注的招。不取版本号：闲谈、静观推高版本，却不该让同一招重掷。"""
    tries = state.attempts.get(stakes.target_id, 0)
    return hashlib.sha256(f"{state.player_id}|{stakes.target_id}|{tries}".encode()).digest()


def _draw(stakes: AnyStakes, digest: bytes) -> Outcome:
    roll = int.from_bytes(digest[:8], "big") % 100
    keep, better, _ = ODDS
    step = 0 if roll < keep else -1 if roll < keep + better else 1  # admissible 由好到坏：好一格是 −1，差一格是 +1
    ladder: tuple[Outcome, ...] = stakes.admissible
    at = ladder.index(stakes.canonical) + step
    if step == 0 or not 0 <= at < len(ladder):  # 越界即留在确定性裁决
        return stakes.canonical
    picked = ladder[at]
    return stakes.canonical if step > 0 and picked in GRAVE else picked


class FortuneResolver(Resolver):
    """
    点选回合的裁决者（FORTUNE_ON_CLICK，缺省开）：不调大模型、不写速写，按种子在区间里取结局——
    canonical 60%、好一格 25%、差一格 15%；越界或差一格落进 GRAVE 都留在 canonical。出手的扣减在所选结局的气血带里由同一颗种子取值。
    它只是提议：领域照样经 settle_any 钳位定案。结果已定（不 contested）返回空提议。
    """

    async def resolve(self, stakes: AnyStakes, scene: LocalSnapshot, state: PlayerState, said: str | None) -> Resolution:
        if not stakes.contested:
            return _RULED
        digest = fortune_seed(stakes, state)
        outcome = _draw(stakes, digest)
        if isinstance(outcome, CombatOutcome):
            low, high = HP_BANDS[outcome]
            hp = low + int.from_bytes(digest[8:16], "big") % (high - low + 1)
            return Resolution(CombatProposal(outcome=outcome, hp_change=hp), "", "气运")
        return Resolution(Proposal(outcome=outcome), "", "气运")
