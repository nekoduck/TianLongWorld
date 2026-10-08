"""
[INPUT]: 依赖 app.config 的 Settings / LLMRole（读 engine/.env 的真实配置），依赖 app.infrastructure.llm.factory 的 build_llm，
         依赖 app.application 的 LLMIntentParser / LLMNarrator / LLMResolutionAgent，依赖 app.domain.rules 的 stakes，
         依赖 app.infrastructure.knowledge_extractor 的抽取管道，
         依赖 tests/test_rules 的 scene() 快照工厂，依赖 tests/fixtures/sample_passage.txt（自撰梗概，非原著文本）
[OUTPUT]: 真实大模型回归用例（标记 live，设置 TLBB_TEST_LIVE_LLM=1 才跑，会产生费用）：四种职责各走一遍真实厂商——
          意图解析把华丽描写降维且规整为正名、叙事流式且分片、结构化抽取被厂商接受并能组装成蓝图、
          地下城主对龚光杰一战给出区间内的结局（而非失灵退回规则）
[POS]: tests 的提示词与厂商契约护栏：改动提示词、schema 规整或换模型之后跑一次，确认真实模型仍守协议
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import os
from pathlib import Path

import pytest

from app.application.intent_parser import LLMIntentParser
from app.application.narrator import LLMNarrator, NarrationRequest
from app.application.ports import LLMClient
from app.application.resolution_agent import LLMResolutionAgent
from app.config import LLMRole, Settings
from app.domain.intent import ActionType, PlayerIntent
from app.domain.rules import stakes
from app.infrastructure.knowledge_extractor import LLMKnowledgeExtractor, SeedingPipeline, SourceDocument
from app.infrastructure.llm.factory import build_llm
from tests.test_rules import scene

pytestmark = pytest.mark.live

SAMPLE = Path(__file__).parent / "fixtures" / "sample_passage.txt"


def client(role: LLMRole) -> LLMClient:
    if os.environ.get("TLBB_TEST_LIVE_LLM") != "1":
        pytest.skip("未设置 TLBB_TEST_LIVE_LLM=1")
    llm = build_llm(Settings(), role)
    if llm is None:
        pytest.skip("engine/.env 的 LLM_PROVIDER 是 mock")
    return llm


@pytest.mark.parametrize(
    ("at", "text", "action", "target"),
    [
        ("loc:无量山", "我躬身向那位西宗女掌门行礼，恳请她传我几招剑法", ActionType.LEARN, "辛双清"),
        ("loc:无量山", "顺着峭壁上的藤蔓，小心翼翼地往崖底攀去", ActionType.MOVE, "崖下"),
        ("loc:大理城", "双手捧着那枚温润的玉佩，恭恭敬敬还给王爷", ActionType.GIVE, None),
        ("loc:无锡城", "我大喝一声，一拳朝那丐帮帮主面门打去", ActionType.ATTACK, "乔峰"),
    ],
)
async def test_live_intent(at: str, text: str, action: ActionType, target: str | None) -> None:
    _, snap = await scene(at)
    intent = await LLMIntentParser(client(LLMRole.INTENT)).parse(text, snap)
    assert intent.action_type is action
    if target is not None:
        assert intent.target_entity is not None and target in intent.target_entity


async def test_live_narration_streams() -> None:
    _, snap = await scene("loc:无量山")
    request = NarrationRequest(snapshot=snap, facts=("阿星初入江湖，现身于无量山。",), player_text=None)
    chunks = [c async for c in LLMNarrator(client(LLMRole.NARRATION)).narrate(request)]
    assert len(chunks) > 1 and len("".join(chunks)) >= 60


async def test_live_game_master_rules_inside_the_rails() -> None:
    state, snap = await scene("loc:无量山")
    at_stake = stakes(PlayerIntent(action_type=ActionType.ATTACK, target_entity="龚光杰"), state, snap)
    assert at_stake is not None and at_stake.contested
    agent = LLMResolutionAgent(client(LLMRole.RESOLUTION))
    resolution = await agent.resolve(at_stake, snap, state, "我赤手空拳，大喝一声扑向那狠辣的东宗弟子")
    assert resolution.by == "地下城主" and resolution.proposal is not None  # 守住了契约，而不是失灵退回规则
    assert resolution.proposal.outcome in at_stake.admissible and resolution.narrative_hint


async def test_live_extraction_is_accepted_and_assembles() -> None:
    pipeline = SeedingPipeline(LLMKnowledgeExtractor(client(LLMRole.EXTRACTION)))
    result = await pipeline.run([SourceDocument(SAMPLE.name, SAMPLE.read_text(encoding="utf-8"))])
    bp = result.blueprint
    assert not result.report.failed_chunks
    assert {"左子穆", "辛双清", "段誉"} <= {c.name for c in bp.characters}
    assert "北冥神功" in {a.name for a in bp.martial_arts} and bp.locations and bp.relations
