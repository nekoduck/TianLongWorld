"""
[INPUT]: 依赖 app.application.resolution_agent 的 FortuneResolver / LLMResolutionAgent / Resolution / Resolver / GRAVE / ODDS，
         依赖 app.application.adjudication 的 AdjudicationSlot / adopted_sketch，依赖 app.application.briefs 的 brief，
         依赖 app.domain 的 combat / social / covert / stakes / rules，依赖 InMemoryWorldGraph 种下带后文剧情的蓝图，
         依赖 tests/test_rules 的 scene / act / PID，依赖 tests/conftest 的 ScriptedLLM，依赖 tests/world 的 WORLD
[OUTPUT]: 气运与一席裁决的单测：同一种子同一结局；大量种子下约 60 / 25 / 15；三路赌注全矩阵 × 多颗种子恒在区间、至多偏一格、
          差一格绝不落进毙命 / 翻脸 / 败露 / 失手、扣减在所选结局的气血带里、settle_any 照单全收；尝试次数换种子而版本号不换；
          结果已定返回空提议；一席裁决：确定之事零调用、点选零地下城主调用（关掉气运即 canonical）、文本恰一次；
          速写只在三路的结局被采纳时保留；三份简报都不含任何人的后文剧情（foreshadow），只用 T=0 描述与外显人设，未知见闻正文不进简报
[POS]: tests 的「点选不花钱、也不更凶险」证明：气运是确定性的、只在区间里走一格，地下城主只为自由文本回合发言，后文剧情进不了任何提示词
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import itertools
import json
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.application.adjudication import AdjudicationSlot, adopted_sketch
from app.application.briefs import brief
from app.application.resolution_agent import (
    GRAVE,
    ODDS,
    FortuneResolver,
    LLMResolutionAgent,
    Resolution,
    Resolver,
)
from app.domain.aggregates import Player, PlayerState
from app.domain.combat import HP_BANDS, CombatOutcome, CombatProposal, assess
from app.domain.covert import assess_covert
from app.domain.events import Conversed, EventEnvelope, FactLearned, Parleyed, PlayerSpawned, RelationChanged
from app.domain.intent import ActionType, Aim, Approach, PlayerIntent
from app.domain.lore import Fact, FactUnlock, Persona
from app.domain.models import Attitude, Disposition, Tier
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.rules import decide, stakes
from app.domain.snapshot import LocalSnapshot
from app.domain.social import assess_social
from app.domain.stakes import AnyStakes, Proposal, settle_any
from app.infrastructure.persistence.memory_graph import InMemoryWorldGraph
from tests.conftest import ScriptedLLM
from tests.test_rules import PID, act, scene
from tests.world import WORLD

FORTUNE = FortuneResolver()


async def draw(at_stake: AnyStakes, state: PlayerState, snap: LocalSnapshot | None = None) -> Proposal | CombatProposal:
    resolution = await FORTUNE.resolve(at_stake, snap, state, None)  # type: ignore[arg-type]
    assert resolution.proposal is not None and resolution.by == "气运" and resolution.narrative_hint == ""
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
            assert await FORTUNE.resolve(at_stake, None, player(0), None) == Resolution(None, "", "规则")  # type: ignore[arg-type]
            continue
        ladder: tuple[object, ...] = at_stake.admissible
        home = ladder.index(at_stake.canonical)
        for seed in range(12):
            proposal = await draw(at_stake, player(seed))
            at = ladder.index(proposal.outcome)
            assert abs(at - home) <= 1
            if at > home:  # 比 canonical 差一格：绝不是毙命 / 翻脸 / 败露 / 失手
                assert proposal.outcome not in GRAVE, (at_stake, proposal)
            if isinstance(proposal, CombatProposal):
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
    at_stake = stakes(hello, state, snap)
    assert at_stake is not None and at_stake.contested
    assert await draw(at_stake, state, snap) == await draw(stakes(hello, chatted, later), chatted, later)  # type: ignore[arg-type]

    tried = Parleyed(npc_id="chr:段誉", aim=Aim.BEFRIEND, approach=Approach.WORDS, outcome=SocialOutcome.NOTHING)
    again, _ = await scene("loc:大理城", tried)
    assert again.attempts == {"chr:段誉": 1}
    first = [await draw(EVEN, player(i)) for i in range(40)]
    second = [await draw(EVEN, player(i, attempts={"chr:龚光杰": 1})) for i in range(40)]
    elsewhere = [await draw(EVEN, player(i, attempts={"chr:段誉": 5})) for i in range(40)]
    assert first != second  # 再试一次：换了一颗种子
    assert first == elsewhere  # 对别人出过几次手，与这一次无关


# ============================================================
#  一席裁决
# ============================================================
class Counting(Resolver):
    def __init__(self, inner: Resolver) -> None:
        self.inner, self.calls = inner, 0

    async def resolve(self, stakes: AnyStakes, scene: LocalSnapshot, state: PlayerState, said: str | None) -> Resolution:
        self.calls += 1
        return await self.inner.resolve(stakes, scene, state, said)


def verdict(outcome: str, hint: str = "他沉吟良久，终于点了点头。") -> str:
    return json.dumps({"outcome": outcome, "narrative_hint": hint}, ensure_ascii=False)


async def test_the_slot_calls_nobody_for_settled_matters_and_the_master_only_on_text() -> None:
    state, snap = await scene("loc:大理城")
    hello = stakes(act(ActionType.TALK, target_entity="段誉", approach=Approach.WORDS), state, snap)
    assert hello is not None and hello.contested
    llm = ScriptedLLM(verdict("SOFTENED"))
    master, luck = Counting(LLMResolutionAgent(llm)), Counting(FORTUNE)
    slot = AdjudicationSlot(master, luck)

    certain = assess(defender_id="chr:段誉", skill_id=None, item_id=None, attacker=Tier.FIRST, defender=Tier.NONE,
                     disposition=Disposition.MERCIFUL, player_hp=100)
    for clicked in (True, False):
        assert await slot.resolve(None, snap, state, None, clicked=clicked) is None
        assert await slot.resolve(certain, snap, state, None, clicked=clicked) is None
    assert (master.calls, luck.calls, len(llm.calls)) == (0, 0, 0)  # 确定之事：零调用

    clicked = await slot.resolve(hello, snap, state, None, clicked=True)
    assert clicked is not None and clicked.by == "气运" and (master.calls, luck.calls, len(llm.calls)) == (0, 1, 0)

    typed = await slot.resolve(hello, snap, state, "段兄，咱们交个朋友", clicked=False)
    assert typed == Resolution(Proposal(SocialOutcome.SOFTENED), "他沉吟良久，终于点了点头。", "地下城主")
    assert (master.calls, luck.calls, len(llm.calls)) == (1, 1, 1)  # 文本回合恰一次

    plain = AdjudicationSlot(master)  # 关掉气运：点选一律取确定性裁决
    assert await plain.resolve(hello, snap, state, None, clicked=True) is None and master.calls == 1


async def test_a_sketch_survives_only_when_its_outcome_is_adopted_on_every_route() -> None:
    state, snap = await scene("loc:无量山")
    hint = "笔墨一句。"
    cases = [
        (act(ActionType.ATTACK, target_entity="龚光杰"), CombatProposal(CombatOutcome.SEVERE_WOUND, -50),
         CombatProposal(CombatOutcome.SUCCESS, 0)),
        (act(ActionType.TALK, target_entity="辛双清", approach=Approach.WORDS), Proposal(SocialOutcome.SOFTENED),
         Proposal(SocialOutcome.GRANTED)),
        (act(ActionType.TAKE, target_entity="无量剑", approach=Approach.STEALTH), Proposal(CovertOutcome.EXPOSED),
         Proposal(CovertOutcome.CLEAN)),
    ]
    for intent, inside, outside in cases:
        at_stake = stakes(intent, state, snap)
        assert at_stake is not None and inside.outcome in at_stake.admissible and outside.outcome not in at_stake.admissible
        kept = Resolution(inside, hint, "地下城主")
        assert adopted_sketch(kept, decide(intent, state, snap, inside)) == hint
        assert AdjudicationSlot.adopted_sketch(kept, decide(intent, state, snap, inside)) == hint
        voided = Resolution(outside, hint, "地下城主")
        assert adopted_sketch(voided, decide(intent, state, snap, outside)) == ""  # 被钳回 canonical：速写与定案矛盾
    assert adopted_sketch(None, []) == "" and adopted_sketch(Resolution(None, hint), []) == ""


# ============================================================
#  简报只用 T=0：后文剧情一律不进
# ============================================================
FORESHADOW = {
    "chr:左子穆": "日后剑湖宫被神农帮围困，他为保性命向童姥服软",
    "chr:龚光杰": "后来被神农帮逼着脱裤子当众出丑",
    "chr:辛双清": "后来归附灵鹫宫，做了童姥的部属",
}
SECRET = "左子穆私藏了一封不可告人的书信"


async def foreshadowed(*events: object) -> tuple[PlayerState, LocalSnapshot]:
    people = tuple(
        c.model_copy(update={"foreshadow": FORESHADOW[c.id], "description": f"{c.true_name}的开篇模样"})
        if c.id in FORESHADOW else c
        for c in WORLD.characters
    )
    lore = {
        "personas": (Persona(character_id="chr:左子穆", likes=("门派声望",), worry="西宗来争剑湖宫", sources=("chunk:1",)),),
        "facts": (
            Fact(id="fact:书信", text=SECRET, subject_ids=("chr:左子穆",), knower_ids=("chr:左子穆",), sources=("chunk:1",)),
            Fact(id="fact:比剑", text="东西二宗五年一比剑", subject_ids=("chr:左子穆", "chr:辛双清"),
                 knower_ids=("chr:辛双清",), unlock=FactUnlock(kind="LEVERAGE", target_id="chr:左子穆"), sources=("chunk:1",)),
        ),
    }
    world = WORLD.model_validate({**WORLD.model_dump(), "characters": [c.model_dump() for c in people],
                                  **{k: [x.model_dump() for x in v] for k, v in lore.items()}})
    assert {c.id: c.foreshadow for c in world.characters if c.foreshadow} == FORESHADOW  # 蓝图里确有后文剧情：测试不是空转
    history = [PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量山"), *events]
    envelopes = [
        EventEnvelope(stream_id=PID, version=i, event_id=uuid4(), recorded_at=datetime.now(UTC), event=e)  # type: ignore[arg-type]
        for i, e in enumerate(history, start=1)
    ]
    graph = InMemoryWorldGraph()
    await graph.seed(world)
    await graph.project(PID, envelopes)
    return Player.from_history(PID, envelopes).state, await graph.local_snapshot(PID)


@pytest.mark.parametrize(
    "intent",
    [
        act(ActionType.ATTACK, target_entity="龚光杰"),
        act(ActionType.TALK, target_entity="左子穆", approach=Approach.WORDS),
        act(ActionType.TALK, target_entity="左子穆", approach=Approach.WORDS, aim=Aim.PROBE),
        act(ActionType.TALK, target_entity="左子穆", approach=Approach.LEVERAGE),
        act(ActionType.LEARN, skill_used="无量剑法", target_entity="辛双清", approach=Approach.WORDS),
        act(ActionType.TAKE, target_entity="无量剑", approach=Approach.STEALTH),
        act(ActionType.TAKE, target_entity="无量剑", approach=Approach.GUILE),
    ],
)
async def test_no_brief_ever_carries_foreshadow(intent: PlayerIntent) -> None:
    friendly = RelationChanged(character_id="chr:左子穆", attitude=Attitude.FRIENDLY, cause="为你言辞所动")
    learned = FactLearned(fact_id="fact:比剑", source_id="chr:辛双清")
    pleading = intent.action_type is ActionType.LEARN  # 左子穆一友善就肯传无量剑法，求艺便不必交涉
    state, snap = await foreshadowed(*((learned,) if pleading else (friendly, learned)))
    at_stake = stakes(intent, state, snap)
    assert at_stake is not None
    text = brief(at_stake, snap, state, "后来的事我都知道")
    assert all(story not in text for story in FORESHADOW.values()), text  # 后文剧情一个字也不进
    assert all(word not in text for word in ("神农帮", "童姥", "灵鹫宫"))
    assert SECRET not in text  # 玩家尚不知道的见闻：打探的标的也只说「一桩事」
    if at_stake.target_id == "chr:左子穆":
        assert "左子穆的开篇模样" in text  # T=0 描述照用
        if "<counterpart>" in text:
            assert "好：门派声望｜恶：无｜心事：西宗来争剑湖宫" in text  # 外显人设
            assert "（恩怨：为你言辞所动）" in text
    if intent.aim is Aim.PROBE:
        assert "标的：此人所知的一桩事（你尚不知其详）" in text
    if intent.approach is Approach.LEVERAGE:
        assert "你知道的事：东西二宗五年一比剑" in text  # 已知的把柄才露正文
