"""
[INPUT]: 依赖 app.domain.npc 的 planning_due / upheaval / free / destinations / AgendaProposal / admit / march / skirmish_stakes / SkirmishStakes / settle_skirmish / meet 与常量，
         依赖 app.domain.agenda 的名词、app.domain.heartbeat 的 Atlas / position / residents_at / BLOOD，依赖 app.domain.events / aggregates（折叠），
         依赖 app.domain.rules 的 stakes / decide（带伤折一档），依赖 tests/test_heartbeat 的 look（内存图谱快照），依赖 tests/test_domain 的 ALL_EVENTS / H_AGENT
[OUTPUT]: 分层 NPC 生态（H-Agent）的单测：
          八种新事件挂进线协议、有界、旧账缺新字段照读；NpcAgenda / Encounter 自守不变量；
          Atlas 的道路耗时、名字、在世人物、核心 NPC（有执念、在世、已到场、有所在）、开篇仇人对；path 是以耗时为权的最省时之路（绕远更快就绕、同耗时取 id 序列字典序最小、
          不通为空、坠落之路与单程坠落的回程不走）；position / residents_at 按此世所在；折叠（议程挂上与摘下、启程之刻、每一跳改所在、中断挂上与裁决后驻足、受伤）；
          宏观层：planning_due 的四种时机（无核心永不、初临江湖、新的一日、冷却已过且江湖震动）、upheaval、free、destinations（三跳以内）、
          AgendaProposal 宽容、admit 闸门（名字或 id 全等落地、非核心 / 不自由 / 去处越界 / 意图不合即丢、每人至多一条、留守即作罢、按轻重至多四条、恒记一条 AgendaPlanned）；
          微观层：march 逐跳按道路耗时推进、抵达即了结、无路与被制住即受阻、带伤或在中断里不动、走进玩家所在即撞见（来到 / 离开）、走到开篇仇人所在即狭路相逢、
          玩家走进离了家的带议程 NPC 所在也算撞见、驻足到 resume_tick 再上路；随机行军的性质（每一跳都是一条路、不越过此刻、同一刻再走一遍无事发生）；
          裁决层：skirmish_stakes 的确定性裁决与区间（仁厚对仁厚相安无事、有狠辣者强者胜、势均力敌两败俱伤、中庸差两档强者胜否则口角、带伤折一档）、
          settle_skirmish（出界取确定性裁决、动手留血迹与往事、落败者带伤一日 / 议程败退 / 往家退一跳、两败俱伤双双带伤且议程中断、消息远近、微观事实过筛、两人驻足）、
          meet（闸门事件之后补 EncounterResolved、来者驻足一个时辰）；带伤的 NPC 在快照里 wounded、出手与暗取时境界折一档而交涉不折
[POS]: tests 的 H-Agent 物理基线：大模型只立议程、只在相撞时裁决，其余一步一步都是可重放的纯函数——这里逐条钉死
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import random
from dataclasses import replace
from functools import reduce

import pytest
from pydantic import ValidationError

from app.domain.agenda import AgendaEnd, Encounter, EncounterKind, NpcAgenda, SkirmishOutcome, encounter_id
from app.domain.aggregates import Player, PlayerState, evolve
from app.domain.ambient import ActivityKind, FactToken, activity_id, token_id, trace_id
from app.domain.combat import CombatOutcome
from app.domain.commands import SPAWN_TICK, TICKS_PER_DAY
from app.domain.covert import CovertStakes
from app.domain.events import (
    ActivityStarted,
    AgendaConcluded,
    AgendaIssued,
    AgendaPlanned,
    DomainEvent,
    EncounterBegan,
    EncounterResolved,
    FactEmerged,
    FactTokenSpawned,
    NpcMoved,
    NpcWounded,
    PlacesLearned,
    PlayerDied,
    PlayerSpawned,
    RenownChanged,
    SkillExecuted,
    TimePassed,
    TraceLeft,
    decode_event,
)
from app.domain.geography import Direction, Passage, TravelMethod
from app.domain.heartbeat import BLOOD, Atlas, position, residents_at
from app.domain.intent import ActionType, Approach, PlayerIntent
from app.domain.lore import Persona
from app.domain.models import (
    Character,
    CharacterRelation,
    CharacterStatus,
    Disposition,
    Era,
    Item,
    Location,
    RelationKind,
    Tier,
    WorldBlueprint,
)
from app.domain.npc import (
    AGENDAS_MAX,
    ENCOUNTER_PAUSE,
    PLAN_COOLDOWN,
    SKIRMISH_PAUSE,
    STAY,
    WOUND_TICKS,
    AgendaProposal,
    SkirmishStakes,
    admit,
    destinations,
    free,
    march,
    meet,
    planning_due,
    settle_skirmish,
    skirmish_stakes,
    upheaval,
)
from app.domain.resolution import fact_id
from app.domain.rules import stakes
from app.domain.social import SocialStakes
from tests.test_domain import ALL_EVENTS, H_AGENT
from tests.test_heartbeat import look

PID = "ply:beat"  # 与 tests/test_heartbeat.look 同一位玩家
T = SPAWN_TICK
PALACE, PATH, CITY, CAVE, FERRY, RIVER, FAR, ISLE = (
    "loc:剑湖宫", "loc:山道", "loc:大理城", "loc:无量玉洞", "loc:善人渡", "loc:江边", "loc:远方", "loc:孤岛")
ZUO, GONG, XIN, DUAN, MA, CROC, WANG, MU, ZHONG, LORD = (
    "chr:左子穆", "chr:龚光杰", "chr:辛双清", "chr:段誉", "chr:马五德", "chr:南海鳄神", "chr:汪剑通", "chr:木婉清", "chr:钟灵",
    "chr:段正淳")


# ============================================================
#  夹具：一张带耗时的小地图——剑湖宫直下大理城十二刻，绕山道只要八刻；孤岛无路可通
# ============================================================
def _place(lid: str, exits: dict[str, str]) -> Location:
    return Location(id=lid, name=lid.removeprefix("loc:"), region="大理", description="", exits=exits)


def _worry(cid: str, worry: str = "") -> Persona:
    return Persona(character_id=cid, worry=worry, likes=() if worry else ("清静",), sources=("chunk:1",))


MAP = WorldBlueprint(
    locations=(
        _place(PALACE, {"下山": PATH, "崖下": CAVE, "南去大理": CITY}),
        _place(PATH, {"上山": PALACE, "南下": CITY}),
        _place(CITY, {"北上": PATH, "北回剑湖宫": PALACE, "东去渡口": FERRY}),
        _place(CAVE, {"攀上": PALACE}),
        _place(FERRY, {"西归": CITY, "顺流": RIVER}),
        _place(RIVER, {"逆流": FERRY, "远行": FAR}),
        _place(FAR, {"归来": RIVER}),
        _place(ISLE, {}),
    ),
    characters=(
        Character(id=ZUO, true_name="左子穆", tier=Tier.THIRD, location_id=PALACE),
        Character(id=GONG, true_name="龚光杰", tier=Tier.THIRD, disposition=Disposition.RUTHLESS, location_id=PALACE),
        Character(id=XIN, true_name="辛双清", tier=Tier.THIRD, location_id=PATH),
        Character(id=DUAN, true_name="段誉", tier=Tier.NONE, disposition=Disposition.MERCIFUL, location_id=CITY),
        Character(id=MA, true_name="马五德", tier=Tier.NONE, location_id=CITY),
        Character(id=LORD, true_name="段正淳", tier=Tier.FIRST, location_id=CITY),
        Character(id=CROC, true_name="南海鳄神", tier=Tier.FIRST, disposition=Disposition.RUTHLESS, location_id=FERRY),
        Character(id=WANG, true_name="汪剑通", tier=Tier.FIRST, status=CharacterStatus.DECEASED, location_id=FERRY),
        Character(id=MU, true_name="木婉清", tier=Tier.SECOND, location_id=CITY, arrives_with="portent:黑玫瑰"),
        Character(id=ZHONG, true_name="钟灵", tier=Tier.NONE, disposition=Disposition.MERCIFUL),
    ),
    items=(Item(id="itm:无量剑", name="无量剑", kind="兵器", owner_id=ZUO),),
    relations=(
        CharacterRelation(source_id=ZUO, target_id=XIN, kind=RelationKind.ENEMY, note="东西宗相争"),
        CharacterRelation(source_id=GONG, target_id=DUAN, kind=RelationKind.ENEMY, era=Era.LATER, note="后来结仇"),
        CharacterRelation(source_id=ZUO, target_id=GONG, kind=RelationKind.MENTOR, note="师徒"),
    ),
    personas=(
        _worry(ZUO, "西宗来夺剑湖宫"), _worry(GONG, "师父偏心"), _worry(XIN, "东宗占着剑湖宫"), _worry(DUAN, "不想学武"),
        _worry(MA, "生意难做"), _worry(CROC), _worry(WANG, "丐帮后继"), _worry(MU, "寻仇"), _worry(ZHONG, "闪电貂"),
    ),
    passages=tuple(
        Passage(from_id=a, to_id=b, direction=d, time_cost=12, basis="山路陡峭", sources=("chunk:1",))
        for a, b, d in ((PALACE, CITY, Direction.SOUTH), (CITY, PALACE, Direction.NORTH))
    ),
)
ATLAS = Atlas.of(MAP)


def world(*events: DomainEvent, at: str = FAR, tick: int | None = None) -> PlayerState:
    state = Player.replay([PlayerSpawned(player_id=PID, name="阿星", location_id=at), *events])
    assert state is not None
    return state if tick is None else replace(state, tick=tick)


def issued(npc: str, target: str, tick: int = T, intent: str = "前去一探", priority: int = 2) -> AgendaIssued:
    return AgendaIssued(agenda=NpcAgenda(npc_id=npc, target_id=target, intent=intent, priority=priority, issued_tick=tick))


def moved(npc: str, a: str, b: str, tick: int, seen: str = "") -> NpcMoved:
    return NpcMoved(npc_id=npc, from_location_id=a, to_location_id=b, tick=tick, witnessed=seen)  # type: ignore[arg-type]


def encounter(kind: EncounterKind, npc: str, other: str, at: str, tick: int) -> Encounter:
    return Encounter(id=encounter_id(kind, npc, other, at, tick), kind=kind, npc_id=npc, other_id=other, location_id=at,
                     tick=tick)


def fold(state: PlayerState, events: list[DomainEvent]) -> PlayerState:
    return reduce(evolve, events, state)


# ============================================================
#  事件与名词
# ============================================================
def test_the_eight_events_are_on_the_wire_bounded_and_read_old_ledgers() -> None:
    assert {type(e) for e in ALL_EVENTS} >= set(H_AGENT)  # 往返用例（tests/test_domain）逐一走过
    for bad in (
        lambda: AgendaPlanned(tick=-1),
        lambda: AgendaConcluded(npc_id=ZUO, how="回家", tick=T),
        lambda: moved(ZUO, PALACE, PATH, T, "路过"),
        lambda: NpcWounded(npc_id=ZUO, until_tick=-1),
        lambda: EncounterResolved(encounter_id="enc:0123456789", kind="偶遇", location_id=PATH, resume_tick=T),
        lambda: EncounterResolved(encounter_id="enc:0123456789", kind=EncounterKind.CROSS_PATHS, location_id=PATH,
                                  resume_tick=T, by="大模型"),
    ):
        with pytest.raises(ValidationError):
            bad()
    old = decode_event({"type": "NpcMoved", "npc_id": ZUO, "from_location_id": PALACE, "to_location_id": PATH, "tick": T})
    assert isinstance(old, NpcMoved) and old.witnessed == ""
    told = decode_event({"type": "PlacesLearned", "location_ids": [CITY]})
    assert isinstance(told, PlacesLearned) and told.source_id is None
    done = decode_event({"type": "EncounterResolved", "encounter_id": "enc:0123456789", "kind": "撞见", "location_id": PATH,
                         "resume_tick": T})
    assert isinstance(done, EncounterResolved) and (done.npc_ids, done.outcome, done.by, done.witnessed) == ((), None, "规则", False)
    planned = decode_event({"type": "AgendaPlanned", "tick": T})
    assert isinstance(planned, AgendaPlanned) and planned.cause == ""


def test_agendas_and_encounters_guard_their_own_shape() -> None:
    good = issued(ZUO, CITY).agenda
    for bad in ({"intent": "去"}, {"intent": "去" * 17}, {"priority": 0}, {"priority": 4}, {"target_id": "大理城"},
                {"npc_id": "ply:x"}, {"issued_tick": -1}, {"extra": 1}):
        with pytest.raises(ValidationError):
            NpcAgenda.model_validate(good.model_dump() | bad)
    meeting = encounter(EncounterKind.MEET_PLAYER, ZUO, PID, PATH, T)
    assert meeting.id == encounter_id(EncounterKind.MEET_PLAYER, ZUO, PID, PATH, T) and meeting.id.startswith("enc:")
    assert meeting.id != encounter(EncounterKind.MEET_PLAYER, ZUO, PID, PATH, T + 1).id
    for bad in ({"id": "enc:xyz"}, {"other_id": "loc:山道"}, {"location_id": "山道"}):
        with pytest.raises(ValidationError):
            Encounter.model_validate(meeting.model_dump() | bad)
    assert [o.fight for o in SkirmishOutcome] == [True, True, True, False, False]


# ============================================================
#  Atlas —— 道路耗时、在世之人、核心 NPC、开篇仇人与最省时之路
# ============================================================
def test_the_atlas_knows_costs_people_the_core_and_rivals() -> None:
    assert ATLAS.cost(PALACE, CITY) == ATLAS.cost(CITY, PALACE) == 12 and ATLAS.cost(PALACE, PATH) == 4
    assert ATLAS.cost(PALACE, ISLE) == 4  # 没有的路按换一处所计
    assert ATLAS.names[PALACE] == "剑湖宫" and ATLAS.names[ZUO] == "左子穆"
    assert WANG not in ATLAS.characters and MU not in ATLAS.characters and ZHONG in ATLAS.characters  # 已故与后来才到场者不在世上
    assert ATLAS.core == tuple(sorted((ZUO, GONG, XIN, DUAN, MA)))  # 有执念、在世、已到场、有所在；鳄神没心事，钟灵无处可寻
    assert ATLAS.rivals == {frozenset((ZUO, XIN))}  # 只认开篇的仇敌：后文结的仇不算，师徒不算


def test_the_path_is_the_quickest_road_not_the_fewest_hops() -> None:
    assert ATLAS.path(PALACE, CITY) == (PALACE, PATH, CITY)  # 直下十二刻，绕山道八刻
    assert ATLAS.path(CAVE, FAR) == (CAVE, PALACE, PATH, CITY, FERRY, RIVER, FAR)
    assert ATLAS.path(PALACE, PALACE) == (PALACE,) and ATLAS.path(PALACE, ISLE) == () and ATLAS.path(ISLE, PALACE) == ()
    a, b, c, d = "loc:a", "loc:b", "loc:c", "loc:d"
    diamond = Atlas(neighbors={a: (b, c), b: (a, d), c: (a, d), d: (b, c)})
    assert diamond.path(a, d) == (a, b, d)  # 同耗时取 id 序列字典序最小
    assert replace(diamond, costs={(a, b): 9}).path(a, d) == (a, c, d)
    assert replace(diamond, falls=frozenset({(a, b), (c, d)})).path(a, d) == ()  # 坠落之路与它爬不回去的回程都不走


def test_nobody_marches_off_a_cliff_or_climbs_back_a_one_way_drop() -> None:
    cliff = Passage(from_id=PALACE, to_id=CAVE, direction=Direction.DOWN, travel_method=TravelMethod.FALL, time_cost=1,
                    basis="失足坠崖", sources=("chunk:1",))
    atlas = Atlas.of(MAP.model_copy(update={"passages": (cliff,)}))
    assert (PALACE, CAVE) in atlas.falls and (CAVE, PALACE) not in atlas.falls  # 洞里写了「攀上」，那条回程是正经的路
    assert atlas.path(PALACE, CAVE) == () and atlas.path(CAVE, PALACE) == (CAVE, PALACE)
    one_way = MAP.model_copy(update={"passages": (cliff,), "locations": tuple(
        loc.model_copy(update={"exits": {}}) if loc.id == CAVE else loc for loc in MAP.locations)})
    assert Atlas.of(one_way).falls == {(PALACE, CAVE), (CAVE, PALACE)}  # 单程坠落：回程没有出口，也爬不回去


def test_where_people_are_follows_the_march() -> None:
    state = world(moved(XIN, PATH, CITY, T + 4))
    assert position(state, ATLAS, XIN) == CITY and position(state, ATLAS, ZUO) == PALACE
    assert position(state, ATLAS, ZHONG) is None and position(state, ATLAS, "chr:无名氏") is None
    assert [c.id for c in residents_at(state, ATLAS, CITY)] == sorted((XIN, DUAN, MA, LORD))  # 木婉清还没到
    assert residents_at(state, ATLAS, PATH) == ()


def test_the_new_events_fold_into_the_parallel_world() -> None:
    meeting = encounter(EncounterKind.MEET_PLAYER, GONG, PID, PATH, T + 4)
    state = world(AgendaPlanned(tick=T, cause="初临江湖"), issued(GONG, CITY, T), issued(ZUO, CAVE, T + 1),
                  moved(GONG, PALACE, PATH, T + 4), EncounterBegan(encounter=meeting), NpcWounded(npc_id=XIN, until_tick=T + 50))
    assert state.agenda_tick == T and set(state.agendas) == {GONG, ZUO} and state.npc_at == {GONG: PATH}
    assert state.npc_since == {GONG: T + 4, ZUO: T + 1} and state.encounters == (meeting,) and state.npc_wounds == {XIN: T + 50}
    later = fold(state, [EncounterResolved(encounter_id=meeting.id, kind=meeting.kind, location_id=PATH, npc_ids=(GONG,),
                                           resume_tick=T + 9), AgendaConcluded(npc_id=ZUO, how=AgendaEnd.DROPPED, tick=T + 9),
                         EncounterBegan(encounter=meeting), EncounterBegan(encounter=meeting)])
    assert later.encounters == (meeting,) and set(later.agendas) == {GONG} and later.npc_since[GONG] == T + 9  # 同一中断不重挂
    early = fold(later, [EncounterResolved(encounter_id=meeting.id, kind=meeting.kind, location_id=PATH, npc_ids=(GONG,),
                                           resume_tick=T)])
    assert early.encounters == () and early.npc_since[GONG] == T + 9  # 驻足只往后延


# ============================================================
#  宏观层 —— 何时规划、谁能领、能去哪、提议怎么过闸
# ============================================================
def test_planning_happens_on_arrival_at_dawn_and_after_upheaval() -> None:
    assert planning_due(world(), Atlas.of(WorldBlueprint(locations=MAP.locations))) is None  # 没有核心 NPC 永不规划
    assert planning_due(world(), ATLAS) == "初临江湖"
    planned = world(AgendaPlanned(tick=T))
    assert planning_due(replace(planned, tick=T + 40), ATLAS) is None
    assert planning_due(replace(planned, tick=TICKS_PER_DAY), ATLAS) == "新的一日"
    blood = [SkillExecuted(skill_id=None, target_id=GONG, outcome=CombatOutcome.SEVERE_WOUND)]
    assert planning_due(replace(planned, tick=T + PLAN_COOLDOWN - 1), ATLAS, blood) is None  # 冷却未过
    assert planning_due(replace(planned, tick=T + PLAN_COOLDOWN), ATLAS, blood) == "江湖震动"
    assert planning_due(replace(planned, tick=T + PLAN_COOLDOWN), ATLAS, []) is None


def test_upheaval_is_bloodshed_death_or_a_fight_between_rivals() -> None:
    def resolved(outcome: SkirmishOutcome | None) -> EncounterResolved:
        return EncounterResolved(encounter_id="enc:0123456789", kind=EncounterKind.CROSS_PATHS, location_id=PATH,
                                 outcome=outcome, resume_tick=T)

    assert upheaval([SkillExecuted(skill_id=None, target_id=GONG, outcome=CombatOutcome.SEVERE_WOUND)])
    assert upheaval([PlayerDied(cause="冒犯", killer_id=CROC)]) and upheaval([resolved(SkirmishOutcome.BOTH_HURT)])
    assert not upheaval([SkillExecuted(skill_id=None, target_id=GONG, outcome=CombatOutcome.MINOR_WOUND)])
    assert not upheaval([resolved(SkirmishOutcome.QUARREL), resolved(None)]) and not upheaval([])


def test_who_is_free_and_where_they_may_go() -> None:
    meeting = encounter(EncounterKind.CROSS_PATHS, ZUO, XIN, PALACE, T)
    state = world(NpcWounded(npc_id=GONG, until_tick=T + 10), EncounterBegan(encounter=meeting), tick=T + 5)
    assert not free(state, ATLAS, GONG) and free(replace(state, tick=T + 10), ATLAS, GONG)  # 伤到那一刻为止
    assert not free(state, ATLAS, ZUO) and not free(state, ATLAS, XIN)  # 来者与对方都在中断里
    assert free(state, ATLAS, DUAN) and not free(state, ATLAS, WANG) and not free(replace(state, subdued=frozenset({DUAN})), ATLAS, DUAN)
    assert destinations(state, ATLAS, ZUO) == (*sorted((CITY, CAVE, PATH)), FERRY, RIVER)  # 三跳以内（直路算一跳），按跳数再按 id
    assert destinations(world(moved(ZUO, PALACE, FAR, T)), ATLAS, ZUO) == (RIVER, FERRY, CITY)  # 按此世所在
    assert destinations(state, ATLAS, ZHONG) == ()


def test_proposals_are_lenient() -> None:
    loose = AgendaProposal.model_validate({"npc": " 左子穆 ", "target": None, "intent": 7, "priority": "9", "why": "多余"})
    assert (loose.npc, loose.target, loose.intent, loose.priority) == ("左子穆", "", "7", 3)
    assert [AgendaProposal(npc="x", target="y", priority=p).priority for p in (0, "x", None, 2)] == [1, 2, 2, 2]  # type: ignore[arg-type]


def test_the_agenda_gate() -> None:
    state = world(tick=T + 3)
    proposals = [
        AgendaProposal(npc="左子穆", target="大理城", intent="去大理城寻访段誉"),
        AgendaProposal(npc="左子穆", target="善人渡", intent="去渡口", priority=1),  # 同一人第二条
        AgendaProposal(npc="chr:龚光杰", target="loc:善人渡", intent="去渡口截人", priority=3),  # id 也认
        AgendaProposal(npc="辛双清", target="远方", intent="远走他乡"),  # 四跳之外
        AgendaProposal(npc="段誉", target="山道", intent="go home"),  # 英文
        AgendaProposal(npc="马五德", target="大理城", intent="回家"),  # 已在此地
        AgendaProposal(npc="南海鳄神", target="大理城", intent="去大理城"),  # 不是核心
        AgendaProposal(npc="左", target="山道", intent="上山"),  # 不猜
        AgendaProposal(npc="段誉", target="剑", intent="上山"),
    ]
    assert admit(proposals, state, ATLAS, "初临江湖") == [
        AgendaPlanned(tick=T + 3, cause="初临江湖"),
        issued(GONG, FERRY, T + 3, "去渡口截人", 3),
        issued(ZUO, CITY, T + 3, "去大理城寻访段誉"),
    ]
    assert admit([], state, ATLAS, "新的一日") == [AgendaPlanned(tick=T + 3, cause="新的一日")]  # 不论放行几条都记一笔
    for intent in ("去", "去" * 17, "去山道第2回", "去〈某地〉"):
        assert admit([AgendaProposal(npc="段誉", target="山道", intent=intent)], state, ATLAS, "c")[1:] == []
    assert admit([AgendaProposal(npc="段誉", target="山道", intent="去" * 16)], state, ATLAS, "c")[1:] == [
        issued(DUAN, PATH, T + 3, "去" * 16)]


def test_staying_drops_an_agenda_and_at_most_four_are_issued() -> None:
    state = world(issued(ZUO, CITY), tick=T + 3)
    assert admit([AgendaProposal(npc="左子穆", target=STAY), AgendaProposal(npc="段誉", target=STAY)], state, ATLAS, "c") == [
        AgendaPlanned(tick=T + 3, cause="c"), AgendaConcluded(npc_id=ZUO, how=AgendaEnd.DROPPED, tick=T + 3)]
    hurt = world(issued(ZUO, CITY), NpcWounded(npc_id=ZUO, until_tick=T + 9), tick=T + 3)
    assert admit([AgendaProposal(npc="左子穆", target=STAY)], hurt, ATLAS, "c")[1:] == []  # 带伤者不领议程
    everyone = [AgendaProposal(npc=n, target="善人渡", intent="去渡口看看", priority=p)
                for n, p in (("左子穆", 1), ("龚光杰", 2), ("辛双清", 3), ("段誉", 2), ("马五德", 2))]
    out = admit(everyone, world(), ATLAS, "c")
    assert len(out) == 1 + AGENDAS_MAX and all(isinstance(e, AgendaIssued) for e in out[1:])
    kept = [e.agenda for e in out[1:] if isinstance(e, AgendaIssued)]
    assert [a.npc_id for a in kept] == [XIN, *sorted((GONG, DUAN, MA))]  # 按轻重再按 id；最轻的左子穆落选


# ============================================================
#  微观层 —— 一跳一跳地走，相撞即停
# ============================================================
def test_marching_hop_by_hop_on_the_road_clock() -> None:
    state = world(issued(GONG, CITY))
    assert march(replace(state, tick=T + 3), ATLAS) == []
    assert march(replace(state, tick=T + 4), ATLAS) == [moved(GONG, PALACE, PATH, T + 4)]  # 辛双清不是他的仇人
    there = march(replace(state, tick=T + 30), ATLAS)
    assert there == [moved(GONG, PALACE, PATH, T + 4), moved(GONG, PATH, CITY, T + 8),
                     AgendaConcluded(npc_id=GONG, how=AgendaEnd.ARRIVED, tick=T + 8)]
    after = fold(replace(state, tick=T + 30), there)
    assert position(after, ATLAS, GONG) == CITY and after.agendas == {} and march(after, ATLAS) == []


def test_marching_stops_where_it_meets_someone() -> None:
    rival = march(world(issued(ZUO, CITY), tick=T + 30), ATLAS)
    clash = encounter(EncounterKind.CROSS_PATHS, ZUO, XIN, PATH, T + 4)
    assert rival == [moved(ZUO, PALACE, PATH, T + 4), EncounterBegan(encounter=clash)]  # 走到开篇仇人所在：狭路相逢
    stuck = fold(world(issued(ZUO, CITY), tick=T + 30), rival)
    assert march(stuck, ATLAS) == []  # 中断未决，两人都不挪步
    met = march(world(issued(GONG, CITY), at=PATH, tick=T + 30), ATLAS)
    assert met == [moved(GONG, PALACE, PATH, T + 4, "来到"),
                   EncounterBegan(encounter=encounter(EncounterKind.MEET_PLAYER, GONG, PID, PATH, T + 4))]
    assert march(world(issued(GONG, CITY), at=PALACE, tick=T + 4), ATLAS) == [moved(GONG, PALACE, PATH, T + 4, "离开")]


def test_walking_in_on_a_travelling_npc_is_a_meeting_too() -> None:
    state = world(issued(DUAN, PALACE), moved(DUAN, CITY, PATH, T + 4), issued(XIN, FERRY, T + 3), at=PATH, tick=T + 5)
    assert march(state, ATLAS) == []  # 段誉下一跳在 T+8；辛双清 T+3 才启程，走一跳要四刻
    assert march(state, ATLAS, player_arrived=True) == [
        EncounterBegan(encounter=encounter(EncounterKind.MEET_PLAYER, DUAN, PID, PATH, T + 5))]  # 在自己家里的辛双清不算撞见


def test_the_wounded_the_held_and_the_lost_do_not_march() -> None:
    hurt = world(issued(GONG, CITY), NpcWounded(npc_id=GONG, until_tick=T + 50), tick=T + 20)
    assert march(hurt, ATLAS) == []
    held = world(issued(GONG, CITY), tick=T + 20)
    assert march(replace(held, subdued=frozenset({GONG})), ATLAS) == [
        AgendaConcluded(npc_id=GONG, how=AgendaEnd.BLOCKED, tick=T + 20)]
    assert march(world(issued(GONG, ISLE), tick=T + 20), ATLAS) == [AgendaConcluded(npc_id=GONG, how=AgendaEnd.BLOCKED, tick=T + 20)]
    home = world(issued(MA, CITY, T + 2), tick=T + 20)
    assert march(home, ATLAS) == [AgendaConcluded(npc_id=MA, how=AgendaEnd.ARRIVED, tick=T + 2)]
    assert march(world(issued(WANG, CITY), tick=T + 20), ATLAS) == []  # 不在世上的人没有行军


def test_a_pause_delays_the_next_hop() -> None:
    meeting = encounter(EncounterKind.MEET_PLAYER, GONG, PID, PATH, T + 4)
    state = world(issued(GONG, CITY), moved(GONG, PALACE, PATH, T + 4), EncounterBegan(encounter=meeting),
                  EncounterResolved(encounter_id=meeting.id, kind=meeting.kind, location_id=PATH, npc_ids=(GONG,),
                                    resume_tick=T + 10))
    assert march(replace(state, tick=T + 13), ATLAS) == []
    assert march(replace(state, tick=T + 14), ATLAS)[0] == moved(GONG, PATH, CITY, T + 14)


def test_arriving_after_a_meeting_is_never_dated_in_the_future() -> None:
    """撞见发生在目标之地：裁决让他驻足到 resume_tick，其间再推一遍，议程了结之刻不得晚于此刻（事件不记未来之事）。"""
    meeting = encounter(EncounterKind.MEET_PLAYER, GONG, PID, PATH, T + 4)
    state = world(issued(GONG, PATH), at=PATH, tick=T + 4)
    assert march(state, ATLAS) == [moved(GONG, PALACE, PATH, T + 4, "来到"), EncounterBegan(encounter=meeting)]
    state = fold(state, [*march(state, ATLAS), *meet(meeting, [], state)])
    assert state.npc_since[GONG] == T + 4 + ENCOUNTER_PAUSE
    assert march(replace(state, tick=T + 5), ATLAS) == [AgendaConcluded(npc_id=GONG, how=AgendaEnd.ARRIVED, tick=T + 5)]


def test_a_new_agenda_does_not_cut_a_pause_short() -> None:
    """裁决之后同一批里立下的新议程（江湖震动、新的一日）不得抹掉驻足：启程之刻取立议程之刻与驻足到之刻的较晚者。"""
    clash = _stakes(ZUO, XIN)
    state = world(issued(ZUO, CITY), moved(ZUO, PALACE, PATH, T + 4), EncounterBegan(encounter=clash.encounter), tick=T + 4)
    settled = settle_skirmish(clash, S.QUARREL, None, state, ATLAS)
    state = fold(state, settled)
    assert state.npc_since[ZUO] == T + 4 + SKIRMISH_PAUSE
    state = fold(state, admit([AgendaProposal(npc="左子穆", target="无量玉洞", intent="去洞里避一避")], state, ATLAS, "c"))
    assert state.agendas[ZUO].issued_tick == T + 4 and state.npc_since[ZUO] == T + 4 + SKIRMISH_PAUSE
    assert march(replace(state, tick=T + 4 + SKIRMISH_PAUSE + 3), ATLAS) == []
    assert march(replace(state, tick=T + 4 + SKIRMISH_PAUSE + 4), ATLAS)[0] == moved(ZUO, PATH, PALACE, T + 16)


@pytest.mark.parametrize("seed", range(60))
def test_random_marches_walk_real_roads_and_never_twice(seed: int) -> None:
    """随机议程 × 随机时光：每一跳都是一条路、从此刻所在出发、不越过此刻；同一刻再推一遍什么也不发生。"""
    rng = random.Random(seed)
    places = sorted(loc.id for loc in MAP.locations)
    state = world(at=rng.choice(places))
    for npc in rng.sample(list(ATLAS.core), k=rng.randint(1, len(ATLAS.core))):
        state = fold(state, [issued(npc, rng.choice(places), T + rng.randint(0, 6))])
    for _ in range(6):
        state = fold(state, [TimePassed(ticks=rng.randint(1, 12))])
        if rng.random() < 0.3:
            state = replace(state, location_id=rng.choice(places))
        events = march(state, ATLAS, player_arrived=rng.random() < 0.5)
        where = {cid: position(state, ATLAS, cid) for cid in ATLAS.characters}
        last: dict[str, int] = {}
        for e in events:
            if isinstance(e, NpcMoved):
                assert e.from_location_id == where[e.npc_id] and e.to_location_id in ATLAS.neighbors[e.from_location_id], e
                assert last.get(e.npc_id, -1) < e.tick <= state.tick, e
                assert e.witnessed == ("来到" if e.to_location_id == state.location_id
                                       else "离开" if e.from_location_id == state.location_id else ""), e
                where[e.npc_id], last[e.npc_id] = e.to_location_id, e.tick
            if isinstance(e, EncounterBegan):
                enc = e.encounter
                assert where[enc.npc_id] == enc.location_id, e
                assert (enc.other_id == PID and state.location_id == enc.location_id) or where[enc.other_id] == enc.location_id, e
        state = fold(state, events)
        assert march(state, ATLAS) == [], seed


# ============================================================
#  裁决层 —— 狭路相逢的区间与定案、撞见的收尾
# ============================================================
def _stakes(comer: str, holder: str, *events: DomainEvent) -> SkirmishStakes:
    return skirmish_stakes(encounter(EncounterKind.CROSS_PATHS, comer, holder, PATH, T + 4), world(*events, tick=T + 4), ATLAS)


S = SkirmishOutcome


@pytest.mark.parametrize(
    ("comer", "holder", "wounded", "canonical", "admissible"),
    [
        (DUAN, ZHONG, None, S.PASS, (S.QUARREL, S.PASS)),  # 两个仁厚者
        (ZUO, XIN, None, S.QUARREL, (S.BOTH_HURT, S.QUARREL, S.PASS)),  # 中庸势均力敌
        (LORD, ZUO, None, S.COMER_WINS, (S.COMER_WINS, S.BOTH_HURT, S.QUARREL)),  # 中庸差两档：强者胜
        (GONG, DUAN, None, S.COMER_WINS, (S.COMER_WINS, S.BOTH_HURT, S.QUARREL)),  # 有狠辣者即动手
        (GONG, DUAN, GONG, S.BOTH_HURT, (S.COMER_WINS, S.BOTH_HURT, S.HOLDER_WINS, S.QUARREL)),  # 带伤折一档：势均力敌
        (ZUO, CROC, None, S.HOLDER_WINS, (S.BOTH_HURT, S.HOLDER_WINS, S.QUARREL)),
        (CROC, GONG, None, S.COMER_WINS, (S.COMER_WINS, S.BOTH_HURT)),  # 两个狠辣者：没有口角
    ],
)
def test_crossing_paths_has_a_canonical_outcome_and_a_range(
    comer: str, holder: str, wounded: str | None, canonical: SkirmishOutcome, admissible: tuple[SkirmishOutcome, ...]
) -> None:
    hurt = (NpcWounded(npc_id=wounded, until_tick=T + 9),) if wounded else ()
    stakes_ = _stakes(comer, holder, *hurt)
    assert (stakes_.canonical, stakes_.admissible) == (canonical, admissible)
    assert stakes_.canonical in stakes_.admissible and (stakes_.comer.id, stakes_.holder.id) == (comer, holder)


def _rumor(text: str, radius: int) -> FactTokenSpawned:
    return FactTokenSpawned(token=FactToken(id=token_id(text, PATH, T + 4), text=text, subject_ids=(ZUO, XIN), origin_id=PATH,
                                            born_tick=T + 4, speed=1, radius=radius, reached=(PATH,)))


def test_a_quarrel_out_of_range_falls_back_to_the_canonical_ruling() -> None:
    state = world(issued(ZUO, CITY), tick=T + 4)
    clash = _stakes(ZUO, XIN)
    out = settle_skirmish(clash, S.HOLDER_WINS, None, state, ATLAS, by="地下城主")  # 不在区间里：作废
    assert out == [_rumor("左子穆与辛双清在山道起了口角", 1),
                   EncounterResolved(encounter_id=clash.encounter.id, kind=EncounterKind.CROSS_PATHS, location_id=PATH,
                                     npc_ids=(ZUO, XIN), outcome=S.QUARREL, by="规则", resume_tick=T + 4 + SKIRMISH_PAUSE)]
    assert settle_skirmish(clash, None, None, state, ATLAS, by="地下城主") == out
    calm = settle_skirmish(clash, S.PASS, "辛双清按剑不语", state, ATLAS, by="地下城主")
    assert calm == [FactEmerged(fact_id=fact_id("辛双清按剑不语"), text="辛双清按剑不语", subject_ids=(ZUO, XIN, PATH)),
                    EncounterResolved(encounter_id=clash.encounter.id, kind=EncounterKind.CROSS_PATHS, location_id=PATH,
                                      npc_ids=(ZUO, XIN), outcome=S.PASS, by="地下城主", resume_tick=T + 12)]


def test_a_fight_wounds_routes_and_sends_the_loser_home() -> None:
    state = world(issued(ZUO, CITY), at=PATH, tick=T + 4)
    base = _stakes(ZUO, XIN)
    won = replace(base, admissible=(S.HOLDER_WINS, S.BOTH_HURT, S.COMER_WINS), canonical=S.BOTH_HURT)
    out = settle_skirmish(won, S.HOLDER_WINS, "左子穆的剑穗被削去半截", state, ATLAS, by="地下城主")
    blood = trace_id(PATH, BLOOD.description, T + 4)
    assert [type(e).__name__ for e in out] == ["TraceLeft", "ActivityStarted", "NpcWounded", "AgendaConcluded", "NpcMoved",
                                               "FactTokenSpawned", "FactEmerged", "EncounterResolved"]
    assert isinstance(out[0], TraceLeft) and out[0].trace.id == blood and out[0].trace.decay_ticks == BLOOD.decay_ticks
    assert isinstance(out[1], ActivityStarted) and out[1].activity.id == activity_id(ActivityKind.FIGHT, PATH, (ZUO, XIN), T + 4)
    assert out[1].activity.trace_id == blood
    assert out[2:5] == [NpcWounded(npc_id=ZUO, until_tick=T + 4 + WOUND_TICKS, cause="山道一战"),
                        AgendaConcluded(npc_id=ZUO, how=AgendaEnd.ROUTED, tick=T + 4),
                        moved(ZUO, PATH, PALACE, T + 4, "离开")]  # 往家退一跳；玩家就在山道
    assert out[5] == _rumor("左子穆与辛双清在山道交手，左子穆落败", 3)
    assert isinstance(out[-1], EncounterResolved) and out[-1].witnessed and out[-1].by == "地下城主"
    after = fold(fold(state, [EncounterBegan(encounter=base.encounter)]), out)
    assert after.encounters == () and position(after, ATLAS, ZUO) == PALACE and ZUO not in after.agendas
    assert not free(after, ATLAS, ZUO) and free(replace(after, tick=T + 4 + WOUND_TICKS), ATLAS, ZUO)  # 带伤一日


def test_both_hurt_wounds_both_and_a_winner_at_home_stays() -> None:
    state = world(issued(ZUO, CITY), issued(XIN, CITY), tick=T + 4)
    clash = _stakes(ZUO, XIN)
    both = settle_skirmish(clash, S.BOTH_HURT, None, state, ATLAS)
    assert [e for e in both if isinstance(e, NpcWounded | AgendaConcluded | NpcMoved)] == [
        NpcWounded(npc_id=ZUO, until_tick=T + 4 + WOUND_TICKS, cause="山道一战"),
        AgendaConcluded(npc_id=ZUO, how=AgendaEnd.INTERRUPTED, tick=T + 4),
        NpcWounded(npc_id=XIN, until_tick=T + 4 + WOUND_TICKS, cause="山道一战"),
        AgendaConcluded(npc_id=XIN, how=AgendaEnd.INTERRUPTED, tick=T + 4)]  # 两败俱伤：各自原地
    assert isinstance(both[-1], EncounterResolved) and both[-1].by == "规则" and not both[-1].witnessed
    lost = settle_skirmish(replace(clash, admissible=(S.COMER_WINS,)), S.COMER_WINS, None, state, ATLAS)
    assert not any(isinstance(e, NpcMoved) for e in lost)  # 辛双清败在自家门口：无处可退


@pytest.mark.parametrize("fact", ["abc", "左子穆说了2句", "风", "风" * 41, "〈某人〉", "  "])
def test_a_flawed_fact_is_dropped(fact: str) -> None:
    out = settle_skirmish(_stakes(ZUO, XIN), S.PASS, fact, world(tick=T + 4), ATLAS, by="地下城主")
    assert not any(isinstance(e, FactEmerged) for e in out)


def test_meeting_the_player_closes_after_the_gated_events() -> None:
    meeting = encounter(EncounterKind.MEET_PLAYER, GONG, PID, PATH, T + 4)
    state = world(at=PATH, tick=T + 5)
    settled: list[DomainEvent] = [RenownChanged(delta=-1, cause="江湖传言")]
    assert meet(meeting, settled, state, by="地下城主") == [
        *settled,
        EncounterResolved(encounter_id=meeting.id, kind=EncounterKind.MEET_PLAYER, location_id=PATH, npc_ids=(GONG,),
                          by="地下城主", resume_tick=T + 5 + ENCOUNTER_PAUSE, witnessed=True)]
    away = meet(meeting, [], world(at=FAR, tick=T + 2), by="随便")[0]
    assert isinstance(away, EncounterResolved) and away.by == "规则" and not away.witnessed and away.resume_tick == T + 4 + ENCOUNTER_PAUSE


# ============================================================
#  战力洗牌 —— 带伤的人在快照里看得出，出手与暗取时境界折一档
# ============================================================
async def test_a_wounded_npc_fights_one_tier_down_but_parleys_the_same() -> None:
    from tests.world import WORLD

    hill = "loc:无量山"
    hurt = NpcWounded(npc_id="chr:左子穆", until_tick=T + 10, cause="无量山一战")
    state, snap = await look(WORLD, hill, hurt)
    zuo = next(c for c in snap.characters if c.id == "chr:左子穆")
    assert zuo.wounded and not next(c for c in snap.characters if c.id == "chr:龚光杰").wounded
    _, fresh = await look(WORLD, hill)
    healthy = fresh.characters
    assert not any(c.wounded for c in healthy)

    def at(snapshot: object, intent: PlayerIntent) -> object:
        return stakes(intent, state, snapshot)  # type: ignore[arg-type]

    attack = PlayerIntent(action_type=ActionType.ATTACK, target_entity="左子穆")
    struck, sound = at(snap, attack), at(fresh, attack)
    assert struck.defender_tier is Tier.NONE and sound.defender_tier is Tier.THIRD  # type: ignore[attr-defined]
    steal = PlayerIntent(action_type=ActionType.TAKE, target_entity="无量剑", approach=Approach.STEALTH)
    sly, wary = at(snap, steal), at(fresh, steal)
    assert isinstance(sly, CovertStakes) and isinstance(wary, CovertStakes) and sly.margin == wary.margin + 1
    talk = PlayerIntent(action_type=ActionType.TALK, target_entity="左子穆", approach=Approach.FORCE)
    assert isinstance(at(snap, talk), SocialStakes) and at(snap, talk) == at(fresh, talk)  # 交涉不折
    healed, later = await look(WORLD, hill, hurt, TimePassed(ticks=10))
    assert not next(c for c in later.characters if c.id == "chr:左子穆").wounded and healed.tick == T + 10
