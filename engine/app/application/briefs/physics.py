"""
[INPUT]: 依赖 application/ports 的 JsonSchema，依赖 application/briefs/combat 的 MEANING / safe，依赖 briefs/social 与 briefs/covert 的 meaning，
         依赖 domain/resolution 的 Envelope / Severity / ActionTrigger / ClockOp / output_for / delta_keys / clock_anchors / hard / FACTS_MAX / MUTATIONS_MAX
         与等价交换的记账（_shortfall：闸门自己的算法，简报照抄它才不会与闸门各说各话），
         依赖 domain/clocks 的 ClockKind / CLOCK_SIZES / STEP_MAX，依赖 domain/combat 的 CombatOutcome / HP_BANDS / Stakes，
         依赖 domain/social / covert 的赌注，依赖 domain/stakes 的 AnyStakes / Outcome，依赖 domain/approach 的 Route，依赖 domain/snapshot 的 LocalSnapshot
[OUTPUT]: 对外提供 safe_band()（闸门认得回来的气血带：剔掉相邻带重叠的端点，简报与气运共用）、physics_section()（<physics> 物理边界：路线、对象、可裁结局及其含义 / 软硬 / 属性写法 / 欠几格代价、确定性裁决、舒适区差距、
          可用的属性键、可挂之处）、schema()（结构化输出契约：字段顺序即推理顺序；deltas 写成 [{key, value}]，key 枚举取 delta_keys；
          clock_mutations 的 op / kind / maximum 是枚举、anchor 枚举取 clock_anchors；action_trigger 枚举，死亡判定只在区间含毙命时出现）、ROUTE（路线的说法）
[POS]: application/briefs 的物理边界与输出层：把 rules 圈出的 Envelope 写成大模型看得懂、闸门守得住的样子。
       每种可裁结局的「写法」由 domain/resolution.output_for 现推（与闸门推结局的口径同出一源），欠几格由闸门的等价交换记账现算——
       大模型照着写，推演就不会被闸门作废。结果已定之事（FIXED）没有可裁结局：只许动时钟、留事实、折损名望，路由恒为「无」
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from app.application.briefs.combat import MEANING, safe
from app.application.briefs.covert import meaning as covert_meaning
from app.application.briefs.social import meaning as social_meaning
from app.application.ports import JsonSchema
from app.domain.approach import Route
from app.domain.clocks import CLOCK_SIZES, STEP_MAX, ClockKind
from app.domain.combat import HP_BANDS, CombatOutcome
from app.domain.covert import CovertStakes
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.resolution import (
    FACTS_MAX,
    MUTATIONS_MAX,
    ActionTrigger,
    ClockOp,
    Envelope,
    Severity,
    _shortfall,
    clock_anchors,
    delta_keys,
    hard,
    output_for,
)
from app.domain.snapshot import LocalSnapshot
from app.domain.social import SocialStakes
from app.domain.stakes import AnyStakes, Outcome

ROUTE = {Route.COMBAT: "出手", Route.SOCIAL: "交涉", Route.COVERT: "暗取", Route.FIXED: "结果已定"}


_GRADED = (CombatOutcome.STALEMATE, CombatOutcome.MINOR_WOUND, CombatOutcome.SEVERE_WOUND)  # 只凭气血推出的三格


def _graded(hp: int) -> CombatOutcome:
    """气血落在哪一格（取最近者，等距取靠前的）：与 domain/resolution._derive 同一口径，用例逐值核对两边不分家。"""
    return min(_GRADED, key=lambda o: (max(HP_BANDS[o][0] - hp, hp - HP_BANDS[o][1], 0), _GRADED.index(o)))


def safe_band(outcome: CombatOutcome) -> tuple[int, int]:
    """
    闸门认得回来的气血带：只凭气血推结局的三格剔掉相邻带重叠的端点（−10 推出的是相持，所以轻伤从 −11 起），
    写进去的数一定推回这一格。简报照它写，气运也照它取扣减。
    """
    low, high = HP_BANDS[outcome]
    if outcome in _GRADED:
        inside = [hp for hp in range(low, high + 1) if _graded(hp) is outcome]
        low, high = min(inside), max(inside)
    return low, high


def _band(outcome: CombatOutcome) -> str:
    low, high = safe_band(outcome)
    return f"{low}" if low == high else f"{low} ~ {high}"


def _meaning(env: Envelope, stakes: AnyStakes | None, outcome: Outcome, scene: LocalSnapshot) -> str:
    if isinstance(outcome, CombatOutcome):
        return MEANING[outcome]
    if isinstance(stakes, SocialStakes) and isinstance(outcome, SocialOutcome):
        return social_meaning(stakes, outcome, scene)
    if isinstance(stakes, CovertStakes) and isinstance(outcome, CovertOutcome):
        return covert_meaning(stakes, outcome, env.target_name)
    return outcome.value


def _recipe(env: Envelope, outcome: Outcome) -> str:
    """这一结局的属性写法：由 output_for 现推，出手的气血写整格气血带而非中值。"""
    out = output_for(env, outcome)
    parts = []
    for key, value in out.deltas.items():
        if key == "气血" and isinstance(outcome, CombatOutcome):
            parts.append(f"气血 {_band(outcome)}")
        else:
            parts.append(f"{key} {value:+d}")
    if out.action_trigger is not ActionTrigger.NONE:
        parts.append(f"action_trigger「{out.action_trigger.value}」")
    if isinstance(outcome, CombatOutcome) and outcome not in (CombatOutcome.SUCCESS, CombatOutcome.DEATH):
        parts.append("不写制住、不写死亡判定")
    return "、".join(parts) or "对象的人情与所图都不写"


def physics_section(env: Envelope, stakes: AnyStakes | None, scene: LocalSnapshot) -> list[str]:
    e = safe
    keys, anchors = delta_keys(env, scene), clock_anchors(scene)
    lines = [f"路线：{ROUTE[env.route]}" + (f"｜对象：{env.target_name}（此刻对你{env.regard.value}）" if env.target_name else "")]
    if env.route is Route.FIXED:
        lines.append("这一举结果已定，规则照常结算它：你只许动时钟、留事实、折损名望；不写气血与人情，action_trigger 写「无」")
    else:
        lines.append("可裁结局（由好到坏；只能推出其中之一，推出区间外的结局整份推演作废）：")
        for o in env.admissible:
            owed = _shortfall(env, o, 0, 0, 0, 0)
            mark = "｜← 确定性裁决" if o == env.canonical else ""
            lines.append(
                f"- {o.value}（{'硬，只能是爆炸' if hard(o) else '软，可作暗流'}）：{_meaning(env, stakes, o, scene)}｜"
                f"写法：{_recipe(env, o)}｜欠代价 {owed} 格{mark}"
            )
        lines.append(f"舒适区差距 strain：{env.strain}（越级、交情不够、技不如人；得手 / 如愿 / 无痕 / 败露时另欠这么多格）")
    lines += [
        "可用的属性键：" + "、".join(keys),
        "可挂之处：" + "、".join(anchors),
    ]
    return ["<physics>", *(e(line) for line in lines), "</physics>"]


def _unique(values: tuple[str, ...] | list[str]) -> list[str]:
    return list(dict.fromkeys(values))  # 同名的人与物只列一次：枚举里重复的值有的厂商不收


def schema(env: Envelope, scene: LocalSnapshot) -> JsonSchema:
    """结构化输出的契约：推理层四段在前、符号层在后——字段顺序即思维顺序。键、挂处、路由都只列此情此景合法的值。"""
    if env.route is Route.FIXED:
        triggers = [ActionTrigger.NONE.value]
    else:
        triggers = [t.value for t in ActionTrigger if t is not ActionTrigger.DEATH or env.lethal]
    mutation = {
        "type": "object",
        "properties": {
            "op": {"type": "string", "enum": [o.value for o in ClockOp]},
            "clock": {"type": "string"},
            "kind": {"type": "string", "enum": [k.value for k in ClockKind]},
            "anchor": {"type": "string", "enum": _unique(clock_anchors(scene))},
            "maximum": {"type": "integer", "enum": list(CLOCK_SIZES)},
            "steps": {"type": "integer", "minimum": 0, "maximum": STEP_MAX},
            "consequence": {"type": "string"},
        },
        "required": ["op", "clock", "steps"],
        "additionalProperties": False,
    }
    delta = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "enum": _unique(delta_keys(env, scene))},
            "value": {"type": "integer"},
        },
        "required": ["key", "value"],
        "additionalProperties": False,
    }
    properties = {
        "collision": {"type": "string"},
        "severity": {"type": "string", "enum": [s.value for s in Severity]},
        "cost": {"type": "string"},
        "convergence": {"type": "string"},
        "deltas": {"type": "array", "items": delta},
        "clock_mutations": {"type": "array", "maxItems": MUTATIONS_MAX, "items": mutation},
        "new_facts": {"type": "array", "maxItems": FACTS_MAX, "items": {"type": "string"}},
        "action_trigger": {"type": "string", "enum": triggers},
    }
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
