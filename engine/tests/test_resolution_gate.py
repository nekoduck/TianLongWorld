"""
[INPUT]: 依赖 app.domain.resolution 的推演契约与闸门（ResolutionOutput / ClockMutation / parse_delta_key / delta_keys / clock_anchors / output_for /
         settle / fact_id / hard 与常量），依赖 app.domain.clocks 的 NarrativeClock / ClockKind / clock_id 与上限，依赖 app.domain.rules 的 envelope / decide，
         依赖 app.domain.events 的六种新事件与路线事件，依赖 tests/test_rules 的 scene / act / practiced / owed
[OUTPUT]: 语义物理引擎符号层的逐条单测：属性键文法（五种键、全角冒号、名字或 id、dict 与 [{key, value}] 两种写法、同键累加、零值丢弃）、
          契约的宽容与严格（指令与事实截断、空白事实丢弃、多余字段与不全的新建指令拒收）、delta_keys / clock_anchors 随区间与此景而变、
          output_for 与 settle 互为往返（每条路线每个可裁结局推回自身）、结局推导（出手：死亡判定 / 制住 / 最近的气血带；交涉：所图 / 对象人情 / 交手；
          暗取：所图 × 察觉）、暗流只许软结局（无软结局改作爆炸）与按路线补挂的时钟、出界整份作废（不收时钟 / 事实 / 名望 / 旁人人情）、
          气血与名望的钳位、旁人人情（只降不升、一人一档、至多两人、结果已定之事不动、经 decide 叠在涟漪之后）、
          时钟（只挂眼前之物、新建 / 推进 / 回退 / 销毁、同挂处同名即推进、每回合每只至多动一次而补代价例外、总数与每个挂处的上限、
          坍缩表逐种类：疑心 / 敌意 / 危机（经 decide 被迫脱身）/ 进展）、等价交换（欠 = 好过确定性裁决的格数 + 得手类的 strain；
          付 = 旁人人情 + 凶险时钟净添的格数 + 名望 + 暗取的气血；不够的补成对象身上的凶险时钟、补满即坍缩、挂不上折名望）、
          微观事实的筛子与 subject_ids、死亡判定的闸门、结果已定之事经 decide 只收时钟 / 事实 / 名望、脱身路由只逃一次
[POS]: tests 的「神经-符号-神经」中间层死线：大模型描述发生了什么，这里钉死领域怎样决定它算不算数——提示词与闸门须逐条一致
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import hashlib

import pytest
from pydantic import ValidationError

from app.domain.aggregates import PlayerState
from app.domain.clocks import CLOCKS_MAX, PER_ANCHOR, ClockKind, NarrativeClock, clock_id
from app.domain.combat import HP_BANDS, CombatOutcome
from app.domain.events import (
    ClockAdvanced,
    ClockCleared,
    ClockCollapsed,
    ClockStarted,
    Conversed,
    DomainEvent,
    FactEmerged,
    HealthChanged,
    Moved,
    PlayerDied,
    RelationChanged,
    RenownChanged,
)
from app.domain.intent import ActionType, Approach, PlayerIntent
from app.domain.models import Attitude
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.resolution import (
    COLLAPSE_HURT,
    SELF,
    ActionTrigger,
    ClockMutation,
    ClockOp,
    DeltaKind,
    Envelope,
    ResolutionOutput,
    Settlement,
    Severity,
    clock_anchors,
    delta_keys,
    fact_id,
    hard,
    output_for,
    parse_delta_key,
    settle,
)
from app.domain.rules import decide, envelope
from app.domain.snapshot import LocalSnapshot
from app.domain.stakes import Proposal
from tests.test_rules import PID, act, owed, practiced, scene

K = ClockKind
T = ActionTrigger
Op = ClockOp
Out = CombatOutcome
S = SocialOutcome
C = CovertOutcome
BLAST, UNDER = Severity.BLAST, Severity.UNDERCURRENT
PEER = practiced("art:北冥神功", 5, "itm:北冥神功卷轴")  # 初窥门径的一流内功：三流，与左子穆旗鼓相当


# ============================================================
#  助手 —— 场面、推演、时钟
# ============================================================
def hang(state: PlayerState, snap: LocalSnapshot) -> LocalSnapshot:
    """快照里只召回挂在眼前之物上的时钟（与图谱实现同口径），领域用例不依赖图谱实现也能摆出场面。"""
    view = {snap.location.id, state.player_id, *(c.id for c in snap.characters), *(i.id for i in snap.items)}
    return snap.model_copy(update={"clocks": tuple(c for c in state.clocks if c.anchor_id in view)})


async def world(at: str, *events: DomainEvent) -> tuple[PlayerState, LocalSnapshot]:
    state, snap = await scene(at, *events)
    return state, hang(state, snap)


def clock(anchor: str, name: str, kind: ClockKind = K.SUSPICION, progress: int = 1, maximum: int = 4,
          then: str = "") -> NarrativeClock:
    return NarrativeClock(id=clock_id(anchor, name), name=name, kind=kind, anchor_id=anchor, progress=progress,
                          maximum=maximum, consequence=then)


def hung(*clocks: NarrativeClock) -> list[DomainEvent]:
    return [ClockStarted(clock=c, cause="推演") for c in clocks]


def out(severity: Severity = BLAST, deltas: dict[str, int] | None = None, mutations: tuple[ClockMutation, ...] = (),
        facts: tuple[str, ...] = (), trigger: ActionTrigger = T.NONE) -> ResolutionOutput:
    return ResolutionOutput(severity=severity, deltas=deltas or {}, clock_mutations=mutations, new_facts=facts,
                            action_trigger=trigger)


def start(name: str, anchor: str, kind: ClockKind = K.SUSPICION, steps: int = 1, maximum: int = 4,
          then: str = "") -> ClockMutation:
    return ClockMutation(op=Op.START, clock=name, kind=kind, anchor=anchor, maximum=maximum, steps=steps,
                         consequence=then)


def move(op: ClockOp, ref: str, steps: int = 1) -> ClockMutation:
    return ClockMutation(op=op, clock=ref, steps=steps)


def only(events: tuple[DomainEvent, ...] | list[DomainEvent], kind: type) -> list:
    return [e for e in events if isinstance(e, kind)]


def mid(outcome: CombatOutcome) -> int:
    low, high = HP_BANDS[outcome]
    return (low + high) // 2


async def fixture(name: str, *before: DomainEvent) -> tuple[PlayerIntent, PlayerState, LocalSnapshot, Envelope]:
    """常用的几处物理边界（区间与确定性裁决由 rules 圈出，这里只取用）；before 是摆场面的前史（悬着的时钟、人情、伤势）。"""
    at, events, intent = {
        "出手·略逊": ("loc:无量山", (), act(ActionType.ATTACK, target_entity="左子穆")),  # 相持 / 轻伤 / 重伤，确定性轻伤，strain 1
        "出手·相当": ("loc:无量山", (PEER,), act(ActionType.ATTACK, target_entity="左子穆")),  # 得手 / 相持 / 轻伤，确定性相持
        "出手·找死": ("loc:无量山", (), act(ActionType.ATTACK, target_entity="南海鳄神")),  # 重伤 / 毙命，确定性毙命
        "交涉·段誉": ("loc:大理城", (), act(ActionType.TALK, target_entity="段誉", approach=Approach.WORDS)),  # 如愿 / 松动 / 无果，确定性松动
        "交涉·左子穆": ("loc:无量山", (), act(ActionType.TALK, target_entity="左子穆", approach=Approach.WORDS)),  # 松动 / 无果 / 碰壁，确定性无果
        "交涉·计谋": ("loc:无量山", (RelationChanged(character_id="chr:左子穆", attitude=Attitude.WARY, cause="c"),),
                    act(ActionType.TALK, target_entity="左子穆", approach=Approach.GUILE)),  # 无果 / 碰壁 / 翻脸，确定性碰壁
        "暗取·略逊": ("loc:无量山", (), act(ActionType.TAKE, target_entity="无量剑", approach=Approach.STEALTH)),  # 未遂 / 败露 / 失手，strain 2
        "暗取·相当": ("loc:无量山", (PEER,), act(ActionType.TAKE, target_entity="无量剑", approach=Approach.STEALTH)),  # 无痕 / 未遂 / 败露，strain 1
        "静观": ("loc:无量山", (), act(ActionType.OBSERVE)),
        "闲谈": ("loc:无量山", (), act(ActionType.TALK, target_entity="左子穆")),
    }[name]
    state, snap = await world(at, *events, *before)
    env = envelope(intent, state, snap)
    assert env is not None
    return intent, state, snap, env


async def gate(name: str, output: ResolutionOutput, *before: DomainEvent) -> Settlement:
    _, state, snap, env = await fixture(name, *before)
    return settle(env, output, state, snap)


# ============================================================
#  推演契约 —— 属性键文法与形状
# ============================================================
@pytest.mark.parametrize(
    ("key", "parsed"),
    [
        ("气血", (DeltaKind.HP, None)), ("名望", (DeltaKind.RENOWN, None)), ("所图", (DeltaKind.AIM, None)),
        ("人情:左子穆", (DeltaKind.REGARD, "左子穆")), ("人情：左子穆", (DeltaKind.REGARD, "左子穆")),
        (" 制住 : 龚光杰 ", (DeltaKind.SUBDUE, "龚光杰")), ("人情:chr:左子穆", (DeltaKind.REGARD, "chr:左子穆")),
    ],
)
def test_delta_keys_have_exactly_five_forms(key: str, parsed: tuple[DeltaKind, str | None]) -> None:
    assert parse_delta_key(key) == parsed


@pytest.mark.parametrize("key", ["内力", "好感:左子穆", "人情:", "人情", "制住", "气血:左子穆", "", "x" * 30])
def test_any_other_key_is_out_of_grammar(key: str) -> None:
    with pytest.raises(ValueError, match="不合文法"):
        parse_delta_key(key)


def test_deltas_come_as_a_dict_or_as_key_value_pairs() -> None:
    pairs = ResolutionOutput.model_validate({"severity": "爆炸", "deltas": [
        {"key": "人情:左子穆", "value": -1}, {"key": " 人情:左子穆", "value": -1}, {"key": "名望", "value": 0}, {"key": "气血", "value": -12},
    ]})
    assert pairs.deltas == {"人情:左子穆": -2, "气血": -12}  # 同键累加、零值丢弃、键去空白
    assert ResolutionOutput.model_validate({"severity": "暗流", "deltas": {" 所图 ": 1}}).deltas == {"所图": 1}
    for bad in ([{"value": 1}], {"内力": 3}, {"气血": -101}, ["气血"]):
        with pytest.raises(ValidationError):
            ResolutionOutput.model_validate({"severity": "爆炸", "deltas": bad})


def test_the_contract_is_lenient_about_counts_and_strict_about_shape() -> None:
    lenient = ResolutionOutput.model_validate({
        "severity": "暗流",
        "clock_mutations": [{"op": "推进", "clock": f"钟{i}"} for i in range(5)],
        "new_facts": ["  ", "一", "二", "", "三", "四"],
    })
    assert [m.clock for m in lenient.clock_mutations] == ["钟0", "钟1", "钟2"]
    assert lenient.new_facts == ("一", "二", "三")
    assert lenient.action_trigger is T.NONE and lenient.deltas == {}
    with pytest.raises(ValidationError):
        ResolutionOutput.model_validate({"severity": "爆炸", "outcome": "得手"})  # 旧契约的单选结局不再收
    with pytest.raises(ValidationError):
        ResolutionOutput.model_validate({"severity": "中等"})
    with pytest.raises(ValidationError, match="kind / anchor / maximum"):
        ClockMutation(op=Op.START, clock="左子穆的疑心", kind=K.SUSPICION)
    with pytest.raises(ValidationError, match="至多"):
        start("一二三四五六七八九十一二三", "左子穆")
    with pytest.raises(ValidationError, match="至少一格"):
        move(Op.ADVANCE, "左子穆的疑心", 0)
    with pytest.raises(ValidationError):
        move(Op.ADVANCE, "左子穆的疑心", 4)  # 一次至多三格
    with pytest.raises(ValidationError):
        start("左子穆的疑心", "左子穆", maximum=5)  # 阈值只有 4 / 6 / 8
    assert move(Op.CLEAR, "左子穆的疑心", 0).steps == 0  # 销毁不论格数


def test_hard_outcomes_are_the_irreversible_ones() -> None:
    assert {o for o in (*Out, *S, *C) if hard(o)} == {
        Out.SUCCESS, Out.SEVERE_WOUND, Out.DEATH, S.GRANTED, S.FALLOUT, C.CLEAN, C.EXPOSED, C.CAUGHT}


async def test_delta_keys_and_anchors_follow_the_envelope_and_the_scene() -> None:
    _, _, snap, env = await fixture("出手·略逊")
    people = [f"人情:{c.name}" for c in snap.characters]
    assert delta_keys(env, snap) == ("气血", "名望", *people)  # 得手不在区间：没有「制住」；出手没有「所图」
    _, _, snap, env = await fixture("出手·相当")
    assert delta_keys(env, snap) == ("气血", "名望", "制住:左子穆", *people)
    _, _, snap, env = await fixture("交涉·段誉")
    assert delta_keys(env, snap) == ("名望", "所图", *(f"人情:{c.name}" for c in snap.characters))  # 交涉不伤人：没有「气血」
    _, _, snap, env = await fixture("暗取·相当")
    assert delta_keys(env, snap) == ("气血", "名望", "所图", *people)
    _, _, snap, env = await fixture("闲谈")
    assert delta_keys(env, snap) == ("名望",)  # 结果已定之事只许动名望（与时钟、事实）
    assert clock_anchors(snap) == ("无量山", "南海鳄神", "左子穆", "辛双清", "龚光杰", "无量剑", "玉佩", SELF)


# ============================================================
#  output_for —— 规则与气运的结局走同一道闸门，且推得回自身
# ============================================================
async def test_output_for_mirrors_every_admissible_outcome_back_to_itself() -> None:
    for name in ("出手·略逊", "出手·相当", "出手·找死", "交涉·段誉", "交涉·左子穆", "交涉·计谋", "暗取·略逊", "暗取·相当"):
        _, state, snap, env = await fixture(name)
        for outcome in env.admissible:
            settled = settle(env, output_for(env, outcome), state, snap)
            assert (settled.outcome, settled.adopted) == (outcome, True), (name, outcome)
            assert settled.severity is BLAST  # output_for 一律声明爆炸：软结局也可以当场了结
        canonical = settle(env, output_for(env), state, snap)
        assert canonical.outcome is env.canonical and not only(canonical.events, ClockStarted), name  # 确定性裁决不欠代价


async def test_output_for_writes_the_matching_deltas() -> None:
    _, _, _, env = await fixture("出手·相当")
    assert output_for(env, Out.SUCCESS).deltas == {"气血": mid(Out.SUCCESS), "制住:左子穆": 1}
    assert output_for(env, Out.MINOR_WOUND, 12).deltas == {"气血": -12}  # 正数按扣减理解
    assert output_for(env, Out.MINOR_WOUND, -99).deltas == {"气血": HP_BANDS[Out.MINOR_WOUND][0]}  # 钳进那一格的气血带
    assert output_for(env, Out.SEVERE_WOUND, -60).deltas == {"气血": mid(Out.STALEMATE)}  # 出界：取确定性裁决，扣减一并作废
    assert output_for(env, S.GRANTED).deltas == {"气血": mid(Out.STALEMATE)}  # 别的路线的结局同样出界
    _, _, _, env = await fixture("出手·找死")
    assert output_for(env, Out.DEATH).action_trigger is T.DEATH
    _, _, _, env = await fixture("交涉·计谋")
    assert [output_for(env, o).deltas for o in env.admissible] == [{}, {"人情:左子穆": -1}, {"人情:左子穆": -2}]
    _, _, _, env = await fixture("交涉·段誉")
    assert output_for(env, S.GRANTED).deltas == {"所图": 1} and output_for(env, S.SOFTENED).deltas == {"人情:段誉": 1}
    _, _, _, env = await fixture("暗取·相当")
    assert [output_for(env, o).deltas for o in env.admissible] == [{"所图": 1}, {}, {"所图": 1, "人情:左子穆": -1}]


# ============================================================
#  结局由属性变化推出
# ============================================================
async def test_combat_outcome_derives_from_subdue_or_the_nearest_hp_band() -> None:
    assert (await gate("出手·相当", out(deltas={"制住:左子穆": 1}))).outcome is Out.SUCCESS
    assert (await gate("出手·相当", out(deltas={"制住:chr:左子穆": 1, "气血": -3}))).hp_change == -3  # id 也认
    assert (await gate("出手·相当", out(deltas={"制住:龚光杰": 1}))).outcome is Out.STALEMATE  # 制住的不是对手：只看气血
    ghost = await gate("出手·相当", out(deltas={"制住:乔峰": 1, "气血": -15}))
    assert ghost.outcome is Out.MINOR_WOUND and any("乔峰" in n for n in ghost.notes)  # 名字落不了地即作罢
    for hp, outcome, settled in [(-3, Out.STALEMATE, -3), (-15, Out.MINOR_WOUND, -15), (-40, Out.SEVERE_WOUND, -45),
                                 (-100, Out.SEVERE_WOUND, -70), (10, Out.STALEMATE, -2)]:
        got = await gate("出手·略逊", out(deltas={"气血": hp}))
        assert (got.outcome, got.hp_change) == (outcome, settled), hp
    silent = await gate("出手·略逊", out())
    assert (silent.outcome, silent.hp_change) == (Out.STALEMATE, mid(Out.STALEMATE))  # 什么都没写：毫发无伤即相持


async def test_social_outcome_derives_from_the_aim_and_the_targets_regard() -> None:
    assert (await gate("交涉·段誉", out(deltas={"所图": 1, "人情:段誉": -1}))).outcome is S.GRANTED  # 所图先于人情
    assert (await gate("交涉·段誉", out(deltas={"人情:段誉": 1}))).outcome is S.SOFTENED
    assert (await gate("交涉·段誉", out(deltas={"人情:段誉": 3}))).outcome is S.SOFTENED  # 对象的人情只看方向
    assert (await gate("交涉·段誉", out())).outcome is S.NOTHING
    assert (await gate("交涉·计谋", out(deltas={"人情:左子穆": -1}))).outcome is S.REBUFFED
    assert (await gate("交涉·计谋", out(deltas={"人情:左子穆": -2}))).outcome is S.FALLOUT
    assert (await gate("交涉·计谋", out(deltas={"人情:左子穆": -3}))).outcome is S.FALLOUT
    confront = await gate("交涉·计谋", out(trigger=T.CONFRONT))
    assert (confront.outcome, confront.trigger) == (S.FALLOUT, T.CONFRONT)
    fallout = await gate("交涉·计谋", out(deltas={"人情:左子穆": -2}))
    assert fallout.trigger is T.CONFRONT  # 翻脸即剑拔弩张：路由记为交手
    assert (await gate("交涉·计谋", out(deltas={"人情:龚光杰": -1}))).outcome is S.NOTHING  # 旁人的人情不定结局


async def test_covert_outcome_derives_from_the_aim_and_whether_you_were_noticed() -> None:
    assert (await gate("暗取·相当", out(deltas={"所图": 1}))).outcome is C.CLEAN
    assert (await gate("暗取·相当", out(deltas={"所图": 1, "人情:左子穆": -1}))).outcome is C.EXPOSED
    assert (await gate("暗取·相当", out(deltas={"所图": 1}, trigger=T.CONFRONT))).outcome is C.EXPOSED
    assert (await gate("暗取·相当", out())).outcome is C.FOILED
    assert (await gate("暗取·略逊", out(deltas={"人情:左子穆": -1}))).outcome is C.CAUGHT
    assert (await gate("暗取·略逊", out(trigger=T.CONFRONT))).outcome is C.CAUGHT


# ============================================================
#  量级 —— 暗流只许软结局，且必须挂上或推进一只时钟
# ============================================================
async def test_an_undercurrent_is_pulled_to_the_nearest_soft_outcome() -> None:
    soft = await gate("出手·相当", out(UNDER, {"制住:左子穆": 1}))
    assert (soft.outcome, soft.severity, soft.hp_change) == (Out.STALEMATE, UNDER, mid(Out.STALEMATE))
    assert any("暗流不许硬结局" in n for n in soft.notes)
    granted = await gate("交涉·段誉", out(UNDER, {"所图": 1}))
    assert (granted.outcome, granted.severity) == (S.SOFTENED, UNDER)
    exposed = await gate("暗取·相当", out(UNDER, {"所图": 1, "人情:左子穆": -1}))
    assert exposed.outcome is C.FOILED  # 败露与无痕都是硬结局：最近的软结局是未遂
    lethal = await gate("出手·找死", out(UNDER, trigger=T.DEATH))
    assert (lethal.outcome, lethal.severity, lethal.trigger) == (Out.DEATH, BLAST, T.DEATH)  # 区间里没有软结局：改作爆炸
    blast = await gate("出手·相当", out(BLAST, {"气血": -3}))
    assert (blast.outcome, blast.severity) == (Out.STALEMATE, BLAST)  # 软结局可以声明为爆炸：当场了结，不必挂钟
    assert not only(blast.events, ClockStarted)
    assert (await gate("出手·相当", out(BLAST, {"制住:左子穆": 1}))).severity is BLAST


@pytest.mark.parametrize(
    ("name", "deltas", "outcome", "auto"),
    [
        ("出手·略逊", {"气血": -15}, Out.MINOR_WOUND, ("chr:左子穆", "左子穆的杀意", K.ENMITY, 4, "怒而动手")),
        ("交涉·左子穆", {"人情:左子穆": -1}, S.REBUFFED, ("chr:左子穆", "左子穆的戒心", K.SUSPICION, 4, "看穿你的用心")),
        ("交涉·段誉", {}, S.NOTHING, ("chr:段誉", "与段誉的交情", K.PROGRESS, 6, "交情更进一步")),
        ("暗取·略逊", {}, C.FOILED, ("chr:左子穆", "左子穆的疑心", K.SUSPICION, 4, "识破你的手脚")),
    ],
)
async def test_an_undercurrent_that_moves_no_clock_gets_one_by_route(
    name: str, deltas: dict[str, int], outcome: object, auto: tuple[str, str, ClockKind, int, str],
) -> None:
    got = await gate(name, out(UNDER, deltas))
    anchor, title, kind, size, then = auto
    assert got.outcome is outcome and got.severity is UNDER
    assert only(got.events, ClockStarted) == [ClockStarted(clock=clock(anchor, title, kind, 1, size, then), cause="暗流")]


async def test_an_undercurrent_that_moves_its_own_clock_gets_no_extra_one() -> None:
    mine = start("左子穆的不耐", "左子穆", K.SUSPICION, 1, then="拂袖而去")
    got = await gate("交涉·左子穆", out(UNDER, {"人情:左子穆": -1}, (mine,)))
    assert [e.clock.name for e in only(got.events, ClockStarted)] == ["左子穆的不耐"]
    assert not only((await gate("闲谈", out(UNDER))).events, ClockStarted)  # 结果已定之事不补挂


# ============================================================
#  出界 —— 整份推演作废，只按确定性裁决结算
# ============================================================
async def test_an_out_of_envelope_output_is_discarded_wholesale() -> None:
    greedy = out(BLAST, {"制住:左子穆": 1, "名望": 5, "人情:龚光杰": -1},
                 (start("左子穆的惧意", "左子穆"),), ("左子穆的剑穗微微发颤",))
    intent, state, snap, env = await fixture("出手·略逊")
    got = settle(env, greedy, state, snap)  # 越级取胜不在区间里
    assert (got.outcome, got.hp_change, got.adopted, got.events, got.trigger) == (
        Out.MINOR_WOUND, mid(Out.MINOR_WOUND), False, (), T.NONE)
    assert decide(intent, state, snap, greedy) == decide(intent, state, snap)  # 时钟、事实、名望、旁人人情一概不收
    intent, state, snap, env = await fixture("暗取·略逊")
    clean = out(BLAST, {"所图": 1}, facts=("剑鞘上留下一道浅痕",))
    assert settle(env, clean, state, snap).adopted is False  # 无痕不在区间里
    assert decide(intent, state, snap, clean) == decide(intent, state, snap)
    intent, state, snap, env = await fixture("交涉·段誉")
    assert settle(env, out(trigger=T.CONFRONT), state, snap).outcome is S.SOFTENED  # 仁厚者不翻脸：出界取松动


# ============================================================
#  气血与名望的钳位
# ============================================================
async def test_hp_follows_the_route() -> None:
    covert = await gate("暗取·相当", out(deltas={"所图": 1, "气血": -50}))
    assert covert.hp_change == -20 and only(covert.events, HealthChanged) == [
        HealthChanged(delta=-20, cause="暗中行事的代价", source_id=None)]
    frail = await gate("暗取·相当", out(deltas={"所图": 1, "气血": -50}), HealthChanged(delta=-85, cause="c", source_id=None))
    assert frail.hp_change == -14  # 留一口气
    assert (await gate("暗取·相当", out(deltas={"气血": 5}))).hp_change == 0  # 疗伤不归这里
    social = await gate("交涉·段誉", out(deltas={"气血": -30, "人情:段誉": 1}))
    assert social.hp_change == 0 and not only(social.events, HealthChanged) and any("不伤人" in n for n in social.notes)
    fixed = await gate("闲谈", out(deltas={"气血": -30}))
    assert fixed.hp_change == 0 and not only(fixed.events, HealthChanged) and fixed.outcome is None


@pytest.mark.parametrize(
    ("name", "severity", "deltas", "renown"),
    [
        ("交涉·段誉", BLAST, {"所图": 1, "名望": 9}, 5),  # 扬名只给得手类结局，且至多 +5
        ("交涉·段誉", BLAST, {"人情:段誉": 1, "名望": 4}, 0),  # 松动不是得手：不许扬名
        ("交涉·段誉", BLAST, {"名望": -40}, -10),
        ("交涉·段誉", UNDER, {"名望": -9}, -3),  # 暗流只会悄悄折损
        ("交涉·段誉", UNDER, {"名望": 3}, 0),
        ("暗取·相当", BLAST, {"所图": 1, "人情:左子穆": -1, "名望": 3}, 3),  # 败露也是到手
        ("闲谈", BLAST, {"名望": 5}, 0),  # 结果已定之事扬不了名
        ("闲谈", BLAST, {"名望": -5}, -5),
    ],
)
async def test_renown_swings_are_clamped(name: str, severity: Severity, deltas: dict[str, int], renown: int) -> None:
    got = await gate(name, out(severity, deltas))
    assert [e.delta for e in only(got.events, RenownChanged)] == ([renown] if renown else [])


# ============================================================
#  旁人的人情 —— 只降不升、一人一档、至多两人
# ============================================================
async def test_bystanders_only_lose_one_step_and_at_most_two_of_them() -> None:
    got = await gate("交涉·左子穆", out(deltas={"人情:龚光杰": -3, "人情:辛双清": -1, "人情:南海鳄神": -1}))
    assert got.outcome is S.NOTHING
    changed = only(got.events, RelationChanged)
    assert len(changed) == 2 and {e.attitude for e in changed} == {Attitude.WARY} and {e.basis for e in changed} == {"代价"}
    assert {e.character_id for e in changed} == {"chr:南海鳄神", "chr:辛双清"}  # 按 id 取前两位
    kind = await gate("交涉·左子穆", out(deltas={"人情:龚光杰": 1}))
    assert not only(kind.events, RelationChanged) and any("只降不升" in n for n in kind.notes)
    foe = RelationChanged(character_id="chr:龚光杰", attitude=Attitude.HOSTILE, cause="c")
    hostile = await gate("交涉·左子穆", out(deltas={"人情:龚光杰": -1, "人情:辛双清": -1}), foe)
    assert [e.character_id for e in only(hostile.events, RelationChanged)] == ["chr:辛双清"]  # 已敌视的不再折
    assert not only((await gate("闲谈", out(deltas={"人情:龚光杰": -1}))).events, RelationChanged)  # 结果已定之事不动人情


async def test_a_bystanders_cost_lands_after_the_ripple() -> None:
    """出手的涟漪先让龚光杰敌视、辛双清（左子穆的仇家）友善；代价再降一档：辛双清回到漠然，龚光杰已敌视就不再折。"""
    intent, state, snap, _ = await fixture("出手·略逊")
    events = decide(intent, state, snap, out(deltas={"气血": -15, "人情:辛双清": -1, "人情:龚光杰": -1}))
    regard = [(e.character_id, e.attitude, e.basis == "代价") for e in only(events, RelationChanged)]
    assert [(a, cost) for who, a, cost in regard if who == "chr:辛双清"] == [(Attitude.FRIENDLY, False), (Attitude.NEUTRAL, True)]
    assert [a for who, a, _ in regard if who == "chr:龚光杰"] == [Attitude.HOSTILE]


# ============================================================
#  时钟
# ============================================================
async def test_clocks_hang_only_on_what_is_in_view() -> None:
    got = await gate("静观", out(mutations=(start("崖边的落石", "无量山", K.PERIL), start("剑上的血迹", "无量剑"),
                                           start("心头的不安", SELF))))
    assert [(e.clock.anchor_id, e.clock.progress) for e in only(got.events, ClockStarted)] == [
        ("loc:无量山", 1), ("itm:无量剑", 1), (PID, 1)]
    far = await gate("静观", out(mutations=(start("乔峰的疑心", "乔峰"), start("大理的风声", "大理城"), start("x", "不存在"))))
    assert not got.notes and not only(far.events, ClockStarted) and len(far.notes) == 3


async def test_a_clock_advances_rewinds_and_clears() -> None:
    doubt = clock("chr:左子穆", "左子穆的疑心", progress=1, then="识破你的手脚")
    adv = await gate("静观", out(mutations=(move(Op.ADVANCE, "左子穆的疑心", 2),)), *hung(doubt))
    assert only(adv.events, ClockAdvanced) == [ClockAdvanced(clock_id=doubt.id, steps=2, name="左子穆的疑心", progress=3,
                                                             maximum=4, cause="推演")]
    by_id = await gate("静观", out(mutations=(move(Op.ADVANCE, doubt.id),)), *hung(doubt))
    assert only(by_id.events, ClockAdvanced)[0].progress == 2
    back = await gate("静观", out(mutations=(move(Op.REWIND, "左子穆的疑心", 3),)), *hung(doubt))
    assert [(e.steps, e.progress) for e in only(back.events, ClockAdvanced)] == [(-1, 0)]  # 至多退到零
    floor = await gate("静观", out(mutations=(move(Op.REWIND, "左子穆的疑心"),)), *hung(doubt.model_copy(update={"progress": 0})))
    assert not floor.events
    gone = await gate("静观", out(mutations=(move(Op.CLEAR, "左子穆的疑心", 0),)), *hung(doubt))
    assert gone.events == (ClockCleared(clock_id=doubt.id, name="左子穆的疑心", cause="化解"),)
    unknown = await gate("静观", out(mutations=(move(Op.ADVANCE, "龚光杰的疑心"),)), *hung(doubt))
    assert not unknown.events and any("没有" in n for n in unknown.notes)


async def test_the_same_name_on_the_same_anchor_is_the_same_clock() -> None:
    doubt = clock("chr:左子穆", "左子穆的疑心", progress=1)
    again = await gate("静观", out(mutations=(start("左子穆的疑心", "左子穆", steps=2),)), *hung(doubt))
    assert [(type(e).__name__, getattr(e, "progress", None)) for e in again.events] == [("ClockAdvanced", 3)]
    elsewhere = await gate("静观", out(mutations=(start("左子穆的疑心", "龚光杰"),)), *hung(doubt))
    assert only(elsewhere.events, ClockStarted)[0].clock.id == clock_id("chr:龚光杰", "左子穆的疑心") != doubt.id


async def test_each_clock_moves_at_most_once_a_turn() -> None:
    doubt = clock("chr:左子穆", "左子穆的疑心", progress=1)
    twice = await gate("静观", out(mutations=(move(Op.ADVANCE, "左子穆的疑心"), move(Op.REWIND, "左子穆的疑心"),
                                             move(Op.CLEAR, "左子穆的疑心", 0))), *hung(doubt))
    assert [type(e).__name__ for e in twice.events] == ["ClockAdvanced"]
    assert sum("已动过" in n for n in twice.notes) == 2
    fresh = await gate("静观", out(mutations=(start("龚光杰的杀意", "龚光杰", K.ENMITY, 3), move(Op.REWIND, "龚光杰的杀意", 3))))
    assert [type(e).__name__ for e in fresh.events] == ["ClockStarted"]  # 先挂三格再退三格：付不了账
    wary = clock("chr:左子穆", "左子穆的戒心", progress=2, then="看穿你的用心")
    paid_back = await gate("交涉·左子穆", out(deltas={"人情:左子穆": 1}, mutations=(move(Op.REWIND, "左子穆的戒心"),)), *hung(wary))
    assert paid_back.outcome is S.SOFTENED  # 好过确定性裁决（无果）一格：回退的那一格不是代价，领域把它补回来
    assert [(e.steps, e.progress, e.cause) for e in only(paid_back.events, ClockAdvanced)] == [(-1, 1, "推演"), (1, 2, "代价")]


async def test_the_clock_caps_hold() -> None:
    two = hung(clock("chr:左子穆", "左子穆的疑心"), clock("chr:左子穆", "左子穆的怒意", K.ENMITY))
    full = await gate("静观", out(mutations=(start("左子穆的戒心", "左子穆"),)), *two)
    assert len(two) == PER_ANCHOR and not full.events and any("已满" in n for n in full.notes)
    afar = hung(*(clock(a, f"钟{i}") for a in ("loc:大理城", "chr:段誉", "chr:乔峰") for i in range(2)))
    assert len(afar) == CLOCKS_MAX
    crowded = await gate("静观", out(mutations=(start("左子穆的戒心", "左子穆"),)), *afar)
    assert not crowded.events  # 悬着的总数已满：眼前看不见的那几只也算
    cleared = await gate("静观", out(mutations=(move(Op.CLEAR, "左子穆的疑心", 0), start("左子穆的戒心", "左子穆"))), *two)
    assert [type(e).__name__ for e in cleared.events] == ["ClockCleared", "ClockStarted"]  # 销毁腾出位置
    deep = await gate("静观", out(mutations=(start("龚光杰的杀意", "龚光杰", K.ENMITY, 3),)))
    assert only(deep.events, ClockStarted)[0].clock.progress == 3  # 新建至多三格，绝不满格悬着


@pytest.mark.parametrize(
    ("kind", "anchor", "before", "fallout", "flee", "trigger"),
    [
        (K.SUSPICION, "chr:左子穆", (), [RelationChanged, RenownChanged], False, T.NONE),
        (K.SUSPICION, "chr:左子穆", (RelationChanged(character_id="chr:左子穆", attitude=Attitude.HOSTILE, cause="c"),),
         [RenownChanged], False, T.NONE),
        (K.ENMITY, "chr:左子穆", (), [RelationChanged], False, T.CONFRONT),
        (K.PERIL, "loc:无量山", (), [HealthChanged], True, T.FLEE),
        (K.PERIL, PID, (), [HealthChanged], True, T.FLEE),
        (K.PERIL, "chr:左子穆", (), [HealthChanged], False, T.NONE),
        (K.PROGRESS, "chr:左子穆", (), [RelationChanged], False, T.NONE),
        (K.PROGRESS, "chr:左子穆", (RelationChanged(character_id="chr:左子穆", attitude=Attitude.FRIENDLY, cause="c"),),
         [RenownChanged], False, T.NONE),
        (K.PROGRESS, "loc:无量山", (), [RenownChanged], False, T.NONE),
    ],
)
async def test_a_full_clock_collapses_by_its_kind(
    kind: ClockKind, anchor: str, before: tuple[DomainEvent, ...], fallout: list[type], flee: bool, trigger: ActionTrigger,
) -> None:
    ripe = clock(anchor, "暗流涌动", kind, progress=3, then="终于爆发")
    got = await gate("静观", out(UNDER, mutations=(move(Op.ADVANCE, "暗流涌动", 2),)), *before, *hung(ripe))
    assert got.events[0] == ClockCollapsed(clock_id=ripe.id, name="暗流涌动", consequence="终于爆发")
    assert [type(e) for e in got.events[1:]] == fallout
    assert (got.severity, got.flee, got.trigger) == (BLAST, flee, trigger)  # 坍缩即爆炸
    cause = "暗流涌动满了：终于爆发"
    for e in got.events[1:]:
        assert e.cause == cause  # type: ignore[attr-defined]
        match e:
            case RelationChanged():
                want = Attitude.HOSTILE if kind.threat else Attitude.FRIENDLY
                assert (e.character_id, e.attitude, e.basis) == ("chr:左子穆", want, kind.value)
            case RenownChanged():
                assert e.delta == (-5 if kind.threat else 3)
            case HealthChanged():
                assert e.delta == -COLLAPSE_HURT and e.source_id is None


async def test_a_peril_collapse_forces_flight_through_decide_and_never_kills() -> None:
    flood = clock("loc:无量山", "山洪将至", K.PERIL, progress=3, then="洪水漫过山道")
    intent, _, _, _ = await fixture("静观")
    state, snap = await world("loc:无量山", *hung(flood))
    events = decide(intent, state, snap, out(UNDER, mutations=(move(Op.ADVANCE, "山洪将至"),)))
    assert events == [
        ClockCollapsed(clock_id=flood.id, name="山洪将至", consequence="洪水漫过山道"),
        HealthChanged(delta=-COLLAPSE_HURT, cause="山洪将至满了：洪水漫过山道", source_id=None),
        Moved(from_location_id="loc:无量山", to_location_id="loc:大理城", exit_label="南下", fleeing=True),
    ]
    state, snap = await world("loc:无量山", HealthChanged(delta=-99, cause="c", source_id=None), *hung(flood))
    events = decide(intent, state, snap, out(UNDER, mutations=(move(Op.ADVANCE, "山洪将至"),)))
    assert not only(events, HealthChanged) and not only(events, PlayerDied) and only(events, Moved)  # 一口气不扣，人照样得逃


# ============================================================
#  等价交换
# ============================================================
@pytest.mark.parametrize(
    ("deltas", "mutations", "cost"),
    [
        ({"人情:左子穆": 1}, (), 1),  # 好过确定性裁决（无果）一格，分文未付
        ({"人情:左子穆": 1, "名望": -5}, (), 0),  # 五点名望一格
        ({"人情:左子穆": 1, "名望": -1}, (), 0),  # 不足五点也算一格（向上取整）
        ({"人情:左子穆": 1, "人情:龚光杰": -1}, (), 0),  # 旁人一档一格
        ({"人情:左子穆": 1}, (("左子穆的提防", K.SUSPICION),), 0),  # 自挂凶险时钟的格数
        ({"人情:左子穆": 1}, (("与左子穆的交情", K.PROGRESS),), 1),  # 进展时钟不是代价
        ({"人情:左子穆": -1}, (), 0),  # 碰壁比无果差：不欠
    ],
)
async def test_owed_minus_paid_in_a_parley(
    deltas: dict[str, int], mutations: tuple[tuple[str, ClockKind], ...], cost: int,
) -> None:
    got = await gate("交涉·左子穆", out(deltas=deltas, mutations=tuple(start(n, "左子穆", k) for n, k in mutations)))
    debt = [e for e in only(got.events, ClockStarted) if e.cause == "代价"]
    assert debt == ([owed("chr:左子穆", "左子穆", "戒心", cost)] if cost else [])


async def test_overreaching_wins_owe_their_strain_too() -> None:
    clean = await gate("暗取·相当", out(deltas={"所图": 1}))  # 好过未遂一格 + 越出舒适区一格
    assert [e for e in only(clean.events, ClockStarted)] == [owed("chr:左子穆", "左子穆", "疑心", 2)]
    assert not only((await gate("暗取·相当", out(deltas={"所图": 1, "气血": -20}))).events, ClockStarted)  # 十点气血一格
    assert only((await gate("暗取·相当", out(deltas={"所图": 1, "气血": -10}))).events, ClockStarted) == [
        owed("chr:左子穆", "左子穆", "疑心", 1)]
    exposed = await gate("暗取·略逊", out(deltas={"所图": 1, "人情:左子穆": -1}))
    assert exposed.outcome is C.EXPOSED and only(exposed.events, ClockStarted) == [owed("chr:左子穆", "左子穆", "疑心", 2)]
    subdued = await gate("出手·相当", out(deltas={"制住:左子穆": 1}))
    assert only(subdued.events, ClockStarted) == [owed("chr:左子穆", "左子穆", "旧恨", 1)]
    lucky = await gate("出手·略逊", out(deltas={"气血": -10}))  # 相持好过轻伤一格；出手的气血在气血带里，不算代价
    assert lucky.outcome is Out.STALEMATE and only(lucky.events, ClockStarted) == [owed("chr:左子穆", "左子穆", "旧恨", 1)]


async def test_a_debt_on_a_ripe_clock_collapses_it_on_the_spot() -> None:
    ripe = clock("chr:左子穆", "左子穆的疑心", progress=3, then="识破你的手脚")
    got = await gate("暗取·相当", out(deltas={"所图": 1}), *hung(ripe))
    assert got.outcome is C.CLEAN and got.severity is BLAST
    assert [type(e).__name__ for e in got.events] == ["ClockCollapsed", "RelationChanged", "RenownChanged"]
    assert only(got.events, RelationChanged)[0].attitude is Attitude.HOSTILE


async def test_a_debt_that_cannot_hang_is_paid_in_renown() -> None:
    afar = hung(*(clock(a, f"钟{i}") for a in ("loc:大理城", "chr:段誉", "chr:乔峰") for i in range(2)))
    got = await gate("暗取·相当", out(deltas={"所图": 1}), *afar)
    assert not only(got.events, ClockStarted) and [e.delta for e in only(got.events, RenownChanged)] == [-10]
    crowded = hung(clock("chr:左子穆", "左子穆的怒意", K.ENMITY), clock("chr:左子穆", "左子穆的不满"))
    got = await gate("暗取·相当", out(deltas={"所图": 1}), *crowded)
    assert not only(got.events, ClockStarted) and [e.delta for e in only(got.events, RenownChanged)] == [-10]


# ============================================================
#  微观事实
# ============================================================
async def test_facts_are_screened_and_name_their_subjects() -> None:
    keep = ("龚光杰的剑穗上沾着湖边的青苔", "无量山的雾气漫过了左子穆的衣角", "风声紧了")
    got = await gate("静观", out(facts=keep))
    assert only(got.events, FactEmerged) == [
        FactEmerged(fact_id=fact_id(keep[0]), text=keep[0], subject_ids=("chr:龚光杰",)),
        FactEmerged(fact_id=fact_id(keep[1]), text=keep[1], subject_ids=("chr:左子穆", "loc:无量山")),
        FactEmerged(fact_id=fact_id(keep[2]), text=keep[2], subject_ids=()),
    ]
    assert fact_id(keep[2]) == "emg:" + hashlib.sha1(keep[2].encode()).hexdigest()[:10]
    for bad in ("龚光杰死了", "你来到崖边", "左子穆把无量剑交给你", "无量剑到手", "风声abc", "第3把剑", "<b>风</b>", "风" * 41):
        assert not only((await gate("静观", out(facts=(bad,)))).events, FactEmerged), bad


# ============================================================
#  路由 —— 死亡判定、脱身、交手
# ============================================================
async def test_death_is_judged_only_when_the_envelope_holds_it() -> None:
    novice = await gate("出手·略逊", out(deltas={"气血": -15}, trigger=T.DEATH))
    assert (novice.outcome, novice.trigger) == (Out.MINOR_WOUND, T.NONE) and any("死亡判定" in n for n in novice.notes)
    assert (await gate("交涉·段誉", out(trigger=T.DEATH))).trigger is T.NONE
    assert (await gate("静观", out(trigger=T.DEATH))).trigger is T.NONE
    intent, state, snap, env = await fixture("出手·找死")
    fatal = settle(env, out(trigger=T.DEATH), state, snap)
    assert (fatal.outcome, fatal.trigger, fatal.hp_change) == (Out.DEATH, T.DEATH, HP_BANDS[Out.DEATH][0])
    assert only(decide(intent, state, snap, out(trigger=T.DEATH)), PlayerDied)
    spared = decide(intent, state, snap, out(deltas={"气血": -60}))
    assert not only(spared, PlayerDied) and owed("chr:南海鳄神", "南海鳄神", "旧恨", 1) in spared


async def test_flight_is_added_once_and_only_where_the_route_did_not_flee() -> None:
    intent, state, snap, _ = await fixture("交涉·左子穆")
    events = decide(intent, state, snap, out(trigger=T.FLEE))
    assert only(events, Moved) == [Moved(from_location_id="loc:无量山", to_location_id="loc:大理城", exit_label="南下", fleeing=True)]
    intent, state, snap, _ = await fixture("出手·略逊")
    severe = decide(intent, state, snap, out(deltas={"气血": -60}, trigger=T.FLEE))  # 重伤本就夺路而逃
    assert len(only(severe, Moved)) == 1


async def test_a_fixed_action_keeps_only_clocks_facts_and_lost_renown() -> None:
    intent, state, snap, env = await fixture("闲谈")
    lean = out(UNDER, {"气血": -30, "名望": -2, "人情:龚光杰": -1, "人情:左子穆": -1},
               (start("左子穆的戒心", "左子穆", then="看穿你的用心"),), ("左子穆说话时总瞥向剑鞘",))
    events = decide(intent, state, snap, lean)
    assert events == [
        Conversed(npc_id="chr:左子穆"),
        RenownChanged(delta=-2, cause="江湖传言"),
        ClockStarted(clock=clock("chr:左子穆", "左子穆的戒心", then="看穿你的用心"), cause="推演"),
        FactEmerged(fact_id=fact_id("左子穆说话时总瞥向剑鞘"), text="左子穆说话时总瞥向剑鞘", subject_ids=("chr:左子穆",)),
    ]
    assert settle(env, lean, state, snap).proposal is None
    assert decide(intent, state, snap, Proposal(S.GRANTED)) == decide(intent, state, snap) == [Conversed(npc_id="chr:左子穆")]
    refused = decide(act(ActionType.TALK, target_entity="乔峰"), state, snap, lean)
    assert [type(e).__name__ for e in refused] == ["ActionFailed"]  # 驳回的举动带着推演也只是驳回
