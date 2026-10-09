"""
[INPUT]: 依赖 app.domain 的 events / aggregates / clocks / ambient / commands / intent / lore（SwarmNode）/ models / snapshot / resolution，依赖 tests/world 的 WORLD，
         依赖 tests/test_domain 的 PID / ALL_EVENTS / HEARTBEAT 与时钟、痕迹、活动、消息样例（DOUBT / FLOOD / STAIN / BOUT / NEWS）、_lore
[OUTPUT]: 领域地基的后半：语义物理引擎的折叠（六种时钟 / 事实 / 名望事件的边界与旧账照读、NarrativeClock 的闸门、时钟按 id 折叠且折叠从不替它坍缩、
          微观事实新者在前至多 EMERGED_MAX 条且重提即提到最前、名望钳在 ±100 并折算为六档说法、快照的时钟与事实按 id 排序且挂处与主体进名称表）；
          世界心跳：七种新事件有界与不可变、旧账 Moved 缺 motivation 照读、Moved 记下最近一次的此行所为；活动 / 痕迹 / 消息自守不变量；
          折叠——tick 累加、痕迹到期消散且已结束的活动随之消失（没留痕迹的一结束就抹去）而进行中的不消失、超上限请走最旧的且同 id 覆盖、
          消息传到之处只增不减且未知消息无事、朽坏进 consumed 且不在行囊、顺手拿走只改持有者；
          Command 与 TimePassed 的刻数 ∈ [1, 672]（远行至多七日）、time_cost 封闭表（驳回 1、移动以那条出路的 way_cost 为准且钳位、没给时同一处所内走动 1 / 换处所 4、修习调息 8）、
          same_place、time_label（第一日·辰正、子初属前一日之夜、跨日、十日以上的数字）、daylight 卯至酉、day_of；
          派生的 Material（weathers_in）/ Ownership / Location.sheltered / Item.material / Item.ownership 不另存；SwarmNode 字段有界与人群闸门（重复、id、地点、门派）；
          快照新视图（活动 / 痕迹 / 人群 / 消息）的缺省值、按 id 排序、current_state、time_label / daylight 与 referenced_ids 增补；ItemView.material / ownership 现算
[POS]: tests 的领域地基（续 tests/test_domain）：时间、过去式与叙事时钟都是事件流折叠出来的，没有状态表
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""


import pytest
from pydantic import ValidationError

from app.domain.aggregates import EMERGED_MAX, Player, PlayerState
from app.domain.ambient import (
    ACTIVITIES_MAX,
    TOKENS_MAX,
    TRACES_MAX,
    Activity,
    ActivityKind,
    ActivityState,
    EnvironmentalTrace,
    FactToken,
    activity_id,
    token_id,
    trace_id,
)
from app.domain.clocks import ClockKind, NarrativeClock, clock_id
from app.domain.commands import (
    MAX_TIME_COST,
    NEARBY_MOVE,
    REFUSED_COST,
    SPAWN_TICK,
    TICKS_PER_DAY,
    TIME_COSTS,
    Command,
    day_of,
    daylight,
    same_place,
    time_cost,
    time_label,
)
from app.domain.events import (
    ActivityStarted,
    ClockAdvanced,
    ClockCleared,
    ClockCollapsed,
    ClockStarted,
    Conversed,
    DomainEvent,
    FactEmerged,
    FactTokenSpawned,
    ItemDecayed,
    ItemPilfered,
    ItemTransferred,
    Maneuvered,
    Moved,
    PlayerSpawned,
    RenownChanged,
    RumorSpread,
    TimePassed,
    TraceLeft,
    decode_event,
)
from app.domain.intent import ActionType, Approach, PlayerIntent
from app.domain.lore import SwarmNode
from app.domain.models import MATERIAL_OF_KIND, EntityKind, Item, Location, Material, Ownership, ownership
from app.domain.outcomes import CovertOutcome
from app.domain.progression import RENOWN_MAX, Renown, renown
from app.domain.snapshot import (
    ActivityView,
    EmergedView,
    ItemView,
    LocalSnapshot,
    LocationView,
    RumorView,
    SwarmView,
    TraceView,
)
from tests.test_domain import ALL_EVENTS, BOUT, DOUBT, FLOOD, HEARTBEAT, NEWS, PID, STAIN, _lore
from tests.world import WORLD


# ============================================================
#  语义物理引擎的折叠：时钟、微观事实、名望
# ============================================================
def test_clock_fact_and_renown_events_are_bounded_and_read_old_ledgers() -> None:
    with pytest.raises(ValidationError, match="已满"):
        NarrativeClock.model_validate(DOUBT.model_dump() | {"progress": 4})  # 满格即坍缩退场，不能悬着
    for bad in ({"maximum": 5}, {"id": "clk:xyz"}, {"anchor_id": "左子穆"}, {"name": ""}, {"name": "一" * 13}, {"progress": -1}):
        with pytest.raises(ValidationError):
            NarrativeClock.model_validate(DOUBT.model_dump() | bad)
    assert DOUBT.remaining == 3 and clock_id("chr:左子穆", "左子穆的疑心") == DOUBT.id != clock_id("chr:龚光杰", "左子穆的疑心")
    assert [k.threat for k in ClockKind] == [True, True, True, False]
    with pytest.raises(ValidationError):
        FactEmerged(fact_id="emg:x", text="一" * 41)
    with pytest.raises(ValidationError):
        RenownChanged(delta=21, cause="c")
    with pytest.raises(ValidationError):
        ClockAdvanced(clock_id=DOUBT.id, steps=9)
    bare = decode_event({"type": "ClockAdvanced", "clock_id": DOUBT.id, "steps": 1})
    assert isinstance(bare, ClockAdvanced) and (bare.name, bare.progress, bare.cause) == ("", 0, "")
    fact = decode_event({"type": "FactEmerged", "fact_id": "emg:1", "text": "风声紧了"})
    assert isinstance(fact, FactEmerged) and fact.subject_ids == ()


def test_clocks_fold_by_id_and_folding_never_collapses_them() -> None:
    base = [PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量山")]
    state = Player.replay([*base, ClockStarted(clock=DOUBT), ClockStarted(clock=FLOOD)])
    assert state is not None and [c.id for c in state.clocks] == sorted([DOUBT.id, FLOOD.id])  # 按 id 排序，与快照同口径
    state = Player.replay([*base, ClockStarted(clock=DOUBT), ClockStarted(clock=FLOOD),
                           ClockAdvanced(clock_id=DOUBT.id, steps=8), ClockAdvanced(clock_id=FLOOD.id, steps=-8),
                           ClockAdvanced(clock_id="clk:0000000000", steps=1)])
    assert state is not None and len(state.clocks) == 2
    assert state.clock(DOUBT.id).progress == 3  # type: ignore[union-attr]  # 钳在阈值减一：满格只由 ClockCollapsed 明写
    assert state.clock(FLOOD.id).progress == 0  # type: ignore[union-attr]  # 回退至多退到零；没挂的时钟推进不了
    restarted = Player.replay([*base, ClockStarted(clock=DOUBT), ClockStarted(clock=DOUBT.model_copy(update={"progress": 2}))])
    assert restarted is not None and [c.progress for c in restarted.clocks] == [2]  # 同 id 再挂即覆盖，不会两只
    gone = Player.replay([*base, ClockStarted(clock=DOUBT), ClockStarted(clock=FLOOD),
                          ClockCollapsed(clock_id=DOUBT.id, name=DOUBT.name), ClockCleared(clock_id=FLOOD.id)])
    assert gone is not None and gone.clocks == () and gone.clock(DOUBT.id) is None


def test_emerged_facts_keep_the_newest_and_renown_is_clamped_and_semantic() -> None:
    base = [PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量山")]
    born = Player.replay(base)
    assert born is not None and (born.clocks, born.emerged, born.renown_points, born.renown) == ((), (), 0, Renown.UNKNOWN)
    facts = [FactEmerged(fact_id=f"emg:{i:010d}", text=f"细节{i}", subject_ids=("chr:左子穆",)) for i in range(EMERGED_MAX + 1)]
    state = Player.replay([*base, *facts])
    assert state is not None and len(state.emerged) == EMERGED_MAX
    assert state.emerged[0].id == facts[-1].fact_id and facts[0].fact_id not in {f.id for f in state.emerged}  # 新者在前，最旧的退场
    again = Player.replay([*base, *facts, facts[5]])
    assert again is not None and again.emerged[0].id == facts[5].fact_id and len({f.id for f in again.emerged}) == EMERGED_MAX
    assert again.emerged[0].subject_ids == ("chr:左子穆",) and again.emerged[0].text == "细节5"
    famed = Player.replay([*base, *(RenownChanged(delta=20, cause="c") for _ in range(6))])
    assert famed is not None and (famed.renown_points, famed.renown) == (RENOWN_MAX, Renown.LEGEND)  # 钳在 +100
    fallen = Player.replay([*base, *(RenownChanged(delta=-20, cause="c") for _ in range(6)), RenownChanged(delta=5, cause="c")])
    assert fallen is not None and (fallen.renown_points, fallen.renown) == (-RENOWN_MAX + 5, Renown.INFAMOUS)
    assert [renown(p) for p in (-30, -29, -10, -9, 9, 10, 29, 30, 59, 60)] == [
        Renown.INFAMOUS, Renown.NOTORIOUS, Renown.NOTORIOUS, Renown.UNKNOWN, Renown.UNKNOWN,
        Renown.NOTED, Renown.NOTED, Renown.FAMED, Renown.FAMED, Renown.LEGEND]


def test_the_snapshot_orders_clocks_and_names_what_they_hang_on() -> None:
    snap = LocalSnapshot(
        player_id=PID, player_name="阿星", alive=True, version=1, location=LocationView(id="loc:无量山", name="无量山"),
        clocks=[FLOOD, DOUBT], emerged=[EmergedView(id="emg:b", text="乙", subject_ids=("chr:龚光杰",)),
                                         EmergedView(id="emg:a", text="甲")],
    )
    assert [c.id for c in snap.clocks] == sorted([DOUBT.id, FLOOD.id]) and [e.id for e in snap.emerged] == ["emg:a", "emg:b"]
    assert snap.clocks_on("chr:左子穆") == (DOUBT,) and snap.clocks_on("chr:龚光杰") == ()
    assert {"chr:左子穆", "loc:无量山", "chr:龚光杰"} <= snap.referenced_ids()  # 时钟的挂处与事实的主体都要有名


# ============================================================
#  世界心跳：七种新事件、命令耗时与报时、此世的活动 / 痕迹 / 消息的折叠
# ============================================================
def test_heartbeat_events_are_bounded_immutable_and_old_moves_read() -> None:
    """七种心跳事件都已挂进 AnyEvent（上面的往返用例逐一走过）；字段有界、事件不可变；旧账的 Moved 缺此行所为照读。"""
    assert {type(e) for e in ALL_EVENTS} >= set(HEARTBEAT)
    for bad in (0, MAX_TIME_COST + 1):
        with pytest.raises(ValidationError):
            TimePassed(ticks=bad)  # 一条命令至少一刻、至多七日（远行以道路注记的耗时计）
    assert TimePassed(ticks=MAX_TIME_COST).ticks == 7 * TICKS_PER_DAY
    with pytest.raises(ValidationError):
        RumorSpread(token_id=NEWS.id, location_ids=())  # 没传到任何地方就不是一次扩散
    with pytest.raises(ValidationError):
        Moved(from_location_id="loc:甲", to_location_id="loc:乙", exit_label="北", motivation="话" * 25)
    with pytest.raises(ValidationError):
        ItemDecayed(item_id="itm:书信", material="纸张")  # 物料是封闭表
    passed = TimePassed(ticks=4)
    with pytest.raises(ValidationError):
        passed.ticks = 8  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ActivityStarted(activity=BOUT).activity.ends_tick = 99  # type: ignore[misc]  # 内里的活动同样冻结
    old = decode_event({"type": "Moved", "from_location_id": "loc:无量山", "to_location_id": "loc:大理城", "exit_label": "南下"})
    assert isinstance(old, Moved) and old.motivation == "" and old.fleeing is False


def test_ambient_shapes_guard_their_own_invariants() -> None:
    with pytest.raises(ValidationError, match="止于开始之前"):
        Activity.model_validate(BOUT.model_dump() | {"ends_tick": SPAWN_TICK - 1})
    with pytest.raises(ValidationError, match="须从发源地"):
        FactToken.model_validate(NEWS.model_dump() | {"reached": ("loc:大理城",)})
    with pytest.raises(ValidationError, match="重复传到"):
        FactToken.model_validate(NEWS.model_dump() | {"reached": ("loc:无量山", "loc:大理城", "loc:大理城")})
    for bad in ({"decay_ticks": 0}, {"decay_ticks": 4 * TICKS_PER_DAY + 1}, {"description": "血" * 25}, {"id": "trc:xyz"}):
        with pytest.raises(ValidationError):
            EnvironmentalTrace.model_validate(STAIN.model_dump() | bad)
    for bad in ({"speed": 3}, {"radius": 7}, {"text": "话" * 41}, {"origin_id": "无量山"}):
        with pytest.raises(ValidationError):
            FactToken.model_validate(NEWS.model_dump() | bad)
    assert (STAIN.expires_tick, STAIN.remaining(SPAWN_TICK + 90), STAIN.remaining(SPAWN_TICK + 200)) == (
        SPAWN_TICK + TICKS_PER_DAY, 6, 0)
    assert (BOUT.state(SPAWN_TICK), BOUT.state(SPAWN_TICK + 1)) == (ActivityState.ONGOING, ActivityState.ENDED)
    assert activity_id(ActivityKind.FIGHT, "loc:无量山", (PID, "chr:龚光杰"), SPAWN_TICK) == BOUT.id  # 确定性 id


def _spawned(*events: DomainEvent) -> PlayerState:
    state = Player.replay([PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量山"), *events])
    assert state is not None
    return state


def _mark(description: str, born: int, decay: int, at: str = "loc:无量山") -> EnvironmentalTrace:
    return EnvironmentalTrace(id=trace_id(at, description, born), location_id=at, description=description,
                              born_tick=born, decay_ticks=decay)


def _deed(kind: ActivityKind, who: tuple[str, ...], start: int, end: int, trace: EnvironmentalTrace | None) -> Activity:
    return Activity(id=activity_id(kind, "loc:无量山", who, start), kind=kind, participants=who, location_id="loc:无量山",
                    started_tick=start, ends_tick=end, trace_id=trace.id if trace else None)


def _news(text: str, born: int, at: str = "loc:无量山") -> FactToken:
    return FactToken(id=token_id(text, at, born), text=text, origin_id=at, born_tick=born, speed=1, radius=2, reached=(at,))


def test_time_accumulates_and_traces_fade_taking_ended_activities_with_them() -> None:
    """
    TimePassed 累加 tick 并剪枝：到期的痕迹消散；已结束的活动随它的痕迹一同消散（没留痕迹的，一结束就抹去）；
    进行中的活动不因痕迹先散而消失。消散不另写事件——它是时间的纯函数。
    """
    scuffle = _mark("地上脚印凌乱，有过打斗", SPAWN_TICK, 32)  # 第 64 刻消散
    litter = _mark("一地狼藉", SPAWN_TICK, 8)  # 第 40 刻消散
    fight = _deed(ActivityKind.FIGHT, (PID, "chr:龚光杰"), SPAWN_TICK, SPAWN_TICK + 1, scuffle)
    bare = _deed(ActivityKind.FIGHT, (PID, "chr:左子穆"), SPAWN_TICK, SPAWN_TICK + 1, None)
    rout = _deed(ActivityKind.ROUT, ("swm:看热闹的山民",), SPAWN_TICK, SPAWN_TICK + 48, litter)  # 第 80 刻人群回来
    born = [TraceLeft(trace=scuffle), TraceLeft(trace=litter), *(ActivityStarted(activity=a) for a in (fight, bare, rout))]
    fresh = _spawned(*born)
    assert fresh.tick == SPAWN_TICK and len(fresh.activities) == 3 and len(fresh.traces) == 2
    noon = _spawned(*born, TimePassed(ticks=10))
    assert noon.tick == SPAWN_TICK + 10
    assert {t.id for t in noon.traces} == {scuffle.id}  # 狼藉散了
    assert {a.id for a in noon.activities} == {fight.id, rout.id}  # 没留痕迹的交手一结束就抹去；溃散仍在进行
    late = _spawned(*born, TimePassed(ticks=10), TimePassed(ticks=21))
    assert late.tick == SPAWN_TICK + 31 and {a.id for a in late.activities} == {fight.id, rout.id}
    gone = _spawned(*born, TimePassed(ticks=10), TimePassed(ticks=22))  # 第 64 刻：脚印散了，那场交手随之了结
    assert gone.traces == () and {a.id for a in gone.activities} == {rout.id}
    back = _spawned(*born, TimePassed(ticks=48))
    assert back.tick == SPAWN_TICK + 48 and back.activities == () and back.traces == ()


def test_ambient_collections_keep_the_newest_within_their_caps() -> None:
    """活动、痕迹、消息超上限请走最旧的，余者按 id 排序；同 id 再起即覆盖（人群再受惊，溃散的时辰往后延）。"""
    traces = [_mark(f"痕迹{i}", SPAWN_TICK + i, 96) for i in range(TRACES_MAX + 1)]
    fights = [_deed(ActivityKind.FIGHT, (PID, "chr:龚光杰"), SPAWN_TICK + i, SPAWN_TICK + 200, None)
              for i in range(ACTIVITIES_MAX + 1)]
    tokens = [_news(f"消息{i}", SPAWN_TICK + i) for i in range(TOKENS_MAX + 1)]
    state = _spawned(*(TraceLeft(trace=t) for t in traces), *(ActivityStarted(activity=a) for a in fights),
                     *(FactTokenSpawned(token=t) for t in tokens))
    for kept, made, cap in ((state.traces, traces, TRACES_MAX), (state.activities, fights, ACTIVITIES_MAX),
                            (state.tokens, tokens, TOKENS_MAX)):
        ids = [x.id for x in kept]
        assert len(kept) == cap and made[0].id not in ids and ids == sorted(ids), ids  # 最旧的一个退场
        assert {x.id for x in made[1:]} == set(ids)
    rout = _deed(ActivityKind.ROUT, ("swm:看热闹的山民",), SPAWN_TICK, SPAWN_TICK + 48, None)
    again = _spawned(ActivityStarted(activity=rout), ActivityStarted(activity=rout.model_copy(update={"ends_tick": SPAWN_TICK + 60})))
    assert [a.ends_tick for a in again.activities] == [SPAWN_TICK + 60]


def test_news_only_spreads_further_and_unknown_news_is_a_no_op() -> None:
    """消息传到之处按先后追加、去重、只增不减（时间再久也不收回）；没有这枚消息就什么也不发生。"""
    told = _spawned(FactTokenSpawned(token=NEWS),
                    RumorSpread(token_id=NEWS.id, location_ids=("loc:大理城", "loc:无量山", "loc:大理城")),
                    RumorSpread(token_id=NEWS.id, location_ids=("loc:无量玉洞",)),
                    RumorSpread(token_id=NEWS.id, location_ids=("loc:大理城",)),
                    TimePassed(ticks=TICKS_PER_DAY))
    assert [t.reached for t in told.tokens] == [("loc:无量山", "loc:大理城", "loc:无量玉洞")]
    base = _spawned(FactTokenSpawned(token=NEWS))
    stray = _spawned(FactTokenSpawned(token=NEWS), RumorSpread(token_id="tok:0000000000", location_ids=("loc:大理城",)))
    assert stray.tokens == base.tokens == (NEWS,)


def test_decay_consumes_and_pilfering_only_moves_the_item() -> None:
    """
    朽坏之物折进 consumed（与用掉之物一样从此不在任何地方，也不在行囊）；被人顺手拿走只改持有者——
    与玩家无涉：不进焦点、不让焦点走味、不记来路、不开也不了结心事、不计尝试次数。
    """
    scroll = ItemTransferred(item_id="itm:北冥神功卷轴", from_holder="loc:无量玉洞", to_holder=PID)
    rotten = _spawned(scroll, ItemDecayed(item_id="itm:北冥神功卷轴", material=Material.PAPER),
                      ItemDecayed(item_id="itm:书信", material=Material.PAPER))
    assert rotten.consumed == {"itm:北冥神功卷轴", "itm:书信"} and rotten.inventory == frozenset()
    probe = [Conversed(npc_id="chr:辛双清"),
             Maneuvered(item_id="itm:无量剑", target_id="chr:左子穆", approach=Approach.STEALTH, outcome=CovertOutcome.FOILED)]
    before = _spawned(*probe)
    after = _spawned(*probe, ItemPilfered(item_id="itm:玉佩", from_holder="loc:无量山", to_holder="chr:南海鳄神"),
                     ItemPilfered(item_id="itm:无量剑", from_holder="chr:左子穆", to_holder="chr:龚光杰"),
                     TimePassed(ticks=8))
    assert after.item_holders == {"itm:玉佩": "chr:南海鳄神", "itm:无量剑": "chr:龚光杰"}
    for name in ("focus", "focus_fresh", "taken_from", "threads", "attempts", "recent_approaches", "inventory"):
        assert getattr(after, name) == getattr(before, name), name
    assert after.focus == ("chr:左子穆", "itm:无量剑", "chr:辛双清") and after.focus_fresh


def test_moved_remembers_the_motivation_of_the_latest_journey() -> None:
    went = Moved(from_location_id="loc:无量山", to_location_id="loc:大理城", exit_label="南下", motivation="去找段誉")
    state = _spawned(went)
    assert state.motivation == "去找段誉" and _spawned().motivation == ""
    back = Moved(from_location_id="loc:大理城", to_location_id="loc:无量山", exit_label="北上")
    assert _spawned(went, back).motivation == ""  # 此行所为只记最近一次移动


# ============================================================
#  命令：没有不花时间的命令；报时
# ============================================================
def test_every_command_must_say_how_long_it_takes() -> None:
    think = PlayerIntent(action_type=ActionType.THINK)
    with pytest.raises(ValidationError):
        Command(intent=think)  # type: ignore[call-arg]  # time_cost 必填
    for bad in (0, -1, MAX_TIME_COST + 1):
        with pytest.raises(ValidationError):
            Command(intent=think, time_cost=bad)
    assert Command(intent=think, time_cost=1).time_cost == 1 and Command(intent=think, time_cost=MAX_TIME_COST).time_cost == 672
    assert set(TIME_COSTS) == set(ActionType) and all(c >= 1 for c in TIME_COSTS.values())  # 封闭表：每个动作都花时间
    assert TIME_COSTS[ActionType.INVALID] == REFUSED_COST == 1 and NEARBY_MOVE == 1


@pytest.mark.parametrize(
    ("action", "kwargs", "cost"),
    [
        (ActionType.MOVE, {"here": "无量山", "there": "大理城"}, 4),
        (ActionType.MOVE, {"here": "无量山", "there": "无量山·剑湖宫"}, 1),
        (ActionType.MOVE, {"here": "剑湖宫·练武厅", "there": "剑湖宫·后院"}, 1),
        (ActionType.MOVE, {"here": "无量山"}, 4),  # 去处不明：按换一处所计
        (ActionType.MOVE, {"approved": False, "here": "无量山", "there": "大理城"}, 1),
        (ActionType.MOVE, {"here": "无量山", "there": "无量山·剑湖宫", "way_cost": 96}, 96),  # 那条出路的耗时优先
        (ActionType.MOVE, {"way_cost": 0}, 1),  # 钳在 [1, MAX_TIME_COST]
        (ActionType.MOVE, {"way_cost": 10_000}, 672),
        (ActionType.MOVE, {"approved": False, "way_cost": 96}, 1),
        (ActionType.TALK, {"way_cost": 96}, 1),  # 只有移动看出路
        (ActionType.OBSERVE, {}, 1),
        (ActionType.THINK, {}, 1),
        (ActionType.TALK, {}, 1),
        (ActionType.ATTACK, {}, 1),
        (ActionType.TAKE, {}, 1),
        (ActionType.GIVE, {}, 1),
        (ActionType.USE, {}, 1),
        (ActionType.LEARN, {}, 8),
        (ActionType.LEARN, {"approved": False}, 1),
        (ActionType.REST, {}, 8),
        (ActionType.INVALID, {}, 1),
    ],
)
def test_time_cost_is_a_closed_table(action: ActionType, kwargs: dict[str, object], cost: int) -> None:
    assert time_cost(action, **kwargs) == cost  # type: ignore[arg-type]


def test_same_place_compares_the_enclosing_place() -> None:
    assert same_place("剑湖宫", "剑湖宫·练武厅") and same_place("剑湖宫·练武厅", "剑湖宫·后院")
    assert same_place("无量山", "无量山") and not same_place("无量山", "大理城")
    assert not same_place("无量山·剑湖宫", "大理城·镇南王府")


@pytest.mark.parametrize(
    ("tick", "label"),
    [
        (SPAWN_TICK, "第一日·辰正"),
        (SPAWN_TICK + 1, "第一日·辰正一刻"),
        (0, "第一日·子正"),
        (3, "第一日·子正三刻"),
        (4, "第一日·丑初"),
        (TICKS_PER_DAY - 4, "第一日·子初"),  # 二十三点：子初属于前一日的夜
        (TICKS_PER_DAY - 1, "第一日·子初三刻"),
        (TICKS_PER_DAY, "第二日·子正"),  # 跨日
        (2 * TICKS_PER_DAY - 1, "第二日·子初三刻"),
        (48, "第一日·午正"),
        (9 * TICKS_PER_DAY, "第十日·子正"),
        (10 * TICKS_PER_DAY + 48, "第十一日·午正"),
        (19 * TICKS_PER_DAY, "第二十日·子正"),
    ],
)
def test_time_label_tells_the_day_and_the_hour(tick: int, label: str) -> None:
    assert time_label(tick) == label


def test_daylight_runs_from_mao_to_you_and_days_count_from_zero() -> None:
    assert [daylight(t) for t in (19, 20, 75, 76)] == [False, True, True, False]  # 五点天亮，十九点入夜
    assert daylight(SPAWN_TICK) and not daylight(TICKS_PER_DAY + 2)
    assert [day_of(t) for t in (0, TICKS_PER_DAY - 1, TICKS_PER_DAY, 3 * TICKS_PER_DAY)] == [0, 0, 1, 3]


# ============================================================
#  派生的物料、归属与室内；人群掌故
# ============================================================
def test_material_ownership_and_shelter_are_derived_not_stored() -> None:
    assert {m: m.weathers_in for m in Material} == {
        Material.METAL: None, Material.STONE: None, Material.WOOD: 60, Material.LEATHER: 60, Material.CLOTH: 15,
        Material.PAPER: 3, Material.MEDICINE: 10, Material.PLANT: None, Material.FOOD: 2, Material.LIVING: None,
        Material.MISC: None,
    }
    assert (MATERIAL_OF_KIND["秘籍"], MATERIAL_OF_KIND["兵器"], MATERIAL_OF_KIND["药草"]) == (
        Material.PAPER, Material.METAL, Material.PLANT)
    items = {i.id: i for i in WORLD.items}
    assert (items["itm:北冥神功卷轴"].material, items["itm:北冥神功卷轴"].ownership) == (Material.PAPER, Ownership.UNOWNED)
    assert (items["itm:无量剑"].material, items["itm:无量剑"].ownership) == (Material.METAL, Ownership.CARRIED)
    assert (items["itm:玉佩"].material, items["itm:玉佩"].ownership) == (Material.MISC, Ownership.STRAYED)  # 信物不在表里
    assert Item(id="itm:谱诀", name="谱诀").ownership is Ownership.UNOWNED
    assert [ownership(*pair) for pair in ((None, "loc:无量山"), (None, "chr:段誉"), ("chr:段誉", "chr:段誉"),
                                          ("chr:段誉", "loc:无量山"), ("chr:段誉", "chr:乔峰"), ("chr:段誉", PID))] == [
        Ownership.UNOWNED, Ownership.UNOWNED, Ownership.CARRIED, Ownership.STRAYED, Ownership.HELD, Ownership.HELD]
    shelters = {name: Location(id=f"loc:{name}", name=name).sheltered
                for name in ("无量玉洞", "剑湖宫·练武厅", "松鹤楼", "大理城", "无量山", "剑湖宫", "剑湖宫·后山")}
    assert shelters == {"无量玉洞": True, "剑湖宫·练武厅": True, "松鹤楼": True, "大理城": False, "无量山": False,
                        "剑湖宫": False, "剑湖宫·后山": False}
    assert "sheltered" not in Location.model_fields and not {"material", "ownership"} & set(Item.model_fields)
    assert not {"material", "ownership"} & set(items["itm:玉佩"].model_dump())  # 不另存：蓝图与掌故的指纹不动
    assert EntityKind.SWARM == "swm"


CROWD = SwarmNode(id="swm:看热闹的山民", name="看热闹的山民", location_id="loc:无量山", size=30, panic_threshold=3,
                  routine="围观比剑", sources=("chunk:1",))


def test_swarms_land_on_the_blueprint() -> None:
    disciples = SwarmNode(id="swm:东宗弟子", name="东宗弟子", location_id="loc:无量山", size=20, panic_threshold=6,
                          routine="练剑", faction="无量剑东宗", sources=("chunk:2", "ev:2"))
    bp = _lore(swarms=(CROWD, disciples))
    assert bp.swarms == (CROWD, disciples) and WORLD.swarms == ()


@pytest.mark.parametrize(
    ("swarms", "message"),
    [
        ((CROWD, CROWD), "重复的人群 id：swm:看热闹的山民"),
        ((CROWD.model_copy(update={"id": "swm:山民"}),), "id 须是 swm:看热闹的山民"),
        ((CROWD.model_copy(update={"location_id": "loc:少林寺"}),), "落在不存在的地点：loc:少林寺"),
        ((CROWD.model_copy(update={"faction": "少林派"}),), "门派「少林派」在蓝图里没有一个人"),
    ],
)
def test_the_lore_gate_rejects_bad_swarms(swarms: tuple[SwarmNode, ...], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _lore(swarms=swarms)


def test_swarm_fields_are_bounded() -> None:
    for bad in ({"name": "人" * 13}, {"size": 2}, {"size": 501}, {"panic_threshold": 0}, {"panic_threshold": 11},
                {"routine": "看" * 13}, {"routine": ""}, {"sources": ()}, {"sources": ("第一回",)}, {"id": "看热闹的山民"},
                {"faction": "派" * 25}):
        with pytest.raises(ValidationError):
            SwarmNode.model_validate(CROWD.model_dump() | bad)


# ============================================================
#  快照的世界心跳视图
# ============================================================
def test_heartbeat_snapshot_views_default_order_and_name_what_they_mention() -> None:
    place = LocationView(id="loc:无量山", name="无量山")
    bare = LocalSnapshot(player_id=PID, player_name="阿星", alive=True, version=1, location=place)
    assert (bare.tick, bare.activities, bare.traces, bare.swarms, bare.rumors) == (SPAWN_TICK, (), (), (), ())
    assert bare.time_label == "第一日·辰正" and bare.daylight
    snap = LocalSnapshot(
        player_id=PID, player_name="阿星", alive=True, version=1, location=place, tick=TICKS_PER_DAY - 4,
        activities=[
            ActivityView(id="act:b", kind=ActivityKind.ROUT, participants=("swm:东宗弟子",), state=ActivityState.ONGOING,
                         started_tick=60),
            {"id": "act:a", "kind": "交手", "participants": (PID, "chr:龚光杰"), "state": "已结束", "started_tick": 40},
        ],
        traces=[TraceView(id="trc:b", description="一地狼藉", remaining=3), TraceView(id="trc:a", description="血迹", remaining=9)],
        swarms=[SwarmView(id="swm:看热闹的山民", name="看热闹的山民", size=30, panic_threshold=3, routine="围观比剑"),
                SwarmView(id="swm:东宗弟子", name="东宗弟子", size=20, panic_threshold=6, routine="练剑", routed=True)],
        rumors=[RumorView(id="tok:b", text="乙", subject_ids=("chr:龚光杰", PID), origin_id="loc:大理城", born_tick=40),
                RumorView(id="tok:a", text="甲", origin_id="loc:无量玉洞", born_tick=50)],
    )
    assert [a.id for a in snap.activities] == ["act:a", "act:b"] and snap.activities[0].state is ActivityState.ENDED
    assert [t.id for t in snap.traces] == ["trc:a", "trc:b"] and [r.id for r in snap.rumors] == ["tok:a", "tok:b"]
    assert [s.id for s in snap.swarms] == sorted(["swm:看热闹的山民", "swm:东宗弟子"])
    assert {s.name: s.current_state for s in snap.swarms} == {"看热闹的山民": "围观比剑", "东宗弟子": "溃散逃离"}
    assert snap.rumors[1].subject_ids == tuple(sorted(("chr:龚光杰", PID)))  # 集合字段按 id 排序
    assert snap.time_label == "第一日·子初" and not snap.daylight
    assert {"chr:龚光杰", "swm:看热闹的山民", "swm:东宗弟子", "loc:大理城", "loc:无量玉洞", PID} <= snap.referenced_ids()


def test_item_views_derive_material_and_ownership_on_the_spot() -> None:
    letter = ItemView(id="itm:书信", name="书信", kind="书信", holder_id="loc:无量山")
    sword = ItemView(id="itm:无量剑", name="无量剑", kind="兵器", holder_id="chr:左子穆", owner_id="chr:左子穆")
    taken = sword.model_copy(update={"holder_id": PID})
    dropped = sword.model_copy(update={"holder_id": "loc:无量山"})
    jade = ItemView(id="itm:玉佩", name="玉佩", kind="信物", holder_id="loc:无量山", owner_id="chr:段正淳")
    assert (letter.material, letter.ownership) == (Material.PAPER, Ownership.UNOWNED)
    assert (sword.material, sword.ownership) == (Material.METAL, Ownership.CARRIED)
    assert (taken.ownership, dropped.ownership) == (Ownership.HELD, Ownership.STRAYED)
    assert (jade.material, jade.ownership) == (Material.MISC, Ownership.STRAYED)
