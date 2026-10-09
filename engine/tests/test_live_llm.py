"""
[INPUT]: 依赖 app.config 的 Settings / LLMRole（读 engine/.env 的真实配置），依赖 app.infrastructure.llm 的 build_llm 与 CallBudget，
         依赖 app.application 的 LLMIntentParser / scene_names / LLMNarrator / MenuPicks / LLMResolutionAgent / OptionGenerator / compose 与 npc_agent 的
         LLMAgendaPlanner / LLMEncounterJudge / NpcDirector，依赖 app.domain.rules 的 envelope，依赖 app.domain.resolution 的 ResolutionOutput / settle，
         依赖 tests/test_npc_agent 的 H-Agent 小地图（MAP / ATLAS / MEETING / CROSSING / look / world），
         依赖 app.infrastructure.knowledge_extractor 的抽取管道，
         依赖 tests/test_rules 的 scene() 快照工厂，依赖 tests/fixtures/sample_passage.txt（自撰梗概，非原著文本）
[OUTPUT]: 真实大模型回归用例（标记 live，设置 TLBB_TEST_LIVE_LLM=1 才跑，会产生费用）：运行期三职责各走一遍真实厂商——
          意图解析把华丽描写降维且规整为正名（迷雾里的移动落在方位把手上）、叙事流式且分片且同一次调用交出 <menu>（正文不漏菜单的字、
          至少三席风味过 compose 的闸门、覆盖不同战术维度）、地下城主对龚光杰一战交出合契约的推演（推理四段 + 符号层，而非失灵退回规则）且推出的结局过得了闸门（不被整份作废）；
          议程职责的结构化输出被接受且经 admit 放行至少一条；判官（地下城主职责）在撞见的结果已定边界里定案、狭路相逢只在可裁区间里挑结局；
          结构化抽取被厂商接受并能组装成蓝图——抽取由 Claude 子代理承担，这一例另须 TLBB_TEST_LIVE_EXTRACTION=1
[POS]: tests 的提示词与厂商契约护栏：改动提示词、schema 规整或换模型之后跑一次，确认真实模型仍守协议（选项风味封装、议程与判官同样在列）；
       整场共用一份保险丝（至多 LIVE_CALLS 次请求），一次回归的花费有顶
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import os
from pathlib import Path

import pytest

from app.application.intent_parser import LLMIntentParser, scene_names
from app.application.narrator import LLMNarrator, MenuPicks, NarrationRequest
from app.application.npc_agent import CanonicalJudge, LLMAgendaPlanner, LLMEncounterJudge, NpcDirector, NullPlanner
from app.application.options import OptionGenerator, compose
from app.application.ports import LLMClient
from app.application.resolution_agent import LLMResolutionAgent
from app.config import LLMRole, Settings
from app.domain import rules
from app.domain.events import AgendaIssued, AgendaPlanned, EncounterResolved
from app.domain.intent import ActionType, PlayerIntent
from app.domain.npc import skirmish_stakes
from app.domain.resolution import ResolutionOutput, settle
from app.infrastructure.knowledge_extractor import LLMKnowledgeExtractor, SeedingPipeline, SourceDocument
from app.infrastructure.llm.budget import CallBudget
from app.infrastructure.llm.factory import build_llm
from tests.test_npc_agent import ATLAS as H_ATLAS
from tests.test_npc_agent import CANON as H_CANON
from tests.test_npc_agent import CAVE as H_CAVE
from tests.test_npc_agent import CROSSING as H_CROSSING
from tests.test_npc_agent import MAP as H_MAP
from tests.test_npc_agent import MEETING as H_MEETING
from tests.test_npc_agent import PATH as H_PATH
from tests.test_npc_agent import look as h_look
from tests.test_npc_agent import world as h_world
from tests.test_rules import scene
from tests.world import WORLD

pytestmark = pytest.mark.live

SAMPLE = Path(__file__).parent / "fixtures" / "sample_passage.txt"
LIVE_CALLS = 24  # 四句意图 + 叙事（含菜单）+ 地下城主 + 议程 + 两场判官（各含重采样）用不满：多出来的请求只可能来自失控
FUSE = CallBudget(LIVE_CALLS)


def client(role: LLMRole) -> LLMClient:
    if os.environ.get("TLBB_TEST_LIVE_LLM") != "1":
        pytest.skip("未设置 TLBB_TEST_LIVE_LLM=1")
    llm = build_llm(Settings(), role, FUSE)
    if llm is None:
        pytest.skip("engine/.env 的 LLM_PROVIDER 是 mock")
    return llm


@pytest.mark.parametrize(
    ("at", "text", "action", "target"),
    [
        ("loc:无量山", "我躬身向那位西宗女掌门行礼，恳请她传我几招剑法", ActionType.LEARN, "辛双清"),
        ("loc:无量山", "顺着峭壁上的藤蔓，小心翼翼地往崖底攀去", ActionType.MOVE, "崖下"),  # 迷雾里只给方位把手：落在「崖下」那条出路上即可
        ("loc:大理城", "双手捧着那枚温润的玉佩，恭恭敬敬还给王爷", ActionType.GIVE, None),
        ("loc:无锡城", "我大喝一声，一拳朝那丐帮帮主面门打去", ActionType.ATTACK, "乔峰"),
    ],
)
async def test_live_intent(at: str, text: str, action: ActionType, target: str | None) -> None:
    _, snap = await scene(at)
    intent = await LLMIntentParser(client(LLMRole.INTENT)).parse(text, snap)
    assert intent.action_type is action
    if action is ActionType.MOVE and target is not None:
        way = next(e for e in snap.exits if e.label == target)
        assert intent.target_entity in snap.exit_names(way)  # 方位把手（「下」）或标签，都落在同一条出路上
    elif target is not None:
        assert intent.target_entity is not None and target in intent.target_entity


async def test_live_narration_streams_and_hands_in_a_flavoured_menu() -> None:
    """叙事流式分片，正文里没有 <menu> 的一个字；同一次调用交出的菜单过得了 compose 的闸门，至少三席换上了说书人的风味。"""
    state, snap = await scene("loc:无量山")
    catalogue = OptionGenerator().catalogue(state, snap)
    request = NarrationRequest(snapshot=snap, facts=("阿星初入江湖，现身于无量山。",), player_text=None, menu=catalogue)
    items = [c async for c in LLMNarrator(client(LLMRole.NARRATION)).narrate(request)]
    text = "".join(c for c in items if isinstance(c, str))
    assert len(items) > 2 and len(text) >= 60
    assert "<menu" not in text.lower() and "```" not in text and '"pick"' not in text
    picks = items[-1]
    assert isinstance(picks, MenuPicks) and 3 <= len(picks.picks) <= 4, picks
    offered = compose(catalogue, picks, OptionGenerator().generate(state, snap), scene_names=scene_names(snap),
                      canon_names=frozenset(n for c in WORLD.characters for n in c.names))
    flavoured = [o for o in offered if o.flavor_text != o.label]
    assert len(flavoured) >= 3, [(o.label, o.flavor_text) for o in offered]  # 风味过闸，而不是退回朴素标签
    assert len({o.tactical_axis for o in offered}) >= 2  # 不同的战术维度


async def test_live_agenda_planner_proposes_through_the_gate() -> None:
    """议程职责：结构化输出被厂商接受，提议经 admit 闸门至少放行一条（去处在三跳以内、意图合格）。"""
    planner = LLMAgendaPlanner(client(LLMRole.AGENDA), budget=30)
    director = NpcDirector(planner, CanonicalJudge(), H_ATLAS, H_MAP.personas, relations=H_MAP.relations)
    out = await director.plan(h_world(), [])
    assert isinstance(out[0], AgendaPlanned) and any(isinstance(e, AgendaIssued) for e in out), out


async def test_live_encounter_judges_meet_and_skirmish_inside_the_envelope() -> None:
    """判官（地下城主职责）：撞见在结果已定的物理边界里交出合契约的推演（由判官定案，而不是失灵退回规则）；狭路相逢只在可裁区间里挑结局。"""
    llm = client(LLMRole.RESOLUTION)
    judge = LLMEncounterJudge(llm, budget=30, canon_names=H_CANON)
    director = NpcDirector(NullPlanner(), judge, H_ATLAS, H_MAP.personas, relations=H_MAP.relations)
    state, snap = await h_look(H_PATH, *H_MEETING)
    met = await director.settle_encounters(state, snap)
    assert isinstance(met[-1], EncounterResolved) and met[-1].by == "地下城主", met
    state, snap = await h_look(H_CAVE, *H_CROSSING)
    crossed = await director.settle_encounters(state, snap)
    done = crossed[-1]
    assert isinstance(done, EncounterResolved) and done.by == "地下城主", crossed
    stakes = skirmish_stakes(state.encounters[0], state, H_ATLAS)
    assert done.outcome in stakes.admissible


async def test_live_game_master_reasons_through_the_gate() -> None:
    state, snap = await scene("loc:无量山")
    intent = PlayerIntent(action_type=ActionType.ATTACK, target_entity="龚光杰")
    env = rules.envelope(intent, state, snap)
    assert env is not None and env.contested
    agent = LLMResolutionAgent(client(LLMRole.RESOLUTION))
    resolution = await agent.resolve(env, snap, state, intent, "我赤手空拳，大喝一声扑向那狠辣的东宗弟子")
    assert resolution.by == "地下城主" and isinstance(resolution.proposal, ResolutionOutput)  # 守住了契约，而不是失灵退回规则
    assert resolution.proposal.collision and resolution.proposal.convergence  # 先推理、后符号
    ruled = settle(env, resolution.proposal, state, snap)
    assert ruled.adopted and ruled.outcome in env.admissible, ruled.notes  # 推出的结局过得了闸门，而不是整份作废


async def test_live_extraction_is_accepted_and_assembles() -> None:
    if os.environ.get("TLBB_TEST_LIVE_EXTRACTION") != "1":
        pytest.skip("抽取由 Claude 子代理承担，不调用付费大模型；确需验证厂商抽取契约时另设 TLBB_TEST_LIVE_EXTRACTION=1")
    pipeline = SeedingPipeline(LLMKnowledgeExtractor(client(LLMRole.EXTRACTION)))
    result = await pipeline.run([SourceDocument(SAMPLE.name, SAMPLE.read_text(encoding="utf-8"))])
    bp = result.blueprint
    assert not result.report.failed_chunks
    assert {"左子穆", "辛双清", "段誉"} <= {c.name for c in bp.characters}
    assert "北冥神功" in {a.name for a in bp.martial_arts} and bp.locations and bp.relations
