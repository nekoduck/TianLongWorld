"""
[INPUT]: 依赖 app.domain.approach 的 MOVES / Route / Row / cell_of / row_of / normalize / infer_aim / AIMS，依赖 app.domain.intent，
         依赖 app.domain.models 的 Attitude，依赖 app.domain.rules 的 normalized / ground，依赖 tests/test_rules 的 scene / act
[OUTPUT]: 兼容表的单测：整张 (动作, 手段) → 路线表逐格照 PROPOSAL_v2 §3.3 核对（含表外格；THINK 沉思一行是之后添的：寻常 → 定）、TAKE 分地上之物与他人之物两行、
          normalize（表外手段退回寻常、所图不配置空、他人之物以格子写明的所图为准、话题落不了地置空、幂等）、
          所图缺省推断（说了的照说 → 格内隐含 → 威逼或带话题打探 → 敌视戒备化解 → 结交）、
          rules.normalized 按此情此景落地话题与判定物在谁手；
          战术维度 axis_of 的封闭表逐格核对（出手恒激化；寻常或言辞的打探、带话题的寻常攀谈、带话题而不写所图的言辞旁观——与所图推断一致；武力激化、计谋 / 潜行 / 借势诡道、言辞 / 人情化解；
          寻常的攀谈 / 赠物化解；其余旁观）、四根轴都有招落上、轴只看意图的动作 / 手段 / 所图 / 话题而不看措辞与指称、.label 中文
[POS]: tests 的「招」词汇基线：表是封闭的——改表就改这里的每一格
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import pytest

from app.domain.approach import AIMS, MOVES, Route, Row, TacticalAxis, axis_of, cell_of, infer_aim, normalize, row_of
from app.domain.intent import ActionType, Aim, Approach, PlayerIntent
from app.domain.models import Attitude
from app.domain.rules import ground, normalized
from tests.test_rules import act, scene

P = Approach
COLUMNS = (P.PLAIN, P.FORCE, P.WORDS, P.FAVOR, P.GUILE, P.STEALTH, P.LEVERAGE)
_: None = None  # 表外：— （由 normalize 退回寻常列）

# PROPOSAL_v2 §3.3 的兼容表，逐格照搬：(路线, 隐含所图, 说法)；THINK 一行是之后添的（世界心跳：沉思只花时间）
TABLE: dict[Row, tuple[tuple[Route, Aim | None, str] | None, ...]] = {
    Row.ATTACK: ((Route.COMBAT, Aim.SUBDUE, ""), (Route.COMBAT, Aim.SUBDUE, ""), _, _,
                 (Route.COMBAT, Aim.SUBDUE, "不越级"), _, _),
    Row.TAKE_GROUND: ((Route.FIXED, None, "物性闸门"), _, _, _, _, _, _),
    Row.TAKE_HELD: ((Route.FIXED, None, "驳回并提示换手段"), (Route.COMBAT, Aim.SEIZE, "夺物"),
                    (Route.SOCIAL, Aim.ASK, "讨要"), (Route.SOCIAL, Aim.ASK, "讨要"),
                    (Route.COVERT, Aim.SEIZE, "骗取"), (Route.COVERT, Aim.SEIZE, "偷取"), (Route.SOCIAL, Aim.ASK, "讨要")),
    Row.TALK: ((Route.FIXED, None, "闲谈"), (Route.SOCIAL, None, "威逼"), (Route.SOCIAL, None, ""),
               (Route.SOCIAL, None, ""), (Route.SOCIAL, None, "套话"), _, (Route.SOCIAL, None, "")),
    Row.GIVE: ((Route.FIXED, None, ""), _, _, (Route.FIXED, None, "投其所好"), _, _, _),
    Row.LEARN: ((Route.FIXED, None, "两道门"), _, (Route.FIXED, Aim.LEARN, ""), (Route.FIXED, Aim.LEARN, ""), _, _, _),
    Row.MOVE: ((Route.FIXED, None, "撂话离场"), _, _, _, _, _, _),
    Row.OBSERVE: ((Route.FIXED, None, ""), _, _, _, _, _, _),
    Row.THINK: ((Route.FIXED, None, ""), _, _, _, _, _, _),  # PROPOSAL_v2 之后添的一行：寻常 → 定
    Row.USE: ((Route.FIXED, None, ""), _, _, _, _, _, _),
    Row.REST: ((Route.FIXED, None, ""), _, _, _, _, _, _),
}


@pytest.mark.parametrize("row", list(TABLE), ids=str)
def test_the_compatibility_table_cell_by_cell(row: Row) -> None:
    for approach, expected in zip(COLUMNS, TABLE[row], strict=True):
        cell = MOVES[row].get(approach)
        if expected is None:
            assert cell is None, (row, approach)
            continue
        assert cell is not None and (cell.route, cell.aim, cell.manner) == expected, (row, approach)
    assert set(MOVES) == set(TABLE) | {Row.INVALID}  # 封闭：表里没有别的行


def test_only_learning_by_words_or_favor_falls_back_to_a_parley() -> None:
    """LEARN×{言辞, 人情} 是「定；UNWILLING → 交·求艺」——全表只有这两格带退路。"""
    fallbacks = {(row, a) for row, cells in MOVES.items() for a, cell in cells.items() if cell.unwilling is not None}
    assert fallbacks == {(Row.LEARN, P.WORDS), (Row.LEARN, P.FAVOR)}
    assert MOVES[Row.LEARN][P.WORDS].unwilling is Route.SOCIAL


def test_take_splits_into_two_rows() -> None:
    assert row_of(ActionType.TAKE) is Row.TAKE_GROUND and row_of(ActionType.TAKE, held=True) is Row.TAKE_HELD
    assert row_of(ActionType.TALK, held=True) is Row.TALK  # 只有 TAKE 分行
    steal = PlayerIntent(action_type=ActionType.TAKE, target_entity="闪电貂", approach=P.STEALTH)
    assert cell_of(steal, held=True).route is Route.COVERT
    assert cell_of(steal).route is Route.FIXED  # 地上之物没有潜行这一格：落回寻常


@pytest.mark.parametrize(
    ("intent", "held", "approach", "aim"),
    [
        (PlayerIntent(action_type=ActionType.ATTACK, target_entity="龚光杰", approach=P.WORDS), False, P.PLAIN, None),
        (PlayerIntent(action_type=ActionType.ATTACK, target_entity="龚光杰", approach=P.GUILE), False, P.GUILE, None),
        (PlayerIntent(action_type=ActionType.MOVE, target_entity="南下", approach=P.STEALTH), False, P.PLAIN, None),
        (PlayerIntent(action_type=ActionType.TALK, target_entity="段誉", approach=P.STEALTH), False, P.PLAIN, None),
        (PlayerIntent(action_type=ActionType.TAKE, target_entity="玉佩", approach=P.GUILE), False, P.PLAIN, None),
        (PlayerIntent(action_type=ActionType.TAKE, target_entity="无量剑", approach=P.GUILE), True, P.GUILE, Aim.SEIZE),
        (PlayerIntent(action_type=ActionType.LEARN, skill_used="一阳指", approach=P.LEVERAGE), False, P.PLAIN, None),
        (PlayerIntent(action_type=ActionType.TALK, target_entity="段誉", aim=Aim.ESCAPE), False, P.PLAIN, None),
        (PlayerIntent(action_type=ActionType.TALK, target_entity="段誉", aim=Aim.PROBE), False, P.PLAIN, Aim.PROBE),
        (PlayerIntent(action_type=ActionType.REST, aim=Aim.LEARN), False, P.PLAIN, None),
        (PlayerIntent(action_type=ActionType.THINK, approach=P.GUILE, aim=Aim.PROBE), False, P.PLAIN, None),
    ],
)
def test_normalize_folds_out_of_table_moves_back(intent: PlayerIntent, held: bool, approach: Approach, aim: Aim | None) -> None:
    fixed = normalize(intent, held=held)
    assert (fixed.approach, fixed.aim) == (approach, aim)
    assert normalize(fixed, held=held) == fixed  # 幂等


def test_normalize_drops_a_topic_that_does_not_ground_and_keeps_one_that_does() -> None:
    chat = PlayerIntent(action_type=ActionType.TALK, target_entity="左子穆", topic="辛双清")
    assert normalize(chat).topic is None  # 缺省：无从落地
    assert normalize(chat, grounds=lambda t: t == "辛双清") is chat  # 合表、落了地：原样奉还
    assert normalize(chat, grounds=lambda t: False).topic is None
    assert all(aims <= set(Aim) for aims in AIMS.values())


@pytest.mark.parametrize(
    ("intent", "attitude", "held", "aim"),
    [
        (PlayerIntent(action_type=ActionType.TALK, target_entity="x", aim=Aim.WARN), Attitude.HOSTILE, False, Aim.WARN),
        (PlayerIntent(action_type=ActionType.TALK, target_entity="x", topic="辛双清"), Attitude.HOSTILE, False, Aim.PROBE),
        (PlayerIntent(action_type=ActionType.TALK, target_entity="x", approach=P.WORDS), Attitude.HOSTILE, False, Aim.DEFUSE),
        (PlayerIntent(action_type=ActionType.TALK, target_entity="x", approach=P.WORDS), Attitude.WARY, False, Aim.DEFUSE),
        (PlayerIntent(action_type=ActionType.TALK, target_entity="x", approach=P.WORDS), Attitude.NEUTRAL, False, Aim.BEFRIEND),
        (PlayerIntent(action_type=ActionType.TALK, target_entity="x"), Attitude.TRUSTED, False, Aim.BEFRIEND),
        (PlayerIntent(action_type=ActionType.TAKE, target_entity="x", approach=P.FORCE), Attitude.NEUTRAL, True, Aim.SEIZE),
        (PlayerIntent(action_type=ActionType.TAKE, target_entity="x", approach=P.FAVOR), Attitude.NEUTRAL, True, Aim.ASK),
        (PlayerIntent(action_type=ActionType.TAKE, target_entity="x", approach=P.STEALTH), Attitude.NEUTRAL, True, Aim.SEIZE),
        (PlayerIntent(action_type=ActionType.LEARN, skill_used="x", approach=P.WORDS), Attitude.NEUTRAL, False, Aim.LEARN),
        (PlayerIntent(action_type=ActionType.MOVE, target_entity="x"), Attitude.NEUTRAL, False, None),
    ],
)
def test_the_default_aim(intent: PlayerIntent, attitude: Attitude, held: bool, aim: Aim | None) -> None:
    """说了的照说；格内隐含的其次；TALK 带话题 → 打探；对方敌视或戒备 → 化解；其余 → 结交。"""
    assert infer_aim(intent, attitude=attitude, held=held) is aim


async def test_the_scene_decides_what_grounds_and_who_holds() -> None:
    state, snap = await scene("loc:无量山")
    assert ground("辛双清", snap) == "chr:辛双清" and ground("无量剑法", snap) == "art:无量剑法"
    assert ground("大理城", snap) == "loc:大理城" and ground("段誉", snap) is None  # 不在此情此景，名称表里也没有
    probe = act(ActionType.TALK, target_entity="左子穆", topic="段誉", approach=P.GUILE)
    assert normalized(probe, state, snap).topic is None
    sword = act(ActionType.TAKE, target_entity="无量剑", approach=P.STEALTH)
    assert normalized(sword, state, snap).approach is P.STEALTH  # 剑在左子穆手中：他人之物那一行有潜行
    assert normalized(sword, state, snap).aim is Aim.SEIZE  # 格子写明了所图：偷取即夺物
    jade = act(ActionType.TAKE, target_entity="玉佩", approach=P.STEALTH)
    assert normalized(jade, state, snap).approach is P.PLAIN  # 玉佩在地上：地上之物只有寻常


# ============================================================
#  战术维度 —— 一招落在哪根轴上，由封闭表定，不由措辞定
# ============================================================
X = TacticalAxis
T = ActionType


@pytest.mark.parametrize(
    ("action", "approach", "aim", "topic", "axis"),
    [
        (T.ATTACK, P.PLAIN, None, None, X.ESCALATE),
        (T.ATTACK, P.FORCE, None, None, X.ESCALATE),
        (T.ATTACK, P.GUILE, None, None, X.ESCALATE),  # 计谋出手也是出手
        (T.TALK, P.PLAIN, None, None, X.PACIFY),  # 寻常攀谈
        (T.TALK, P.PLAIN, None, "无量山", X.OBSERVE),  # 带话题的寻常攀谈即打探（问路也在这里）
        (T.TALK, P.PLAIN, Aim.PROBE, None, X.OBSERVE),
        (T.TALK, P.WORDS, Aim.PROBE, "辛双清", X.OBSERVE),
        (T.TALK, P.WORDS, Aim.BEFRIEND, None, X.PACIFY),
        (T.TALK, P.WORDS, Aim.DEFUSE, None, X.PACIFY),
        (T.TALK, P.WORDS, None, "辛双清", X.OBSERVE),  # 言辞带话题而不写所图，规则推断为打探（infer_aim），轴与之一致
        (T.TALK, P.WORDS, Aim.BEFRIEND, "辛双清", X.PACIFY),  # 写明了结交，话题只是由头
        (T.TALK, P.FAVOR, Aim.BEFRIEND, None, X.PACIFY),
        (T.TALK, P.FAVOR, Aim.PROBE, None, X.PACIFY),  # 人情打探仍是化解：旁观的打探只认寻常与言辞
        (T.TALK, P.FORCE, Aim.PROBE, None, X.ESCALATE),  # 威逼打探是激化
        (T.TALK, P.GUILE, Aim.PROBE, None, X.TRICKERY),  # 套话
        (T.TALK, P.LEVERAGE, Aim.DEFUSE, None, X.TRICKERY),  # 借势
        (T.TAKE, P.PLAIN, None, None, X.OBSERVE),
        (T.TAKE, P.FORCE, Aim.SEIZE, None, X.ESCALATE),
        (T.TAKE, P.WORDS, Aim.ASK, None, X.PACIFY),
        (T.TAKE, P.FAVOR, Aim.ASK, None, X.PACIFY),
        (T.TAKE, P.GUILE, Aim.SEIZE, None, X.TRICKERY),
        (T.TAKE, P.STEALTH, Aim.SEIZE, None, X.TRICKERY),
        (T.TAKE, P.LEVERAGE, Aim.ASK, None, X.TRICKERY),
        (T.GIVE, P.PLAIN, None, None, X.PACIFY),
        (T.GIVE, P.FAVOR, None, None, X.PACIFY),
        (T.LEARN, P.PLAIN, None, None, X.OBSERVE),
        (T.LEARN, P.WORDS, Aim.LEARN, None, X.PACIFY),
        (T.LEARN, P.FAVOR, Aim.LEARN, None, X.PACIFY),
        (T.MOVE, P.PLAIN, None, None, X.OBSERVE),
        (T.OBSERVE, P.PLAIN, None, None, X.OBSERVE),
        (T.THINK, P.PLAIN, None, None, X.OBSERVE),
        (T.USE, P.PLAIN, None, None, X.OBSERVE),
        (T.REST, P.PLAIN, None, None, X.OBSERVE),
        (T.INVALID, P.PLAIN, None, None, X.OBSERVE),
    ],
)
def test_the_tactical_axis_cell_by_cell(action: ActionType, approach: Approach, aim: Aim | None, topic: str | None,
                                        axis: TacticalAxis) -> None:
    intent = PlayerIntent(action_type=action, target_entity="某人", approach=approach, aim=aim, topic=topic)
    assert axis_of(intent) is axis
    reworded = intent.model_copy(update={"target_entity": "另一人", "narrative_style": "长啸一声", "motivation": "随口"})
    assert axis_of(reworded) is axis  # 轴只看动作、手段、所图与话题，不看措辞与指称


def test_every_cell_of_the_table_lands_on_one_of_four_axes() -> None:
    reached = {
        axis_of(PlayerIntent(action_type=ActionType.TAKE if row in (Row.TAKE_GROUND, Row.TAKE_HELD) else ActionType(row.value),
                             approach=approach, aim=cell.aim))
        for row, cells in MOVES.items() for approach, cell in cells.items()
    }
    assert reached == set(TacticalAxis)
    assert [(a.value, a.label) for a in TacticalAxis] == [
        ("ESCALATE", "激化"), ("TRICKERY", "诡道"), ("PACIFY", "化解"), ("OBSERVE", "旁观")]
