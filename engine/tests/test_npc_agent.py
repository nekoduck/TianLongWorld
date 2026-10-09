"""
[INPUT]: 依赖 app.application.npc_agent 的 NpcDirector / LLMAgendaPlanner / NullPlanner / AgendaPlanner / LLMEncounterJudge / CanonicalJudge 与三份系统提示，
         依赖 app.application.briefs.agenda 的 agenda_brief / distance / veil，依赖 app.application.briefs 的 schema，依赖 app.config 的 Settings / LLMRole，
         依赖 app.domain 的 agenda / npc / heartbeat / clocks / resolution / events / aggregates，依赖 InMemoryWorldGraph（快照），依赖 tests/conftest 的 ScriptedLLM
[OUTPUT]: H-Agent 应用层的单测（ScriptedLLM 替身，不触网）——
          配置：AGENDA 职责与它的模型 / 思考档位回退、议程预算的边界、npc_agenda 缺省开；
          议程简报：只给能领议程的核心 NPC、局部认知（只有传到他所在之处的消息）、开篇的仇敌与羁绊（后文结的仇不算）、恩怨、可去之处与路程、
          不露任何 id、不写玩家身在何处；契约的 npc / target 枚举；distance 的路程说法；
          议程规划：宽容解析（围栏、顶层数组、多余字段、意图首尾标点、坏条目丢弃）、不合契约重采样、LLMError / 超时 / 两次不合契约皆为 []；
          NpcDirector.plan 只在规划时机调用（初临江湖 → 同日不再规划）、经 admit 闸门、没人能领议程不调大模型、规划者抛错照记 AgendaPlanned、
          NullPlanner 一条也不记、死者不规划；
          来意的迷雾 veil（叫不出名的地方抹成「别处」，问过路 / 到过 / 名胜照名写，认得的长名不被短名吃掉、处所简称同样抹掉）；
          撞见：判官拿到地下城主结果已定之事的系统提示 + 撞见说明、<encounter> 写来者与来意（过 veil）、契约即 briefs.schema；推演过闸（时钟挂在来者身上、事实入账、
          此景之外的原著名字被预筛）→ EncounterResolved(地下城主)；判官限额（一回合一场，余者确定性）；来者不在眼前或玩家已不在此地即确定性；
          危机坍缩被迫脱身；狭路相逢：简报写来者 / 在此者 / 仇怨 / 可裁结局与确定性裁决，结局认中文与英文名、区间外即确定性、细节过筛子与名录预筛、失灵即确定性；
          CanonicalJudge 与 NullPlanner 零调用
[POS]: tests 的 H-Agent 神经层与编排基线：大模型只在宏观层与裁决层发言，失灵一律退回确定性裁决——这里逐条钉死
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from functools import reduce
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.application import briefs
from app.application.briefs.agenda import DESTINATIONS_MAX, Brief, agenda_brief, distance, veil
from app.application.npc_agent import (
    AGENDA_SYSTEM,
    MEET_SYSTEM,
    SKIRMISH_SYSTEM,
    AgendaPlanner,
    CanonicalJudge,
    LLMAgendaPlanner,
    LLMEncounterJudge,
    NpcDirector,
    NullPlanner,
)
from app.application.ports import JsonSchema, LLMClient
from app.application.resolution_agent import GM_SYSTEMS
from app.config import LLMRole, Settings
from app.domain.agenda import Encounter, EncounterKind, NpcAgenda, SkirmishOutcome, encounter_id
from app.domain.aggregates import Player, PlayerState, evolve
from app.domain.ambient import FactToken, token_id
from app.domain.approach import Route
from app.domain.clocks import ClockKind, NarrativeClock, clock_id
from app.domain.commands import SPAWN_TICK
from app.domain.events import (
    AgendaIssued,
    AgendaPlanned,
    ClockCollapsed,
    ClockStarted,
    DomainEvent,
    EncounterBegan,
    EncounterResolved,
    EventEnvelope,
    FactEmerged,
    FactTokenSpawned,
    HealthChanged,
    Moved,
    NpcMoved,
    NpcWounded,
    PlacesLearned,
    PlayerDied,
    PlayerSpawned,
    RelationChanged,
    TimePassed,
)
from app.domain.heartbeat import Atlas
from app.domain.lore import Persona
from app.domain.models import (
    Attitude,
    Character,
    CharacterRelation,
    Disposition,
    Era,
    Location,
    RelationKind,
    Tier,
    WorldBlueprint,
)
from app.domain.npc import AgendaProposal, skirmish_stakes
from app.domain.resolution import envelope_of
from app.domain.snapshot import LocalSnapshot
from app.errors import LLMError
from app.infrastructure.persistence.memory_graph import InMemoryWorldGraph
from tests.conftest import ScriptedLLM

PID = "ply:agent"
T = SPAWN_TICK
PALACE, PATH, CITY, CAVE, FAR = "loc:剑湖宫", "loc:山道", "loc:大理城", "loc:无量玉洞", "loc:无锡城"
ZUO, XIN, GONG, DUAN, CROC, QIAO = "chr:左子穆", "chr:辛双清", "chr:龚光杰", "chr:段誉", "chr:南海鳄神", "chr:乔峰"


def _place(lid: str, exits: dict[str, str]) -> Location:
    return Location(id=lid, name=lid.removeprefix("loc:"), region="大理", description="", exits=exits)


def _persona(cid: str, worry: str, likes: tuple[str, ...] = (), dislikes: tuple[str, ...] = ()) -> Persona:
    return Persona(character_id=cid, worry=worry, likes=likes, dislikes=dislikes, sources=("chunk:1",))


MAP = WorldBlueprint(
    locations=(
        _place(PALACE, {"下山": PATH, "崖下": CAVE}),
        _place(PATH, {"上山": PALACE, "南下": CITY}),
        _place(CITY, {"北上": PATH}),
        _place(CAVE, {"攀上": PALACE}),
        _place(FAR, {}),
    ),
    characters=(
        Character(id=ZUO, true_name="左子穆", faction="无量剑东宗", tier=Tier.THIRD, location_id=PALACE),
        Character(id=XIN, true_name="辛双清", faction="无量剑西宗", tier=Tier.THIRD, location_id=PATH),
        Character(id=GONG, true_name="龚光杰", faction="无量剑东宗", tier=Tier.THIRD, disposition=Disposition.RUTHLESS,
                  location_id=PALACE),
        Character(id=DUAN, true_name="段誉", tier=Tier.NONE, disposition=Disposition.MERCIFUL, location_id=CITY),
        Character(id=CROC, true_name="南海鳄神", titles=("岳老三",), tier=Tier.FIRST, disposition=Disposition.RUTHLESS,
                  location_id=CITY),
        Character(id=QIAO, true_name="乔峰", tier=Tier.PEERLESS, location_id=FAR),
    ),
    relations=(
        CharacterRelation(source_id=ZUO, target_id=XIN, kind=RelationKind.ENEMY, note="东西宗相争"),
        CharacterRelation(source_id=ZUO, target_id=GONG, kind=RelationKind.MENTOR, note="师徒"),
        CharacterRelation(source_id=GONG, target_id=DUAN, kind=RelationKind.ENEMY, era=Era.LATER, note="后来结仇"),
    ),
    personas=(
        _persona(ZUO, "西宗来夺剑湖宫", ("剑法",), ("西宗",)),
        _persona(XIN, "东宗占着剑湖宫"),
        _persona(GONG, "师父偏心"),
        _persona(DUAN, "不想学武"),
    ),
)
ATLAS = Atlas.of(MAP)
PERSONAS = {p.character_id: p for p in MAP.personas}
CANON = frozenset(n for c in MAP.characters for n in c.names) | frozenset(loc.name for loc in MAP.locations)


def world(*events: DomainEvent, at: str = CAVE) -> PlayerState:
    state = Player.replay([PlayerSpawned(player_id=PID, name="阿星", location_id=at), *events])
    assert state is not None
    return state


async def look(at: str, *events: DomainEvent) -> tuple[PlayerState, LocalSnapshot]:
    """在 MAP 上投胎于 at、重放 events：聚合根的状态与内存图谱的快照（与回合编排同一口径）。"""
    history = [PlayerSpawned(player_id=PID, name="阿星", location_id=at), *events]
    envelopes = [
        EventEnvelope(stream_id=PID, version=i, event_id=uuid4(), recorded_at=datetime.now(UTC), event=e)
        for i, e in enumerate(history, start=1)
    ]
    graph = InMemoryWorldGraph()
    await graph.seed(MAP)
    await graph.project(PID, envelopes)
    return Player.from_history(PID, envelopes).state, await graph.local_snapshot(PID)


def director(llm: LLMClient | None = None, *, planner: AgendaPlanner | None = None) -> NpcDirector:
    judge = LLMEncounterJudge(llm, budget=5, canon_names=CANON) if llm is not None else CanonicalJudge()
    return NpcDirector(planner or NullPlanner(), judge, ATLAS, MAP.personas, relations=MAP.relations)


def issued(npc: str, target: str, intent: str = "去大理城探听西宗动静", tick: int = T) -> AgendaIssued:
    return AgendaIssued(agenda=NpcAgenda(npc_id=npc, target_id=target, intent=intent, priority=2, issued_tick=tick))


def began(kind: EncounterKind, npc: str, other: str, at: str, tick: int) -> EncounterBegan:
    return EncounterBegan(encounter=Encounter(
        id=encounter_id(kind, npc, other, at, tick), kind=kind, npc_id=npc, other_id=other, location_id=at, tick=tick))


def rumor(text: str, at: str, *reached: str) -> FactTokenSpawned:
    return FactTokenSpawned(token=FactToken(id=token_id(text, at, T), text=text, origin_id=at, born_tick=T, speed=1,
                                            radius=3, reached=(at, *reached)))


def gm(**fields: Any) -> str:
    """一份结果已定之事的推演（撞见）：缺省是暗流、什么也不动。"""
    return json.dumps({"collision": "来者与你素无瓜葛", "severity": "暗流", "cost": "不欠", "convergence": "无事",
                       "deltas": [], "clock_mutations": [], "new_facts": [], "action_trigger": "无", **fields},
                      ensure_ascii=False)


class Slow(LLMClient):
    async def complete(self, system: str, user: str, schema: JsonSchema | None = None) -> str:
        await asyncio.sleep(5)
        return "{}"


class Boom(AgendaPlanner):
    async def plan(self, brief: str, schema: JsonSchema) -> list[AgendaProposal]:
        raise RuntimeError("规划者自己炸了")


# ============================================================
#  配置
# ============================================================
def test_the_agenda_role_has_its_own_model_thinking_and_budget() -> None:
    assert LLMRole.AGENDA.value == "agenda" and list(LLMRole)[-1] is LLMRole.AGENDA
    base = Settings(_env_file=None, llm_model="flash", llm_thinking="low")
    assert base.npc_agenda is True and base.llm_agenda_budget == 10.0
    assert base.llm_profile(LLMRole.AGENDA) == ("flash", "low")  # 留空退回缺省档
    own = Settings(_env_file=None, llm_model="flash", llm_agenda_model="pro", llm_agenda_thinking="medium")
    assert own.llm_profile(LLMRole.AGENDA) == ("pro", "medium")
    for bad in (0.5, 61.0):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, llm_agenda_budget=bad)


# ============================================================
#  议程简报 —— 局部认知、开篇恩怨、可去之处，不露 id
# ============================================================
def _blocks(text: str) -> dict[str, str]:
    parts = text.split("<npc>")[1:]
    return {p.split("｜", 1)[0].strip(): p.split("</npc>")[0] for p in parts}


def test_the_agenda_brief_is_local_knowledge_without_ids() -> None:
    state = world(
        rumor("阿星在剑湖宫与龚光杰交手", PALACE),
        rumor("大理城里来了个书呆子", CITY),
        RelationChanged(character_id=GONG, attitude=Attitude.HOSTILE, cause="你当众折了他的剑", basis="交手"),
        issued(GONG, CITY, "去大理城寻人晦气"),
    )
    brief = agenda_brief(state, ATLAS, (ZUO, XIN, GONG), PERSONAS, MAP.relations, "初临江湖")
    text = brief.text
    for prefix in ("chr:", "loc:", "tok:", "enc:", "ply:"):
        assert prefix not in text
    assert "<cause>初临江湖</cause>" in text and "<time>第一日·辰正｜白昼</time>" in text
    assert "<player>阿星（" in text and "无量玉洞" not in text.split("<npc>")[0]  # 玩家身在何处不写
    blocks = _blocks(text)
    assert set(blocks) == {"左子穆", "辛双清", "龚光杰"}  # 只写调用方给的几位
    zuo, xin, gong = blocks["左子穆"], blocks["辛双清"], blocks["龚光杰"]
    assert "阿星在剑湖宫与龚光杰交手" in zuo and "书呆子" not in zuo  # 只知传到自己所在之处的消息
    assert "没有任何消息传到此地" in xin and "交手" not in xin
    assert "执念：西宗来夺剑湖宫" in zuo and "好：剑法｜恶：西宗" in zuo and "此刻所在：剑湖宫（居所）" in zuo
    assert "开篇仇敌：辛双清（东西宗相争）｜羁绊：龚光杰（师徒）" in zuo
    assert "开篇仇敌：无" in gong and "段誉" not in gong.split("可去之处")[0]  # 后来才结的仇不是 T=0 的事实
    assert "对阿星：敌视（恩怨：你当众折了他的剑）" in gong and "对阿星：漠然" in zuo
    assert "眼下议程：前往大理城——去大理城寻人晦气（要紧）" in gong and "眼下议程：无" in zuo
    assert "可去之处：山道（一个时辰内）、无量玉洞（一个时辰内）、大理城（一个时辰内）" in zuo  # 三跳以内，按（跳数, id）
    agendas = brief.schema["properties"]["agendas"]
    item = agendas["items"]["properties"]
    assert item["npc"]["enum"] == ["左子穆", "辛双清", "龚光杰"] and agendas["maxItems"] == 3
    assert item["target"]["enum"][-1] == "留守" and set(item["target"]["enum"]) == {"剑湖宫", "山道", "大理城", "无量玉洞", "留守"}
    assert item["intent"]["maxLength"] == 16 and (item["priority"]["minimum"], item["priority"]["maximum"]) == (1, 3)
    assert brief.names == ("左子穆", "辛双清", "龚光杰")


def test_the_agenda_brief_lists_only_the_nearest_destinations() -> None:
    spokes = tuple(_place(f"loc:岔路{i:02d}", {"回": "loc:渡口"}) for i in range(30))
    hub = _place("loc:渡口", {f"去{i:02d}": s.id for i, s in enumerate(spokes)})
    bp = WorldBlueprint(locations=(hub, *spokes), personas=(_persona("chr:艄公", "江上风浪大"),),
                        characters=(Character(id="chr:艄公", true_name="艄公", location_id="loc:渡口"),))
    atlas = Atlas.of(bp)
    brief = agenda_brief(world(at="loc:渡口"), atlas, atlas.core, {p.character_id: p for p in bp.personas}, (), "新的一日")
    targets = brief.schema["properties"]["agendas"]["items"]["properties"]["target"]["enum"]
    assert len(targets) == DESTINATIONS_MAX + 1 and "岔路29" not in brief.text  # 载荷有界：三跳以内的去处再多，简报只列最近的
    assert "别处的事，此人一无所知" in brief.text


def test_distance_speaks_in_shichen_and_days() -> None:
    assert [distance(t) for t in (1, 8, 9, 24, 47, 48, 95, 96, 192, 288)] == [
        "一个时辰内", "一个时辰内", "约两个时辰", "约三个时辰", "约六个时辰", "约半日", "约半日", "约一日", "约两日", "约三日"]


# ============================================================
#  议程规划者 —— 宽容解析、重采样、失灵即 []
# ============================================================
SCHEMA: JsonSchema = {"type": "object"}


async def test_the_llm_planner_reads_tolerantly() -> None:
    reply = '```json\n{"agendas": [{"npc": "左子穆", "target": "大理城", "intent": "「去大理城探听西宗动静。」", "priority": "3", "why": "多余"}, ' \
            '{"npc": "辛双清"}, "胡言", {"npc": "龚光杰", "target": "留守", "intent": "", "priority": 9}]}\n```'
    llm = ScriptedLLM(reply)
    got = await LLMAgendaPlanner(llm, budget=5).plan("简报", SCHEMA)
    assert [(p.npc, p.target, p.intent, p.priority) for p in got] == [
        ("左子穆", "大理城", "去大理城探听西宗动静", 3), ("龚光杰", "留守", "", 3)]
    assert llm.calls == [(AGENDA_SYSTEM, "简报", SCHEMA)]
    bare = await LLMAgendaPlanner(ScriptedLLM('[{"npc": "左子穆", "target": "山道", "intent": "下山走走"}]')).plan("b", SCHEMA)
    assert [(p.npc, p.target, p.priority) for p in bare] == [("左子穆", "山道", 2)]  # 顶层直接是数组、priority 缺省要紧


async def test_the_llm_planner_resamples_then_gives_up_quietly() -> None:
    llm = ScriptedLLM("不是 JSON", '{"agendas": []}')
    assert await LLMAgendaPlanner(llm, budget=5).plan("b", SCHEMA) == [] and len(llm.calls) == 2
    twice = ScriptedLLM("{坏", '{"agendas": "也不是数组"}', '{"agendas": [{"npc": "左子穆", "target": "山道", "intent": "下山"}]}')
    assert await LLMAgendaPlanner(twice, budget=5).plan("b", SCHEMA) == [] and len(twice.calls) == 2  # 只重采样一次
    broke = ScriptedLLM(LLMError("欠费", retryable=False))  # type: ignore[arg-type]
    assert await LLMAgendaPlanner(broke, budget=5).plan("b", SCHEMA) == []
    assert await LLMAgendaPlanner(Slow(), budget=0.05).plan("b", SCHEMA) == []


# ============================================================
#  NpcDirector.plan —— 只在规划时机、经闸门、失灵照记一轮
# ============================================================
async def test_the_director_plans_only_when_due_and_through_the_gate() -> None:
    reply = json.dumps({"agendas": [
        {"npc": "左子穆", "target": "大理城", "intent": "去大理城探听西宗动静", "priority": 2},
        {"npc": "乔峰", "target": "山道", "intent": "下山看看", "priority": 3},  # 不是核心 NPC：闸门不认
        {"npc": "辛双清", "target": "无锡城", "intent": "远走他乡", "priority": 3},  # 去处越界
        {"npc": "龚光杰", "target": "留守", "intent": "", "priority": 1},
    ]}, ensure_ascii=False)
    llm = ScriptedLLM(reply)
    state = world()
    out = await director(planner=LLMAgendaPlanner(llm, budget=5)).plan(state, [])
    assert out == [AgendaPlanned(tick=T, cause="初临江湖"), issued(ZUO, CITY)]
    system, user, schema = llm.calls[0]
    assert schema is not None and system == AGENDA_SYSTEM and "<cause>初临江湖</cause>" in user and "乔峰" not in user and "段誉" in user
    assert schema["properties"]["agendas"]["items"]["properties"]["npc"]["enum"] == ["左子穆", "段誉", "辛双清", "龚光杰"]
    later = reduce(evolve, out, state)
    assert await director(planner=LLMAgendaPlanner(llm, budget=5)).plan(later, []) == [] and len(llm.calls) == 1  # 同一日不再规划


async def test_the_director_records_a_round_even_when_no_one_can_go() -> None:
    planned = [AgendaPlanned(tick=T, cause="初临江湖")]
    assert await director(planner=NullPlanner()).plan(world(), []) == []  # 不立议程的世界不记空转
    assert await director(planner=Boom()).plan(world(), []) == planned  # 规划者抛错也照记
    broke = ScriptedLLM(LLMError("限流", retryable=True))  # type: ignore[arg-type]
    assert await director(planner=LLMAgendaPlanner(broke, budget=5)).plan(world(), []) == planned
    hurt = world(*(NpcWounded(npc_id=c, until_tick=T + 96) for c in ATLAS.core))
    idle = ScriptedLLM()
    assert await director(planner=LLMAgendaPlanner(idle, budget=5)).plan(hurt, []) == planned and idle.calls == []
    dead = world(PlayerDied(cause="找死"))
    assert await director(planner=Boom()).plan(dead, []) == []
    bare = NpcDirector(NullPlanner(), CanonicalJudge(), Atlas.of(WorldBlueprint(locations=MAP.locations)))
    assert await bare.plan(world(), []) == []  # 没有核心 NPC 永不规划


# ============================================================
#  撞见 —— 地下城主在结果已定的物理边界里推演余波
# ============================================================
MEETING = (issued(ZUO, CITY, tick=T), NpcMoved(npc_id=ZUO, from_location_id=PALACE, to_location_id=PATH, tick=T + 4,
                                                witnessed="来到"),
           TimePassed(ticks=4), began(EncounterKind.MEET_PLAYER, ZUO, PID, PATH, T + 4))


async def test_a_meeting_is_judged_inside_the_fixed_envelope() -> None:
    state, snap = await look(PATH, *MEETING)
    assert snap.character(ZUO) is not None and len(state.encounters) == 1
    reply = gm(clock_mutations=[{"op": "新建", "clock": "左子穆的戒心", "kind": "疑心", "anchor": "左子穆", "maximum": 4,
                                 "steps": 1, "consequence": "他认定你是西宗的探子"}],
               new_facts=["左子穆按剑打量你，眉头微皱", "乔峰在远处冷笑"])
    llm = ScriptedLLM(reply)
    out = await director(llm).settle_encounters(state, snap)
    assert [type(e) for e in out] == [ClockStarted, FactEmerged, EncounterResolved]
    started, fact, done = out
    assert isinstance(started, ClockStarted) and started.clock.anchor_id == ZUO and started.clock.kind is ClockKind.SUSPICION
    assert isinstance(fact, FactEmerged) and fact.text == "左子穆按剑打量你，眉头微皱"  # 此景之外的乔峰被预筛
    assert isinstance(done, EncounterResolved) and (done.by, done.npc_ids, done.witnessed, done.resume_tick) == (
        "地下城主", (ZUO,), True, T + 8)
    system, user, schema = llm.calls[0]
    assert system == MEET_SYSTEM and system.startswith(GM_SYSTEMS[Route.FIXED]) and "撞见" in system
    assert "<encounter>" in user and "<intent>" not in user and "<player_input" not in user
    assert "来者：左子穆｜来意：去别处探听西宗动静（要紧；途经此地，正往别处去）｜对你漠然" in user  # 大理城还在迷雾里
    assert "<physics>" in user and "路线：结果已定｜对象：左子穆" in user
    assert schema == briefs.schema(envelope_of(None, state, snap, target_id=ZUO), snap)
    for prefix in ("chr:", "loc:", "enc:", "ply:"):
        assert prefix not in user
    folded = reduce(evolve, out, state)
    assert folded.encounters == () and folded.npc_since[ZUO] == T + 8


async def test_errands_keep_the_fog_over_places_you_cannot_name() -> None:
    """来意是大模型写的，常带目标地名：玩家叫不出名的地方抹成「别处」，问过路、到过、名胜都照名写；认得的长名不被短名吃掉。"""
    state, snap = await look(PATH)
    assert veil("去大理城探听西宗动静", state, snap, ATLAS) == "去别处探听西宗动静"
    assert veil("守着山道，再去剑湖宫", state, snap, ATLAS) == "守着山道，再去别处"  # 此地照名写，山道上得去的剑湖宫也还在迷雾里
    told, told_snap = await look(PATH, PlacesLearned(location_ids=(CITY,), source_id=XIN))
    assert veil("去大理城探听西宗动静", told, told_snap, ATLAS) == "去大理城探听西宗动静"
    famous = replace(ATLAS, renowned=frozenset({FAR}))
    assert veil("远赴无锡城", state, snap, famous) == "远赴无锡城"
    nested = Atlas.of(MAP.model_copy(update={"locations": (*MAP.locations, _place("loc:大理城·镇南王府", {}))}))
    assert veil("去镇南王府", told, told_snap, nested) == "去别处" and veil("在大理城等", told, told_snap, nested) == "在大理城等"


async def test_one_judge_a_turn_and_the_rest_by_rule() -> None:
    state, snap = await look(
        PATH, issued(GONG, CITY, "去大理城寻人晦气"),
        NpcMoved(npc_id=GONG, from_location_id=PALACE, to_location_id=PATH, tick=T + 4, witnessed="来到"),
        NpcMoved(npc_id=XIN, from_location_id=PATH, to_location_id=PALACE, tick=T + 4, witnessed="离开"), TimePassed(ticks=4),
        began(EncounterKind.MEET_PLAYER, GONG, PID, PATH, T + 4), began(EncounterKind.CROSS_PATHS, XIN, ZUO, PALACE, T + 4),
    )
    llm = ScriptedLLM(gm(new_facts=["龚光杰斜眼瞥你，嘴角一撇"]))
    out = await director(llm).settle_encounters(state, snap)
    resolved = [e for e in out if isinstance(e, EncounterResolved)]
    assert [(e.kind, e.by) for e in resolved] == [(EncounterKind.MEET_PLAYER, "地下城主"), (EncounterKind.CROSS_PATHS, "规则")]
    assert len(llm.calls) == 1  # 狭路相逢已无限额：确定性裁决，一个大模型也不调
    assert resolved[1].outcome is SkirmishOutcome.QUARREL  # 同境界、都是中庸：口角
    both = ScriptedLLM(gm(), json.dumps({"reasoning": "势均力敌", "outcome": "相安无事", "fact": ""}, ensure_ascii=False))
    two = await director(both).settle_encounters(state, snap, dm_quota=2)
    assert [e.by for e in two if isinstance(e, EncounterResolved)] == ["地下城主", "地下城主"] and len(both.calls) == 2
    none = ScriptedLLM()
    zero = await director(none).settle_encounters(state, snap, dm_quota=0)
    assert [e.by for e in zero if isinstance(e, EncounterResolved)] == ["规则", "规则"] and none.calls == []


async def test_a_meeting_falls_back_to_rule_when_the_comer_is_not_in_sight() -> None:
    away = (*MEETING, Moved(from_location_id=PATH, to_location_id=CITY, exit_label="南下"))
    state, snap = await look(PATH, *away)  # 玩家已走开：撞见只记驻足
    llm = ScriptedLLM()
    out = await director(llm).settle_encounters(state, snap)
    assert [type(e) for e in out] == [EncounterResolved] and llm.calls == []
    assert isinstance(out[0], EncounterResolved) and (out[0].by, out[0].witnessed) == ("规则", False)
    state, snap = await look(PATH, *MEETING)
    failing = ScriptedLLM(LLMError("断网", retryable=True))  # type: ignore[arg-type]
    out = await director(failing).settle_encounters(state, snap)
    assert [(type(e), getattr(e, "by", None)) for e in out] == [(EncounterResolved, "规则")]
    assert await director().settle_encounters(state, snap) == out  # CanonicalJudge 同口径
    assert await director(ScriptedLLM()).settle_encounters(replace(state, alive=False), snap) == []  # 死者没有心跳


async def test_a_meeting_whose_peril_collapses_forces_you_to_flee() -> None:
    peril = NarrativeClock(id=clock_id(PID, "山道塌方"), name="山道塌方", kind=ClockKind.PERIL, anchor_id=PID, progress=3,
                           maximum=4, consequence="山石滚落")
    state, snap = await look(PATH, ClockStarted(clock=peril), *MEETING)
    llm = ScriptedLLM(gm(severity="爆炸", clock_mutations=[{"op": "推进", "clock": "山道塌方", "steps": 1}]))
    out = await director(llm).settle_encounters(state, snap)
    kinds = [type(e) for e in out]
    assert ClockCollapsed in kinds and HealthChanged in kinds and kinds[-1] is EncounterResolved
    fled = next(e for e in out if isinstance(e, Moved))
    assert fled.fleeing and fled.from_location_id == PATH
    assert reduce(evolve, out, state).hp > 0  # 危机坍缩留一口气


# ============================================================
#  狭路相逢 —— 判官只在可裁区间里挑结局与一条细节
# ============================================================
CROSSING = (NpcMoved(npc_id=XIN, from_location_id=PATH, to_location_id=PALACE, tick=T + 4), TimePassed(ticks=4),
            began(EncounterKind.CROSS_PATHS, XIN, ZUO, PALACE, T + 4))


def skirmish(outcome: str, fact: str = "") -> str:
    return json.dumps({"reasoning": "同为三流，各不相让", "outcome": outcome, "fact": fact}, ensure_ascii=False)


async def test_a_skirmish_is_picked_inside_the_admissible_range() -> None:
    state, snap = await look(CAVE, *CROSSING)
    llm = ScriptedLLM(skirmish("两败俱伤", "辛双清剑尖一挑，削落左子穆半幅衣袖"))
    out = await director(llm).settle_encounters(state, snap)
    done = out[-1]
    assert isinstance(done, EncounterResolved) and (done.outcome, done.by, done.npc_ids, done.witnessed) == (
        SkirmishOutcome.BOTH_HURT, "地下城主", (XIN, ZUO), False)
    assert {e.npc_id for e in out if isinstance(e, NpcWounded)} == {XIN, ZUO}
    assert [e.text for e in out if isinstance(e, FactEmerged)] == ["辛双清剑尖一挑，削落左子穆半幅衣袖"]
    system, user, schema = llm.calls[0]
    assert schema is not None and system == SKIRMISH_SYSTEM
    assert "来者：辛双清｜无量剑西宗｜境界三流｜性情中庸｜执念：东宗占着剑湖宫" in user and "在此者：左子穆" in user
    assert "<feud>开篇仇敌：东西宗相争</feud>" in user and "- 口角：只是言语交锋、互相叫骂，没有动手｜← 确定性裁决" in user
    assert "<encounter>\n狭路相逢｜剑湖宫｜第一日·巳初｜白昼\n</encounter>" in user
    assert schema["properties"]["outcome"]["enum"] == ["两败俱伤", "口角", "相安无事"]
    assert list(schema["properties"]) == ["reasoning", "outcome", "fact"]
    for prefix in ("chr:", "loc:", "enc:", "ply:"):
        assert prefix not in user


@pytest.mark.parametrize(("reply", "outcome", "by", "fact"), [
    (skirmish("BOTH_HURT"), SkirmishOutcome.BOTH_HURT, "地下城主", None),  # 枚举认英文名
    (skirmish("来者胜", "辛双清扬长而去"), SkirmishOutcome.QUARREL, "规则", None),  # 区间外：确定性裁决，细节一并作废
    (skirmish("口角", "辛双清把左子穆制住"), SkirmishOutcome.QUARREL, "地下城主", None),  # 细节夹带状态变化
    (skirmish("口角", "乔峰的名头压得两人都不敢动手"), SkirmishOutcome.QUARREL, "地下城主", None),  # 点了此景之外的原著名字
    (skirmish("口角", "两人隔着3丈对骂"), SkirmishOutcome.QUARREL, "地下城主", None),  # 阿拉伯数字不收
    (skirmish("相安无事", "左子穆冷哼一声，拂袖不语"), SkirmishOutcome.PASS, "地下城主", "左子穆冷哼一声，拂袖不语"),
])
async def test_a_skirmish_reply_is_read_tolerantly_and_screened(
    reply: str, outcome: SkirmishOutcome, by: str, fact: str | None
) -> None:
    state, snap = await look(CAVE, *CROSSING)
    out = await director(ScriptedLLM(reply)).settle_encounters(state, snap)
    done = out[-1]
    assert isinstance(done, EncounterResolved) and (done.outcome, done.by) == (outcome, by)
    assert [e.text for e in out if isinstance(e, FactEmerged)] == ([fact] if fact else [])


async def test_a_skirmish_falls_back_to_rule_when_the_judge_fails() -> None:
    state, snap = await look(CAVE, *CROSSING)
    for llm in (ScriptedLLM(LLMError("欠费", retryable=False)), ScriptedLLM("胡言", "{乱")):  # type: ignore[arg-type]
        out = await director(llm).settle_encounters(state, snap)
        done = out[-1]
        assert isinstance(done, EncounterResolved) and (done.outcome, done.by) == (SkirmishOutcome.QUARREL, "规则")
    slow = NpcDirector(NullPlanner(), LLMEncounterJudge(Slow(), budget=0.05), ATLAS, PERSONAS)
    out = await slow.settle_encounters(state, snap)
    assert isinstance(out[-1], EncounterResolved) and out[-1].by == "规则"
    rule = await director().settle_encounters(state, snap)
    assert isinstance(rule[-1], EncounterResolved) and rule[-1].outcome is SkirmishOutcome.QUARREL


async def test_the_canonical_judge_and_null_planner_never_answer() -> None:
    brief = Brief("简报", {"type": "object"})
    state, snap = await look(CAVE, *CROSSING)
    assert await CanonicalJudge().meet(envelope_of(None, state, snap), brief) is None
    stakes = skirmish_stakes(state.encounters[0], state, ATLAS)
    assert await CanonicalJudge().skirmish(stakes, brief) == (None, None)
    assert await NullPlanner().plan("b", {}) == []
