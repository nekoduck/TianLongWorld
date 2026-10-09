"""
[INPUT]: 依赖 app.application.resolution_agent 的 FortuneResolver / Resolution / GRAVE / ODDS，依赖 app.domain.resolution 的 Envelope / output_for / settle，
         依赖 app.domain 的 combat / social / covert / stakes / rules，依赖 tests/test_rules 的 scene / act
[OUTPUT]: 气运的单测：同一种子同一结局；大量种子下约 60 / 25 / 15；三路赌注全矩阵 × 多颗种子恒在区间、至多偏一格、
          差一格绝不落进毙命 / 翻脸 / 败露 / 失手、扣减在所选结局的气血带里、settle_any 照单全收且经 output_for → settle 过闸后结局不变；
          尝试次数换种子而版本号不换；结果已定或不 contested 返回空提议
[POS]: tests 的「点选不花钱、也不更凶险」证明：气运只看 Envelope、是确定性的、只在区间里走一格（一席裁决的调用规则与简报的 T=0 证明在 test_resolution）
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import itertools
from collections import Counter
from dataclasses import replace

from app.application.resolution_agent import GRAVE, ODDS, FortuneResolver, Resolution
from app.domain import rules
from app.domain.aggregates import PlayerState
from app.domain.combat import HP_BANDS, CombatOutcome, assess
from app.domain.covert import assess_covert
from app.domain.events import Conversed, Maneuvered, Parleyed, SkillExecuted
from app.domain.intent import ActionType, Aim, Approach
from app.domain.models import Attitude, Disposition, Tier
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.resolution import Envelope
from app.domain.snapshot import LocalSnapshot
from app.domain.social import assess_social
from app.domain.stakes import AnyStakes, Proposal, route_of, settle_any
from tests.test_rules import act, scene

FORTUNE = FortuneResolver()


def env_of(at_stake: AnyStakes) -> Envelope:
    """气运只看 Envelope 的区间、确定性裁决与对象：赌注直接换算，不必摆一整个场面。"""
    return Envelope(route=route_of(at_stake), target_id=at_stake.target_id, target_name="某人",
                    admissible=tuple(at_stake.admissible), canonical=at_stake.canonical)


async def luck(env: Envelope, state: PlayerState, snap: LocalSnapshot | None = None) -> Resolution:
    return await FORTUNE.resolve(env, snap, state, None, None)  # type: ignore[arg-type]


async def draw(at_stake: AnyStakes | Envelope, state: PlayerState, snap: LocalSnapshot | None = None) -> Proposal:
    env = at_stake if isinstance(at_stake, Envelope) else env_of(at_stake)
    resolution = await luck(env, state, snap)
    assert isinstance(resolution.proposal, Proposal) and resolution.by == "气运"
    return resolution.proposal


def player(n: int | str, **update: object) -> PlayerState:
    return replace(PlayerState(player_id=f"ply:{n}", name="阿星", location_id="loc:无量山"), **update)  # type: ignore[arg-type]


EVEN = assess(defender_id="chr:龚光杰", skill_id=None, item_id=None, attacker=Tier.THIRD, defender=Tier.THIRD,
              disposition=Disposition.NEUTRAL, player_hp=100)  # 旗鼓相当：得手 / 相持 / 轻伤，canonical 相持


# ============================================================
#  气运：确定、约 60 / 25 / 15、至多偏一格、差一格不落进 GRAVE
# ============================================================
async def test_the_same_seed_draws_the_same_fortune() -> None:
    assert EVEN.admissible == (CombatOutcome.SUCCESS, CombatOutcome.STALEMATE, CombatOutcome.MINOR_WOUND)
    for n in range(20):
        assert await draw(EVEN, player(n)) == await draw(EVEN, player(n))


async def test_fortune_keeps_canonical_most_of_the_time_and_strays_one_step_either_way() -> None:
    n = 4000
    tally = Counter([(await draw(EVEN, player(i))).outcome for i in range(n)])
    share = [tally[o] * 100 / n for o in (CombatOutcome.STALEMATE, CombatOutcome.SUCCESS, CombatOutcome.MINOR_WOUND)]
    for got, want in zip(share, ODDS, strict=True):  # canonical / 好一格 / 差一格
        assert abs(got - want) < 3, share


def _ladders() -> list[AnyStakes]:
    """三路赌注的全矩阵：境界差 × 性情 × 伤势；交情 × 手段 × 性情 × 够不够得着；境界 × 戒心 × 手段 × 被制住。"""
    out: list[AnyStakes] = []
    for me, him, temper, hp in itertools.product(Tier, Tier, Disposition, (100, 60, 30, 10)):
        out.append(assess(defender_id="chr:x", skill_id=None, item_id=None, attacker=me, defender=him,
                          disposition=temper, player_hp=hp))
    for regard, way, temper, edge, reach in itertools.product(Attitude, Approach, Disposition, (-2, 0, 2), (True, False)):
        out.append(assess_social(npc_id="chr:x", aim=Aim.BEFRIEND, approach=way, attitude=regard, disposition=temper,
                                 edge=edge, reachable=reach))
    for me, him, regard, way, held in itertools.product(Tier, Tier, Attitude, (Approach.GUILE, Approach.STEALTH), (True, False)):
        out.append(assess_covert(target_id="chr:x", item_id="itm:y", approach=way, attitude=regard, player=me, holder=him,
                                 subdued=held))
    return out


async def test_fortune_never_leaves_the_rails_nor_turns_graver_than_canonical() -> None:
    ladders = _ladders()
    routes = Counter(type(s).__name__ for s in ladders if s.contested)
    assert set(routes) == {"Stakes", "SocialStakes", "CovertStakes"}
    for at_stake in ladders:
        if not at_stake.contested:
            assert await luck(env_of(at_stake), player(0)) == Resolution(None, "规则")
            continue
        ladder: tuple[object, ...] = at_stake.admissible
        home = ladder.index(at_stake.canonical)
        for seed in range(12):
            proposal = await draw(at_stake, player(seed))
            at = ladder.index(proposal.outcome)
            assert abs(at - home) <= 1
            if at > home:  # 比 canonical 差一格：绝不是毙命 / 翻脸 / 败露 / 失手
                assert proposal.outcome not in GRAVE, (at_stake, proposal)
            if isinstance(proposal.outcome, CombatOutcome):
                low, high = HP_BANDS[proposal.outcome]
                assert low <= proposal.hp_change <= high
            ruling = settle_any(at_stake, proposal)
            assert ruling.adopted and ruling.outcome is proposal.outcome  # 领域照单全收


async def test_grave_outcomes_only_ever_arrive_as_the_canonical_ruling() -> None:
    """极端找死（重伤 / 毙命，canonical 毙命）：气运只可能让人走运逃出一命，绝不会让本该重伤的人毙命。"""
    doomed = assess(defender_id="chr:x", skill_id=None, item_id=None, attacker=Tier.NONE, defender=Tier.PEERLESS,
                    disposition=Disposition.RUTHLESS, player_hp=100)
    assert (doomed.admissible, doomed.canonical) == ((CombatOutcome.SEVERE_WOUND, CombatOutcome.DEATH), CombatOutcome.DEATH)
    seen = Counter([(await draw(doomed, player(i))).outcome for i in range(400)])
    assert seen[CombatOutcome.SEVERE_WOUND] > 0 and seen[CombatOutcome.DEATH] > 0
    gong = assess(defender_id="chr:x", skill_id=None, item_id=None, attacker=Tier.NONE, defender=Tier.THIRD,
                  disposition=Disposition.NEUTRAL, player_hp=100)
    assert gong.canonical is CombatOutcome.MINOR_WOUND  # 差一格是重伤：不在 GRAVE 里，可以走到
    assert CombatOutcome.SEVERE_WOUND in {(await draw(gong, player(i))).outcome for i in range(200)}
    peeved = assess_social(npc_id="chr:x", aim=Aim.BEFRIEND, approach=Approach.GUILE, attitude=Attitude.WARY,
                           disposition=Disposition.NEUTRAL)
    assert peeved.admissible[-1] is SocialOutcome.FALLOUT and peeved.canonical is SocialOutcome.REBUFFED
    assert SocialOutcome.FALLOUT not in {(await draw(peeved, player(i))).outcome for i in range(400)}
    lift = assess_covert(target_id="chr:x", item_id="itm:y", approach=Approach.STEALTH, attitude=Attitude.NEUTRAL,
                         player=Tier.THIRD, holder=Tier.THIRD)
    assert lift.canonical is CovertOutcome.FOILED
    assert {(await draw(lift, player(i))).outcome for i in range(400)} == {CovertOutcome.CLEAN, CovertOutcome.FOILED}


async def test_attempts_reseed_the_draw_but_the_version_does_not() -> None:
    hello = act(ActionType.TALK, target_entity="段誉", approach=Approach.WORDS)
    state, snap = await scene("loc:大理城")
    chatted, later = await scene("loc:大理城", Conversed(npc_id="chr:段誉"), Conversed(npc_id="chr:段正淳"))
    assert later.version == snap.version + 2 and chatted.attempts == state.attempts == {}
    env, same = rules.envelope(hello, state, snap), rules.envelope(hello, chatted, later)
    assert env is not None and same is not None and env.contested
    assert await draw(env, state, snap) == await draw(same, chatted, later)

    tried = Parleyed(npc_id="chr:段誉", aim=Aim.BEFRIEND, approach=Approach.WORDS, outcome=SocialOutcome.NOTHING)
    again, _ = await scene("loc:大理城", tried)
    assert again.attempts == {"chr:段誉": 1}
    first = [await draw(EVEN, player(i)) for i in range(40)]
    second = [await draw(EVEN, player(i, attempts={"chr:龚光杰": 1})) for i in range(40)]
    elsewhere = [await draw(EVEN, player(i, attempts={"chr:段誉": 5})) for i in range(40)]
    assert first != second  # 再试一次：换了一颗种子
    assert first == elsewhere  # 对别人出过几次手，与这一次无关


async def test_fixed_matters_draw_nothing_and_every_draw_survives_the_gate() -> None:
    """结果已定之事不掷；三路的每一次气运经 decide（output_for → settle）过闸后，入账的结局正是它取的那一个。"""
    state, snap = await scene("loc:无量山")
    look = act(ActionType.OBSERVE)
    fixed = rules.envelope(look, state, snap)
    assert fixed is not None and await luck(fixed, state, snap) == Resolution(None, "规则")
    for intent in (act(ActionType.ATTACK, target_entity="龚光杰"),
                   act(ActionType.TALK, target_entity="辛双清", approach=Approach.WORDS),
                   act(ActionType.TAKE, target_entity="无量剑", approach=Approach.STEALTH)):
        env = rules.envelope(intent, state, snap)
        assert env is not None and env.contested
        for n in range(30):
            proposal = await draw(env, replace(state, attempts={env.target_id or "": n}), snap)
            ruled = next(e for e in rules.decide(intent, state, snap, proposal)
                         if isinstance(e, SkillExecuted | Parleyed | Maneuvered))
            assert ruled.outcome is proposal.outcome, (intent, proposal)
