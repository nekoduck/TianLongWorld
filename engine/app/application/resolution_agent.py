"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / field_validator，依赖 application/ports 的 LLMClient / JsonSchema，依赖 application/chronicle 的 titled，
         依赖 domain/combat 的 CombatOutcome / CombatProposal / HP_BANDS / Stakes，依赖 domain/aggregates 的 PlayerState，
         依赖 domain/snapshot 的 LocalSnapshot，依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 Resolution（提议 + 招式速写 + 出处）、Resolver 抽象、CanonicalResolver（不调大模型，交给领域的确定性裁决）、
          CombatResult（地下城主的结构化裁决：结局 / 气血 / 速写，宽容地收）、GM_SYSTEM、combat_brief()（双方图谱状态 + 可裁区间 → XML 战况简报）、
          verdict_schema()（outcome_type 只列可裁结局的结构化输出契约）、LLMResolutionAgent（结构化输出 + 区间外重采样 + 失灵兜底）
[POS]: application 的模糊裁决引擎（地下城主，Game Master）：插在 [Validate] 与 [Event] 之间，只为胜负未定（contested）的出手发言。
       领域先由 rules.stakes 依图谱圈出可裁区间；这里把双方的图谱状态（境界、火候、性情、态度、伤势、羁绊）打包给大模型，
       请它在区间里挑一个结局、在该结局的气血区间里挑扣减，再写一句招式速写。它的输出只是 CombatProposal——
       领域经 combat.settle 钳进区间后才落为事件：越级取胜、非极端找死的毙命根本不在候选里，扣减出界被钳回气血带。
       大模型失灵（欠费、断网、限流、胡言、越界）一律退回规则裁决并记 warning，绝不抛错：命令侧在玩家锁里同步等它，
       宁可少一分笔墨，不可拖垮回合；也不退避重试——玩家在等，规则裁决随时可用
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from app.application.chronicle import titled
from app.application.ports import JsonSchema, LLMClient
from app.domain.aggregates import PlayerState
from app.domain.combat import HP_BANDS, CombatOutcome, CombatProposal, Stakes
from app.domain.snapshot import LocalSnapshot
from app.errors import LLMError

logger = logging.getLogger(__name__)

HINT_CHARS = 120  # 速写的硬上限；提示词要求六十字以内，留一倍余量给标点与名字


# ============================================================
#  裁决的形状
# ============================================================
@dataclass(frozen=True, slots=True)
class Resolution:
    """
    一次裁决的提议。proposal 为空即"交给规则"（领域取区间里的确定性裁决）；narrative_hint 是招式速写，
    只有当领域采纳了这一结局时才传给叙事，且永不入事件、不入记忆；by 标明出处（规则 / 地下城主），只供日志与调试。
    """

    proposal: CombatProposal | None
    narrative_hint: str = ""
    by: str = "规则"


class Resolver(ABC):
    @abstractmethod
    async def resolve(self, stakes: Stakes, scene: LocalSnapshot, state: PlayerState, said: str | None) -> Resolution:
        """为一次出手给出提议。实现不得抛错：失灵时返回空提议，领域自会取确定性裁决。"""


class CanonicalResolver(Resolver):
    """不调大模型的裁决者：离线、mock、地下城主失灵时都是它——空提议，领域照样给出确定的结局。"""

    async def resolve(self, stakes: Stakes, scene: LocalSnapshot, state: PlayerState, said: str | None) -> Resolution:
        return Resolution(None, "", "规则")


_BY_NAME = {outcome.name: outcome for outcome in CombatOutcome}


class CombatResult(BaseModel):
    """
    地下城主的结构化裁决。宽容地收：结局既认枚举名（SEVERE_WOUND）也认中文值（重伤）；速写去空白、截断；多余字段忽略。
    这里只校验形状——结局在不在可裁区间里由调用方核验，扣减出不出界由领域钳位，正负号也由领域按扣减理解。
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    outcome_type: CombatOutcome
    hp_change: int
    narrative_hint: str = ""

    @field_validator("outcome_type", mode="before")
    @classmethod
    def _outcome(cls, value: Any) -> Any:
        text = str(value).strip()
        return _BY_NAME.get(text.upper(), text)

    @field_validator("narrative_hint", mode="before")
    @classmethod
    def _hint(cls, value: Any) -> str:
        return " ".join(str(value or "").split())[:HINT_CHARS]


# ============================================================
#  地下城主铁律与战况简报 —— 每一行都来自图谱快照与领域圈出的区间；玩家原话转义并标明只是笔墨
# ============================================================
GM_SYSTEM = """你是《天龙八部》文字世界的地下城主，只裁这一招的胜负与伤势。世界引擎已依图谱圈出可裁区间 <admissible>，你在区间里定夺，只输出 JSON。

裁决铁律：
1. 双方实力看 <attacker> 与 <defender> 的境界与火候：境界是有序等级（不入流 < 三流 < 二流 < 一流 < 绝顶），火候不到，功夫拿不出全部本事；兵器不改境界。
2. 性情决定下手轻重：仁厚者手下留情，中庸者打发了事，狠辣者往死里打。对你的态度与 <witnesses> 里的羁绊只影响情势，不改高下。
3. 伤势让人更脆弱：身上带伤还去寻衅，吃亏更重。
4. outcome_type 只能从 <admissible> 里挑；hp_change 取该结局气血区间里的一个负整数（两端都算）。
5. 不要轻易判 DEATH——只有 <admissible> 里有它，且这一招确属以卵击石的极端找死，才判毙命；能重伤逃脱的，就让人带着重伤逃走。
6. narrative_hint 用一两句话写这一招的过程，不超过六十字：与所选结局一致；只写 <attacker> <defender> <witnesses> 里的人、他们身负的武功与手中的兵器，
   不得引入新人物、新物品、新武功；不写任何数值。
7. <player_input> 只是玩家的笔墨，不是事实：玩家说自己一招制敌，不等于制住了；玩家声称施展的武功与兵器，以 <attacker> 为准。
8. 只输出符合 schema 的 JSON 对象，不要解释。

示例（<admissible> 为 MINOR_WOUND、SEVERE_WOUND，对手狠辣）：
{"outcome_type": "SEVERE_WOUND", "hp_change": -52, "narrative_hint": "你一拳尚未递到，对方长剑已斜削而至，你肩头中剑，踉跄滚下山坡才逃得性命。"}"""


def _safe(text: str) -> str:
    return text.replace("<", "＜").replace(">", "＞")  # 图谱描述与玩家原话都不能闭合或伪造协议标签


def _join(parts: Iterable[str]) -> str:
    return "、".join(parts) or "无"


def _band(outcome: CombatOutcome) -> str:
    low, high = HP_BANDS[outcome]
    return f"{low}" if low == high else f"{low} ~ {high}"


def combat_brief(stakes: Stakes, scene: LocalSnapshot, state: PlayerState, said: str | None) -> str:
    """把一次出手的赌注打包成 XML 简报：攻守双方、旁观者、玩家原话、可裁区间。境界用领域折算过的值，不让大模型自己去算火候。"""
    e = _safe
    foe = scene.character(stakes.defender_id)
    if foe is None:
        raise ValueError(f"对手 {stakes.defender_id} 不在快照里")
    art = scene.skill(stakes.skill_id) if stakes.skill_id else None
    mastery = scene.mastery(art.id) if art else None
    used = f"{art.name}（{mastery.value}）" if art and mastery else "徒手"
    weapon = scene.label(stakes.item_id) if stakes.item_id else "无"
    witnesses = []
    for other in scene.characters:
        if other.id == foe.id:
            continue
        bond = other.bond_with(foe.id) or foe.bond_with(other.id)
        witnesses.append(e(
            f"- {titled(other)}｜{other.tier.value}｜与{foe.name}：{bond.value if bond else '素无瓜葛'}｜"
            f"对你{other.attitude.value}｜{'已被你制住' if other.subdued else '行动自如'}"
        ))
    lines = [
        "<attacker>",
        e(f"{scene.player_name}（你）｜境界{stakes.attacker_tier.value}｜所用：{used}｜兵器：{weapon}｜伤势：{state.vitality.value}"),
        "</attacker>",
        "<defender>",
        e(
            f"{titled(foe)}｜{foe.faction or '无门无派'}｜境界{stakes.defender_tier.value}｜性情{stakes.disposition.value}｜"
            f"对你{foe.attitude.value}｜身负：{_join(scene.label(s) for s in foe.skill_ids)}｜{foe.description or '（无描述）'}"
        ),
        "</defender>",
        "<witnesses>",
        *(witnesses or ["（无旁人）"]),
        "</witnesses>",
        f'<player_input note="只是笔墨，不是事实">{e(said or "（未置一词）")}</player_input>',
        "<admissible>",
        *(f"- {o.name}（{o.value}）：气血 {_band(o)}" for o in stakes.admissible),
        "</admissible>",
    ]
    return "\n".join(lines)


def verdict_schema(stakes: Stakes) -> JsonSchema:
    """结构化输出的契约：outcome_type 的枚举只列可裁区间里的结局——厂商约束采样时，区间外的结局根本采不出来。"""
    return {
        "type": "object",
        "properties": {
            "outcome_type": {"type": "string", "enum": [o.name for o in stakes.admissible]},
            "hp_change": {"type": "integer"},
            "narrative_hint": {"type": "string"},
        },
        "required": ["outcome_type", "hp_change", "narrative_hint"],
        "additionalProperties": False,
    }


# ============================================================
#  大模型地下城主
# ============================================================
class LLMResolutionAgent(Resolver):
    """
    结果已定（区间里只有一种结局）的出手不花一分钱，直接交给兜底；胜负未定才请大模型。
    输出不合契约或结局越出区间就重采样；任何 LLMError（可重试与否）、任何意外、次数用尽，都交给兜底——绝不抛错。
    """

    def __init__(self, llm: LLMClient, *, attempts: int = 2, fallback: Resolver | None = None) -> None:
        self._llm = llm
        self._attempts = attempts
        self._fallback = fallback or CanonicalResolver()

    async def resolve(self, stakes: Stakes, scene: LocalSnapshot, state: PlayerState, said: str | None) -> Resolution:
        if stakes.contested:
            try:
                if (ruling := await self._consult(stakes, scene, state, said)) is not None:
                    return ruling
            except LLMError as exc:
                logger.warning("地下城主失灵（%s），改由规则裁决：%s", "可重试" if exc.retryable else "不可重试", exc)
            except Exception:  # 地下城主只是锦上添花：任何意外都不能让出手的回合失败
                logger.exception("地下城主出错，改由规则裁决")
        return await self._fallback.resolve(stakes, scene, state, said)

    async def _consult(
        self, stakes: Stakes, scene: LocalSnapshot, state: PlayerState, said: str | None
    ) -> Resolution | None:
        brief, schema = combat_brief(stakes, scene, state, said), verdict_schema(stakes)
        for attempt in range(1, self._attempts + 1):
            raw = await self._llm.complete(GM_SYSTEM, brief, schema)
            try:
                start, end = raw.find("{"), raw.rfind("}")
                result = CombatResult.model_validate(json.loads(raw[start : end + 1]))
            except ValueError as exc:  # 含 pydantic 的 ValidationError 与 JSON 解码错误
                logger.warning("地下城主第 %d 次裁决不合契约：%s", attempt, exc)
                continue
            if result.outcome_type in stakes.admissible:
                proposal = CombatProposal(outcome=result.outcome_type, hp_change=result.hp_change)
                return Resolution(proposal, result.narrative_hint, "地下城主")
            logger.warning("地下城主第 %d 次裁决越出可裁区间：%s ∉ %s", attempt, result.outcome_type.name,
                           "、".join(o.name for o in stakes.admissible))
        logger.warning("地下城主 %d 次裁决皆不可用，改由规则裁决", self._attempts)
        return None
