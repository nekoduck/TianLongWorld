"""
[INPUT]: 依赖 app.domain.heartbeat 的 Atlas / intensity / deed / aftermath / spread / ecology 与 BLOOD / SCUFFLE / LITTER / ROUT_TICKS / PILFER_ODDS，
         依赖 app.domain 的 ambient / commands / events / models / lore / outcomes / combat / intent / snapshot / aggregates，
         依赖 app.application.world_clock 的 WorldClock，依赖 InMemoryWorldGraph 生成快照，依赖 tests/world 的 WORLD
[OUTPUT]: 世界物理（domain/heartbeat）的单测：
          Atlas.of（道路双向、室内之地、正典物品排除后来才出现者、常驻之人排除已故与后来才到场者）与 ring 的广度优先次序（跳数, id）；
          intensity 烈度表（交手五档、身死、有人出手伤你、暗取被察觉、当场翻脸，取最烈的一件）；
          deed 五种消息（交手 / 夺物、暗取败露与失手、当场翻脸、物归原主）与不成消息的情形（暗中得手无人察觉、未遂、闲谈、修习、交涉如愿、拾取、赠给非物主）；
          aftermath（见血与不见血的痕迹与一刻即止的交手活动、烈度高过阈值才溃散且一群一个活动共一道狼藉、已溃散的不再溃散、
          有人群在场（未溃散；这一招吓跑的也算目睹）的消息每刻两处而已散的人群不算、radius 随烈度、正文截到 40 字）；spread（每刻 speed 处、radius 截止、传满即停、按 id 次序）；
          ecology（只在跨过黎明时结算、露天无主之物按物料日数朽坏而室内或有主或金铁不朽、持有者覆盖正典、已朽的不再朽、
          顺手牵羊只拿遗落或无主的可携无险之物、物主在侧不拿、仁厚者与被制住者不拿、狠辣者先伸手、拿走之后不再拿、同一天先风化后顺手牵羊、哈希确定）；
          WorldClock.advance（余波 → TimePassed → 扩散 → 生态的次序、折叠后的世界、死者无心跳）
[POS]: tests 的世界心跳物理基线：时间、余波、传闻与日常生态都是纯函数——同样的世界与同样的命令，得出逐字相同的事件
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from functools import reduce
from uuid import uuid4

import pytest

from app.application.world_clock import WorldClock
from app.domain.aggregates import Player, PlayerState, evolve
from app.domain.ambient import (
    TOKEN_CHARS,
    Activity,
    ActivityKind,
    EnvironmentalTrace,
    FactToken,
    activity_id,
    token_id,
    trace_id,
)
from app.domain.combat import CombatOutcome
from app.domain.commands import SPAWN_TICK, TICKS_PER_DAY, Command
from app.domain.events import (
    ActivityStarted,
    Conversed,
    DomainEvent,
    EventEnvelope,
    FactTokenSpawned,
    HealthChanged,
    ItemDecayed,
    ItemPilfered,
    ItemTransferred,
    Maneuvered,
    Moved,
    Parleyed,
    PlayerDied,
    PlayerSpawned,
    RelationChanged,
    RumorSpread,
    SkillExecuted,
    SkillPracticed,
    TimePassed,
    TraceLeft,
)
from app.domain.heartbeat import (
    BLOOD,
    LITTER,
    PILFER_ODDS,
    ROUT_TICKS,
    SCUFFLE,
    Atlas,
    Mark,
    aftermath,
    deed,
    ecology,
    intensity,
    spread,
)
from app.domain.intent import ActionType, Aim, Approach, PlayerIntent
from app.domain.lore import SwarmNode
from app.domain.models import Attitude, Character, Item, Location, Material, WorldBlueprint
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.snapshot import LocalSnapshot, LocationView
from app.infrastructure.persistence.memory_graph import InMemoryWorldGraph
from tests.world import WORLD

PID = "ply:beat"
Out = CombatOutcome
HILL, CITY, CAVE, WUXI = "loc:无量山", "loc:大理城", "loc:无量玉洞", "loc:无锡城"
GONG, ZUO, XIN, CROC = "chr:龚光杰", "chr:左子穆", "chr:辛双清", "chr:南海鳄神"


# ============================================================
#  夹具：WORLD 的小变体、快照、玩家状态、气运
# ============================================================
def _bp(**update: object) -> WorldBlueprint:
    """在 WORLD 上改几样东西，经蓝图闸门重新校验（model_copy 不校验）。"""
    return WorldBlueprint.model_validate({**WORLD.model_dump(), **update})


def crowd(name: str = "看热闹的山民", threshold: int = 3, at: str = HILL) -> SwarmNode:
    return SwarmNode(id=f"swm:{name}", name=name, location_id=at, size=30, panic_threshold=threshold, routine="围观比剑",
                     sources=("chunk:1",))


ROAD = Location(id="loc:山道", name="山道", region="大理", description="山脚下的土路", exits={"上山": HILL})  # 露天、无人常驻
LITTERED = (  # 露天与室内、无主与遗落、经不起与经得起风雨的几样东西
    Item(id="itm:书信", name="书信", kind="书信", location_id="loc:山道"),  # 纸帛：三日
    Item(id="itm:干粮", name="干粮", kind="食物", location_id="loc:山道"),  # 饮食：两日
    Item(id="itm:铁剑", name="铁剑", kind="兵器", location_id="loc:山道"),  # 金铁：不朽
    Item(id="itm:家书", name="家书", kind="书信", owner_id="chr:段誉", location_id="loc:山道"),  # 有主：遗落而不朽
    Item(id="itm:账簿", name="账簿", kind="文书", location_id=CAVE),  # 室内：不朽
)
ROADSIDE = _bp(locations=(*WORLD.locations, ROAD), items=(*WORLD.items, *LITTERED))


async def look(bp: WorldBlueprint, at: str, *events: DomainEvent, pid: str = PID) -> tuple[PlayerState, LocalSnapshot]:
    """在 bp 上投胎于 at、重放 events：聚合根的状态与内存图谱的快照（与回合编排同一口径）。"""
    history = [PlayerSpawned(player_id=pid, name="阿星", location_id=at), *events]
    envelopes = [
        EventEnvelope(stream_id=pid, version=i, event_id=uuid4(), recorded_at=datetime.now(UTC), event=e)
        for i, e in enumerate(history, start=1)
    ]
    graph = InMemoryWorldGraph()
    await graph.seed(bp)
    await graph.project(pid, envelopes)
    return Player.from_history(pid, envelopes).state, await graph.local_snapshot(pid)


def born(at: str = HILL, pid: str = PID, **update: object) -> PlayerState:
    state = Player.replay([PlayerSpawned(player_id=pid, name="阿星", location_id=at)])
    assert state is not None
    return replace(state, **update)  # type: ignore[arg-type]


def dawn(day: int) -> int:
    return day * TICKS_PER_DAY


def lucky(pid: str, day: int, item: str) -> bool:
    """顺手牵羊的机会：sha256(玩家|日|物) 首字节落在 PILFER_ODDS 之内——与实现各算各的，比对的是契约。"""
    return hashlib.sha256(f"{pid}|{day}|{item}".encode()).digest()[0] < PILFER_ODDS


def player_where(fate: dict[int, bool], item: str) -> str:
    """找一位玩家：他的世界里 item 在这几个黎明是否碰上顺手牵羊，恰如 fate 所写。"""
    return next(p for p in (f"ply:t{i}" for i in range(4000))
                if all(lucky(p, day, item) is hit for day, hit in fate.items()))


def strike(target: str, outcome: CombatOutcome) -> SkillExecuted:
    return SkillExecuted(skill_id=None, target_id=target, outcome=outcome)


def mark(at: str, m: Mark, tick: int) -> EnvironmentalTrace:
    return EnvironmentalTrace(id=trace_id(at, m.description, tick), location_id=at, description=m.description,
                              born_tick=tick, decay_ticks=m.decay_ticks)


def news(text: str, subjects: tuple[str, ...], tick: int, *, speed: int = 1, radius: int, at: str = HILL) -> FactToken:
    return FactToken(id=token_id(text, at, tick), text=text, subject_ids=subjects, origin_id=at, born_tick=tick,
                     speed=speed, radius=radius, reached=(at,))


# ============================================================
#  Atlas —— 静态地理
# ============================================================
def test_the_atlas_reads_roads_both_ways_shelter_canon_items_and_residents() -> None:
    late = Character(id="chr:木婉清", true_name="木婉清", location_id=HILL, arrives_with="portent:黑玫瑰")
    mare = Item(id="itm:黑玫瑰", name="黑玫瑰", kind="坐骑", location_id=HILL, arrives_with="portent:黑玫瑰")
    atlas = Atlas.of(_bp(locations=(*WORLD.locations, ROAD), characters=(*WORLD.characters, late),
                         items=(*WORLD.items, mare)))
    assert atlas.neighbors[HILL] == ("loc:大理城", "loc:山道", "loc:无量玉洞")  # 山道只写了上山一头，下山照样走得通
    assert atlas.neighbors["loc:山道"] == (HILL,) and atlas.neighbors[WUXI] == (CITY,)
    assert all(tuple(sorted(v)) == v for v in atlas.neighbors.values())
    assert atlas.sheltered == frozenset({CAVE})  # 洞有顶；城、山、道都露天
    assert [i.id for i in atlas.items] == sorted(i.id for i in WORLD.items)  # 后来才出现的黑玫瑰不在 T=0 的世上
    assert [c.id for c in atlas.residents[HILL]] == sorted([ZUO, GONG, XIN, CROC])  # 木婉清后来才到
    assert [c.id for c in atlas.residents[WUXI]] == ["chr:乔峰"]  # 汪剑通已故
    assert CAVE not in atlas.residents and "loc:山道" not in atlas.residents


def test_the_ring_is_breadth_first_by_hops_then_id() -> None:
    atlas = Atlas.of(WORLD)
    assert atlas.ring(CAVE, 0) == (CAVE,)
    assert atlas.ring(CAVE, 1) == (CAVE, HILL)
    assert atlas.ring(CAVE, 2) == (CAVE, HILL, CITY)
    assert atlas.ring(CAVE, 6) == (CAVE, HILL, CITY, WUXI)  # 天下就这么大：传满即止
    assert atlas.ring(HILL, 1) == (HILL, CITY, CAVE) and CITY < CAVE  # 同一跳按 id
    assert atlas.ring(HILL, 2) == (HILL, CITY, CAVE, WUXI)
    assert atlas.ring("loc:少林寺", 3) == ("loc:少林寺",)  # 不通路的地方只有它自己


# ============================================================
#  烈度
# ============================================================
@pytest.mark.parametrize(
    ("events", "level"),
    [
        ([], 0),
        ([strike(GONG, Out.SUCCESS)], 6),
        ([strike(GONG, Out.STALEMATE)], 4),
        ([strike(GONG, Out.MINOR_WOUND)], 5),
        ([strike(GONG, Out.SEVERE_WOUND)], 7),
        ([strike(CROC, Out.DEATH)], 9),
        ([PlayerDied(cause="冒犯", killer_id=CROC)], 9),
        ([HealthChanged(delta=-30, cause="龚光杰的旧恨满了", source_id=GONG)], 5),  # 有人出手伤你（敌意坍缩的那一击）
        ([HealthChanged(delta=-15, cause="朱蛤蜇手", source_id="itm:朱蛤")], 0),  # 险物伤手：没有人出手
        ([HealthChanged(delta=-30, cause="山洪将至满了")], 0),
        ([HealthChanged(delta=25, cause="调息疗伤", source="rest")], 0),
        ([Maneuvered(item_id="itm:无量剑", target_id=ZUO, approach=Approach.STEALTH, outcome=CovertOutcome.EXPOSED)], 3),
        ([Maneuvered(item_id="itm:无量剑", target_id=ZUO, approach=Approach.STEALTH, outcome=CovertOutcome.CAUGHT)], 3),
        ([Maneuvered(item_id="itm:无量剑", target_id=ZUO, approach=Approach.STEALTH, outcome=CovertOutcome.CLEAN)], 0),
        ([Maneuvered(item_id="itm:无量剑", target_id=ZUO, approach=Approach.GUILE, outcome=CovertOutcome.FOILED)], 0),
        ([Parleyed(npc_id=ZUO, aim=Aim.DEFUSE, approach=Approach.FORCE, outcome=SocialOutcome.FALLOUT)], 2),
        ([Parleyed(npc_id=ZUO, aim=Aim.BEFRIEND, approach=Approach.WORDS, outcome=SocialOutcome.GRANTED)], 0),
        ([Conversed(npc_id=ZUO), SkillPracticed(skill_id="art:无量剑法", proficiency_gained=3)], 0),
        ([Parleyed(npc_id=ZUO, aim=Aim.DEFUSE, approach=Approach.FORCE, outcome=SocialOutcome.FALLOUT),
          strike(ZUO, Out.STALEMATE), HealthChanged(delta=-10, cause="与左子穆交手", source_id=ZUO)], 5),  # 取最烈的一件
    ],
)
def test_intensity_is_the_fiercest_of_the_batch(events: list[DomainEvent], level: int) -> None:
    assert intensity(events) == level


# ============================================================
#  公开之事 → 消息正文与主体
# ============================================================
@pytest.mark.parametrize(
    ("outcome", "text"),
    [
        (Out.SUCCESS, "阿星出手制住了龚光杰"),
        (Out.STALEMATE, "阿星与龚光杰动手，不分胜负"),
        (Out.MINOR_WOUND, "阿星与龚光杰动手，挂了彩"),
        (Out.SEVERE_WOUND, "阿星被龚光杰打成重伤"),
        (Out.DEATH, "阿星死在龚光杰手下"),
    ],
)
async def test_a_fight_is_news(outcome: CombatOutcome, text: str) -> None:
    _, before = await look(WORLD, HILL)
    assert deed([strike(GONG, outcome), HealthChanged(delta=-10, cause="c", source_id=GONG)], before) == (text, (PID, GONG))


async def test_the_five_kinds_of_news() -> None:
    _, hill = await look(WORLD, HILL)
    seized = [strike(ZUO, Out.SUCCESS), ItemTransferred(item_id="itm:无量剑", from_holder=ZUO, to_holder=PID)]
    assert deed(seized, hill) == ("阿星出手制住左子穆，夺走了无量剑", (PID, ZUO, "itm:无量剑"))

    def sneak(outcome: CovertOutcome) -> list[DomainEvent]:
        got = outcome in (CovertOutcome.CLEAN, CovertOutcome.EXPOSED)
        return [Maneuvered(item_id="itm:无量剑", target_id=ZUO, approach=Approach.STEALTH, outcome=outcome),
                *([ItemTransferred(item_id="itm:无量剑", from_holder=ZUO, to_holder=PID)] if got else [])]

    assert deed(sneak(CovertOutcome.EXPOSED), hill) == ("阿星暗取左子穆的无量剑，被当场识破", (PID, ZUO, "itm:无量剑"))
    assert deed(sneak(CovertOutcome.CAUGHT), hill) == ("阿星想暗取左子穆的无量剑，失了手", (PID, ZUO, "itm:无量剑"))
    fallout = Parleyed(npc_id=ZUO, aim=Aim.ASK, approach=Approach.FORCE, outcome=SocialOutcome.FALLOUT)
    assert deed([fallout, RelationChanged(character_id=ZUO, attitude=Attitude.HOSTILE, cause="翻脸")], hill) == (
        "阿星与左子穆言语不合，当场翻脸", (PID, ZUO))
    _, city = await look(WORLD, HILL, ItemTransferred(item_id="itm:玉佩", from_holder=HILL, to_holder=PID),
                         Moved(from_location_id=HILL, to_location_id=CITY, exit_label="南下"))
    returned = [ItemTransferred(item_id="itm:玉佩", from_holder=PID, to_holder="chr:段正淳"),
                RelationChanged(character_id="chr:段正淳", attitude=Attitude.TRUSTED, cause="物归原主", basis="物归原主")]
    assert deed(returned, city) == ("阿星把玉佩还给了段正淳", (PID, "chr:段正淳", "itm:玉佩"))
    assert deed([ItemTransferred(item_id="itm:玉佩", from_holder=PID, to_holder="chr:段誉")], city) is None  # 赠给非物主

    # 没人看见或不值一提的事不成消息：暗中得手无人察觉、未遂、闲谈、修习、交涉如愿、地上拾物
    assert deed(sneak(CovertOutcome.CLEAN), hill) is None
    assert deed(sneak(CovertOutcome.FOILED), hill) is None
    quiet: list[list[DomainEvent]] = [
        [], [Conversed(npc_id=ZUO)], [SkillPracticed(skill_id="art:无量剑法", proficiency_gained=3, source_id=XIN)],
        [Parleyed(npc_id=ZUO, aim=Aim.BEFRIEND, approach=Approach.WORDS, outcome=SocialOutcome.GRANTED)],
        [ItemTransferred(item_id="itm:玉佩", from_holder=HILL, to_holder=PID)],
    ]
    assert [deed(events, hill) for events in quiet] == [None] * len(quiet)
    assert deed([fallout, *seized], hill) == ("阿星与左子穆言语不合，当场翻脸", (PID, ZUO))  # 按事件次序取第一件


# ============================================================
#  余波 —— 这一招在此地留下了什么
# ============================================================
@pytest.mark.parametrize(
    ("outcome", "scar", "radius"),
    [
        (Out.SUCCESS, SCUFFLE, 3),
        (Out.STALEMATE, SCUFFLE, 3),
        (Out.MINOR_WOUND, BLOOD, 3),
        (Out.SEVERE_WOUND, BLOOD, 4),
        (Out.DEATH, BLOOD, 4),
    ],
)
async def test_a_fight_leaves_a_trace_a_one_tick_activity_and_news(outcome: CombatOutcome, scar: Mark, radius: int) -> None:
    _, before = await look(WORLD, HILL)
    tick = SPAWN_TICK + 3
    events = [strike(GONG, outcome)]
    trace = mark(HILL, scar, tick)
    fight = Activity(id=activity_id(ActivityKind.FIGHT, HILL, (PID, GONG), tick), kind=ActivityKind.FIGHT,
                     participants=(PID, GONG), location_id=HILL, started_tick=tick, ends_tick=tick + 1, trace_id=trace.id)
    text, subjects = deed(events, before) or ("", ())
    assert aftermath(events, before, tick) == [
        TraceLeft(trace=trace), ActivityStarted(activity=fight),
        FactTokenSpawned(token=news(text, subjects, tick, speed=1, radius=radius)),  # 无人群在场：每刻一处
    ]
    assert (BLOOD.decay_ticks, SCUFFLE.decay_ticks) == (TICKS_PER_DAY, 32)  # 血迹一日方散，脚印一个上午


@pytest.mark.parametrize(
    ("events", "radius"),
    [
        ([ItemTransferred(item_id="itm:玉佩", from_holder=PID, to_holder="chr:段正淳")], 1),  # 物归原主：烈度 0
        ([Parleyed(npc_id="chr:段正淳", aim=Aim.ASK, approach=Approach.FORCE, outcome=SocialOutcome.FALLOUT)], 2),
        ([Maneuvered(item_id="itm:玉佩", target_id="chr:段正淳", approach=Approach.STEALTH, outcome=CovertOutcome.CAUGHT)], 2),
        ([strike("chr:段正淳", Out.STALEMATE)], 3),
        ([strike("chr:段正淳", Out.SEVERE_WOUND)], 4),
    ],
)
async def test_news_travels_further_the_fiercer_the_deed(events: list[DomainEvent], radius: int) -> None:
    _, city = await look(WORLD, HILL, ItemTransferred(item_id="itm:玉佩", from_holder=HILL, to_holder=PID),
                         Moved(from_location_id=HILL, to_location_id=CITY, exit_label="南下"))
    spawned = [e.token for e in aftermath(events, city, 40) if isinstance(e, FactTokenSpawned)]
    assert len(spawned) == 1 and spawned[0].radius == radius and spawned[0].reached == (CITY,)


async def test_quiet_deeds_leave_nothing_behind() -> None:
    _, before = await look(_bp(swarms=(crowd(threshold=1),)), HILL)
    clean = [Maneuvered(item_id="itm:无量剑", target_id=ZUO, approach=Approach.STEALTH, outcome=CovertOutcome.CLEAN),
             ItemTransferred(item_id="itm:无量剑", from_holder=ZUO, to_holder=PID)]
    assert aftermath(clean, before, 40) == []  # 暗中得手无人察觉：没人知道的事，谁也不该知道
    assert aftermath([Conversed(npc_id=ZUO)], before, 40) == []
    assert aftermath([], before, 40) == []


async def test_a_crowd_routs_only_when_the_deed_outdoes_its_nerve() -> None:
    """烈度高过（不是等于）惊惧阈值才溃散：一道狼藉，每群一个 ROUT_TICKS 刻的溃散活动；有人群在场，消息每刻传两处。"""
    timid, steady, stoic = crowd("看热闹的山民", 3), crowd("东宗弟子", 4), crowd("观礼宾客", 8)
    _, before = await look(_bp(swarms=(timid, steady, stoic)), HILL)
    tick = 50
    out = aftermath([strike(GONG, Out.STALEMATE)], before, tick)  # 烈度 4
    litter = mark(HILL, LITTER, tick)
    routs = [e.activity for e in out if isinstance(e, ActivityStarted) and e.activity.kind is ActivityKind.ROUT]
    assert [type(e).__name__ for e in out] == ["TraceLeft", "ActivityStarted", "TraceLeft", "ActivityStarted", "FactTokenSpawned"]
    assert out[2] == TraceLeft(trace=litter) and (LITTER.decay_ticks, ROUT_TICKS) == (48, 48)
    assert [(a.participants, a.started_tick, a.ends_tick, a.trace_id) for a in routs] == [
        ((timid.id,), tick, tick + ROUT_TICKS, litter.id)]  # 阈值 4 的东宗弟子与 8 的宾客不动
    token = next(e.token for e in out if isinstance(e, FactTokenSpawned))
    assert token.speed == 2 and token.radius == 3
    fierce = aftermath([strike(GONG, Out.SEVERE_WOUND)], before, tick)  # 烈度 7
    routed = {e.activity.participants for e in fierce if isinstance(e, ActivityStarted) and e.activity.kind is ActivityKind.ROUT}
    assert routed == {(timid.id,), (steady.id,)} and sum(isinstance(e, TraceLeft) for e in fierce) == 2  # 一道血迹 + 一道狼藉
    words = aftermath([Parleyed(npc_id=ZUO, aim=Aim.ASK, approach=Approach.FORCE, outcome=SocialOutcome.FALLOUT)], before, tick)
    assert [type(e).__name__ for e in words] == ["FactTokenSpawned"]  # 烈度 2：谁也没吓跑，消息照样走得快
    assert words[0].token.speed == 2 and words[0].token.radius == 2  # type: ignore[attr-defined]


async def test_a_routed_crowd_does_not_rout_again() -> None:
    timid = crowd(threshold=3)
    earlier = Activity(id=activity_id(ActivityKind.ROUT, HILL, (timid.id,), SPAWN_TICK), kind=ActivityKind.ROUT,
                       participants=(timid.id,), location_id=HILL, started_tick=SPAWN_TICK,
                       ends_tick=SPAWN_TICK + ROUT_TICKS, trace_id=None)
    _, before = await look(_bp(swarms=(timid,)), HILL, ActivityStarted(activity=earlier))
    assert [s.routed for s in before.swarms] == [True]
    out = aftermath([strike(GONG, Out.SEVERE_WOUND)], before, SPAWN_TICK + 1)
    assert not any(isinstance(e, ActivityStarted) and e.activity.kind is ActivityKind.ROUT for e in out)
    assert [e.trace.description for e in out if isinstance(e, TraceLeft)] == [BLOOD.description]  # 没有新的狼藉
    assert next(e.token for e in out if isinstance(e, FactTokenSpawned)).speed == 1  # 人群已散，无人目睹：消息每刻只传一处
    _, later = await look(_bp(swarms=(timid,)), HILL, ActivityStarted(activity=earlier), TimePassed(ticks=ROUT_TICKS))
    assert [s.routed for s in later.swarms] == [False]  # 半日之后人群回来，又会受惊


def test_the_news_text_is_cut_to_a_token() -> None:
    before = LocalSnapshot(player_id=PID, player_name="阿星", alive=True, version=1, location=LocationView(id=HILL, name="无量山"),
                           labels={GONG: "龚" * 60})
    token = next(e.token for e in aftermath([strike(GONG, Out.STALEMATE)], before, 40) if isinstance(e, FactTokenSpawned))
    assert len(token.text) == TOKEN_CHARS and token.id == token_id(token.text, HILL, 40)


# ============================================================
#  扩散 —— 消息一刻一两处地传开
# ============================================================
def test_news_spreads_speed_places_a_tick_up_to_its_radius() -> None:
    atlas = Atlas.of(WORLD)
    slow = news("甲", (PID,), 40, speed=1, radius=2, at=CAVE)
    assert spread([slow], atlas, 1) == [RumorSpread(token_id=slow.id, location_ids=(HILL,))]
    assert spread([slow], atlas, 4) == [RumorSpread(token_id=slow.id, location_ids=(HILL, CITY))]  # 两跳为止
    fast = slow.model_copy(update={"speed": 2})
    assert spread([fast], atlas, 1) == [RumorSpread(token_id=slow.id, location_ids=(HILL, CITY))]
    halfway = slow.model_copy(update={"reached": (CAVE, HILL)})
    assert spread([halfway], atlas, 1) == [RumorSpread(token_id=slow.id, location_ids=(CITY,))]
    full = slow.model_copy(update={"reached": (CAVE, HILL, CITY)})
    assert spread([full], atlas, 9) == []  # 传满即停
    assert spread([slow.model_copy(update={"radius": 0})], atlas, 9) == []
    other = news("乙", (PID,), 41, speed=1, radius=1, at=WUXI)
    both = spread([other, slow], atlas, 1)
    assert [r.token_id for r in both] == sorted([slow.id, other.id])  # 按 id 次序
    assert {r.token_id: r.location_ids for r in both}[other.id] == (CITY,)


# ============================================================
#  生态 —— 每个黎明一次：风化与顺手牵羊
# ============================================================
def _decays(out: list[DomainEvent]) -> list[tuple[str, Material]]:
    return [(e.item_id, e.material) for e in out if isinstance(e, ItemDecayed)]


def test_nothing_happens_until_a_dawn_is_crossed() -> None:
    atlas = Atlas.of(ROADSIDE)
    assert ecology(born(tick=dawn(1) - 1), atlas, SPAWN_TICK) == []
    assert ecology(born(tick=dawn(5) + 40), atlas, dawn(5) + 1) == []  # 同一日之内


def test_open_air_unowned_things_weather_by_material() -> None:
    """露天、无主、经不起风雨的东西，自 T=0 搁满物料的日数即朽：饮食两日、纸帛三日；室内、有主、金铁不朽。"""
    atlas = Atlas.of(ROADSIDE)
    assert _decays(ecology(born(tick=dawn(1)), atlas, dawn(1) - 1)) == []
    assert _decays(ecology(born(tick=dawn(2)), atlas, dawn(2) - 1)) == [("itm:干粮", Material.FOOD)]
    rotten = frozenset({"itm:干粮"})  # 第二个黎明朽掉的干粮已折进 consumed
    assert _decays(ecology(born(tick=dawn(3), consumed=rotten), atlas, dawn(3) - 1)) == [("itm:书信", Material.PAPER)]
    assert _decays(ecology(born(tick=dawn(3)), atlas, dawn(3) - 1)) == [  # 没折进去的照样朽：同一个黎明按 id 依次过
        ("itm:书信", Material.PAPER), ("itm:干粮", Material.FOOD)]
    rotted = _decays(ecology(born(tick=dawn(100)), atlas, SPAWN_TICK))  # 一口气跨过一百个黎明：各朽一次，按日先后
    assert rotted == [("itm:干粮", Material.FOOD), ("itm:书信", Material.PAPER)]
    gone = born(tick=dawn(3), consumed=frozenset({"itm:书信", "itm:干粮"}))
    assert _decays(ecology(gone, atlas, dawn(3) - 1)) == []  # 已经朽了（或用掉了）的不再朽


def test_the_parallel_world_decides_where_a_thing_lies() -> None:
    """持有者看此世的覆盖层：拿在手里的不朽，搬进洞里的不朽，丢在露天的照样按 T=0 起算的日数朽坏。"""
    atlas = Atlas.of(ROADSIDE)
    rotten = frozenset({"itm:干粮"})
    carried = born(tick=dawn(3), consumed=rotten, item_holders={"itm:书信": PID})
    sheltered = born(tick=dawn(3), consumed=rotten, item_holders={"itm:书信": CAVE})
    assert _decays(ecology(carried, atlas, dawn(3) - 1)) == [] == _decays(ecology(sheltered, atlas, dawn(3) - 1))
    tossed = born(tick=dawn(3), consumed=rotten, item_holders={"itm:北冥神功卷轴": "loc:山道"})
    assert _decays(ecology(tossed, atlas, dawn(3) - 1)) == [("itm:书信", Material.PAPER), ("itm:北冥神功卷轴", Material.PAPER)]


def test_a_stray_thing_is_pilfered_by_a_ruthless_local_on_a_lucky_dawn() -> None:
    """玉佩遗落在无量山（物主段正淳不在那里）：机会落进哈希的那个黎明，狠辣者先伸手，同类按 id（南海鳄神先于龚光杰）。"""
    atlas = Atlas.of(WORLD)
    pid = player_where({1: True}, "itm:玉佩")
    out = ecology(born(pid=pid, tick=dawn(1)), atlas, dawn(1) - 1)
    assert out == [ItemPilfered(item_id="itm:玉佩", from_holder=HILL, to_holder=CROC)]
    assert ecology(born(pid=pid, tick=dawn(1)), atlas, dawn(1) - 1) == out  # 确定：同样的世界，同样的结局
    croc_down = born(pid=pid, tick=dawn(1), subdued=frozenset({CROC}))
    assert ecology(croc_down, atlas, dawn(1) - 1)[0].to_holder == GONG  # type: ignore[attr-defined]
    assert ZUO < GONG  # 按 id 左子穆在前：狠辣者先于中庸者伸手
    ruthless_down = born(pid=pid, tick=dawn(1), subdued=frozenset({CROC, GONG}))
    assert ecology(ruthless_down, atlas, dawn(1) - 1)[0].to_holder == ZUO  # type: ignore[attr-defined]
    all_down = born(pid=pid, tick=dawn(1), subdued=frozenset({CROC, GONG, ZUO, XIN}))
    assert ecology(all_down, atlas, dawn(1) - 1) == []  # 被制住的人伸不了手
    unlucky = player_where({1: False}, "itm:玉佩")
    assert ecology(born(pid=unlucky, tick=dawn(1)), atlas, dawn(1) - 1) == []


def test_pilfering_follows_the_hash_dawn_by_dawn_and_happens_once() -> None:
    atlas = Atlas.of(WORLD)
    for pid in (f"ply:h{i}" for i in range(8)):
        for day in range(1, 9):
            hit = ecology(born(pid=pid, tick=dawn(day)), atlas, dawn(day) - 1)
            assert bool(hit) is lucky(pid, day, "itm:玉佩"), (pid, day)
        days = [d for d in range(1, 9) if lucky(pid, d, "itm:玉佩")]
        whole = ecology(born(pid=pid, tick=dawn(8)), atlas, SPAWN_TICK)
        assert len(whole) == (1 if days else 0)  # 拿走之后它在某人身上，不再遗落在地


def test_only_stray_or_unowned_portable_harmless_things_are_taken() -> None:
    atlas = Atlas.of(WORLD)
    pid = player_where({1: True}, "itm:玉佩")
    state = born(pid=pid, tick=dawn(1))
    jade = next(i for i in WORLD.items if i.id == "itm:玉佩")

    def with_jade(**update: object) -> Atlas:
        return Atlas.of(_bp(items=tuple(jade.model_copy(update=update) if i.id == jade.id else i for i in WORLD.items)))

    assert ecology(state, with_jade(portable=False), dawn(1) - 1) == []
    assert ecology(state, with_jade(hazard="淬了毒"), dawn(1) - 1) == []
    assert ecology(state, with_jade(owner_id=ZUO), dawn(1) - 1) == []  # 物主在侧，无人敢拿
    assert ecology(state, with_jade(owner_id=None), dawn(1) - 1) == [  # 无主之物照拿
        ItemPilfered(item_id="itm:玉佩", from_holder=HILL, to_holder=CROC)]
    assert ecology(born(pid=pid, tick=dawn(1), item_holders={"itm:玉佩": PID}), atlas, dawn(1) - 1) == []  # 在你身上
    sword = next(p for p in (f"ply:t{i}" for i in range(4000)) if lucky(p, 1, "itm:无量剑") and not lucky(p, 1, "itm:玉佩"))
    assert ecology(born(pid=sword, tick=dawn(1)), atlas, dawn(1) - 1) == []  # 无量剑在左子穆手里：随身之物从不被顺走


def test_the_merciful_do_not_pilfer() -> None:
    """大理城里只剩仁厚之人（段延庆被制住）：遗落在那里的东西没人伸手。"""
    jade = next(i for i in WORLD.items if i.id == "itm:玉佩")
    atlas = Atlas.of(_bp(items=tuple(jade.model_copy(update={"owner_id": None, "location_id": CITY}) if i.id == jade.id
                                     else i for i in WORLD.items)))
    pid = player_where({1: True}, "itm:玉佩")
    assert ecology(born(pid=pid, tick=dawn(1)), atlas, dawn(1) - 1) == [
        ItemPilfered(item_id="itm:玉佩", from_holder=CITY, to_holder="chr:段延庆")]
    assert ecology(born(pid=pid, tick=dawn(1), subdued=frozenset({"chr:段延庆"})), atlas, dawn(1) - 1) == []


def test_weathering_comes_before_pilfering_on_the_same_dawn() -> None:
    """露天无主的纸帛在无量山：前两个黎明没碰上顺手牵羊，第三个黎明即使碰上也先朽了。"""
    letter = Item(id="itm:书信", name="书信", kind="书信", location_id=HILL)
    atlas = Atlas.of(_bp(items=(*WORLD.items, letter)))
    pid = player_where({1: False, 2: False, 3: True}, "itm:书信")
    out = [e for e in ecology(born(pid=pid, tick=dawn(3)), atlas, SPAWN_TICK) if getattr(e, "item_id", "") == "itm:书信"]
    assert out == [ItemDecayed(item_id="itm:书信", material=Material.PAPER)]


# ============================================================
#  世界时钟 —— 余波 → 时间 → 扩散 → 生态
# ============================================================
async def test_the_world_clock_beats_in_causal_order() -> None:
    pid = player_where({1: True}, "itm:玉佩")
    state, before = await look(WORLD, HILL, TimePassed(ticks=dawn(1) - 1 - SPAWN_TICK), pid=pid)
    assert state.tick == dawn(1) - 1
    decided: list[DomainEvent] = [strike(GONG, Out.MINOR_WOUND), HealthChanged(delta=-20, cause="与龚光杰交手", source_id=GONG)]
    command = Command(intent=PlayerIntent(action_type=ActionType.ATTACK, target_entity="龚光杰"), time_cost=1)
    beats = WorldClock(Atlas.of(WORLD)).advance(command, before, state, decided)
    deeds = aftermath(decided, before, state.tick)
    token = next(e.token for e in deeds if isinstance(e, FactTokenSpawned))
    assert beats == [
        *deeds,  # 余波：出招那一刻
        TimePassed(ticks=1),
        RumorSpread(token_id=token.id, location_ids=(CITY,)),  # 这一刻里消息传出一处
        ItemPilfered(item_id="itm:玉佩", from_holder=HILL, to_holder=CROC),  # 跨过了第二日的黎明
    ]
    after = reduce(evolve, [*decided, *beats], state)
    assert after.tick == dawn(1) and after.tokens[0].reached == (HILL, CITY)
    assert after.item_holders == {"itm:玉佩": CROC} and len(after.activities) == 1 and len(after.traces) == 1


async def test_a_quiet_command_only_passes_time_and_spreads_old_news() -> None:
    old = news("阿星与龚光杰动手，不分胜负", (PID, GONG), SPAWN_TICK, radius=2)
    state, before = await look(WORLD, HILL, FactTokenSpawned(token=old))
    command = Command(intent=PlayerIntent(action_type=ActionType.THINK), time_cost=1)
    assert WorldClock(Atlas.of(WORLD)).advance(command, before, state, []) == [
        TimePassed(ticks=1), RumorSpread(token_id=old.id, location_ids=(CITY,))]
    rest = Command(intent=PlayerIntent(action_type=ActionType.REST), time_cost=8)
    assert WorldClock(Atlas.of(WORLD)).advance(rest, before, state, []) == [
        TimePassed(ticks=8), RumorSpread(token_id=old.id, location_ids=(CITY, CAVE, WUXI))]  # 两跳之内，传满即停


async def test_the_dead_have_no_heartbeat() -> None:
    state, before = await look(WORLD, HILL)
    decided: list[DomainEvent] = [strike(CROC, Out.DEATH), HealthChanged(delta=-100, cause="c", source_id=CROC),
                                  PlayerDied(cause="冒犯南海鳄神", killer_id=CROC)]
    command = Command(intent=PlayerIntent(action_type=ActionType.ATTACK, target_entity="南海鳄神"), time_cost=1)
    assert WorldClock(Atlas.of(WORLD)).advance(command, before, state, decided) == []
