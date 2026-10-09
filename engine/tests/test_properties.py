"""
[INPUT]: 依赖 app.domain.rules 的 decide / stakes / normalized，依赖 app.domain.stakes 的 Proposal / settle_any / route_of，
         依赖 app.domain.combat / social / covert 的三路赌注，依赖 app.domain.aggregates 的 Player，依赖 InMemoryWorldGraph，依赖 tests/world 的 WORLD
[OUTPUT]: 裁决的性质测试：200 条随机意图序列（随机动作 × 手段 × 所图 × 指称 × 话题 × 随机提议，含别的路线的结局与离谱的扣减）在 WORLD 上逐招重放，
          每一招都守住——定案的结局恒在可裁区间里且等于 settle_any 的定案；确定之事（寻常的交谈 / 取物 / 修习，以及移动、静观、赠物、服药、调息、天道）没有赌注；
          人情阶梯每次至多一档，例外只有直落敌视（翻脸、出手、被窃察觉）与物归原主直升信赖；交涉与暗中永不产出 PlayerDied、永不伤人，
          也永不把人推上信赖；没有赌注的举动不产出 Parleyed / Maneuvered / SkillExecuted
[POS]: tests 的不变量网：单测钉的是例子，这里钉的是「无论大模型提议什么、玩家怎么出招」都成立的东西
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import random
from datetime import UTC, datetime
from uuid import uuid4

from app.domain.aggregates import Player
from app.domain.combat import CombatOutcome, CombatProposal, Stakes
from app.domain.covert import CovertStakes
from app.domain.events import (
    DomainEvent,
    EventEnvelope,
    HealthChanged,
    Maneuvered,
    Parleyed,
    PlayerDied,
    PlayerSpawned,
    RelationChanged,
    SkillExecuted,
)
from app.domain.intent import ActionType, Aim, Approach, PlayerIntent
from app.domain.models import Attitude
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.rules import TRUST_RESTORED, decide, normalized, stakes
from app.domain.snapshot import LocalSnapshot
from app.domain.social import SocialStakes
from app.domain.stakes import Proposal, settle_any
from app.infrastructure.persistence.memory_graph import InMemoryWorldGraph
from tests.world import WORLD

SEQUENCES = 200
STEPS = 10
OUTCOMES = [*CombatOutcome, *SocialOutcome, *CovertOutcome]
_FIXED_ACTIONS = {ActionType.MOVE, ActionType.OBSERVE, ActionType.GIVE, ActionType.USE, ActionType.REST, ActionType.INVALID}
_PLAIN_FIXED = {ActionType.TALK, ActionType.TAKE, ActionType.LEARN}


_JUNK = ["少林寺", "倚天剑", "黑虎掏心", "段"]
_WEIGHTS = {  # 有赌注的动作多出几招，每种动作都出
    ActionType.TALK: 4, ActionType.TAKE: 4, ActionType.ATTACK: 2, ActionType.LEARN: 2, ActionType.MOVE: 2,
    ActionType.GIVE: 1, ActionType.OBSERVE: 1, ActionType.USE: 1, ActionType.REST: 1, ActionType.INVALID: 1,
}


def _intent(rng: random.Random, snap: LocalSnapshot) -> PlayerIntent:
    """多半出得像样的招（指称按动作取此情此景里的名字），少半夹带乱写的指称、表外的手段与所图——两种都得守住。"""
    people = [n for c in snap.characters for n in (c.name, *c.titles)] or _JUNK
    things = [i.name for i in snap.items] or _JUNK
    pack = [i.name for i in snap.inventory] or _JUNK
    arts = [s.name for s in snap.skills] or _JUNK
    roads = [e.label for e in snap.exits] or _JUNK
    pool = {ActionType.TALK: people, ActionType.ATTACK: people, ActionType.GIVE: people, ActionType.TAKE: things,
            ActionType.MOVE: roads, ActionType.LEARN: arts, ActionType.USE: pack}

    def pick(options: list[str]) -> str | None:
        roll = rng.random()
        return rng.choice(options) if roll < 0.8 else (rng.choice(_JUNK) if roll < 0.9 else None)

    action = rng.choices(list(_WEIGHTS), weights=list(_WEIGHTS.values()))[0]
    target = pick(pool.get(action, people))
    return PlayerIntent(
        action_type=action, target_entity=target,
        item_used=pick(pack) if action in (ActionType.GIVE, ActionType.ATTACK) and rng.random() < 0.6 else None,
        skill_used=pick(arts) if action in (ActionType.ATTACK, ActionType.LEARN) and rng.random() < 0.5 else None,
        approach=rng.choice(list(Approach)), aim=rng.choice([None, None, *Aim]),
        topic=pick([*people, *things, *arts]) if rng.random() < 0.3 else None,
        reason="不合天道" if action is ActionType.INVALID else None,
    )


def _proposal(rng: random.Random) -> Proposal | CombatProposal | None:
    roll = rng.random()
    if roll < 0.3:
        return None
    outcome = rng.choice(OUTCOMES)
    if roll < 0.5 and isinstance(outcome, CombatOutcome):
        return CombatProposal(outcome=outcome, hp_change=rng.randint(-150, 30))
    return Proposal(outcome=outcome, hp_change=rng.randint(-150, 30))


def _decided_outcome(events: list[DomainEvent]) -> object | None:
    outcomes = [e.outcome for e in events if isinstance(e, SkillExecuted | Parleyed | Maneuvered)]
    assert len(outcomes) <= 1, events
    return outcomes[0] if outcomes else None


async def test_two_hundred_random_sequences_keep_every_rail() -> None:
    graph = InMemoryWorldGraph()
    await graph.seed(WORLD)
    spawns = sorted(loc.id for loc in WORLD.locations)
    seen_routes: set[str] = set()
    for seq in range(SEQUENCES):
        rng = random.Random(seq)
        pid = f"ply:prop{seq}"
        history: list[DomainEvent] = [PlayerSpawned(player_id=pid, name="阿星", location_id=rng.choice(spawns),
                                                    aptitude=rng.choice([0.8, 1.0, 1.3]))]
        player = Player(pid)
        for step in range(STEPS):
            envelopes = [
                EventEnvelope(stream_id=pid, version=player.version + i, event_id=uuid4(), recorded_at=datetime.now(UTC),
                              event=e)  # type: ignore[arg-type]
                for i, e in enumerate(history, start=1)
            ]
            for envelope in envelopes:
                player.apply(envelope.event)
            await graph.project(pid, envelopes)
            state, snap = player.state, await graph.local_snapshot(pid)
            if not state.alive:
                break
            intent, proposal = _intent(rng, snap), _proposal(rng)
            at_stake = stakes(intent, state, snap)
            events = decide(intent, state, snap, proposal)
            label = f"seq {seq} step {step}: {intent!r} / {proposal!r} → {events!r}"
            plain = normalized(intent, state, snap)

            # 确定之事没有赌注
            if plain.action_type in _FIXED_ACTIONS or (plain.action_type in _PLAIN_FIXED and plain.approach is Approach.PLAIN):
                assert at_stake is None, label
            # 定案的结局恒在区间里，且就是 settle_any 的定案；没有赌注就没有结局
            outcome = _decided_outcome(events)
            if at_stake is None:
                assert outcome is None, label
            else:
                seen_routes.add(type(at_stake).__name__)
                assert outcome in at_stake.admissible and outcome == settle_any(at_stake, proposal).outcome, label
            # 人情阶梯：每次至多一档；例外只有直落敌视与物归原主直升信赖
            for e in events:
                if isinstance(e, RelationChanged):
                    before = state.attitude_of(e.character_id)
                    exempt = e.attitude is Attitude.HOSTILE or (e.basis == TRUST_RESTORED and e.attitude is Attitude.TRUSTED)
                    assert abs(e.attitude.rank - before.rank) <= 1 or exempt, label
            # 交涉与暗中永不致死、永不伤人（WORLD 里没有险物），也永不把人推上信赖
            if isinstance(at_stake, SocialStakes | CovertStakes):
                assert not any(isinstance(e, PlayerDied | HealthChanged) for e in events), label
                assert all(e.attitude is not Attitude.TRUSTED for e in events if isinstance(e, RelationChanged)), label
            if isinstance(at_stake, Stakes) and any(isinstance(e, PlayerDied) for e in events):
                assert CombatOutcome.DEATH in at_stake.admissible, label
            history = events
    assert seen_routes == {"Stakes", "SocialStakes", "CovertStakes"}  # 三路都真的走到了
