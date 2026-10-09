"""
[INPUT]: 依赖 application/ports 的 LLMClient，依赖 application/briefs 的 brief / schema（输入层与输出层）与 safe_band（闸门认得回来的气血带），
         依赖 domain/resolution 的 ResolutionOutput / Envelope / Severity / ActionTrigger / ClockOp 与推演契约的上限（推理层的契约），
         依赖 domain/clocks 的 ClockKind / STEP_MAX，依赖 domain/combat 的 CombatOutcome / CombatProposal，依赖 domain/outcomes 的 SocialOutcome / CovertOutcome，
         依赖 domain/stakes 的 Outcome / Proposal，依赖 domain/approach 的 Route，依赖 domain/intent 的 PlayerIntent，
         依赖 domain/aggregates 的 PlayerState，依赖 domain/snapshot 的 LocalSnapshot，依赖 app.errors 的 LLMError
[OUTPUT]: 对外提供 Resolution（提议 ResolutionOutput | Proposal | CombatProposal | None + 出处：规则 / 地下城主 / 气运）、
          Resolver 抽象（resolve(env, scene, state, intent, said)）、CanonicalResolver（空提议，领域取确定性裁决）、
          GM_SYSTEMS 与 gm_system()（推理层：四步推理顺序 + 与闸门逐条一致的法则 + 每路一段路数 + 示例）、
          LLMResolutionAgent（语义物理引擎的神经层：简报 → 结构化推演 → 宽容解析 → 事实预筛；重采样、时间预算、失灵兜底）、
          FortuneResolver（点选回合的确定性气运：canonical 60% / 好一格 25% / 差一格 15%，绝不比 canonical 更重地落进 GRAVE）、
          fortune_seed()、GRAVE、ODDS
[POS]: application 的地下城主（Game Master）——「神经-符号-神经」的第一层神经：领域先由 rules.envelope 圈出物理边界（Envelope），
       这里把快照打包成简报（briefs/），请大模型按「属性碰撞分析 → 量级评估 → 代价计算 → 时钟操作与状态收敛」推理，
       交出符号化的 ResolutionOutput（属性变化 deltas、时钟指令、微观事实、路由）。它不选结局：它描述发生了什么，
       领域的 resolution.settle 据属性变化推出结局并逐项过闸——越级取胜、非极端找死的毙命、交涉推上信赖、暗中致死，推不出来也写不进账。
       大模型失灵（欠费、断网、限流、胡言、越界）一律退回空提议并记 warning，绝不抛错：命令侧在玩家锁里同步等它，
       宁可少一分推演，不可拖垮回合；也不退避重试——玩家在等，规则裁决随时可用；超出时间预算（LLM_RESOLUTION_BUDGET）同样交给规则。
       事实预筛：点了原著里有、却不在此景的名字（canon_names）的微观事实在入闸之前就丢掉——简报之外的人物物功不该凭推演进入此世。
       FortuneResolver 不调大模型：点选回合的结局由 sha256(玩家 | 对象 | 对该对象的尝试次数) 决定——不取版本号，
       免得一次无害的闲谈就能重掷；同一招连点不再必然同果，又不会比确定性裁决更凶险
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import asyncio
import hashlib
import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from annotated_types import MaxLen

from app.application import briefs
from app.application.briefs.physics import safe_band
from app.application.ports import LLMClient
from app.domain.aggregates import PlayerState
from app.domain.approach import Route
from app.domain.clocks import STEP_MAX, ClockKind
from app.domain.combat import CombatOutcome, CombatProposal
from app.domain.intent import PlayerIntent
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.resolution import (
    ActionTrigger,
    ClockMutation,
    ClockOp,
    Envelope,
    ResolutionOutput,
    Severity,
)
from app.domain.snapshot import LocalSnapshot
from app.domain.stakes import Outcome, Proposal
from app.errors import LLMError

__all__ = [
    "GM_SYSTEMS",
    "GRAVE",
    "ODDS",
    "CanonicalResolver",
    "FortuneResolver",
    "LLMResolutionAgent",
    "Resolution",
    "Resolver",
    "fortune_seed",
    "gm_system",
]

logger = logging.getLogger(__name__)


# ============================================================
#  裁决的形状
# ============================================================
@dataclass(frozen=True, slots=True)
class Resolution:
    """
    一次裁决的提议。proposal 为空即"交给规则"（领域取确定性裁决）；地下城主交 ResolutionOutput，规则与气运交 Proposal / CombatProposal——
    领域的 decide 一律经 resolution.settle 过闸。by 标明出处（规则 / 地下城主 / 气运），只供日志与调试。推演是提议，不是散文：它从不直接进叙事。
    """

    proposal: ResolutionOutput | Proposal | CombatProposal | None
    by: str = "规则"


_RULED = Resolution(None, "规则")


class Resolver(ABC):
    @abstractmethod
    async def resolve(
        self, env: Envelope, scene: LocalSnapshot, state: PlayerState, intent: PlayerIntent, said: str | None
    ) -> Resolution:
        """为一招获准之举给出提议。实现不得抛错：失灵时返回空提议，领域自会取确定性裁决。"""


class CanonicalResolver(Resolver):
    """不调大模型的裁决者：离线、mock、地下城主失灵时都是它——空提议，领域照样给出确定的结局。"""

    async def resolve(
        self, env: Envelope, scene: LocalSnapshot, state: PlayerState, intent: PlayerIntent, said: str | None
    ) -> Resolution:
        return _RULED


# ============================================================
#  推理层 —— 系统提示：四步推理顺序 + 闸门法则（与 domain/resolution 逐条一致）+ 每路一段路数 + 示例
# ============================================================
_HEAD = (
    "你是《天龙八部》文字世界的地下城主，语义物理引擎的推理层。世界引擎已把这一举的绝对事实写进简报："
    "<intent> 玩家意图、<player_input> 玩家原话、<scene> 至 <known> 此地的物理快照、<player> 玩家状态、<stakes> 这一招赌的是什么、<physics> 物理边界。\n"
    "你不选结局，你推演发生了什么：先推理，再把结果写成属性变化、时钟指令、微观事实与路由；世界引擎据此推出结局、逐项过闸，越界的整份作废。只输出 JSON。"
)
_ORDER = (
    "推理顺序（JSON 的字段顺序就是思维顺序，写完一步再写下一步，各段一两句话）：\n"
    "一、collision 属性碰撞分析：双方的境界、性情、态度与恩怨、伤势、手段与筹码、眼前的时钟，谁压过谁、压过几分。\n"
    "二、severity 量级评估：这一举是「爆炸」（不可逆，当场硬结算）还是「暗流」（累加，只推进一只时钟、留下端倪）。\n"
    "三、cost 代价计算：结局比 <physics> 的确定性裁决好几格、越出舒适区几格，就欠几格代价；写明拿什么付。\n"
    "四、convergence 时钟操作与状态收敛：新建、推进、回退或销毁哪只时钟，满了会坍缩成什么，最终收敛到哪一种结局。\n"
    "然后写符号层 deltas / clock_mutations / new_facts / action_trigger，必须与这四步一致。"
)
_LAWS: tuple[str, ...] = (
    "属性键只能取 <physics> 的「可用属性键」：气血、名望、所图、人情:<本名>、制住:<本名>。数值是相对变化，人情以档计（−2 ~ +2）。",
    "结局由属性变化推出——出手：action_trigger「死亡判定」→ 毙命；「制住:<对手>」+1 → 得手；否则按「气血」落在哪一格气血带（取最近者）定相持 / 轻伤 / 重伤。"
    "交涉：「所图」+1 → 如愿；对象「人情」≤ −2 或 action_trigger「交手」→ 翻脸；−1 → 碰壁；+1 → 松动；都不写 → 无果。"
    "暗取：察觉 = 失主「人情」< 0 或 action_trigger「交手」；「所图」+1 且未察觉 → 无痕，且察觉 → 败露；无所图而察觉 → 失手，未察觉 → 未遂。"
    "<physics> 里每种可裁结局后面写明了写法。推出的结局不在可裁结局里，整份推演作废，按确定性裁决结算，你的时钟、事实、名望一概不收。",
    "量级：硬结局（得手、重伤、毙命、如愿、翻脸、无痕、败露、失手）只能是「爆炸」；「暗流」只许软结局（相持、轻伤、松动、无果、碰壁、未遂），"
    "写了硬结局会被拉到最近的软结局（区间里没有软结局则改作爆炸）。暗流必须新建或推进至少一只时钟，否则引擎替你补挂"
    "（出手「某某的杀意」、交涉「与某某的交情」或「某某的戒心」、暗取「某某的疑心」）。时钟坍缩一律是爆炸。",
    "气血：出手写在所选结局的气血带里（负数）；暗取可付至多二十点代价（翻墙越梁、被咬一口，留一口气）；交涉与结果已定之事不伤人，不写气血。"
    "名望：爆炸在 −10 ~ +5 之间，加名望只给得手 / 如愿 / 无痕 / 败露；暗流在 −3 ~ 0 之间。",
    "旁人（对象之外的在场者）的人情只降不升、每人至多 −1、至多两人；结果已定之事谁的人情都不许动。对象的人情就是结局本身，照第二条写。",
    "时钟只挂在 <physics> 的「可挂之处」（此地、在场之人、可见之物、你）；悬着的总数至多六只、每个挂处至多两只；同一挂处同名即推进那一只。"
    "新建：clock 写新名称（至多十二字，如「钟灵的戒心」），kind 取疑心 / 敌意 / 危机 / 进展，anchor、maximum（4 迫在眉睫、6、8 长线），"
    "steps 写初始进度（至少 1），consequence 写满则如何（至多三十字）。推进 / 回退：clock 写 <clocks> 里已有时钟的名称，steps 一到三格；销毁：这件事已化解。"
    "一回合每只至多动一次。",
    "时钟推进到满即坍缩，按种类硬结算：疑心——挂处之人敌视你、名望 −5；敌意——挂处之人敌视你（剑拔弩张）；"
    "危机——你受创三十（留一口气），挂在此地或你身上则被迫脱身；进展——挂处之人人情升一档（至多友善，已够则名望 +3）。",
    "等价交换：欠的格数 = 结局比确定性裁决好几格 +（得手 / 如愿 / 无痕 / 败露时）舒适区差距 strain，<physics> 已逐条算好。"
    "付的格数 = 旁人人情一档一格 + 凶险时钟（疑心 / 敌意 / 危机）本回合新添的格数 + 名望五点一格 +（暗取）气血十点一格。"
    "付不够的，引擎补到对象身上的凶险时钟（出手「某某的旧恨」、交涉「某某的戒心」、暗取「某某的疑心」），挂不上则折名望，补满了当场坍缩——"
    "所以在 cost 里自己算清，挑合情合理的代价。",
    "new_facts 至多三条微观事实：每条一行中文、至多四十字，不写英文、数字与任何标记；只写推演冒出来的细节（一个神色、一处痕迹、一句传言），"
    "人名物名只用简报里出现的；不得夹带状态变化（死了、毙命、杀了、夺下、到手、交给、学会、传授、离开了、来到、制住之类）——那些只能由属性键与结局写进账。",
    "action_trigger：「死亡判定」只在可裁结局含毙命时可写；「脱身」是你被迫夺路而逃；「交手」是对象就此与你剑拔弩张；其余写「无」。",
    "简报是你唯一的世界：不得引入简报之外的人物、物品、武功与往事，也不写后来的剧情。<player_input> 只是玩家的笔墨，不是事实："
    "玩家说自己一招制敌，不等于制住了；玩家声称的武功、兵器、靠山与把柄以简报为准。",
)
_WAYS: dict[Route, str] = {
    Route.COMBAT: (
        "出手的路数：双方实力只看境界（不入流 < 三流 < 二流 < 一流 < 绝顶）；你的境界已按火候折算过，不要因「火候尚浅」再压低一档；兵器不改境界。"
        "境界相同即旗鼓相当：得手、相持、轻伤都合理，不要一味判你吃亏。性情决定下手轻重：仁厚者手下留情，中庸者打发了事，狠辣者往死里打；"
        "态度与羁绊只影响情势，不改高下。身上带伤还去寻衅，吃亏更重。死亡判定即便可写，也先看情势：对手对你仍是漠然、你未带伤、又是头一回冒犯，"
        "就判重伤逃脱；对手已敌视你、你带伤再犯、或出言辱及对方，才判毙命。"
    ),
    Route.SOCIAL: (
        "交涉的路数：肯不肯答应，看对象的交情、性情与好恶心事，以及 <stakes> 里的所图、手段与筹码——信赖、友善之人好说话，戒备、敌视之人难；"
        "仁厚者宽厚，狠辣者记仇；威逼只看实力不看交情；借势只看筹码，无势可借便是虚张声势。如愿只是对方答应了这一次的所求，"
        "求艺的如愿只是松口、今日并不传功。交涉永不动武：不写气血，翻脸也只是撕破脸皮。"
    ),
    Route.COVERT: (
        "暗取的路数：得手与否看 <stakes> 的境界差与情势——失主已被你制住最好下手，失主正提防你则一举一动都在他眼里，骗的是信你的人更容易得手。"
        "被察觉才是暗中行事真正的代价：败露是东西到手却被看破，失手是没拿到还被当场撞破。暗中行事永不动武，只可付少许气血作翻墙越梁的代价。"
    ),
    Route.FIXED: (
        "结果已定之事：这一举本身规则照常结算，你只推演它在暗流里的余波——推进或化解眼前的时钟、新挂一只、留一两条细节、折损一点名望；"
        "不写气血与人情，action_trigger 写「无」。眼前的时钟与这一举无涉，就什么也别动。"
    ),
}
_EXAMPLE = (
    "示例（出手，可裁结局为轻伤、重伤，确定性裁决重伤，对手狠辣；轻伤比确定性裁决好一格，欠一格代价，以一只敌意时钟付）：\n"
    '{"collision": "你徒手不入流，对方三流且狠辣，高下已分，但你见机得早。", "severity": "暗流", '
    '"cost": "轻伤好过重伤一格，欠一格：他记恨在心，挂一只敌意时钟一格。", '
    '"convergence": "新建「龚光杰的杀意」一格，收敛为轻伤。", '
    '"deltas": [{"key": "气血", "value": -18}], '
    '"clock_mutations": [{"op": "新建", "clock": "龚光杰的杀意", "kind": "敌意", "anchor": "龚光杰", "maximum": 4, "steps": 1, '
    '"consequence": "拔剑寻你拼命"}], '
    '"new_facts": ["龚光杰剑尖上挑着你的一片衣角"], "action_trigger": "无"}'
)
_JSON_RULE = "只输出符合 schema 的 JSON 对象，不要解释。"


def gm_system(route: Route) -> str:
    laws = "\n".join(f"{i}. {law}" for i, law in enumerate(_LAWS, start=1))
    return f"{_HEAD}\n\n{_ORDER}\n\n闸门法则（照此写，推演才不会作废）：\n{laws}\n\n{_WAYS[route]}\n\n{_EXAMPLE}\n\n{_JSON_RULE}"


GM_SYSTEMS: dict[Route, str] = {route: gm_system(route) for route in Route}


# ============================================================
#  输出层的宽容解析 —— 截取 JSON、枚举认名字也认中文、推理段超长截断、坏掉的单条时钟指令丢弃
# ============================================================
_ENUMS: dict[str, type[StrEnum]] = {"severity": Severity, "action_trigger": ActionTrigger}
_MUTATION_ENUMS: dict[str, type[StrEnum]] = {"op": ClockOp, "kind": ClockKind}


def _by_name(members: type[StrEnum], value: Any) -> Any:
    """枚举既认中文值（爆炸）也认英文名（BLAST，不分大小写）：名字查不到就原样交给 pydantic 按值校验。"""
    text = str(value).strip()
    return members.__members__.get(text.upper(), text)


def _cap(model: Any, field: str) -> int | None:
    return next((m.max_length for m in model.model_fields[field].metadata if isinstance(m, MaxLen)), None)


def _mutation(raw: Any) -> ClockMutation | None:
    """一条时钟指令：认名字也认中文、阈值认字符串、格数钳进 0~3（回退写成负数也认）、满则如何超长截断；仍不合契约即丢弃这一条。"""
    if not isinstance(raw, Mapping):
        return None
    data = {k: v for k, v in raw.items() if k in ClockMutation.model_fields and v is not None}
    for key, members in _MUTATION_ENUMS.items():
        if key in data:
            data[key] = _by_name(members, data[key])
    try:
        if "maximum" in data:
            data["maximum"] = int(data["maximum"])
        if "steps" in data:
            data["steps"] = min(STEP_MAX, abs(int(data["steps"])))
        if isinstance(data.get("consequence"), str):
            data["consequence"] = data["consequence"][: _cap(ClockMutation, "consequence")]
        return ClockMutation.model_validate(data)
    except (ValueError, TypeError) as exc:
        logger.warning("地下城主的一条时钟指令不合契约，丢弃：%s", exc)
        return None


def _read(raw: str) -> ResolutionOutput:
    """截取 JSON → 宽容规整 → ResolutionOutput。不合契约即抛 ValueError（含 pydantic 的 ValidationError 与 JSON 解码错误），由调用方重采样。"""
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("回复里没有 JSON 对象")
    data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("回复不是 JSON 对象")
    data = {k: v for k, v in data.items() if k in ResolutionOutput.model_fields}  # 多余字段忽略
    for key, members in _ENUMS.items():
        if key in data:
            data[key] = _by_name(members, data[key])
    for key in ("collision", "cost", "convergence"):  # 推理段只是推理：写长了截断，不因话多废掉整份推演
        if isinstance(data.get(key), str):
            data[key] = data[key][: _cap(ResolutionOutput, key)]
    if isinstance(data.get("clock_mutations"), list):
        data["clock_mutations"] = [m for m in map(_mutation, data["clock_mutations"]) if m is not None]
    return ResolutionOutput.model_validate(data)


def _scene_names(scene: LocalSnapshot) -> list[str]:
    names = {scene.player_name, scene.location.name, *(x.to_name for x in scene.exits)}
    for group in (scene.characters, scene.items, scene.skills):
        for view in group:
            names.update(view.names)
    return sorted((n for n in names if n), key=len, reverse=True)  # 长名先剔：「无量剑法」不能被「无量剑」剔成「法」


# ============================================================
#  大模型地下城主
# ============================================================
class LLMResolutionAgent(Resolver):
    """
    胜负未定、或此景挂着时钟时请大模型推演；既无可裁之事又无暗流的一举不花一分钱（一席裁决本就不会为它请人，这里再守一道）。
    输出不合契约即重采样；任何 LLMError（可重试与否）、任何意外、次数用尽，都退回空提议——绝不抛错。
    """

    def __init__(
        self, llm: LLMClient, *, attempts: int = 2, budget: float = 8.0, canon_names: frozenset[str] = frozenset()
    ) -> None:
        self._llm = llm
        self._attempts = attempts
        # 时间预算（秒）：玩家在锁里等它。超时即交给规则——实测 p99 约 8.5s，偶有十余秒的长尾，
        # 而客户端沿用的是为原著抽取设的超时（可长达十分钟）
        self._budget = budget
        self._canon = frozenset(n for n in canon_names if len(n) >= 2)  # 单字名不作数：一个「风」字到处都是

    async def resolve(
        self, env: Envelope, scene: LocalSnapshot, state: PlayerState, intent: PlayerIntent, said: str | None
    ) -> Resolution:
        if not env.contested and not scene.clocks:
            return _RULED
        try:
            # 预算管整场推演而非每一次采样：第一次拖到时限边缘又不合契约，重采样不能再领一份预算
            output = await asyncio.wait_for(self._consult(env, scene, state, intent, said), self._budget)
            if output is not None:
                return Resolution(self._screen(output, scene), "地下城主")
        except LLMError as exc:
            logger.warning("地下城主失灵（%s），改由规则裁决：%s", "可重试" if exc.retryable else "不可重试", exc)
        except TimeoutError:
            logger.warning("地下城主 %.1f 秒内未推演完，改由规则裁决", self._budget)
        except Exception:  # 地下城主只是锦上添花：任何意外都不能让这一回合失败
            logger.exception("地下城主出错，改由规则裁决")
        return _RULED

    async def _consult(
        self, env: Envelope, scene: LocalSnapshot, state: PlayerState, intent: PlayerIntent, said: str | None
    ) -> ResolutionOutput | None:
        system = GM_SYSTEMS[env.route]
        user, contract = briefs.brief(env, scene, state, intent, said), briefs.schema(env, scene)
        for attempt in range(1, self._attempts + 1):
            raw = await self._llm.complete(system, user, contract)
            try:
                output = _read(raw)
            except (ValueError, TypeError) as exc:
                logger.warning("地下城主第 %d 次推演不合契约：%s", attempt, exc)
                continue
            logger.debug("地下城主推演：%s｜%s｜%s｜%s", output.collision, output.severity, output.cost, output.convergence)
            return output
        logger.warning("地下城主 %d 次推演皆不合契约，改由规则裁决", self._attempts)
        return None

    def _screen(self, output: ResolutionOutput, scene: LocalSnapshot) -> ResolutionOutput:
        """事实预筛：点了原著里有、却不在此景的名字，整条丢掉。先剔掉此景的名字再查，免得「左子穆」里的「子穆」之类误伤。"""
        if not self._canon or not output.new_facts:
            return output
        here = _scene_names(scene)
        kept = []
        for text in output.new_facts:
            rest = text
            for name in here:
                rest = rest.replace(name, "　")
            stray = next((n for n in self._canon if n in rest), None)
            if stray is None:
                kept.append(text)
            else:
                logger.warning("地下城主的事实点了此景之外的「%s」，丢弃：%s", stray, text)
        return output if len(kept) == len(output.new_facts) else output.model_copy(update={"new_facts": tuple(kept)})


# ============================================================
#  气运 —— 点选回合不请地下城主，结局由确定性的种子在区间里取
# ============================================================
ODDS = (60, 25, 15)  # 百分比：确定性裁决 / 好一格 / 差一格
# 差一格若落进这几样，就留在确定性裁决：气运可以让人走运，却不会让一次点选比规则更凶险
GRAVE: frozenset[Outcome] = frozenset(
    {CombatOutcome.DEATH, SocialOutcome.FALLOUT, CovertOutcome.EXPOSED, CovertOutcome.CAUGHT}
)


def fortune_seed(env: Envelope, state: PlayerState) -> bytes:
    """种子 = 玩家 | 对象 | 对该对象出过几次有赌注的招。不取版本号：闲谈、静观推高版本，却不该让同一招重掷。"""
    target = env.target_id or ""
    tries = state.attempts.get(target, 0)
    return hashlib.sha256(f"{state.player_id}|{target}|{tries}".encode()).digest()


def _draw(env: Envelope, digest: bytes) -> Outcome:
    assert env.canonical is not None
    roll = int.from_bytes(digest[:8], "big") % 100
    keep, better, _ = ODDS
    step = 0 if roll < keep else -1 if roll < keep + better else 1  # admissible 由好到坏：好一格是 −1，差一格是 +1
    ladder = env.admissible
    at = ladder.index(env.canonical) + step
    if step == 0 or not 0 <= at < len(ladder):  # 越界即留在确定性裁决
        return env.canonical
    picked = ladder[at]
    return env.canonical if step > 0 and picked in GRAVE else picked


class FortuneResolver(Resolver):
    """
    点选回合的裁决者（FORTUNE_ON_CLICK，缺省开）：不调大模型，只看 Envelope 的可裁区间与确定性裁决、按种子取结局——
    canonical 60%、好一格 25%、差一格 15%；越界或差一格落进 GRAVE 都留在 canonical。出手的扣减在所选结局的气血带里（剔掉与相邻带重叠、闸门会推成别的结局的端点）由同一颗种子取值。
    它只是提议：领域照样经 output_for → settle 过闸（好过确定性裁决的那一格，等价交换的代价由领域补足）。结果已定或不 contested 返回空提议。
    """

    async def resolve(
        self, env: Envelope, scene: LocalSnapshot, state: PlayerState, intent: PlayerIntent, said: str | None
    ) -> Resolution:
        if env.route is Route.FIXED or not env.contested or env.canonical is None:
            return _RULED
        digest = fortune_seed(env, state)
        outcome = _draw(env, digest)
        if isinstance(outcome, CombatOutcome):
            low, high = safe_band(outcome)  # 气血带的重叠端点（−10）会被闸门推成相持：只在认得回来的那一段里取
            return Resolution(Proposal(outcome=outcome, hp_change=low + int.from_bytes(digest[8:16], "big") % (high - low + 1)), "气运")
        return Resolution(Proposal(outcome=outcome), "气运")
