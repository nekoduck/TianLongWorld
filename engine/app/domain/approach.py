"""
[INPUT]: 依赖 domain/intent 的 ActionType / Approach / Aim / PlayerIntent，依赖 domain/models 的 Attitude
[OUTPUT]: 对外提供 Route（战 / 交 / 暗 / 定）、Row（兼容表的行：TAKE 分地上之物与他人之物）、Cell（一格：路线 + 隐含所图 + 说法 + 不肯时的退路）、
          MOVES 封闭兼容表、row_of() / cell_of()（意图落在哪一格）、AIMS（各动作配得上的所图）、
          UNCOERCIBLE（威逼图不来的所图）、normalize()（表外手段退回寻常、所图与动作不配置空、落不了地的话题置空、
          他人之物以格子的所图为准、威逼不图结交 / 化解 / 求艺）、infer_aim()（所图的缺省推断）
[POS]: domain 的「招」词汇：同一个动作可以怎么做、走哪一路裁决。兼容表逐格照搬 PROPOSAL_v2 §3.3，封闭——表外的组合不拒收，
       在 normalize 里退回「寻常」列，与指称截断同理：一个修饰词不该让整回合失败。
       本模块只认意图与人情，不读快照：TAKE 的物在谁手、话题落不落得了地，由 rules 判定后作为参数交进来（rules 依赖本模块，反之不然）
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from app.domain.intent import ActionType, Aim, Approach, PlayerIntent
from app.domain.models import Attitude


class Route(StrEnum):
    """裁决路线：战 = combat 区间，交 = social 区间，暗 = covert 区间，定 = 确定性规则（无赌注）。"""

    COMBAT = "战"
    SOCIAL = "交"
    COVERT = "暗"
    FIXED = "定"


class Row(StrEnum):
    """兼容表的行：动作本身，TAKE 再按物在何处分两行。"""

    ATTACK = "ATTACK"
    TAKE_GROUND = "TAKE 地上之物"
    TAKE_HELD = "TAKE 他人之物"
    TALK = "TALK"
    GIVE = "GIVE"
    LEARN = "LEARN"
    MOVE = "MOVE"
    OBSERVE = "OBSERVE"
    THINK = "THINK"
    USE = "USE"
    REST = "REST"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class Cell:
    """表里的一格。aim 是这一格隐含的所图（夺物、讨要、求艺），manner 是说法（威逼、套话、骗取……），unwilling 是师父不肯时的退路。"""

    route: Route
    aim: Aim | None = None
    manner: str = ""
    unwilling: Route | None = None


_R, _A, _P = Route, Aim, Approach
_FIXED = Cell(_R.FIXED)

MOVES: dict[Row, dict[Approach, Cell]] = {
    Row.ATTACK: {
        _P.PLAIN: Cell(_R.COMBAT, _A.SUBDUE),
        _P.FORCE: Cell(_R.COMBAT, _A.SUBDUE),
        _P.GUILE: Cell(_R.COMBAT, _A.SUBDUE, "不越级"),
    },
    Row.TAKE_GROUND: {_P.PLAIN: Cell(_R.FIXED, manner="物性闸门")},
    Row.TAKE_HELD: {
        _P.PLAIN: Cell(_R.FIXED, manner="驳回并提示换手段"),
        _P.FORCE: Cell(_R.COMBAT, _A.SEIZE, "夺物"),
        _P.WORDS: Cell(_R.SOCIAL, _A.ASK, "讨要"),
        _P.FAVOR: Cell(_R.SOCIAL, _A.ASK, "讨要"),
        _P.GUILE: Cell(_R.COVERT, _A.SEIZE, "骗取"),
        _P.STEALTH: Cell(_R.COVERT, _A.SEIZE, "偷取"),
        _P.LEVERAGE: Cell(_R.SOCIAL, _A.ASK, "讨要"),
    },
    Row.TALK: {
        _P.PLAIN: Cell(_R.FIXED, manner="闲谈"),
        _P.FORCE: Cell(_R.SOCIAL, manner="威逼"),
        _P.WORDS: Cell(_R.SOCIAL),
        _P.FAVOR: Cell(_R.SOCIAL),
        _P.GUILE: Cell(_R.SOCIAL, manner="套话"),
        _P.LEVERAGE: Cell(_R.SOCIAL),
    },
    Row.GIVE: {_P.PLAIN: _FIXED, _P.FAVOR: Cell(_R.FIXED, manner="投其所好")},
    Row.LEARN: {
        _P.PLAIN: Cell(_R.FIXED, manner="两道门"),
        _P.WORDS: Cell(_R.FIXED, _A.LEARN, unwilling=_R.SOCIAL),
        _P.FAVOR: Cell(_R.FIXED, _A.LEARN, unwilling=_R.SOCIAL),
    },
    Row.MOVE: {_P.PLAIN: Cell(_R.FIXED, manner="撂话离场")},
    Row.OBSERVE: {_P.PLAIN: _FIXED},
    Row.THINK: {_P.PLAIN: _FIXED},  # 沉思：只花时间（PROPOSAL_v2 之后添的一行）
    Row.USE: {_P.PLAIN: _FIXED},
    Row.REST: {_P.PLAIN: _FIXED},
    Row.INVALID: {_P.PLAIN: _FIXED},
}

# 各动作配得上的所图：不配的所图置空，交给缺省推断
AIMS: dict[ActionType, frozenset[Aim]] = {
    ActionType.ATTACK: frozenset({_A.SUBDUE, _A.SEIZE}),
    ActionType.TAKE: frozenset({_A.SEIZE, _A.ASK}),
    ActionType.TALK: frozenset({_A.PROBE, _A.BEFRIEND, _A.DEFUSE, _A.ASK, _A.LEARN, _A.WARN}),
    ActionType.LEARN: frozenset({_A.LEARN}),
    ActionType.MOVE: frozenset({_A.ESCAPE}),
    ActionType.GIVE: frozenset({_A.BEFRIEND, _A.DEFUSE}),
}
# 威逼（TALK×武力）图不来的：刀架在脖子上换不来交情、和解与真传——这些所图一律改为打探
UNCOERCIBLE: frozenset[Aim] = frozenset({_A.BEFRIEND, _A.DEFUSE, _A.LEARN})


def row_of(action: ActionType, *, held: bool = False) -> Row:
    if action is ActionType.TAKE:
        return Row.TAKE_HELD if held else Row.TAKE_GROUND
    return Row(action.value)


def cell_of(intent: PlayerIntent, *, held: bool = False) -> Cell:
    """意图落在哪一格；表外的手段落在「寻常」那一格。"""
    row = MOVES[row_of(intent.action_type, held=held)]
    return row.get(intent.approach, row[Approach.PLAIN])


def normalize(intent: PlayerIntent, *, held: bool = False, grounds: Callable[[str], bool] | None = None) -> PlayerIntent:
    """
    规整而不拒收：手段不在这一行 → 寻常；所图与动作不配 → None（交给 infer_aim）；话题落不了地 → None；
    取他人之物而格子写明了所图 → 以格子为准；威逼而图结交 / 化解 / 求艺 → 打探。
    held 说的是 TAKE 的物是否在他人手中（rules 判定）；grounds 判定话题能否落到图谱实体（缺省即都落不了地）。幂等。
    """
    kind = row_of(intent.action_type, held=held)
    row = MOVES[kind]
    approach = intent.approach if intent.approach in row else Approach.PLAIN
    aim = intent.aim if intent.aim in AIMS.get(intent.action_type, frozenset()) else None
    if kind is Row.TAKE_HELD and (implied := row[approach].aim) is not None:
        aim = implied  # 取他人之物：格子写明了所图（夺物 / 讨要），以格子为准——好言相求换不来「夺」，暗中下手也不是「讨」
    if intent.action_type is ActionType.TALK and approach is Approach.FORCE and aim in UNCOERCIBLE:
        aim = Aim.PROBE
    topic = intent.topic if intent.topic and grounds is not None and grounds(intent.topic) else None
    if (approach, aim, topic) == (intent.approach, intent.aim, intent.topic):
        return intent
    return intent.model_copy(update={"approach": approach, "aim": aim, "topic": topic})


def infer_aim(intent: PlayerIntent, *, attitude: Attitude = Attitude.NEUTRAL, held: bool = False) -> Aim | None:
    """
    所图的缺省推断（确定性）：说了的照说；这一格隐含的所图（夺物 / 讨要 / 求艺）其次；
    TALK 威逼或带话题 → 打探；对方敌视或戒备 → 化解；其余 → 结交。别的动作没有隐含所图即 None。
    """
    if intent.aim is not None:
        return intent.aim
    if (implied := cell_of(intent, held=held).aim) is not None:
        return implied
    if intent.action_type is not ActionType.TALK:
        return None
    if intent.topic or intent.approach is Approach.FORCE:
        return Aim.PROBE
    return Aim.DEFUSE if attitude.rank < Attitude.NEUTRAL.rank else Aim.BEFRIEND
