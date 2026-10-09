"""
[INPUT]: 依赖 app.domain.rules 的 decide / stakes / normalized / command / resolve（与 rules.base 的 names），依赖 app.domain.commands 的 REFUSED_COST，依赖 app.domain.stakes 的 Proposal / settle_any / route_of，
         依赖 app.domain.combat / social / covert 的三路赌注，依赖 app.domain.aggregates 的 Player，依赖 InMemoryWorldGraph，依赖 tests/world 的 WORLD
[OUTPUT]: 裁决的性质测试：200 条随机意图序列（随机动作 × 手段 × 所图 × 指称 × 话题 × 随机提议，含别的路线的结局与离谱的扣减，
          以及随机的推演 ResolutionOutput：随机量级、属性键（含落不了地的名字）、时钟指令（含眼前没有的挂处与时钟）、事实（含夹带状态字眼的）、路由）
          在 WORLD 上逐招重放，每一招都守住——定案的结局恒在可裁区间里且等于闸门（resolution.settle）或 settle_any 的定案；确定之事（寻常的交谈 / 取物 / 修习，以及移动、静观、赠物、服药、调息、天道）没有赌注；
          人情阶梯每次至多一档，例外只有直落敌视（翻脸、出手、被窃察觉）与物归原主直升信赖（且那件东西不是从物主本人手里拿来的）；
          暗取差了两境以上只有失手、东西绝不到手；威逼从不图结交 / 化解 / 求艺，这三种所图如愿时对方的人情不降（旁人的人情可作等价交换的代价降一档）；交涉与暗中永不产出 PlayerDied、永不伤人，
          也永不把人推上信赖（交涉的气血只可能来自危机时钟坍缩，暗取的代价至多 20 点）；没有赌注的举动不产出 Parleyed / Maneuvered / SkillExecuted；
          名望每回合的闸门涨落在 [−20, +5] 且只有得手类结局才涨、折叠后钳在 ±100；时钟从不满格悬着，总数与每个挂处都不超上限；
          世界心跳：随机意图含 THINK（沉思与静观同为确定之事），每一招的 rules.command 都花时间（≥1 刻，驳回恰一刻）且装的是规整过的意图；
          空间与迷雾：去向随机取标签、方位把手与去处真名（含未知的），获准的移动恰落在 exit_names 认得出的那条出路上且耗时即其 time_cost，
          驳回的理由不露未知去处的标签与真名；问路（话题随机取此地与去处之名）只出在寻常攀谈里，得知的都是此地相邻、此前还不认得的去处，指路人即交谈对象
[POS]: tests 的不变量网：单测钉的是例子，这里钉的是「无论大模型提议什么、玩家怎么出招」都成立的东西
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import random
from datetime import UTC, datetime
from uuid import uuid4

from app.domain.aggregates import Player, PlayerState
from app.domain.approach import UNCOERCIBLE
from app.domain.clocks import CLOCKS_MAX, PER_ANCHOR, ClockKind
from app.domain.combat import CombatOutcome, CombatProposal, Stakes
from app.domain.commands import REFUSED_COST
from app.domain.covert import CovertStakes
from app.domain.events import (
    ActionFailed,
    ClockAdvanced,
    ClockCollapsed,
    ClockStarted,
    DomainEvent,
    EventEnvelope,
    HealthChanged,
    ItemTransferred,
    Maneuvered,
    Moved,
    Parleyed,
    PlacesLearned,
    PlayerDied,
    PlayerSpawned,
    RelationChanged,
    RenownChanged,
    SkillExecuted,
)
from app.domain.intent import ActionType, Aim, Approach, PlayerIntent
from app.domain.models import Attitude
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.progression import RENOWN_MAX
from app.domain.resolution import (
    ActionTrigger,
    ClockMutation,
    ClockOp,
    Envelope,
    ResolutionOutput,
    Severity,
    clock_anchors,
    delta_keys,
    settle,
)
from app.domain.rules import TRUST_RESTORED, command, decide, envelope, normalized, resolve, stakes
from app.domain.rules.base import names
from app.domain.snapshot import LocalSnapshot
from app.domain.social import SocialStakes
from app.domain.stakes import Proposal, settle_any
from app.infrastructure.persistence.memory_graph import InMemoryWorldGraph
from tests.world import WORLD

SEQUENCES = 200
STEPS = 10
OUTCOMES = [*CombatOutcome, *SocialOutcome, *CovertOutcome]
_FIXED_ACTIONS = {ActionType.MOVE, ActionType.OBSERVE, ActionType.THINK, ActionType.GIVE, ActionType.USE, ActionType.REST,
                  ActionType.INVALID}
_PLAIN_FIXED = {ActionType.TALK, ActionType.TAKE, ActionType.LEARN}


_JUNK = ["少林寺", "倚天剑", "黑虎掏心", "段"]
_WEIGHTS = {  # 有赌注的动作多出几招，每种动作都出
    ActionType.TALK: 4, ActionType.TAKE: 4, ActionType.ATTACK: 2, ActionType.LEARN: 2, ActionType.MOVE: 2,
    ActionType.GIVE: 1, ActionType.OBSERVE: 1, ActionType.THINK: 1, ActionType.USE: 1, ActionType.REST: 1,
    ActionType.INVALID: 1,
}


def _intent(rng: random.Random, snap: LocalSnapshot) -> PlayerIntent:
    """多半出得像样的招（指称按动作取此情此景里的名字），少半夹带乱写的指称、表外的手段与所图——两种都得守住。"""
    people = [n for c in snap.characters for n in (c.name, *c.titles)] or _JUNK
    things = [i.name for i in snap.items] or _JUNK
    pack = [i.name for i in snap.inventory] or _JUNK
    arts = [s.name for s in snap.skills] or _JUNK
    roads = [n for e in snap.exits for n in (e.label, snap.handle(e), e.to_name)] or _JUNK  # 连未知去处的真名也试：迷雾须守住
    places = [snap.location.name, *(e.to_name for e in snap.exits)]
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
        topic=pick([*people, *things, *arts, *places]) if rng.random() < 0.3 else None,
        reason="不合天道" if action is ActionType.INVALID else None,
    )


_FACTS = ["风声紧了", "左子穆的剑穗微微发颤", "龚光杰死了", "你来到崖边", "abc", "无量山的雾气更浓了", "风" * 41]
_WINS = {CombatOutcome.SUCCESS, SocialOutcome.GRANTED, CovertOutcome.CLEAN, CovertOutcome.EXPOSED}


def _output(rng: random.Random, env: Envelope | None, snap: LocalSnapshot) -> ResolutionOutput:
    """一份随机的推演：多半用得上此景的属性键与挂处，少半夹带落不了地的名字、眼前没有的时钟与夹带状态字眼的事实。"""
    keys = [*(delta_keys(env, snap) if env else ("名望",)), "人情:乔峰", "制住:龚光杰", "所图", "气血"]
    deltas: dict[str, int] = {}
    for key in rng.sample(keys, k=rng.randint(0, 4)):
        deltas[key] = 1 if key == "所图" or key.startswith("制住") else rng.choice([-60, -20, -3, -1, 1, 2, 9])
    anchors = [*clock_anchors(snap), "乔峰"]
    names = [c.name for c in snap.clocks] * 4 + ["没有这只钟"]
    mutations: list[ClockMutation] = []
    for _ in range(rng.randint(0, 3)):
        op = rng.choices(list(ClockOp), weights=[3, 5, 1, 1])[0]  # 新建 / 推进 / 回退 / 销毁：多推进，坍缩才真的发生
        if op is ClockOp.START:
            mutations.append(ClockMutation(op=op, clock=rng.choice(["那人的疑心", "山雨欲来", "交情", *names]),
                                           kind=rng.choice(list(ClockKind)), anchor=rng.choice(anchors),
                                           maximum=rng.choice([4, 6, 8]), steps=rng.randint(1, 3), consequence="满则生变"))
        else:
            mutations.append(ClockMutation(op=op, clock=rng.choice(names), steps=rng.randint(1, 3)))
    return ResolutionOutput(
        severity=rng.choice(list(Severity)), deltas=deltas, clock_mutations=tuple(mutations),
        new_facts=tuple(rng.sample(_FACTS, k=rng.randint(0, 2))), action_trigger=rng.choice(list(ActionTrigger)),
    )


def _hang(state: PlayerState, snap: LocalSnapshot) -> LocalSnapshot:
    """快照只召回挂在眼前之物上的时钟：与图谱实现同口径，领域的性质不受图谱实现的进度左右。"""
    view = {snap.location.id, state.player_id, *(c.id for c in snap.characters), *(i.id for i in snap.items)}
    return snap.model_copy(update={"clocks": tuple(c for c in state.clocks if c.anchor_id in view)})


def _proposal(rng: random.Random, env: Envelope | None, snap: LocalSnapshot) -> ResolutionOutput | Proposal | CombatProposal | None:
    roll = rng.random()
    if roll < 0.2:
        return None
    if roll < 0.65:
        return _output(rng, env, snap)
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
    asked_the_way = 0
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
            for wrapped in envelopes:
                player.apply(wrapped.event)
            await graph.project(pid, envelopes)
            state = player.state
            snap = _hang(state, await graph.local_snapshot(pid))
            if not state.alive:
                break
            # 时钟从不满格悬着；总数与每个挂处都在上限之内；名望折叠后钳在 ±100
            assert all(c.progress < c.maximum for c in state.clocks) and len(state.clocks) <= CLOCKS_MAX, state.clocks
            anchors = [c.anchor_id for c in state.clocks]
            assert all(anchors.count(a) <= PER_ANCHOR for a in anchors), state.clocks
            assert -RENOWN_MAX <= state.renown_points <= RENOWN_MAX
            intent = _intent(rng, snap)
            env = envelope(intent, state, snap)
            proposal = _proposal(rng, env, snap)
            at_stake = stakes(intent, state, snap)
            events = decide(intent, state, snap, proposal)
            label = f"seq {seq} step {step}: {intent!r} / {proposal!r} → {events!r}"
            plain = normalized(intent, state, snap)

            # 没有不花时间的命令：驳回恰花一刻，获准的照封闭表；Command 装的是规整过的意图
            cmd = command(intent, state, snap)
            refused = len(events) == 1 and isinstance(events[0], ActionFailed)
            assert cmd.time_cost >= 1 and cmd.intent == plain and (not refused or cmd.time_cost == REFUSED_COST), label

            # 空间与迷雾：移动只落在 exit_names 认得出的那条出路上、耗时即那条出路的耗时；驳回不露未知去处的标签与真名；
            # 问路只出在寻常攀谈里，得知的都是此地相邻、此前还不认得的去处，指路人即交谈对象
            fog = [e for e in snap.exits if not e.known]
            if plain.action_type is ActionType.MOVE and events and isinstance(events[0], Moved) and not events[0].fleeing:
                way = resolve(plain.target_entity, snap.exits, snap.exit_names)
                assert way is not None and way.to_id == events[0].to_location_id and cmd.time_cost == way.time_cost, label
            if refused and plain.action_type is ActionType.MOVE and isinstance(events[0], ActionFailed):
                told = events[0].reason.replace(f"「{plain.target_entity}」", "")
                assert not any(e.to_name in told or e.label in told for e in fog), label
            for e in events:
                if isinstance(e, PlacesLearned):
                    asked_the_way += 1
                    known = state.visited | state.heard | {x.to_id for x in snap.exits if x.known}
                    assert plain.action_type is ActionType.TALK and plain.approach is Approach.PLAIN, label
                    assert set(e.location_ids) <= {x.to_id for x in snap.exits} | {snap.location.id}, label
                    assert not set(e.location_ids) & known and list(e.location_ids) == sorted(set(e.location_ids)), label
                    assert e.source_id == resolve(plain.target_entity, snap.characters, names).id, label  # type: ignore[union-attr]
            # 确定之事没有赌注
            if plain.action_type in _FIXED_ACTIONS or (plain.action_type in _PLAIN_FIXED and plain.approach is Approach.PLAIN):
                assert at_stake is None, label
            # 定案的结局恒在区间里，且就是 settle_any 的定案；没有赌注就没有结局
            outcome = _decided_outcome(events)
            if at_stake is None:
                assert outcome is None, label
            else:
                seen_routes.add(type(at_stake).__name__)
                assert env is not None, label
                if isinstance(proposal, ResolutionOutput):
                    expected = settle(env, proposal, state, snap).outcome  # 推演：结局由属性变化推出，出界即确定性裁决
                    seen_routes.add("推演")
                else:
                    expected = settle_any(at_stake, proposal).outcome
                assert outcome in at_stake.admissible and outcome == expected, label
            # 时钟事件从不写出满格；名望的闸门涨落在 [−20, +5]，只有得手类结局才涨
            assert all(e.clock.progress < e.clock.maximum for e in events if isinstance(e, ClockStarted)), label
            assert all(e.progress < e.maximum for e in events if isinstance(e, ClockAdvanced)), label
            for e in events:
                if isinstance(e, RenownChanged) and e.cause == "江湖传言":
                    assert -20 <= e.delta <= 5 and (e.delta < 0 or outcome in _WINS), label
            collapsed = [e for e in events if isinstance(e, ClockCollapsed)]
            if collapsed:
                seen_routes.add("坍缩")
            # 人情阶梯：每次至多一档；例外只有直落敌视与物归原主直升信赖——而且那件东西不是从物主本人手里拿来的
            returned = {e.item_id for e in events if isinstance(e, ItemTransferred) and e.from_holder == pid}
            for e in events:
                if isinstance(e, RelationChanged):
                    before = state.attitude_of(e.character_id)
                    exempt = e.attitude is Attitude.HOSTILE or (e.basis == TRUST_RESTORED and e.attitude is Attitude.TRUSTED)
                    assert abs(e.attitude.rank - before.rank) <= 1 or exempt, label
                    if e.basis == TRUST_RESTORED:
                        assert any(state.taken_from.get(item) != e.character_id for item in returned), label
            # 暗取差了两境以上：东西绝不到手
            if isinstance(at_stake, CovertStakes) and at_stake.margin <= -2:
                assert at_stake.admissible == (CovertOutcome.CAUGHT,), label
                assert not any(isinstance(e, ItemTransferred) for e in events), label
            # 威逼图不来交情、和解与真传；交涉如愿而对方人情反降的，只能是威逼得逞（旁人的人情可以作等价交换的代价降一档）
            if isinstance(at_stake, SocialStakes):
                assert not (at_stake.approach is Approach.FORCE and at_stake.aim in UNCOERCIBLE), label
                if outcome is SocialOutcome.GRANTED and at_stake.aim in UNCOERCIBLE:
                    assert all(e.attitude.rank >= state.attitude_of(e.character_id).rank
                               for e in events if isinstance(e, RelationChanged) and e.character_id == at_stake.npc_id), label
            # 交涉与暗中永不致死、也永不把人推上信赖（WORLD 里没有险物）；交涉永不伤人——气血只可能来自危机时钟的坍缩，
            # 暗取的代价至多 20 点（留一口气）
            if isinstance(at_stake, SocialStakes | CovertStakes):
                assert not any(isinstance(e, PlayerDied) for e in events), label
                assert all(e.attitude is not Attitude.TRUSTED for e in events if isinstance(e, RelationChanged)), label
                hurt = [e for e in events if isinstance(e, HealthChanged) and "满了" not in e.cause]
                if isinstance(at_stake, SocialStakes):
                    assert not hurt, label
                else:
                    assert sum(e.delta for e in hurt) >= -20 and state.hp + sum(e.delta for e in hurt) >= 1, label
                if any(isinstance(e, HealthChanged) and "满了" in e.cause for e in events):
                    assert any(f.name in e.cause for e in events if isinstance(e, HealthChanged)
                               for f in collapsed), label
            if isinstance(at_stake, Stakes) and any(isinstance(e, PlayerDied) for e in events):
                assert CombatOutcome.DEATH in at_stake.admissible, label
            history = events
    assert seen_routes == {"Stakes", "SocialStakes", "CovertStakes", "推演", "坍缩"}  # 三路、推演与坍缩都真的走到了
    assert asked_the_way > 0  # 问路也真的走到了
