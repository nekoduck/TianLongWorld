"""
[INPUT]: 依赖 app.application.options 的 OptionGenerator，依赖 app.application.navigation 的 navigation / NavigationOption，
         依赖 app.domain 的 aggregates / events / rules / models / progression / intent / geography，
         依赖 InMemoryWorldGraph，读入库的原著蓝图 data/world/blueprint.json 与实录事件流 tests/fixtures/live_session_events.jsonl
[OUTPUT]: canon_graph()（种下原著蓝图的内存图谱，供别的用例复用）、recorded() / turn_ends() / replay()（实录逐回合重放，每回合带退路菜单与导航），
          以及菜单的验收指标：世界不变则菜单、目录与导航逐字不变 100%、上回合的焦点在场即被提到 ≥80%、仇人在侧恰有一条导航标着 retreat 且就是 rules.retreat 那条 100%
          （四下无敌不标、菜单里不再有出路）、导航每条出路一项且未知去处的名字不进菜单与导航的任何下发字段、
          调息按伤势加权（安然无恙 0、轻伤只补位且 ≤80%、重伤及以上且无仇人 100%）、退路菜单 3~4 席（可供之招不足三招时有几招给几招）且 why ≤12 字、
          退路菜单与目录都是 affordances 的子集、目录 ≤12 招且每根有招的轴都在；重放回合 1 段誉不再生好感、回合 11 不再「素不相识」；
          P1 验收：有人在场的回合里菜单 (动作, 手段) ≥3 种的比例 ≥80%、退路菜单铺开战术轴（至少两根 100%、三根以上 ≥60%）、
          带机械后果的选项（canonical 下 rules.decide 至少一条事件）≥90%、去专名后的标签模板 ≥15 种、同一对象至多两席、
          拾起无量玉璧 / 莽牯朱蛤 / 蒲团的选项为 0（实录每回合的全部候选 + 在它们的正典所在投胎）
[POS]: tests 的零费用回归基线：29 回合真实 Gemini 实录只把事件流入库（无叙事、无密钥），逐版本折叠聚合、投影内存图谱、
       在每个回合边界重算快照、菜单与导航——改选项算法或改蓝图之后都在这里重算一遍指标（P1 审计让段延庆后来才到场，样本门槛随之重定：焦点在眼前 ≥4 回合、仇人在侧 ≥3 回合）。
       掌故入库（人设 13、见闻 43）后重算：打探之招开始出现（回合 5「向钟灵探问消息」）。
       意图风味封装与导航剥离之后重定基线（移动不再占菜单的席位）：(动作, 手段) ≥3 种 13/13、战术轴至少两根 13/13 / 三根以上 9/13 / 四根全占 8/13、
       机械后果 82/89（静观补位更常见，静观本就不写事件）、模板 19 种（出路的三种措辞去了，问路与静观等补位的说法来了）、焦点 4/4、
       轻伤回合的调息 15/20（空旷的石室、洞穴、江畔正席不足三席，调息补位）、菜单 3 席 15 回 / 4 席 8 回 / 2 席 6 回（只有调息与静观可做）；
       风险档 稳妥 72 / 有险 17；仇人在侧的回合 1、6、7 各有一条 retreat
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cache
from itertools import pairwise
from pathlib import Path
from uuid import uuid4

from app.application.navigation import NavigationOption, navigation
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
    PlayerSpawned,
    RelationChanged,
    decode_event,
)
from app.domain.geography import UNKNOWN_PLACE
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
    menu: tuple[ActionOption, ...]  # 退路菜单（说书人不交菜单时下发的那一份）
    nav: tuple[NavigationOption, ...] = ()


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
        turns.append(Turn(number, player.state, snap, OptionGenerator().generate(player.state, snap),
                          navigation(player.state, snap)))
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
    assert all(a.menu == b.menu and a.nav == b.nav for a, b in pairs)  # 旧算法：0/5
    gen = OptionGenerator()
    for t in turns:
        bumped = t.snap.model_copy(update={"version": t.snap.version + 7})
        assert gen.generate(t.state, bumped) == t.menu and navigation(t.state, bumped) == t.nav
        assert gen.catalogue(t.state, bumped) == gen.catalogue(t.state, t.snap)


async def test_the_menu_follows_whom_you_just_dealt_with() -> None:
    """
    上回合的焦点（focus[0]）仍在眼前，菜单就提到他：≥80%（旧算法按版本轮换，回合 1 刚打完龚光杰，菜单给的是左子穆）。
    「在眼前」指在场之人或不在你行囊里的可见之物——揣进怀里的通天草是你的东西，不是眼前的对象。
    P1 审计重定基线：段延庆后来才到场（arrives_with），实录回合 22~28 他已不在澜沧江畔，可统计的回合从 8 降到 4（回合 1、5、6、7），比例不变。
    """
    turns = [t for t in await replay() if t.state.alive]

    def in_view(t: Turn, entity: str) -> bool:
        thing = t.snap.item(entity)
        return t.snap.character(entity) is not None or (thing is not None and thing.holder_id != t.snap.player_id)

    present = [t for t in turns if t.state.focus and in_view(t, t.state.focus[0])]
    mentioned = [t for t in present if any(t.state.focus[0] in _subjects(o, t) for o in t.menu)]
    assert len(present) >= 4 and len(mentioned) / len(present) >= 0.8
    first = turns[1]  # 回合 1：一拳打向龚光杰，重伤
    assert first.state.focus[0] == "chr:龚光杰" and any(o.intent.target_entity == "龚光杰" for o in first.menu)


async def test_a_way_out_whenever_a_foe_is_present() -> None:
    """
    仇人在侧必有脱身之路：100%。移动从菜单剥离成导航之后，脱身席改由导航的 retreat 体现——恰有一条、就是 rules.retreat 会走的那条
    （先走来路，绝不逃回险地）；四下无敌的回合一条也不标。P1 审计重定基线：段延庆后来才到场，仇人在侧的回合从 7 降到 3（回合 1、6、7）。
    """
    turns = [t for t in await replay() if t.state.alive]
    hunted = [t for t in turns if _foes(t.snap) and t.snap.exits]
    assert len(hunted) >= 3
    for t in hunted:
        way = rules.retreat(t.state, t.snap)
        assert way is not None
        marked = [n for n in t.nav if n.retreat]
        assert len(marked) == 1 and marked[0].intent.target_entity == t.snap.handle(way)
    assert not any(n.retreat for t in turns if not _foes(t.snap) for n in t.nav)
    assert not any(o.intent.action_type is ActionType.MOVE for t in turns for o in t.menu)  # 菜单里不再有出路


async def test_navigation_covers_every_road_and_keeps_the_fog() -> None:
    """
    每回合每条出路一项导航（全部获准）、指令只用方位把手、耗时等于 rules.command；未知去处只露「未知区域」，
    菜单与导航下发的字段里都没有未知去处的名字（实录：剑湖宫多数出路至今未知，方位推不出的写「不明」）。
    """
    for t in await replay():
        if not t.state.alive:
            assert t.nav == ()
            continue
        assert len(t.nav) == len(t.snap.exits)
        hidden = {e.to_name for e in t.snap.exits if not e.known} - {e.to_name for e in t.snap.exits if e.known}
        for n in t.nav:
            assert n.underlying_command == rules.command(n.intent, t.state, t.snap)
            assert n.target == UNKNOWN_PLACE or n.discovery.known
        shown = "".join(n.model_dump_json(exclude={"underlying_command"}) + n.intent.model_dump_json() for n in t.nav)
        shown += "".join(o.label + o.why for o in t.menu)
        for known in sorted({e.to_name for e in t.snap.exits if e.known}, key=len, reverse=True):
            shown = shown.replace(known, "·")  # 认得的「剑湖宫」里本就含着未知的「剑湖」
        assert not [name for name in hidden if name in shown], t.number


FILLER = (ActionType.REST, ActionType.OBSERVE, ActionType.USE)


async def test_rest_is_offered_by_the_wound() -> None:
    """
    调息：安然无恙 0 次（旧 2/3）、重伤及以上且无仇人在侧 100%；轻伤只补位——菜单里有轻伤的调息，正席必不足三席。
    重定基线：出路归了导航，空旷之处（石室、洞穴、江畔）的正席常常不足三席，轻伤回合的调息从 ≤50% 升到 15/20，
    但它仍只是补位：有人在场、正席够三席的轻伤回合（剑湖宫回合 3）不给。
    """
    turns = [t for t in await replay() if t.state.alive]

    def rested(t: Turn) -> bool:
        return any(o.intent.action_type is ActionType.REST for o in t.menu)

    hale = [t for t in turns if t.state.vitality is Vitality.HALE]
    hurt = [t for t in turns if t.state.vitality is Vitality.HURT]
    heavy = [t for t in turns if t.state.vitality.rank >= Vitality.WOUNDED.rank and not _foes(t.snap)]
    assert hale and hurt and heavy
    assert not any(rested(t) for t in hale)
    assert all(len([o for o in t.menu if o.intent.action_type not in FILLER]) < 3 for t in hurt if rested(t))
    assert any(not rested(t) for t in hurt) and sum(map(rested, hurt)) <= 0.8 * len(hurt)
    assert all(rested(t) for t in heavy)


async def test_every_option_says_why_in_a_few_words() -> None:
    """
    退路菜单 3~4 席——可供之招本就不足三招的回合（实录：空旷的石室与江畔只有调息与静观，出路归了导航）有几招给几招；
    每席 why ≤12 字且获准；退路菜单与可供性目录都取自 affordances（点选按 id 在那里核验）。
    """
    gen = OptionGenerator()
    for t in await replay():
        pool = {o.id: o for o in gen.affordances(t.state, t.snap)}
        assert min(3, len(pool)) <= len(t.menu) <= 4 or not t.state.alive
        assert all(0 < len(o.why) <= 12 for o in t.menu)
        assert all(isinstance(rules.adjudicate(o.intent, t.state, t.snap), rules.Approval) for o in t.menu)
        catalogue = gen.catalogue(t.state, t.snap)
        assert all(pool[o.id].underlying_command == o.underlying_command for o in (*t.menu, *catalogue))
        assert len(catalogue) <= 12 and {o.tactical_axis for o in catalogue} == {o.tactical_axis for o in pool.values()}


async def test_replaying_turn_one_no_longer_befriends_duan_yu() -> None:
    """实录回合 1：一拳打向龚光杰，段誉沿仇敌边（掌掴）生出好感。审计把这条边定为「将至」——比剑时段誉失笑才结的仇，T=0 还不存在，名声只认开篇羁绊。"""
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


# ============================================================
#  P1 验收 —— 选项成为「招」
# ============================================================
IMMOVABLE = ("itm:无量玉璧", "itm:莽牯朱蛤", "itm:大蒲团", "itm:小蒲团")


def every(state: PlayerState, snap: LocalSnapshot) -> tuple[ActionOption, ...]:
    """全部合法的招：席位、补位、同一对象的上限都拉满。"""
    return OptionGenerator(max_options=999, min_options=999, per_target=999).generate(state, snap)


def _names() -> list[str]:
    bp = canon()
    names: set[str] = set()
    for c in bp.characters:
        names |= {c.true_name, *c.titles, *c.aliases}
    for i in bp.items:
        names |= {i.name, *i.aliases}
    for a in bp.martial_arts:
        names.add(a.name)
    for loc in bp.locations:
        names |= {loc.name, *loc.aliases}
    return sorted((n for n in names if n), key=len, reverse=True)


def denamed(label: str, names: list[str]) -> str:
    """去专名：出路名整个换成占位，再由长到短抹掉人物物功地的名字，连着的占位并成一个。"""
    text = re.sub(r"「[^」]*」", "「·」", label)
    for name in names:
        text = text.replace(name, "·")
    return re.sub(r"·+", "·", text)


async def test_people_in_view_get_several_kinds_of_moves() -> None:
    """
    有人在场的回合里，菜单的 (动作, 手段) 组合 ≥3 种：≥80%（P0：回合 8~12 木婉清当面，菜单是一招恳请加三条出路；如今 13/13）；
    退路菜单铺开战术轴：有人在场、目录里有三根以上的轴时，菜单至少占两根轴 100%、占三根以上 ≥60%（实录 13/13 至少两根、9/13 三根以上、8/13 四根全占）。
    """
    turns = [t for t in await replay() if t.state.alive and t.snap.characters]
    varied = [t for t in turns if len({(o.intent.action_type, o.intent.approach) for o in t.menu}) >= 3]
    assert len(turns) >= 10 and len(varied) / len(turns) >= 0.8
    gen = OptionGenerator()
    wide = [t for t in turns if len({o.tactical_axis for o in gen.catalogue(t.state, t.snap)}) >= 3]
    spread = [len({o.tactical_axis for o in t.menu}) for t in wide]
    assert wide and min(spread) >= 2 and sum(n >= 3 for n in spread) / len(spread) >= 0.6


async def test_options_have_mechanical_consequences() -> None:
    """
    带机械后果的选项（canonical 下 rules.decide 至少产出一条事件）≥90%：静观之外，每一招都改变点什么。
    导航剥离后 82/89——出路不再占席，空旷之处补位的静观多了（静观只花时间，世界心跳之外不写事件）。
    """
    pairs = [(t, o) for t in await replay() for o in t.menu]
    moving = [o for t, o in pairs if rules.decide(o.intent, t.state, t.snap)]
    assert len(moving) / len(pairs) >= 0.9


async def test_labels_are_not_one_voice() -> None:
    """去专名后的标签模板 ≥15 种：同一类招随人与物换着说法（措辞变体按意图哈希挑，世界不变则逐字不变）。导航剥离后 19 种。"""
    names = _names()
    templates = {denamed(o.label, names) for t in await replay() for o in t.menu}
    assert len(templates) >= 15, sorted(templates)
    assert denamed("沿「入练武厅」前往剑湖宫·练武厅", names) == "沿「·」前往·"  # 去专名的口径（出路标签也照样抹得掉）


async def test_one_target_never_holds_more_than_two_seats() -> None:
    for t in await replay():
        held = Counter(
            next((x for x in (ok.target, ok.source) if x and x.startswith("chr:")), None)
            for o in t.menu if isinstance(ok := rules.adjudicate(o.intent, t.state, t.snap), rules.Approval)
        )
        held.pop(None, None)
        assert max(held.values(), default=0) <= 2


async def spawned(location_id: str) -> tuple[PlayerState, LocalSnapshot]:
    graph = await canon_graph()
    spawn = PlayerSpawned(player_id="ply:metrics", name="阿星", location_id=location_id)
    await graph.project("ply:metrics", [EventEnvelope(stream_id="ply:metrics", version=1, event_id=uuid4(),
                                                      recorded_at=datetime.now(UTC), event=spawn)])
    return Player.replay([spawn]), await graph.local_snapshot("ply:metrics")  # type: ignore[return-value]


async def test_immovable_things_are_never_offered() -> None:
    """拾起无量玉璧 / 莽牯朱蛤 / 蒲团的选项为 0：实录每回合的全部候选里没有，在它们的正典所在投胎也没有（物性闸门由规则把关）。"""
    def takes(state: PlayerState, snap: LocalSnapshot) -> list[str]:
        return [o.label for o in every(state, snap) if o.intent.action_type is ActionType.TAKE
                and any(snap.item(i) is not None and o.intent.target_entity in snap.item(i).names  # type: ignore[union-attr]
                        for i in IMMOVABLE)]

    for t in await replay():
        if t.state.alive:
            assert takes(t.state, t.snap) == []
    homes = {i.location_id for i in canon().items if i.id in IMMOVABLE}
    assert homes == {"loc:无量山", "loc:幽谷·玉像石室"}
    for home in homes:
        state, snap = await spawned(home)
        assert {i.id for i in snap.items} & set(IMMOVABLE)  # 它们确实就在眼前
        assert takes(state, snap) == []
