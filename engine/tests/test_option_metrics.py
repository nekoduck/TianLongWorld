"""
[INPUT]: 依赖 app.application.options 的 OptionGenerator，依赖 app.domain 的 aggregates / events / rules / models / progression，
         依赖 InMemoryWorldGraph，读入库的原著蓝图 data/world/blueprint.json 与实录事件流 tests/fixtures/live_session_events.jsonl
[OUTPUT]: canon_graph()（种下原著蓝图的内存图谱，供别的用例复用）、recorded() / turn_ends() / replay()（实录逐回合重放），
          以及菜单的验收指标：世界不变菜单逐字不变 100%、上回合的焦点在场即被提到 ≥80%、仇人在侧必有出路 100%、
          调息按伤势加权（安然无恙 0、轻伤 ≤50%、重伤及以上且无仇人 100%）、why ≤12 字；重放回合 1 段誉不再生好感、回合 11 不再「素不相识」
[POS]: tests 的零费用回归基线：29 回合真实 Gemini 实录只把事件流入库（无叙事、无密钥），逐版本折叠聚合、投影内存图谱、
       在每个回合边界重算快照与菜单——改选项算法或改蓝图之后都在这里重算一遍指标（P1 审计改蓝图后重定基线）
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from itertools import pairwise
from pathlib import Path
from uuid import uuid4

from app.application.options import ActionOption, OptionGenerator
from app.domain import rules
from app.domain.aggregates import Player, PlayerState
from app.domain.combat import CombatOutcome, CombatProposal
from app.domain.events import (
    ActionFailed,
    DomainEvent,
    EventEnvelope,
    HealthChanged,
    Moved,
    PlayerDied,
    RelationChanged,
    decode_event,
)
from app.domain.intent import ActionType, PlayerIntent
from app.domain.models import Attitude, WorldBlueprint
from app.domain.progression import Vitality
from app.domain.snapshot import LocalSnapshot
from app.infrastructure.persistence.memory_graph import InMemoryWorldGraph

ENGINE = Path(__file__).resolve().parents[1]
BLUEPRINT = ENGINE / "data" / "world" / "blueprint.json"
SESSION = ENGINE / "tests" / "fixtures" / "live_session_events.jsonl"


@cache
def canon() -> WorldBlueprint:
    return WorldBlueprint.model_validate_json(BLUEPRINT.read_text(encoding="utf-8"))


async def canon_graph() -> InMemoryWorldGraph:
    graph = InMemoryWorldGraph()
    await graph.seed(canon())
    return graph


# ============================================================
#  实录重放 —— 事件流 → 回合边界 → (状态, 快照, 菜单)
# ============================================================
def recorded() -> tuple[str, list[EventEnvelope]]:
    rows = [json.loads(line) for line in SESSION.read_text(encoding="utf-8").splitlines() if line.strip()]
    player_id = rows[0]["player_id"]
    envelopes = [
        EventEnvelope(stream_id=player_id, version=row["v"], event_id=uuid4(), recorded_at=datetime.now(UTC),
                      event=decode_event({k: v for k, v in row.items() if k != "v"}))
        for row in rows
    ]
    return player_id, envelopes


def _aftermath(event: DomainEvent) -> bool:
    """交手的余波随那一招入账：伤人者明写的伤、人情涟漪、身死、夺路而逃。其余每条事件都开启新的一回合。"""
    match event:
        case RelationChanged() | PlayerDied():
            return True
        case HealthChanged(source_id=str()):
            return True
        case Moved(fleeing=True):
            return True
    return False


def turn_ends(envelopes: list[EventEnvelope]) -> list[int]:
    """每回合结束时的流版本：事件流里没有回合号，回合边界由「主事件开新回合、余波随前一条」推出。"""
    heads = [e.version for e in envelopes if not _aftermath(e.event)]
    return [*(v - 1 for v in heads[1:]), envelopes[-1].version]


@dataclass(frozen=True)
class Turn:
    number: int
    state: PlayerState
    snap: LocalSnapshot
    menu: tuple[ActionOption, ...]


async def replay() -> list[Turn]:
    player_id, envelopes = recorded()
    graph = await canon_graph()
    player = Player(player_id)
    turns: list[Turn] = []
    for number, end in enumerate(turn_ends(envelopes)):
        batch = envelopes[player.version:end]
        for envelope in batch:
            player.apply(envelope.event)
        await graph.project(player_id, batch)
        snap = await graph.local_snapshot(player_id)
        turns.append(Turn(number, player.state, snap, OptionGenerator().generate(player.state, snap)))
    return turns


async def state_at(version: int) -> tuple[PlayerState, LocalSnapshot]:
    player_id, envelopes = recorded()
    graph = await canon_graph()
    await graph.project(player_id, envelopes[:version])
    return Player.from_history(player_id, envelopes[:version]).state, await graph.local_snapshot(player_id)


def _foes(snap: LocalSnapshot) -> bool:
    return any(c.attitude is Attitude.HOSTILE and not c.subdued for c in snap.characters)


def _subjects(option: ActionOption, turn: Turn) -> set[str | None]:
    ok = rules.adjudicate(option.intent, turn.state, turn.snap)
    assert isinstance(ok, rules.Approval)
    return {ok.target, ok.item, ok.source, ok.skill}


# ============================================================
#  指标
# ============================================================
async def test_the_recording_replays_turn_by_turn() -> None:
    turns = await replay()
    assert len(turns) == 30 and turns[0].snap.location.name == "剑湖宫·练武厅"
    assert all(t.state.alive for t in turns[:-1]) and not turns[-1].state.alive and turns[-1].menu == ()
    assert [t.state.vitality.value for t in turns[:3]] == ["安然无恙", "重伤", "重伤"]  # 与实录状态栏一致


async def test_an_unchanged_world_keeps_the_menu_word_for_word() -> None:
    """世界不变（只多了驳回，版本号变了）菜单逐字不变：100%。实录里回合 9→12、26→27 都是这样，旧菜单按版本轮换、一回一副面孔。"""
    turns = [t for t in await replay() if t.state.alive]
    pairs = [
        (a, b) for a, b in pairwise(turns)
        if a.state == b.state and a.snap.model_copy(update={"version": 0}) == b.snap.model_copy(update={"version": 0})
    ]
    assert len(pairs) >= 4
    assert all(a.menu == b.menu for a, b in pairs)  # 旧算法：0/5
    for t in turns:
        bumped = t.snap.model_copy(update={"version": t.snap.version + 7})
        assert OptionGenerator().generate(t.state, bumped) == t.menu


async def test_the_menu_follows_whom_you_just_dealt_with() -> None:
    """
    上回合的焦点（focus[0]）仍在眼前，菜单就提到他：≥80%（旧算法按版本轮换，回合 1 刚打完龚光杰，菜单给的是左子穆）。
    「在眼前」指在场之人或不在你行囊里的可见之物——揣进怀里的通天草是你的东西，不是眼前的对象。
    """
    turns = [t for t in await replay() if t.state.alive]

    def in_view(t: Turn, entity: str) -> bool:
        thing = t.snap.item(entity)
        return t.snap.character(entity) is not None or (thing is not None and thing.holder_id != t.snap.player_id)

    present = [t for t in turns if t.state.focus and in_view(t, t.state.focus[0])]
    mentioned = [t for t in present if any(t.state.focus[0] in _subjects(o, t) for o in t.menu)]
    assert len(present) >= 5 and len(mentioned) / len(present) >= 0.8
    first = turns[1]  # 回合 1：一拳打向龚光杰，重伤
    assert first.state.focus[0] == "chr:龚光杰" and any(o.intent.target_entity == "龚光杰" for o in first.menu)


async def test_a_way_out_whenever_a_foe_is_present() -> None:
    turns = [t for t in await replay() if t.state.alive and _foes(t.snap) and t.snap.exits]
    assert len(turns) >= 5
    for t in turns:
        assert t.menu[0].intent.action_type is ActionType.MOVE and t.menu[0].why == "仇人在侧，先脱身"


async def test_rest_is_offered_by_the_wound() -> None:
    """调息：安然无恙 0 次（旧 2/3）、轻伤回合 ≤50%（旧 16/20）、重伤及以上且无仇人在侧 100%。"""
    turns = [t for t in await replay() if t.state.alive]

    def rested(t: Turn) -> bool:
        return any(o.intent.action_type is ActionType.REST for o in t.menu)

    hale = [t for t in turns if t.state.vitality is Vitality.HALE]
    hurt = [t for t in turns if t.state.vitality is Vitality.HURT]
    heavy = [t for t in turns if t.state.vitality.rank >= Vitality.WOUNDED.rank and not _foes(t.snap)]
    assert hale and hurt and heavy
    assert not any(rested(t) for t in hale)
    assert sum(map(rested, hurt)) <= len(hurt) / 2
    assert all(rested(t) for t in heavy)


async def test_every_option_says_why_in_a_few_words() -> None:
    for t in await replay():
        assert 3 <= len(t.menu) <= 4 or not t.state.alive
        assert all(0 < len(o.why) <= 12 for o in t.menu)
        assert all(isinstance(rules.adjudicate(o.intent, t.state, t.snap), rules.Approval) for o in t.menu)


async def test_replaying_turn_one_no_longer_befriends_duan_yu() -> None:
    """实录回合 1：一拳打向龚光杰，段誉沿后文才结下的仇敌边（掌掴）生出好感。如今「敌人之敌」暂停，涟漪只剩同门与师徒。"""
    _, envelopes = recorded()
    assert any(isinstance(e.event, RelationChanged) and e.event.character_id == "chr:段誉" for e in envelopes[:8])
    state, snap = await state_at(1)
    punch = PlayerIntent(action_type=ActionType.ATTACK, target_entity="龚光杰")
    events = rules.decide(punch, state, snap, CombatProposal(CombatOutcome.SEVERE_WOUND, -58))
    changed = {e.character_id: e.attitude for e in events if isinstance(e, RelationChanged)}
    assert "chr:段誉" not in changed
    assert changed["chr:龚光杰"] is Attitude.HOSTILE and changed["chr:左子穆"] is Attitude.HOSTILE


async def test_replaying_turn_eleven_no_longer_says_strangers() -> None:
    """实录回合 11：木婉清对你漠然，拒绝理由却写死为「素不相识」。如今照实写：素无交情。"""
    _, envelopes = recorded()
    recorded_refusal = envelopes[20].event
    assert isinstance(recorded_refusal, ActionFailed) and "素不相识" in recorded_refusal.reason
    state, snap = await state_at(20)
    ask = PlayerIntent(action_type=ActionType.LEARN, target_entity="木婉清", skill_used="晓风拂柳")
    refused = rules.decide(ask, state, snap)
    assert isinstance(refused[0], ActionFailed) and refused[0].reason == "木婉清不肯将晓风拂柳传给素无交情之人。"
