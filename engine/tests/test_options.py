"""
[INPUT]: 依赖 app.application.options 的门面与 phrasing / sources / salience / menu，依赖 app.application.narrator 的 MenuPick / MenuPicks，
         依赖 app.domain 的 rules / stakes / events / lore / models / snapshot / intent / approach（TacticalAxis / axis_of），
         依赖 tests/test_rules 的 scene() 快照工厂与夹具，依赖 tests/test_option_metrics 的 canon()（入库原著蓝图的专名表）
[OUTPUT]: options 包的单测：门面照旧可导入（另转出 compose）、ActionOption 的意图风味封装（flavor_text 缺省等于 label、战术轴恒为 axis_of(指令意图)、
          underlying_command 恒等于 rules.command、intent 是指令的意图、手搭选项只给意图也行）、措辞表（每键 2~3 个变体、骨架 ≤10 字、只认已知槽位、
          不含任何实体名、按意图哈希确定性挑选、缺槽即抛错）、风险档等于 risk_of(rules.stakes)、菜单里不再有移动、
          三种出口（affordances 全部可供之招按分排序且不封顶、catalogue 至多十二招且每根有招的轴都在、generate 是退路菜单且尽量铺开不同的轴，
          后两者都是 affordances 的子集）、Person 源按兼容表展开的招（攀谈 / 言辞结交或化解 / 出手 / 讨要偷取夺物各一条 / 恳请传功只在师父不肯时 /
          不向仇人恳求 / 人情已达成不再结交）、问路（有未知去处才出、只出一条、找不敌视你的人里人情最好者、旁观轴、标签不带地名、经规则记下 PlacesLearned、
          去处都认得了即不出）、打探只在此人知情而你未知时才出且标签不含见闻正文、借势只在选得出筹码时才出、Ground 源（不可携带之物不出、疗伤之药按伤势、
          解毒之药不上菜单）、Thread 源（求教被拒 → 「换个手段」恳请；言辞试过 → 改以人情、同一手段纠缠扣分；暗取未遂 → 言辞讨要；对象不在场不出）、
          同一对象至多两席、id 是方向 + 指令意图的摘要；menu.compose 的闸门（目录编号宽容、重复与越界的编号作废、风味的字数 / 一行中文 / 英文数字标记 /
          结局与状态字眼 / 场景之外的原著名字、不合格的风味退回朴素标签而招照留、重复文案退回标签、按 id 去重、至多四席、不足三席退路补、一招没挑中即原样退路）
[POS]: tests 的选项单元层：test_application 管 P0 的席位与措辞随凭借而变，test_option_metrics 管实录重放上的验收指标，test_navigation 管方位导航，这里管「招」本身与风味闸门
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import re
from collections import Counter

import pytest

from app.application.narrator import MenuPick, MenuPicks
from app.application.options import ActionOption, OptionCategory, OptionGenerator, compose
from app.application.options.menu import MAX_SEATS, VERDICT_WORDS, flavor
from app.application.options.phrasing import TEMPLATES, bare, render
from app.domain import rules
from app.domain.approach import TacticalAxis, axis_of
from app.domain.commands import Command
from app.domain.events import (
    ActionFailed,
    Conversed,
    HealthChanged,
    Maneuvered,
    Parleyed,
    PlacesLearned,
    RelationChanged,
)
from app.domain.intent import ActionType, Aim, Approach, PlayerIntent
from app.domain.lore import FactUnlock
from app.domain.models import Attitude, ItemUse
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.snapshot import FactView, ItemView, LocalSnapshot
from app.domain.stakes import Risk, risk_of
from tests.test_rules import PID, scene
from tests.world import WORLD

WOUNDED = HealthChanged(delta=-58, cause="与龚光杰交手", source_id="chr:龚光杰")
SLOTS = {"npc", "item", "art", "src"}


def every(state, snap) -> tuple[ActionOption, ...]:  # type: ignore[no-untyped-def]
    """全部合法的招：席位、补位、同一对象的上限都拉满。"""
    return OptionGenerator(max_options=999, min_options=999, per_target=999).generate(state, snap)


def moves(options: tuple[ActionOption, ...], who: str) -> set[tuple[ActionType, Approach]]:
    return {(o.intent.action_type, o.intent.approach) for o in options if who in (o.intent.target_entity, o.label)}


# ============================================================
#  门面与措辞
# ============================================================
def test_the_facade_keeps_the_old_import_path() -> None:
    import app.application.options as options

    assert set(options.__all__) == {"ActionOption", "OptionCategory", "OptionGenerator", "compose"}
    assert {"risk", "flavor_text", "label", "tactical_axis", "underlying_command"} <= set(ActionOption.model_fields)
    assert "intent" not in ActionOption.model_fields and OptionCategory.RECOVER.value == "休养"  # 意图只是指令的只读属性


async def test_every_option_wraps_a_standard_command_on_a_tactical_axis() -> None:
    """意图风味封装：指令恒为 rules.command 算出的那一条，战术轴恒为 axis_of(指令意图)，未经说书人之手的 flavor_text 就是朴素标签。"""
    for at in ("loc:无量山", "loc:大理城"):
        state, snap = await scene(at, WOUNDED)
        for o in OptionGenerator().affordances(state, snap):
            assert o.underlying_command == rules.command(o.intent, state, snap) and o.intent is o.underlying_command.intent
            assert o.tactical_axis is axis_of(o.intent) and o.flavor_text == o.label
            assert o.intent.action_type is not ActionType.MOVE  # 移动归导航
    talk = PlayerIntent(action_type=ActionType.TALK, target_entity="段誉")
    hand = ActionOption.of(OptionCategory.SOCIAL, "与段誉攀谈", talk)  # 手搭选项只给意图：耗时按命令耗时表
    assert hand.underlying_command == Command(intent=talk, time_cost=1) and hand.tactical_axis is TacticalAxis.PACIFY


async def test_three_outlets_share_one_pool() -> None:
    """affordances 不封顶、按分排序；catalogue 至多十二招、每根有招的轴都在；generate 是 3~4 席的退路。后两者都是 affordances 的子集。"""
    state, snap = await scene("loc:无量山")
    gen = OptionGenerator()
    pool = gen.affordances(state, snap)
    assert {o.id for o in pool} == {o.id for o in every(state, snap)} and len({o.id for o in pool}) == len(pool)
    catalogue, menu = gen.catalogue(state, snap), gen.generate(state, snap)
    assert len(pool) > 12 and len(catalogue) == 12 and 3 <= len(menu) <= 4
    by_id = {o.id: o for o in pool}
    assert all(by_id[o.id].underlying_command == o.underlying_command for o in (*catalogue, *menu))
    assert {o.tactical_axis for o in catalogue} == {o.tactical_axis for o in pool} == set(TacticalAxis)
    assert len({o.tactical_axis for o in menu}) >= 3  # 退路也尽量铺开不同的轴
    assert gen.catalogue(state, snap) == catalogue and gen.affordances(state, snap) == pool  # 世界不变，逐字不变
    small = OptionGenerator(catalogue_size=3).catalogue(state, snap)
    assert len({o.tactical_axis for o in small}) == 3  # 取不满四轴时也是每轴各一招
    from dataclasses import replace

    dead = replace(state, alive=False)
    assert gen.affordances(dead, snap) == gen.catalogue(dead, snap) == gen.generate(dead, snap) == ()


def test_every_template_is_short_slotted_and_nameless() -> None:
    """每个键 2~3 个变体；去掉槽位 ≤10 字；只用已知槽位；模板里没有任何实体名（原著蓝图与测试世界的人物物功地都不许出现）。"""
    from tests.test_option_metrics import canon

    names: set[str] = set()
    for bp in (canon(), WORLD):
        for c in bp.characters:
            names |= {c.true_name, *c.titles, *c.aliases}
        for i in bp.items:
            names |= {i.name, *i.aliases}
        for a in bp.martial_arts:
            names |= {a.name}
        for loc in bp.locations:
            names |= {loc.name, *loc.aliases}
    names = {n for n in names if len(n) >= 2}
    for key, variants in TEMPLATES.items():
        assert 2 <= len(variants) <= 3, key
        for template in variants:
            assert 0 < len(bare(template)) <= 10, template
            assert set(re.findall(r"\{(\w+)\}", template)) <= SLOTS, template
            assert not [n for n in names if n in bare(template)], template


def test_phrasing_is_deterministic_by_intent_and_never_half_rendered() -> None:
    slots = (("npc", "左子穆"),)
    picks = {render("talk", slots, f"{n:08x}") for n in range(6)}
    assert picks == {t.format(npc="左子穆") for t in TEMPLATES["talk"]}  # 不同的意图哈希换着说
    assert render("talk", slots, "0000000a") == render("talk", slots, "0000000a")
    with pytest.raises(KeyError):
        render("take.ask", slots, "00000000")  # 缺了物：宁可炸，也不下发半截标签


async def test_options_carry_the_risk_of_their_worst_outcome() -> None:
    state, snap = await scene("loc:无量山")
    options = every(state, snap)
    assert all(o.risk is risk_of(rules.stakes(o.intent, state, snap)) for o in options)
    assert {o.risk for o in options if o.intent.action_type in (ActionType.OBSERVE, ActionType.TAKE)
            and o.intent.approach is Approach.PLAIN} == {Risk.SAFE}  # 确定之事稳妥
    crocodile = next(o for o in options if o.intent.action_type is ActionType.ATTACK and o.intent.target_entity == "南海鳄神")
    assert crocodile.risk is Risk.GRAVE  # 不入流挑衅一流狠辣之人：极端找死
    steal = next(o for o in options if o.intent.approach is Approach.STEALTH)
    assert steal.risk is Risk.RISKY and "无量剑" in steal.label  # 暗取最坏是失手：有险，不露结局


# ============================================================
#  Person —— 一个人身上的几种招
# ============================================================
async def test_a_person_offers_moves_by_the_compatibility_table() -> None:
    state, snap = await scene("loc:无量山")
    options = every(state, snap)
    assert all(isinstance(rules.adjudicate(o.intent, state, snap), rules.Approval) for o in options)
    assert len({o.id for o in options}) == len(options)
    assert {(ActionType.TALK, Approach.PLAIN), (ActionType.TALK, Approach.WORDS), (ActionType.ATTACK, Approach.FORCE),
            (ActionType.LEARN, Approach.WORDS)} <= moves(options, "左子穆")
    sword = [o for o in options if o.intent.action_type is ActionType.TAKE and o.intent.target_entity == "无量剑"]
    assert {o.intent.approach for o in sword} == {Approach.WORDS, Approach.STEALTH, Approach.FORCE}  # 讨要 / 偷取 / 夺物各一条
    assert len({o.label for o in sword}) == 3 and len({o.category for o in sword}) == 3
    befriend = [o for o in options if o.intent.aim is Aim.BEFRIEND]
    assert {o.intent.target_entity for o in befriend} == {"左子穆", "龚光杰", "辛双清", "南海鳄神"}  # 漠然者皆可结交
    assert all(o.why == "可结善缘" for o in befriend)


async def test_regard_decides_befriend_defuse_or_nothing() -> None:
    """敌视 / 戒备者给「化解」，友善以上者不再给「结交」（交涉至多推到友善），不向仇人恳求传功。"""
    hostile = RelationChanged(character_id="chr:龚光杰", attitude=Attitude.HOSTILE, cause="遭你出手相攻")
    wary = RelationChanged(character_id="chr:左子穆", attitude=Attitude.WARY, cause="见你出手")
    friend = RelationChanged(character_id="chr:辛双清", attitude=Attitude.FRIENDLY, cause="你替她解围")
    state, snap = await scene("loc:无量山", hostile, wary, friend)
    options = every(state, snap)
    aims = {o.intent.target_entity: o.intent.aim for o in options if o.intent.approach is Approach.WORDS
            and o.intent.action_type is ActionType.TALK}
    assert aims == {"龚光杰": Aim.DEFUSE, "左子穆": Aim.DEFUSE, "南海鳄神": Aim.BEFRIEND}  # 辛双清已是友善，无可再图
    assert not [o for o in options if o.intent.action_type is ActionType.LEARN and o.intent.target_entity == "龚光杰"]


def _pleas(options: tuple[ActionOption, ...]) -> list[ActionOption]:
    return [o for o in options if o.intent.action_type is ActionType.LEARN and o.intent.approach is Approach.WORDS]


async def test_pleading_appears_only_when_the_master_refuses() -> None:
    """求艺（寻常）由 LearnRule 找肯教之人；「恳请某人传授某功」只在人人不肯、改走交涉时才是一招。"""
    state, snap = await scene("loc:无量山")
    pleas = _pleas(every(state, snap))
    assert {o.intent.target_entity for o in pleas} == {"左子穆", "龚光杰", "辛双清"}  # 三人都素无交情
    assert all(rules.adjudicate(o.intent, state, snap).route.value == "交" for o in pleas)  # type: ignore[union-attr]
    assert all(o.why == "恳求或能打动" for o in pleas)
    friend = RelationChanged(character_id="chr:辛双清", attitude=Attitude.FRIENDLY, cause="你替她解围")
    state, snap = await scene("loc:无量山", friend)
    options = every(state, snap)
    assert _pleas(options) == []  # 辛双清肯教：向谁恳求都由肯教的人传，不必恳求
    plain = [o for o in options if o.intent.action_type is ActionType.LEARN and o.intent.approach is Approach.PLAIN]
    assert len(plain) == 1 and "辛双清" in plain[0].label


def _fact(known: bool, unlock: FactUnlock | None = None) -> FactView:
    return FactView(id="fact:东西宗相争", text="左子穆与辛双清东西宗相争多年", subject_ids=("chr:左子穆", "chr:辛双清"),
                    knower_ids=("chr:左子穆",), unlock=unlock, known=known)


def _with_facts(snap: LocalSnapshot, *facts: FactView) -> LocalSnapshot:
    return snap.model_copy(update={"facts": facts, "labels": {**snap.labels, **{f.id: f.text for f in facts}}})


async def test_probing_needs_an_unknown_fact_the_person_knows_and_never_spells_it() -> None:
    state, snap = await scene("loc:无量山")
    assert not [o for o in every(state, snap) if o.intent.aim is Aim.PROBE]  # 无可打听
    secret = _with_facts(snap, _fact(known=False))
    probes = [o for o in every(state, secret) if o.intent.aim is Aim.PROBE]
    assert [o.intent.target_entity for o in probes] == ["左子穆"]  # 只向知情人打听
    assert probes[0].why == "他或知内情" and "左子穆" in probes[0].label
    assert not any(part in o.label for o in every(state, secret) for part in ("东西宗", "相争", "多年"))  # 标签从不带见闻正文
    known = _with_facts(snap, _fact(known=True))
    assert not [o for o in every(state, known) if o.intent.aim is Aim.PROBE]  # 已知不再打听


async def test_leverage_is_offered_only_when_the_domain_finds_a_lever() -> None:
    state, snap = await scene("loc:无量山")
    assert not [o for o in every(state, snap) if o.intent.approach is Approach.LEVERAGE]  # 无势可借不出借势
    lever = _with_facts(snap, _fact(known=True, unlock=FactUnlock(kind="LEVERAGE", target_id="chr:左子穆")))
    levered = [o for o in every(state, lever) if o.intent.approach is Approach.LEVERAGE]
    assert {o.intent.target_entity for o in levered} == {"左子穆", "辛双清"}  # 把柄的主体与 unlock 目标
    assert all(o.why == "有势可借" and o.intent.action_type is ActionType.TALK for o in levered)


# ============================================================
#  Ground / Self
# ============================================================
async def test_ground_offers_only_what_can_be_carried_and_drugs_by_the_wound() -> None:
    state, snap = await scene("loc:无量山")
    jade = ItemView(id="itm:无量玉璧", name="无量玉璧", holder_id="loc:无量山", portable=False)
    salve = ItemView(id="itm:金创药", name="金创药", holder_id=PID, use=ItemUse(effect="疗伤", potency=2))
    antidote = ItemView(id="itm:蛇药", name="秘制蛇药", holder_id=PID, use=ItemUse(effect="解毒", potency=2))
    world = snap.model_copy(update={"items": (*snap.items, jade, salve, antidote)})
    labels = [o.label for o in every(state, world)]
    assert not [x for x in labels if "无量玉璧" in x]  # 不可携带：物性闸门把它挡在菜单外
    assert not [x for x in labels if "金创药" in x or "蛇药" in x]  # 无伤不疗；解毒之药 P1 无毒可解，不诱人白白吃掉
    hurt, snap = await scene("loc:无量山", WOUNDED)
    world = snap.model_copy(update={"items": (*snap.items, salve, antidote)})
    drugs = [o for o in every(hurt, world) if o.intent.action_type is ActionType.USE]
    assert [(o.intent.item_used, o.category, o.why) for o in drugs] == [("金创药", OptionCategory.RECOVER, "伤药在身")]


# ============================================================
#  Thread —— 心事未了，换个手段
# ============================================================
REFUSED = ActionFailed(action=ActionType.LEARN, target="无量剑法", reason_code="UNWILLING", reason="左子穆不肯。",
                       unlock="友善", target_id="chr:左子穆", subject_id="art:无量剑法")


async def test_a_refused_lesson_offers_another_approach() -> None:
    """求教被拒（寻常）开出线索：下一回合菜单里有「恳请」——还没试过的言辞，why 写「换个手段」，跟进席也是它。"""
    state, snap = await scene("loc:无量山", Conversed(npc_id="chr:左子穆"), REFUSED)
    assert state.threads and state.threads[0].tried == (Approach.PLAIN,)
    menu = OptionGenerator().generate(state, snap)
    plea = next(o for o in menu if o.intent.action_type is ActionType.LEARN)
    assert plea.intent.approach is Approach.WORDS and plea.why == "换个手段" and plea is menu[0]
    assert plea.intent.target_entity == "左子穆"
    assert plea.label in {t.format(npc="左子穆", art="无量剑法") for t in TEMPLATES["plead"]}


async def test_after_words_the_thread_turns_to_favor_and_the_old_plea_goes_stale() -> None:
    tried = Parleyed(npc_id="chr:左子穆", aim=Aim.LEARN, approach=Approach.WORDS, outcome=SocialOutcome.NOTHING,
                     subject_id="art:无量剑法")
    state, snap = await scene("loc:无量山", REFUSED, tried)
    assert state.threads[0].tried == (Approach.PLAIN, Approach.WORDS)
    options = every(state, snap)
    favor = [o for o in options if o.why == "换个手段"]
    assert [(o.intent.action_type, o.intent.approach) for o in favor] == [(ActionType.LEARN, Approach.FAVOR)]
    assert OptionGenerator().generate(state, snap)[0] == favor[0]  # 心事 +3、焦点 +5：跟进席就是它
    words = next(o for o in options if o.intent.action_type is ActionType.LEARN and o.intent.approach is Approach.WORDS
                 and o.intent.target_entity == "左子穆")
    assert words.why != "换个手段"  # 试过的言辞仍是合法的一招，只是纠缠不休（−2），不再说成换个手段
    away, elsewhere = await scene("loc:大理城", REFUSED, tried)
    assert away.threads and not [o for o in every(away, elsewhere) if o.why in ("换个手段", "心事未了")]  # 对象不在眼前


# ============================================================
#  席位上限
# ============================================================
def _person(o: ActionOption, state, snap) -> str | None:  # type: ignore[no-untyped-def]
    ok = rules.adjudicate(o.intent, state, snap)
    assert isinstance(ok, rules.Approval)
    return next((x for x in (ok.target, ok.source) if x and x.startswith("chr:")), None)


@pytest.mark.parametrize("at", ["loc:无量山", "loc:大理城"])
async def test_one_person_never_takes_more_than_two_seats(at: str) -> None:
    """MMR 之外的硬上限：同一对象至多两席——左子穆身上有七种招（攀谈、结交、恳请、出手、讨要、偷取、夺剑），菜单只给他两席。"""
    focus = Conversed(npc_id="chr:左子穆" if at == "loc:无量山" else "chr:段正淳")  # 线索的对象（左子穆）只在无量山
    state, snap = await scene(at, focus, REFUSED)
    assert len([o for o in every(state, snap) if _person(o, state, snap) == focus.npc_id]) >= 3
    menu = OptionGenerator().generate(state, snap)
    people = Counter(p for o in menu if (p := _person(o, state, snap)))
    assert 3 <= len(menu) <= 4 and max(people.values()) <= 2
    assert len({(o.intent.action_type, o.intent.approach) for o in menu}) >= 3


async def test_a_foiled_theft_offers_asking_instead() -> None:
    """暗取未遂开出夺物线索：下一回合对失主出一条没试过的手段（偏好次序里第一条：言辞讨要），why 写「换个手段」。"""
    foiled = Maneuvered(item_id="itm:无量剑", target_id="chr:左子穆", approach=Approach.STEALTH, outcome=CovertOutcome.FOILED)
    state, snap = await scene("loc:无量山", foiled)
    retry = [o for o in every(state, snap) if o.why == "换个手段"]
    assert [(o.intent.action_type, o.intent.approach, o.intent.target_entity) for o in retry] == [
        (ActionType.TAKE, Approach.WORDS, "无量剑")
    ]
    assert retry[0] in OptionGenerator().generate(state, snap)


def test_option_ids_are_still_direction_plus_intent_hash() -> None:
    intent = PlayerIntent(action_type=ActionType.TALK, target_entity="段誉", approach=Approach.WORDS, aim=Aim.BEFRIEND)
    option = ActionOption.of(OptionCategory.SOCIAL, "x", Command(intent=intent, time_cost=1))
    assert option.id == f"social-{ActionOption.digest(intent)}" and len(ActionOption.digest(intent)) == 8
    assert option.id == ActionOption.of(OptionCategory.SOCIAL, "y", intent.model_copy(update={"motivation": "z"})).id


async def test_ids_follow_the_normalized_command() -> None:
    """id 取规整后的意图：讨要他人之物时兼容表补上的所图（讨要）就在指令里，同一条指令只有一个 id。"""
    state, snap = await scene("loc:无量山")
    ask = next(o for o in every(state, snap) if o.intent.action_type is ActionType.TAKE and o.intent.approach is Approach.WORDS)
    assert ask.intent.aim is Aim.ASK and ask.id == f"social-{ActionOption.digest(ask.intent)}"


# ============================================================
#  问路 —— 有未知去处，就有人可问
# ============================================================
def _ways(options: tuple[ActionOption, ...]) -> list[ActionOption]:
    return [o for o in options if o.intent.action_type is ActionType.TALK and o.intent.topic]


async def test_asking_the_way_needs_an_unknown_road_and_a_willing_soul() -> None:
    state, snap = await scene("loc:无量山")
    assert [e.known for e in snap.exits] == [False, False]
    ways = _ways(every(state, snap))
    assert len(ways) == 1  # 满屋子的人只出一条
    ask = ways[0]
    assert ask.intent.topic == "无量山" and ask.intent.approach is Approach.PLAIN and ask.category is OptionCategory.EXPLORE
    assert ask.tactical_axis is TacticalAxis.OBSERVE and ask.why == "前路未明"
    assert ask.label in {t.format(npc=ask.intent.target_entity) for t in TEMPLATES["ask.way"]}
    assert not any(e.to_name in ask.label or e.label in ask.label for e in snap.exits)  # 标签不带地名
    told = next(e for e in rules.decide(ask.intent, state, snap) if isinstance(e, PlacesLearned))
    assert set(told.location_ids) == {"loc:大理城", "loc:无量玉洞"}  # 规则把四下的未知去处指给你
    friend = RelationChanged(character_id="chr:辛双清", attitude=Attitude.FRIENDLY, cause="你替她解围")
    state, snap = await scene("loc:无量山", friend)
    assert [o.intent.target_entity for o in _ways(every(state, snap))] == ["辛双清"]  # 人情最好的那位
    state, snap = await scene("loc:无量山", told)
    assert all(e.known for e in snap.exits) and _ways(every(state, snap)) == []  # 去处都认得了，不再问路
    foes = [RelationChanged(character_id=c, attitude=Attitude.HOSTILE, cause="遭你出手相攻")
            for c in ("chr:段延庆", "chr:段正淳", "chr:段誉")]
    state, snap = await scene("loc:大理城", *foes)
    assert _ways(every(state, snap)) == []  # 在场的只有仇人：不问


# ============================================================
#  compose —— 说书人的挑选过闸
# ============================================================
SCENE = ("左子穆", "龚光杰", "辛双清", "南海鳄神", "无量剑", "无量剑法", "玉佩", "无量山")
CANON = frozenset({"左子穆", "龚光杰", "辛双清", "南海鳄神", "无量剑", "无量剑法", "玉佩", "无量山", "段誉", "大理城", "北冥神功", "风"})


async def _menus() -> tuple[tuple[ActionOption, ...], tuple[ActionOption, ...]]:
    state, snap = await scene("loc:无量山")
    gen = OptionGenerator()
    return gen.catalogue(state, snap), gen.generate(state, snap)


def _picks(*pairs: tuple[str, str]) -> MenuPicks:
    return MenuPicks(tuple(MenuPick(k, f) for k, f in pairs))


@pytest.mark.parametrize(("text", "ok"), [
    ("按剑而立，冷眼看左子穆", True),
    ("「拂袖而去」。", True),  # 引号与句末句号剥掉
    ("剑", False),  # 不足两字
    ("一" * 25, False),
    ("暗中出手\n再说", False),
    ("以 m1 出手", False),
    ("连出3剑", False),
    ("连出３剑", False),
    ("〈动作〉", False),
    ("一剑毙命", False),
    ("出手制住龚光杰", False),
    ("好言相劝，令他如愿", False),
    ("向段誉讨教", False),  # 场景之外的原著名字
    ("问左子穆讨无量剑法", True),  # 长名先剔：「无量剑法」不被「无量剑」剔成「法」
    ("迎风出剑", True),  # 单字名不作数
])
def test_flavor_gate(text: str, ok: bool) -> None:
    assert (flavor(text, SCENE, CANON) is not None) is ok
    assert set(VERDICT_WORDS) >= {"得手", "制住", "毙命", "重伤", "如愿", "翻脸", "败露"}


async def test_compose_dresses_the_picks_and_falls_back_on_plain_labels() -> None:
    catalogue, fallback = await _menus()
    picks = _picks(("m2", "按剑而立，冷眼旁观"), (" M5 ", "一剑毙命"), ("m2", "再来一次"), ("m99", "越界"), ("m7", "按剑而立，冷眼旁观"))
    menu = compose(catalogue, picks, fallback, scene_names=SCENE, canon_names=CANON)
    assert [o.id for o in menu[:3]] == [catalogue[1].id, catalogue[4].id, catalogue[6].id]
    assert menu[0].flavor_text == "按剑而立，冷眼旁观" and menu[0].label == catalogue[1].label  # 过闸的换上风味，朴素标签照留
    assert menu[1].flavor_text == catalogue[4].label  # 写了结果：招照留，文案退回朴素标签
    assert menu[2].flavor_text == catalogue[6].label  # 文案重复：退回朴素标签
    assert len(menu) == 3 and len({o.id for o in menu}) == 3
    assert all(o.underlying_command == next(c for c in catalogue if c.id == o.id).underlying_command for o in menu)


async def test_compose_fills_caps_and_survives_silence() -> None:
    catalogue, fallback = await _menus()
    one = compose(catalogue, _picks(("m1", "伸手去拾")), fallback, scene_names=SCENE, canon_names=CANON)
    assert len(one) == 3 and one[0].flavor_text == "伸手去拾"
    assert [o.id for o in one[1:]] == [o.id for o in fallback if o.id != catalogue[0].id][:2]  # 退路按次序补、按 id 去重
    assert all(o.flavor_text == o.label for o in one[1:])
    many = compose(catalogue, _picks(*((f"m{i}", f"第{'一二三四五六'[i - 1]}招") for i in range(1, 7))), fallback,
                   scene_names=SCENE, canon_names=CANON)
    assert len(many) == MAX_SEATS and [o.flavor_text for o in many] == ["第一招", "第二招", "第三招", "第四招"]
    for silent in (None, MenuPicks(), _picks(("m99", "越界"))):
        assert compose(catalogue, silent, fallback, scene_names=SCENE, canon_names=CANON) == fallback  # 一招没挑中：原样退路
