"""
[INPUT]: 依赖 app.domain 的 models / lore / intent / outcomes / events / aggregates / progression / snapshot / clocks，依赖 tests/world 的 WORLD
[OUTPUT]: 本体完整性、事件不可变与 JSONB 往返及旧账上抛、渐进式状态的折算、聚合根纯函数折叠（含焦点与恩怨缘由）的单测；
          P1 词汇：人情阶梯 rank / step、关系 era 与物性缺省、掌故闸门（人设与见闻的悬空引用、知情人须与主体有涉、unlock 须落在边上、字数与出处、
          主体与知情人不得重复、关系边只认开篇、后来才到场的物品不作主体与险物目标）、
          手段 / 所图 / 话题与 USE、四种新事件往返、旧账缺新字段照读、HealthChanged 上抛（调息疗伤 → rest）与焦点只因调息而不新鲜、
          快照新视图的缺省值与固定排序；P1 阶段 B 的折叠：已知见闻、用掉之物离开行囊（易手覆盖照旧）、交涉与暗取进焦点 / 近来手段 / 尝试次数 / 心事线索、
          物品最初的来路 taken_from（转手再拿回来不改）；ActionFailed.target_id / subject_id 与 Parleyed.subject_id 往返且旧账缺省为 None；
          语义物理引擎：六种新事件（ClockStarted / ClockAdvanced / ClockCollapsed / ClockCleared / FactEmerged / RenownChanged）往返与边界、
          NarrativeClock 的闸门（满格不悬着、阈值三档、id 与挂处的前缀）、时钟按 id 折叠且折叠从不替它坍缩（推进钳在阈值减一、回退钳在零、同 id 再挂即覆盖）、
          微观事实新者在前至多 EMERGED_MAX 条且重提即提到最前、名望钳在 ±100 并折算为六档说法、快照的时钟与事实按 id 排序且挂处与主体进名称表
[POS]: tests 的领域地基：蓝图是最后一道闸门（悬空引用 / 根基成环 / 人物主键不是本名 / 关系边自环或一对人两条边一律拒收，下落不明的物品合法存在）；
       "当前状态 = reduce(evolve, 历史)"——不查状态表，只凭事件流重算位置、行囊、火候与气血；拿到秘籍不等于学会
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.aggregates import EMERGED_MAX, Player, evolve
from app.domain.clocks import ClockKind, NarrativeClock, clock_id
from app.domain.combat import CombatOutcome
from app.domain.events import (
    EVENT_ADAPTER,
    LEGACY_MASTERY_POINTS,
    ActionFailed,
    ClockAdvanced,
    ClockCleared,
    ClockCollapsed,
    ClockStarted,
    Conversed,
    EventEnvelope,
    FactEmerged,
    FactLearned,
    HealthChanged,
    ItemConsumed,
    ItemTransferred,
    Maneuvered,
    Moved,
    Parleyed,
    PlayerDied,
    PlayerSpawned,
    RelationChanged,
    RenownChanged,
    SkillExecuted,
    SkillPracticed,
    decode_event,
)
from app.domain.intent import ActionType, Aim, Approach, PlayerIntent
from app.domain.lore import Fact, FactUnlock, Persona
from app.domain.models import (
    Acquisition,
    Attitude,
    Character,
    CharacterRelation,
    Disposition,
    Era,
    Item,
    ItemUse,
    Location,
    MartialArt,
    Practice,
    Provenance,
    RelationKind,
    Tier,
    Transmission,
    WorldBlueprint,
    prerequisite_cycle,
)
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.progression import (
    MAX_HP,
    RENOWN_MAX,
    Guidance,
    Mastery,
    Renown,
    Vitality,
    aptitude_for,
    effective_tier,
    gain,
    mastery_of,
    renown,
    vitality,
)
from app.domain.snapshot import (
    BondView,
    CharacterView,
    EmergedView,
    ExitView,
    FactView,
    ItemView,
    LocalSnapshot,
    LocationView,
    PersonaView,
)
from app.errors import PlayerDeadError, UnknownPlayerError
from tests.world import WORLD

PID = "ply:test"


# ============================================================
#  本体
# ============================================================
def test_tier_is_ordinal_not_numeric() -> None:
    assert [t.rank for t in Tier] == [0, 1, 2, 3, 4]
    assert Tier.PEERLESS.rank > Tier.FIRST.rank > Tier.NONE.rank


def test_world_fixture_is_consistent() -> None:
    assert len(WORLD.locations) == 4 and len(WORLD.characters) == 9 and len(WORLD.martial_arts) == 7


def test_character_key_is_the_true_name_and_titles_still_resolve() -> None:
    yanqing = next(c for c in WORLD.characters if c.id == "chr:段延庆")
    assert yanqing.name == "段延庆" and yanqing.names == ("段延庆", "恶贯满盈", "延庆太子")
    usurper = Character(id="chr:恶贯满盈", true_name="段延庆", titles=("恶贯满盈",))
    with pytest.raises(ValidationError, match="主键须是本名"):
        WorldBlueprint(characters=(usurper,))  # 称号再响，也不能篡位成主键


def test_blueprint_rejects_dangling_exit() -> None:
    with pytest.raises(ValidationError, match="不存在的LOCATION"):
        WorldBlueprint(locations=(Location(id="loc:甲", name="甲", exits={"北": "loc:乙"}),))


def test_blueprint_rejects_foundation_cycle() -> None:
    a = MartialArt(id="art:甲", name="甲", practice=Practice(skills=("art:乙",)))
    b = MartialArt(id="art:乙", name="乙", practice=Practice(skills=("art:甲",)))
    with pytest.raises(ValidationError, match="根基成环"):
        WorldBlueprint(martial_arts=(a, b))
    assert prerequisite_cycle({"x": ("y",), "y": ()}) == []


def test_blueprint_rejects_self_relations_and_parallel_edges() -> None:
    a = Character(id="chr:甲", true_name="甲")
    b = Character(id="chr:乙", true_name="乙")
    with pytest.raises(ValidationError, match="自环"):
        WorldBlueprint(characters=(a,), relations=(CharacterRelation(source_id="chr:甲", target_id="chr:甲", kind=RelationKind.SWORN),))
    twice = (
        CharacterRelation(source_id="chr:甲", target_id="chr:乙", kind=RelationKind.SWORN),
        CharacterRelation(source_id="chr:乙", target_id="chr:甲", kind=RelationKind.ENEMY),
    )
    with pytest.raises(ValidationError, match="至多一条"):
        WorldBlueprint(characters=(a, b), relations=twice)  # 不论方向与类别：人情涟漪与掌故落边都按"一对人一条边"读


def test_acquisition_and_practice_are_separate_gates() -> None:
    art = next(a for a in WORLD.martial_arts if a.id == "art:凌波微步")
    assert art.acquisition.items == ("itm:北冥神功卷轴",) and art.acquisition.location_id == "loc:无量玉洞"
    assert art.practice.skills == ("art:北冥神功",)  # 门径在琅嬛福地的卷轴里，根基是北冥神功
    with pytest.raises(ValidationError, match="自悟"):
        Acquisition(transmission=Transmission.SELF)
    dangling = MartialArt(id="art:甲", name="甲", acquisition=Acquisition(items=("itm:无此物",)))
    with pytest.raises(ValidationError, match="不存在的ITEM"):
        WorldBlueprint(martial_arts=(dangling,))


def test_items_may_be_lost_but_never_misplaced() -> None:
    misplaced = Item(id="itm:玉佩", name="玉佩", owner_id="chr:段正淳", location_id="loc:无量山")
    assert misplaced.canon_holder == "loc:无量山"  # 失物：物主不在身边，物理所在优先
    orphan = Item(id="itm:谱诀", name="谱诀")  # 原著提到它，却没写它在哪：下落不明，等自愈代理安放
    assert orphan.lost and orphan.canon_holder is None and orphan.provenance is Provenance.CANON
    assert WorldBlueprint(items=(orphan,)).items == (orphan,)


def test_intent_normalizes_instead_of_rejecting() -> None:
    intent = PlayerIntent(action_type="ATTACK", target_entity="  ", item_used="剑" * 50, narrative_style=None)
    assert intent.target_entity is None and len(intent.item_used or "") == 24 and intent.narrative_style == ""
    assert intent.approach is Approach.PLAIN and intent.aim is None and intent.topic is None  # 旧意图一律寻常


def test_intent_carries_approach_aim_and_topic() -> None:
    asked = PlayerIntent(action_type="LEARN", skill_used="无量剑法", approach="言辞", aim="求艺", topic=" 东西宗比剑 ")
    assert (asked.approach, asked.aim, asked.topic) == (Approach.WORDS, Aim.LEARN, "东西宗比剑")
    assert PlayerIntent(action_type="TALK", topic="").topic is None
    assert len(PlayerIntent(action_type="TALK", topic="话" * 50).topic or "") == 24  # 与其余指称同样规整
    assert PlayerIntent(action_type="USE", item_used="金创药").action_type is ActionType.USE
    with pytest.raises(ValidationError):
        PlayerIntent(action_type="TALK", approach="法术")


# ============================================================
#  P1 本体：人情阶梯、关系 era、物性、掌故闸门
# ============================================================
def test_attitude_is_a_five_step_ladder() -> None:
    assert [a.rank for a in Attitude] == [-2, -1, 0, 1, 2]
    assert [a.value for a in (Attitude.HOSTILE, Attitude.NEUTRAL, Attitude.FRIENDLY)] == ["敌视", "漠然", "友善"]  # 旧值不动
    assert Attitude.NEUTRAL.step(1) is Attitude.FRIENDLY and Attitude.FRIENDLY.step(1) is Attitude.TRUSTED
    assert Attitude.TRUSTED.step(3) is Attitude.TRUSTED and Attitude.WARY.step(-5) is Attitude.HOSTILE  # 钳在两端
    assert Attitude.HOSTILE.step(2) is Attitude.NEUTRAL and Attitude.WARY.step(0) is Attitude.WARY


def test_p1_ontology_defaults_keep_old_blueprints_valid() -> None:
    rel = WORLD.relations[0]
    assert rel.era is Era.OPENING  # 审计之前一律视为开篇
    duan = next(c for c in WORLD.characters if c.id == "chr:段誉")
    assert duan.foreshadow == "" and duan.arrives_with is None
    jade = next(i for i in WORLD.items if i.id == "itm:玉佩")
    assert jade.portable and jade.hazard is None and jade.use is None and jade.arrives_with is None
    assert WORLD.personas == () and WORLD.facts == ()
    assert ItemUse(effect="疗伤").potency == 1
    with pytest.raises(ValidationError):
        ItemUse(effect="疗伤", potency=4)
    with pytest.raises(ValidationError):
        ItemUse(effect="续命")


def _lore(**extra: object) -> WorldBlueprint:
    """在 WORLD 上叠一件有毒之物与掌故，经蓝图闸门重新校验（model_copy 不校验）。"""
    toad = Item(id="itm:朱蛤", name="朱蛤", kind="毒物", location_id="loc:无量山", portable=False, hazard="剧毒")
    return WorldBlueprint.model_validate({**WORLD.model_dump(), "items": (*WORLD.items, toad), **extra})


PERSONA = Persona(character_id="chr:左子穆", likes=("门下弟子争气",), worry="西宗夺剑湖宫", sources=("chunk:3",))
RIVALRY = Fact(
    id="fact:东西宗比剑", text="无量剑东西二宗五年一比剑，胜者入住剑湖宫",
    subject_ids=("chr:左子穆", "chr:辛双清"), knower_ids=("chr:左子穆", "chr:龚光杰"),
    unlock=FactUnlock(kind="LEVERAGE", target_id="chr:左子穆"), sources=("ev:3",),
)


def test_lore_lands_on_the_blueprint() -> None:
    sword = Fact(id="fact:左子穆的剑法", text="左子穆精于无量剑法", subject_ids=("chr:左子穆",),
                 knower_ids=("chr:辛双清",), unlock=FactUnlock(kind="TEACHING", target_id="art:无量剑法"), sources=("chunk:3",))
    venom = Fact(id="fact:朱蛤有毒", text="山间朱蛤剧毒，碰不得", subject_ids=("itm:朱蛤",), knower_ids=("chr:辛双清",),
                 unlock=FactUnlock(kind="HAZARD", target_id="itm:朱蛤"), sources=("chunk:9",))
    worry = Fact(id="fact:左子穆的心事", text="左子穆最怕西宗夺去剑湖宫", subject_ids=("chr:左子穆",),
                 knower_ids=("chr:左子穆",), unlock=FactUnlock(kind="MOTIVE", target_id="chr:左子穆"), sources=("chunk:3",))
    bp = _lore(personas=(PERSONA,), facts=(RIVALRY, sword, venom, worry))
    assert len(bp.facts) == 4 and bp.personas == (PERSONA,)
    toad = next(i for i in bp.items if i.id == "itm:朱蛤")
    assert not toad.portable and toad.hazard == "剧毒"


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"personas": (PERSONA.model_copy(update={"character_id": "chr:无名氏"}),)}, "人设引用了不存在的人物"),
        ({"personas": (PERSONA, PERSONA)}, "重复的人设"),
        ({"facts": (RIVALRY, RIVALRY)}, "重复的见闻"),
        ({"facts": (RIVALRY.model_copy(update={"subject_ids": ("chr:无名氏",)}),)}, "主体不存在"),
        ({"facts": (RIVALRY.model_copy(update={"knower_ids": ("loc:无量山",)}),)}, "知情人不是蓝图里的人物"),
        ({"facts": (RIVALRY.model_copy(update={"knower_ids": ("chr:乔峰",)}),)}, "与主体无涉"),  # 不同门、无关系边
        ({"facts": (RIVALRY.model_copy(update={"unlock": FactUnlock(kind="TEACHING", target_id="art:一阳指")}),)},
         "落不到蓝图的边上"),  # 两位主体都不会一阳指
        ({"facts": (RIVALRY.model_copy(update={"unlock": FactUnlock(kind="LEVERAGE", target_id="chr:段誉")}),)},
         "落不到蓝图的边上"),  # 段誉与主体之间没有关系边
        ({"facts": (RIVALRY.model_copy(update={"unlock": FactUnlock(kind="HAZARD", target_id="itm:玉佩")}),)},
         "落不到蓝图的边上"),  # 玉佩无毒
        ({"facts": (RIVALRY.model_copy(update={"unlock": FactUnlock(kind="MOTIVE", target_id="chr:左子穆")}),)},
         "落不到蓝图的边上"),  # 没有人设
    ],
)
def test_lore_gate_rejects(extra: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _lore(**extra)


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"subject_ids": ("chr:左子穆", "chr:辛双清", "chr:左子穆")}, "主体有重复：chr:左子穆"),
        ({"knower_ids": ("chr:龚光杰", "chr:龚光杰")}, "知情人有重复：chr:龚光杰"),
    ],
)
def test_lore_gate_rejects_repeated_ids(update: dict[str, object], message: str) -> None:
    """主体与知情人不得重复：两套图谱对重复的处理不同（内存照留、Neo4j 合并），从源头拒收，快照才逐字段同构。"""
    with pytest.raises(ValidationError, match=message):
        _lore(facts=(RIVALRY.model_copy(update=update),))


@pytest.mark.parametrize("era", [Era.IMMINENT, Era.LATER])
def test_lore_gate_only_counts_bonds_of_the_opening(era: Era) -> None:
    """将至、后文才结下的关系在 T=0 还不存在：既不让人知情，也不作把柄。"""
    later = tuple(r.model_copy(update={"era": era}) for r in WORLD.relations)
    rival = Fact(id="fact:左子穆的剑", text="左子穆剑法凌厉", subject_ids=("chr:左子穆",), knower_ids=("chr:辛双清",),
                 sources=("chunk:1",))  # 不同门：辛双清知情全凭东西宗相争这条边
    with pytest.raises(ValidationError, match="与主体无涉"):
        _lore(relations=later, facts=(rival,))
    lever = RIVALRY.model_copy(update={"knower_ids": ("chr:左子穆",)})
    with pytest.raises(ValidationError, match="落不到蓝图的边上"):  # 左子穆与辛双清的东西宗之争尚未结下
        _lore(relations=later, facts=(lever,))
    assert _lore(facts=(rival, lever)).facts == (rival, lever)  # 同样的边结于开篇即可


def test_lore_gate_rejects_items_that_only_arrive_later() -> None:
    """后来才到场的物品（arrives_with）属于 P2 的世界事件：不能作见闻的主体，也不能作险物 unlock 的目标。"""
    toad = Item(id="itm:朱蛤", name="朱蛤", kind="毒物", location_id="loc:无量山", portable=False, hazard="剧毒",
                arrives_with="portent:莽牯朱蛤")
    items = (*WORLD.items, toad)
    venom = Fact(id="fact:朱蛤有毒", text="山间朱蛤剧毒，碰不得", subject_ids=("itm:朱蛤",), knower_ids=("chr:辛双清",),
                 unlock=FactUnlock(kind="HAZARD", target_id="itm:朱蛤"), sources=("chunk:9",))
    with pytest.raises(ValidationError, match="后来才到场"):
        WorldBlueprint.model_validate({**WORLD.model_dump(), "items": items, "facts": (venom,)})
    warned = RIVALRY.model_copy(update={"unlock": FactUnlock(kind="HAZARD", target_id="itm:朱蛤")})
    with pytest.raises(ValidationError, match="落不到蓝图的边上"):
        WorldBlueprint.model_validate({**WORLD.model_dump(), "items": items, "facts": (warned,)})


def test_lore_fields_are_bounded() -> None:
    with pytest.raises(ValidationError):
        Persona(character_id="chr:左子穆", likes=("话" * 17,), sources=("chunk:1",))  # 每条 ≤16 字
    with pytest.raises(ValidationError):
        Persona(character_id="chr:左子穆", sources=())  # 须有出处
    with pytest.raises(ValidationError):
        Persona(character_id="chr:左子穆", sources=("第三回",))  # 出处须是 ev:<块> 或 chunk:<块>
    with pytest.raises(ValidationError):
        RIVALRY.model_validate({**RIVALRY.model_dump(), "text": "话" * 41})  # 见闻 ≤40 字
    with pytest.raises(ValidationError):
        RIVALRY.model_validate({**RIVALRY.model_dump(), "id": "东西宗比剑"})  # id 须是 fact:<slug>
    with pytest.raises(ValidationError):
        RIVALRY.model_validate({**RIVALRY.model_dump(), "knower_ids": ()})  # 没有知情人的见闻无从打探


def test_knowers_may_be_fellows_or_bound_to_the_subject() -> None:
    """知情人须与主体有涉：本人、同门（同一门派）、或有关系边；物品的"本人"是物主，武学是身负者，地点是身在其中者。"""
    fellow = RIVALRY.model_copy(update={"knower_ids": ("chr:龚光杰",), "unlock": None})  # 与左子穆同门
    kin = Fact(id="fact:玉佩", text="玉佩是段家之物", subject_ids=("itm:玉佩",), knower_ids=("chr:段誉",), sources=("chunk:1",))
    bp = _lore(facts=(fellow, kin))  # 段誉与物主段正淳有父子之边
    assert {f.id for f in bp.facts} == {"fact:东西宗比剑", "fact:玉佩"}
    assert CharacterRelation(source_id="chr:甲", target_id="chr:乙", kind=RelationKind.KIN).era is Era.OPENING


# ============================================================
#  事件
# ============================================================
DOUBT = NarrativeClock(id=clock_id("chr:左子穆", "左子穆的疑心"), name="左子穆的疑心", kind=ClockKind.SUSPICION,
                       anchor_id="chr:左子穆", progress=1, maximum=4, consequence="识破你的手脚")
FLOOD = NarrativeClock(id=clock_id("loc:无量山", "山洪将至"), name="山洪将至", kind=ClockKind.PERIL,
                       anchor_id="loc:无量山", progress=2, maximum=6, consequence="洪水漫过山道")
ALL_EVENTS = [
    PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量山", aptitude=1.2),
    Moved(from_location_id="loc:无量山", to_location_id="loc:大理城", exit_label="南下"),
    ItemTransferred(item_id="itm:玉佩", from_holder="loc:无量山", to_holder=PID),
    SkillPracticed(skill_id="art:无量剑法", proficiency_gained=10, source_id="chr:辛双清"),
    SkillExecuted(skill_id=None, target_id="chr:左子穆", outcome=CombatOutcome.MINOR_WOUND),
    HealthChanged(delta=-18, cause="与左子穆交手", source_id="chr:左子穆"),
    Conversed(npc_id="chr:段誉"),
    RelationChanged(character_id="chr:段正淳", attitude=Attitude.FRIENDLY, cause="物归原主"),
    ActionFailed(action=ActionType.MOVE, target="少林寺", reason_code="NO_PATH", reason="无路"),
    ActionFailed(action=ActionType.LEARN, target="一阳指", reason_code="UNWILLING", reason="交情尚浅", unlock="信赖",
                 aim=Aim.LEARN, approach=Approach.WORDS, target_id="chr:段正淳", subject_id="art:一阳指"),
    HealthChanged(delta=5, cause="服用金创药", source_id="itm:金创药", source="item"),
    Conversed(npc_id="chr:左子穆", topic_id="fact:东西宗比剑"),
    RelationChanged(character_id="chr:龚光杰", attitude=Attitude.WARY, cause="师徒左子穆受你攻击", basis="师徒"),
    SkillExecuted(skill_id=None, target_id="chr:龚光杰", outcome=CombatOutcome.STALEMATE, approach=Approach.GUILE),
    Parleyed(npc_id="chr:左子穆", aim=Aim.LEARN, approach=Approach.WORDS, outcome=SocialOutcome.SOFTENED,
             leverage_ids=("fact:东西宗比剑",), subject_id="art:无量剑法"),
    FactLearned(fact_id="fact:东西宗比剑", source_id="chr:左子穆"),
    ItemConsumed(item_id="itm:金创药", effect="疗伤"),
    Maneuvered(item_id="itm:无量剑", target_id="chr:左子穆", approach=Approach.STEALTH, outcome=CovertOutcome.FOILED),
    ClockStarted(clock=DOUBT, cause="推演"),
    ClockStarted(clock=FLOOD, cause="暗流"),
    ClockAdvanced(clock_id=DOUBT.id, steps=2, name="左子穆的疑心", progress=3, maximum=4, cause="推演"),
    ClockAdvanced(clock_id=FLOOD.id, steps=-1, name="山洪将至", progress=1, maximum=6, cause="推演"),
    ClockCollapsed(clock_id=DOUBT.id, name="左子穆的疑心", consequence="识破你的手脚"),
    ClockCleared(clock_id=FLOOD.id, name="山洪将至", cause="化解"),
    FactEmerged(fact_id="emg:0123456789", text="左子穆的剑穗上沾着湖边的青苔", subject_ids=("chr:左子穆",)),
    RenownChanged(delta=-5, cause="左子穆的疑心满了：识破你的手脚"),
    PlayerDied(cause="冒犯", killer_id="chr:南海鳄神"),
]


@pytest.mark.parametrize("event", ALL_EVENTS, ids=lambda e: type(e).__name__)
def test_events_round_trip_through_json(event: object) -> None:
    raw = EVENT_ADAPTER.dump_json(event)  # type: ignore[arg-type]
    assert EVENT_ADAPTER.validate_json(raw) == event


@pytest.mark.parametrize("event", ALL_EVENTS, ids=lambda e: type(e).__name__)
def test_decode_event_is_the_single_read_path(event: object) -> None:
    assert decode_event(EVENT_ADAPTER.dump_json(event)) == event  # type: ignore[arg-type]


def test_legacy_vocabulary_is_upcast_on_read() -> None:
    learned = decode_event('{"type": "SkillLearned", "skill_id": "art:一阳指", "source_id": "chr:段正淳"}')
    assert learned == SkillPracticed(skill_id="art:一阳指", proficiency_gained=LEGACY_MASTERY_POINTS, source_id="chr:段正淳")
    assert mastery_of(LEGACY_MASTERY_POINTS, 1.0) is Mastery.ADEPT  # 当年学会了，今天仍是融会贯通
    repelled = decode_event({"type": "SkillExecuted", "skill_id": None, "target_id": "chr:左子穆", "outcome": "受挫"})
    assert isinstance(repelled, SkillExecuted) and repelled.outcome is CombatOutcome.MINOR_WOUND
    old_spawn = decode_event({"type": "PlayerSpawned", "player_id": PID, "name": "阿星", "location_id": "loc:无量山"})
    assert isinstance(old_spawn, PlayerSpawned) and old_spawn.aptitude == 1.0  # 悟性之前的旧账：中人之资


def test_old_ledgers_read_without_the_new_fields() -> None:
    """P1 给旧事件加的字段一律有缺省值：账本里没有它们的旧账照读。"""
    failed = decode_event({"type": "ActionFailed", "action": "TALK", "target": None, "reason_code": "X", "reason": "y"})
    assert isinstance(failed, ActionFailed) and (failed.unlock, failed.aim, failed.approach) == ("", None, Approach.PLAIN)
    assert (failed.target_id, failed.subject_id) == (None, None)
    parley = decode_event({"type": "Parleyed", "npc_id": "chr:段誉", "aim": "结交", "approach": "言辞", "outcome": "无果"})
    assert isinstance(parley, Parleyed) and parley.subject_id is None
    talked = decode_event({"type": "Conversed", "npc_id": "chr:段誉"})
    assert isinstance(talked, Conversed) and talked.topic_id is None
    hit = decode_event({"type": "SkillExecuted", "skill_id": None, "target_id": "chr:段誉", "outcome": "得手"})
    assert isinstance(hit, SkillExecuted) and hit.approach is Approach.PLAIN
    regard = decode_event({"type": "RelationChanged", "character_id": "chr:段誉", "attitude": "友善", "cause": "c"})
    assert isinstance(regard, RelationChanged) and regard.basis == "" and regard.attitude is Attitude.FRIENDLY


def test_rest_is_upcast_from_the_legacy_cause() -> None:
    """唯一的上抛：source 之前的调息写的是 cause="调息疗伤"——读作 rest；别的旧账读作 blow；写明了 source 的一字不动。"""
    rest = decode_event({"type": "HealthChanged", "delta": 25, "cause": "调息疗伤"})
    assert isinstance(rest, HealthChanged) and rest.source == "rest"
    blow = decode_event({"type": "HealthChanged", "delta": -18, "cause": "与左子穆交手", "source_id": "chr:左子穆"})
    assert isinstance(blow, HealthChanged) and blow.source == "blow"
    explicit = decode_event({"type": "HealthChanged", "delta": 25, "cause": "调息疗伤", "source": "item"})
    assert isinstance(explicit, HealthChanged) and explicit.source == "item"


def test_only_resting_lets_the_focus_go_stale() -> None:
    """焦点「不新鲜」认 source="rest"：服药的气血回升不是走开；旧账里的调息经上抛同样算数。"""
    talk = [PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量山"), Conversed(npc_id="chr:左子穆")]
    dosed = Player.replay([*talk, HealthChanged(delta=10, cause="服用金创药", source_id="itm:金创药", source="item")])
    assert dosed is not None and dosed.focus_fresh
    legacy = decode_event({"type": "HealthChanged", "delta": 10, "cause": "调息疗伤"})
    rested = Player.replay([*talk, legacy])
    assert rested is not None and not rested.focus_fresh


def test_provenance_remembers_where_an_item_first_came_from() -> None:
    """taken_from 记下物品最初从谁手里到你身上：转手第三人再拿回来不改（偷来的底子洗不白），离手也不抹。"""
    base = [PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量山")]
    stolen = ItemTransferred(item_id="itm:无量剑", from_holder="chr:左子穆", to_holder=PID)
    passed = ItemTransferred(item_id="itm:无量剑", from_holder=PID, to_holder="chr:龚光杰")
    back = ItemTransferred(item_id="itm:无量剑", from_holder="chr:龚光杰", to_holder=PID)
    found = ItemTransferred(item_id="itm:玉佩", from_holder="loc:无量山", to_holder=PID)
    elsewhere = ItemTransferred(item_id="itm:打狗棒", from_holder="chr:乔峰", to_holder="chr:段誉")
    state = Player.replay([*base, stolen, passed, back, found, elsewhere])
    assert state is not None and state.taken_from == {"itm:无量剑": "chr:左子穆", "itm:玉佩": "loc:无量山"}


def test_the_new_vocabulary_folds_into_the_aggregate() -> None:
    """
    Parleyed / Maneuvered：进焦点（交涉的对方；失主在前、那件东西在后）、近来手段、尝试次数与心事线索；
    FactLearned 记入已知见闻；ItemConsumed 记入用掉之物，行囊派生时排除它（易手覆盖照旧——用掉之物从此不在任何人身上）。
    """
    base = [PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量山")]
    news = [e for e in ALL_EVENTS if isinstance(e, Parleyed | FactLearned | ItemConsumed | Maneuvered)]
    assert len(news) == 4
    state = Player.replay([*base, *news])
    assert state is not None
    assert state.known_facts == {"fact:东西宗比剑"} and state.consumed == {"itm:金创药"}
    assert state.focus == ("chr:左子穆", "itm:无量剑") and state.focus_fresh
    assert state.recent_approaches == (Approach.STEALTH, Approach.WORDS) and state.attempts == {"chr:左子穆": 2}
    assert {t.key for t in state.threads} == {("chr:左子穆", Aim.LEARN), ("chr:左子穆", Aim.SEIZE)}
    salve = ItemTransferred(item_id="itm:金创药", from_holder="loc:无量山", to_holder=PID)
    held = Player.replay([*base, salve])
    used = Player.replay([*base, salve, ItemConsumed(item_id="itm:金创药", effect="疗伤")])
    assert held is not None and used is not None
    assert held.inventory == {"itm:金创药"} and used.inventory == frozenset()
    assert used.item_holders == {"itm:金创药": PID}  # 易手覆盖照旧，只是不再算在行囊里
    struck = Player.replay([*base, *(SkillExecuted(skill_id=None, target_id="chr:左子穆", outcome=CombatOutcome.STALEMATE,
                                                   approach=a) for a in (Approach.PLAIN, Approach.GUILE, Approach.FORCE,
                                                                         Approach.PLAIN, Approach.GUILE))])
    assert struck is not None and struck.attempts == {"chr:左子穆": 5}
    assert struck.recent_approaches == (Approach.GUILE, Approach.PLAIN, Approach.FORCE, Approach.GUILE)  # 至多四个，新者在前


def test_events_are_immutable() -> None:
    with pytest.raises(ValidationError):
        ALL_EVENTS[1].to_location_id = "loc:少林寺"  # type: ignore[attr-defined,misc]
    with pytest.raises(ValidationError):
        SkillPracticed(skill_id="art:一阳指", proficiency_gained=0)  # 修习至少有一分所得


# ============================================================
#  渐进式状态：内部是整数，对外是语义
# ============================================================
def test_mastery_is_points_times_aptitude() -> None:
    assert mastery_of(0, 1.3) is None
    assert [mastery_of(p, 1.0) for p in (1, 20, 45, 75, 110)] == list(Mastery)
    assert mastery_of(19, 1.0) is Mastery.NOVICE and mastery_of(19, 1.1) is Mastery.MINOR  # 悟性高一分，早一步小成
    assert effective_tier(Tier.FIRST, Mastery.NOVICE) is Tier.THIRD
    assert effective_tier(Tier.FIRST, Mastery.MINOR) is Tier.SECOND
    assert effective_tier(Tier.THIRD, Mastery.NOVICE) is Tier.NONE  # 折扣不跌穿不入流
    assert effective_tier(Tier.FIRST, Mastery.PEAK) is Tier.FIRST  # 火候再高也不越过功夫本身的境界
    assert [effective_tier(Tier.PEERLESS, m) for m in Mastery] == [  # 火候封顶：绝顶神功初窥门径也拿不出一流本事
        Tier.THIRD, Tier.SECOND, Tier.FIRST, Tier.PEERLESS, Tier.PEERLESS,
    ]


def test_loftier_arts_are_harder_to_practice() -> None:
    assert [gain(Guidance.MANUAL, t) for t in Tier] == [10, 10, 7, 5, 3]
    assert gain(Guidance.ALONE, Tier.PEERLESS) == 2 and gain(Guidance.TEACHER, Tier.FIRST) == 8


def test_aptitude_is_innate_and_vitality_is_semantic() -> None:
    assert aptitude_for("ply:甲") == aptitude_for("ply:甲") and 0.5 <= aptitude_for("ply:乙") <= 1.5
    assert [vitality(hp) for hp in (100, 89, 49, 19)] == list(Vitality)


# ============================================================
#  聚合根：只凭事件流重算 Location 与 Inventory
# ============================================================
def _history(*events: object) -> list[EventEnvelope]:
    return [
        EventEnvelope(stream_id=PID, version=i, event_id=uuid4(), recorded_at=datetime.now(UTC), event=e)  # type: ignore[arg-type]
        for i, e in enumerate(events, start=1)
    ]


def test_location_and_inventory_are_folded_from_history() -> None:
    player = Player.from_history(PID, _history(
        PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量山"),
        ItemTransferred(item_id="itm:玉佩", from_holder="loc:无量山", to_holder=PID),
        Moved(from_location_id="loc:无量山", to_location_id="loc:无量玉洞", exit_label="崖下"),
        ItemTransferred(item_id="itm:北冥神功卷轴", from_holder="loc:无量玉洞", to_holder=PID),
        Moved(from_location_id="loc:无量玉洞", to_location_id="loc:无量山", exit_label="攀上"),
        Moved(from_location_id="loc:无量山", to_location_id="loc:大理城", exit_label="南下"),
        ItemTransferred(item_id="itm:玉佩", from_holder=PID, to_holder="chr:段正淳"),
    ))
    assert player.version == 7
    assert player.state.location_id == "loc:大理城"
    assert player.state.inventory == {"itm:北冥神功卷轴"}
    assert player.state.item_holders["itm:玉佩"] == "chr:段正淳"  # 世界相对原著的偏离也在状态里


def test_replay_is_a_pure_reduce() -> None:
    events = [e for e in ALL_EVENTS if not isinstance(e, PlayerDied)]
    once, twice = Player.replay(events), Player.replay(events)
    assert once == twice and once is not twice
    assert once is not None and once.subdued == frozenset() and once.skills == {"art:无量剑法"}
    assert once.hp == MAX_HP - 18 + 5 and once.vitality is Vitality.HURT  # 交手所伤，服药略回


def test_skill_level_is_the_reduce_of_practice_scaled_by_aptitude() -> None:
    def practiced(aptitude: float) -> Player:
        return Player.from_history(PID, _history(
            PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量玉洞", aptitude=aptitude),
            ItemTransferred(item_id="itm:北冥神功卷轴", from_holder="loc:无量玉洞", to_holder=PID),
            SkillPracticed(skill_id="art:北冥神功", proficiency_gained=10, source_id="itm:北冥神功卷轴"),
            SkillPracticed(skill_id="art:北冥神功", proficiency_gained=10, source_id="itm:北冥神功卷轴"),
            SkillPracticed(skill_id="art:北冥神功", proficiency_gained=5),
        ))

    dull, gifted = practiced(0.8), practiced(1.3)
    assert dull.state.practice == gifted.state.practice == {"art:北冥神功": 25}  # 事件只记"练了多少"
    assert dull.mastery("art:北冥神功") is Mastery.MINOR  # 25 × 0.8 = 20
    assert gifted.mastery("art:北冥神功") is Mastery.MINOR  # 25 × 1.3 = 32.5
    assert dull.mastery("art:凌波微步") is None


def test_acquiring_a_manual_is_not_learning() -> None:
    state = Player.replay([
        PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量玉洞"),
        ItemTransferred(item_id="itm:北冥神功卷轴", from_holder="loc:无量玉洞", to_holder=PID),
    ])
    assert state is not None and state.inventory == {"itm:北冥神功卷轴"} and state.skills == frozenset()


def test_health_is_clamped_and_success_subdues() -> None:
    state = Player.replay([
        PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量山"),
        HealthChanged(delta=+30, cause="调息疗伤"),
        SkillExecuted(skill_id=None, target_id="chr:左子穆", outcome=CombatOutcome.SUCCESS),
        HealthChanged(delta=-500, cause="与南海鳄神交手", source_id="chr:南海鳄神"),
    ])
    assert state is not None and state.hp == 0 and state.alive  # 气血归零不等于死：死亡只认明写的 PlayerDied
    assert state.subdued == {"chr:左子穆"}


def test_focus_and_grudges_are_folded_from_existing_events() -> None:
    """焦点 = 亲手打过交道的人与物（新者在前、至多四个）；目睹者的人情涟漪、驳回的原话、去过的地方都不算。恩怨缘由取最近一次。"""
    state = Player.replay([
        PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量山"),
        Conversed(npc_id="chr:辛双清"),
        SkillExecuted(skill_id=None, target_id="chr:左子穆", outcome=CombatOutcome.SUCCESS),
        RelationChanged(character_id="chr:左子穆", attitude=Attitude.HOSTILE, cause="遭你出手相攻"),
        RelationChanged(character_id="chr:龚光杰", attitude=Attitude.HOSTILE, cause="师徒左子穆受你攻击"),
        ItemTransferred(item_id="itm:无量剑", from_holder="chr:左子穆", to_holder=PID),
        ActionFailed(action=ActionType.TALK, target="段誉", reason_code="NOT_PRESENT", reason="此处不见「段誉」。"),
        Moved(from_location_id="loc:无量山", to_location_id="loc:大理城", exit_label="南下"),
    ])
    assert state is not None
    assert state.focus == ("chr:左子穆", "itm:无量剑", "chr:辛双清")  # 从被制住者身上取物：人在前、物在后，旧焦点去重
    assert state.attitude_causes == {"chr:左子穆": "遭你出手相攻", "chr:龚光杰": "师徒左子穆受你攻击"}
    later = Player.replay([
        PlayerSpawned(player_id=PID, name="阿星", location_id="loc:无量玉洞"),
        ItemTransferred(item_id="itm:北冥神功卷轴", from_holder="loc:无量玉洞", to_holder=PID),
        SkillPracticed(skill_id="art:北冥神功", proficiency_gained=5, source_id="itm:北冥神功卷轴"),
        SkillPracticed(skill_id="art:北冥神功", proficiency_gained=5),  # 闭门苦练没有对象
        *(Conversed(npc_id=f"chr:路人{n}") for n in range(4)),
        RelationChanged(character_id="chr:路人3", attitude=Attitude.FRIENDLY, cause="相谈甚欢"),
        RelationChanged(character_id="chr:路人3", attitude=Attitude.HOSTILE, cause="出言不逊"),
    ])
    assert later is not None and later.focus == ("chr:路人3", "chr:路人2", "chr:路人1", "chr:路人0")  # 至多四个
    assert later.attitude_causes == {"chr:路人3": "出言不逊"}


def test_stream_must_start_with_spawn_and_be_contiguous() -> None:
    with pytest.raises(ValueError, match="PlayerSpawned"):
        evolve(None, Conversed(npc_id="chr:段誉"))
    broken = _history(ALL_EVENTS[0], ALL_EVENTS[1])
    broken[1] = broken[1].model_copy(update={"version": 3})
    with pytest.raises(ValueError, match="断裂"):
        Player.from_history(PID, broken)


def test_unknown_and_dead_players_cannot_act() -> None:
    with pytest.raises(UnknownPlayerError):
        _ = Player(PID).state
    dead = Player.from_history(PID, _history(ALL_EVENTS[0], ALL_EVENTS[-1]))
    with pytest.raises(PlayerDeadError, match="已经死了"):
        dead.ensure_alive()


# ============================================================
#  P1 快照视图：缺省值让图谱实现不填也能构造，新集合同样按固定键排序
# ============================================================
def test_p1_snapshot_views_default_and_order_canonically() -> None:
    assert ExitView(label="南下", to_id="loc:大理城", to_name="大理城").hostile_ahead is False
    jade = ItemView(id="itm:玉佩", name="玉佩", holder_id="loc:无量山")
    assert jade.portable and jade.hazard is None and jade.use is None
    master = CharacterView(
        id="chr:左子穆", name="左子穆", tier=Tier.THIRD, disposition=Disposition.NEUTRAL,
        bonds=[{"other_id": "chr:龚光杰", "kind": "师徒", "lead": True}, {"other_id": "chr:龚光杰", "kind": "师徒"}],
    )  # 图谱实现没填的新字段取缺省值，排序键与实现无关
    assert [(b.lead, b.era) for b in master.bonds] == [(False, Era.OPENING), (True, Era.OPENING)]
    assert master.persona is None and BondView(other_id="chr:甲", kind=RelationKind.KIN).lead is False
    told = CharacterView(id="chr:甲", name="甲", tier=Tier.NONE, disposition=Disposition.NEUTRAL,
                         persona=PersonaView(likes=("清静",), worry="西宗"))
    assert told.persona is not None and told.persona.dislikes == ()
    snap = LocalSnapshot(
        player_id=PID, player_name="阿星", alive=True, version=1,
        location=LocationView(id="loc:无量山", name="无量山"),
        facts=[
            FactView(id="fact:b", text="乙", subject_ids=("chr:辛双清", "chr:左子穆"), knower_ids=("chr:左子穆",)),
            FactView(id="fact:a", text="甲", subject_ids=("itm:朱蛤",), knower_ids=("chr:辛双清",),
                     unlock=FactUnlock(kind="HAZARD", target_id="itm:朱蛤")),
        ],
    )
    assert [f.id for f in snap.facts] == ["fact:a", "fact:b"]
    assert snap.facts[1].subject_ids == ("chr:左子穆", "chr:辛双清")  # 集合字段按 id 排序
    assert {"itm:朱蛤", "chr:辛双清", "chr:左子穆"} <= snap.referenced_ids()  # 名称表须覆盖见闻牵涉的一切
    assert LocalSnapshot(player_id=PID, player_name="阿星", alive=True, version=1,
                         location=LocationView(id="loc:无量山", name="无量山")).facts == ()


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
