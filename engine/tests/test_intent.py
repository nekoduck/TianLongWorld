"""
[INPUT]: 依赖 app.application.intent_parser 的 HeuristicIntentParser / LLMIntentParser / INTENT_SYSTEM / INTENT_SCHEMA，
         依赖 app.domain.intent 的 ActionType / Approach / Aim / PlayerIntent，依赖 app.domain.approach 的 MOVES / AIMS / row_of，
         依赖 app.domain.snapshot 的视图（手搭剑湖宫一景：钟灵与闪电貂、木婉清与晓风拂柳、左子穆与神农帮的见闻、行囊里的金创药），
         依赖 tests/conftest 的 ScriptedLLM
[OUTPUT]: 意图解析 v2 单测：schema 只有形状（枚举与模型的开发者 docstring 不进 model_json_schema）、INTENT_SYSTEM 写明 USE 与七种手段 / 九种所图 /
          话题且「用于」与兼容表一致、撂话离场是 MOVE；离线解析认得手段与所图的关键词（潜行 / 计谋 / 言辞 / 威逼 / 借势 / 人情 / 打探 + 话题 / 化解 / 服药），
          话题落不了地不打探、偷袭不是潜行、借势的靠山不是说话的对象；大模型路径照传 approach / aim / topic，守卫的字段再检查带上话题
[POS]: tests 的意图解析 v2 护栏：解析器只产出「想怎么做、图什么」，表外的组合由 _fit 与 rules.normalize 同一口径退回寻常
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
from typing import Any

import pytest

from app.application.intent_parser import INTENT_SCHEMA, INTENT_SYSTEM, HeuristicIntentParser, LLMIntentParser
from app.domain.approach import AIMS, MOVES, row_of
from app.domain.intent import ActionType, Aim, Approach, PlayerIntent
from app.domain.lore import FactUnlock
from app.domain.models import Disposition, ItemUse, Tier
from app.domain.snapshot import CharacterView, ExitView, FactView, ItemView, LocalSnapshot, LocationView, SkillView

PID = "ply:阿星"
SAFE = PlayerIntent  # 读起来更像期望值


def hall(**extra: Any) -> LocalSnapshot:
    """手搭的剑湖宫一景：P1 用到的人、物、功、见闻各一份——不依赖入库蓝图，别的赛道改蓝图也不牵动这里。"""
    people = [
        CharacterView(id="chr:钟灵", name="钟灵", aliases=("灵儿",), tier=Tier.NONE, disposition=Disposition.MERCIFUL),
        CharacterView(id="chr:木婉清", name="木婉清", aliases=("木姑娘",), tier=Tier.THIRD, disposition=Disposition.RUTHLESS,
                      skill_ids=("art:晓风拂柳",)),
        CharacterView(id="chr:龚光杰", name="龚光杰", tier=Tier.THIRD, disposition=Disposition.RUTHLESS),
        CharacterView(id="chr:左子穆", name="左子穆", aliases=("左先生",), tier=Tier.SECOND, disposition=Disposition.NEUTRAL),
    ]
    items = [
        ItemView(id="itm:闪电貂", name="闪电貂", holder_id="chr:钟灵", owner_id="chr:钟灵", hazard="有毒"),
        ItemView(id="itm:金创药", name="金创药", holder_id=PID, use=ItemUse(effect="疗伤", potency=2)),
        ItemView(id="itm:通天草", name="通天草", holder_id=PID),
    ]
    feud = FactView(id="fact:神农帮结仇", text="无量剑与神农帮为采药结仇", subject_ids=("chr:左子穆",),
                    knower_ids=("chr:左子穆",), unlock=FactUnlock(kind="MOTIVE", target_id="chr:左子穆"))
    return LocalSnapshot(
        player_id=PID, player_name="阿星", alive=True, version=1,
        location=LocationView(id="loc:剑湖宫·练武厅", name="剑湖宫·练武厅"),
        exits=(ExitView(label="出厅", to_id="loc:剑湖宫", to_name="剑湖宫"),),
        characters=tuple(people), items=tuple(items), facts=(feud,),
        skills=(SkillView(id="art:晓风拂柳", name="晓风拂柳", tier=Tier.THIRD),),
        labels={"loc:剑湖宫": "剑湖宫"}, **extra,
    )


# ============================================================
#  schema 与提示词
# ============================================================
def descriptions(node: object) -> list[str]:
    if isinstance(node, dict):
        return [v for k, v in node.items() if k == "description"] + [d for v in node.values() for d in descriptions(v)]
    if isinstance(node, list):
        return [d for v in node for d in descriptions(v)]
    return []


def test_schema_carries_shape_only() -> None:
    """枚举与模型的开发者 docstring 不进 schema：「approach.py」「None 即没说」这类话不该发给大模型。"""
    schema = PlayerIntent.model_json_schema()
    assert descriptions(schema) == [] and descriptions(INTENT_SCHEMA) == []
    defs = schema["$defs"]
    assert defs["Approach"]["enum"] == [a.value for a in Approach] and defs["Aim"]["enum"] == [a.value for a in Aim]
    assert "USE" in defs["ActionType"]["enum"] and {"approach", "aim", "topic"} <= set(schema["properties"])


def test_intent_system_documents_every_approach_and_aim() -> None:
    assert "- USE：" in INTENT_SYSTEM and "topic（话题）" in INTENT_SYSTEM
    for approach in Approach:
        assert f"- {approach.value}：" in INTENT_SYSTEM, approach
    for aim in Aim:
        assert f"- {aim.value}：" in INTENT_SYSTEM, aim
    assert "撂下一句话就走" in INTENT_SYSTEM and "不写进 topic" in INTENT_SYSTEM


def test_intent_system_follows_the_compatibility_table() -> None:
    """「用于 / 见于」由兼容表生成：潜行只用于取他人之物，寻常没有「用于」，脱身只见于 MOVE。"""
    lines = {line.split("：")[0][2:]: line for line in INTENT_SYSTEM.splitlines() if line.startswith("- ")}
    assert lines["潜行"].endswith("用于 TAKE（他人之物）") and "用于" not in lines["寻常"]
    assert lines["武力"].endswith("用于 ATTACK、TAKE（他人之物）、TALK") and lines["脱身"].endswith("见于 MOVE")


def test_intent_system_examples_are_valid_intents() -> None:
    examples = [json.loads(line) for line in INTENT_SYSTEM.splitlines() if line.startswith("{")]
    parsed = [PlayerIntent.model_validate(e) for e in examples]
    assert {p.action_type for p in parsed} >= {ActionType.USE, ActionType.TAKE, ActionType.TALK, ActionType.MOVE}
    for p in parsed:  # 示例自己守兼容表：手段在这一行里，所图配得上动作
        assert p.approach in MOVES[row_of(p.action_type, held=True)], p
        assert p.aim is None or p.aim in AIMS[p.action_type], p
    assert any(p.aim is Aim.PROBE and p.topic for p in parsed)


# ============================================================
#  离线解析
# ============================================================
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("趁钟灵不备，偷偷摸走她的闪电貂", SAFE(action_type=ActionType.TAKE, target_entity="闪电貂", approach=Approach.STEALTH)),
        ("骗钟灵把闪电貂借我玩玩", SAFE(action_type=ActionType.TAKE, target_entity="闪电貂", approach=Approach.GUILE)),
        ("向钟灵讨要闪电貂", SAFE(action_type=ActionType.TAKE, target_entity="闪电貂", approach=Approach.WORDS)),
        ("念在旧日人情，求钟灵把闪电貂借我", SAFE(action_type=ActionType.TAKE, target_entity="闪电貂", approach=Approach.FAVOR)),
        ("恳请木婉清传我晓风拂柳",
         SAFE(action_type=ActionType.LEARN, target_entity="木婉清", skill_used="晓风拂柳", approach=Approach.WORDS)),
        ("喝问龚光杰", SAFE(action_type=ActionType.TALK, target_entity="龚光杰", approach=Approach.FORCE)),
        ("威胁钟灵交出闪电貂",
         SAFE(action_type=ActionType.TALK, target_entity="钟灵", approach=Approach.FORCE, aim=Aim.ASK, topic="闪电貂")),
        ("向左子穆打听神农帮",
         SAFE(action_type=ActionType.TALK, target_entity="左子穆", approach=Approach.WORDS, aim=Aim.PROBE, topic="神农帮")),
        ("向左子穆打听一下钟灵的来历",
         SAFE(action_type=ActionType.TALK, target_entity="左子穆", approach=Approach.WORDS, aim=Aim.PROBE, topic="钟灵")),
        ("向左子穆打听消息", SAFE(action_type=ActionType.TALK, target_entity="左子穆")),  # 话题落不了地：只是闲谈
        ("劝龚光杰息怒", SAFE(action_type=ActionType.TALK, target_entity="龚光杰", approach=Approach.WORDS, aim=Aim.DEFUSE)),
        ("借左子穆之名喝令龚光杰让开", SAFE(action_type=ActionType.TALK, target_entity="龚光杰", approach=Approach.LEVERAGE)),
        ("搬出左先生来压龚光杰", SAFE(action_type=ActionType.TALK, target_entity="龚光杰", approach=Approach.LEVERAGE)),
        ("服下金创药", SAFE(action_type=ActionType.USE, item_used="金创药")),
        ("掏出金创药敷在伤处，运功疗伤", SAFE(action_type=ActionType.USE, item_used="金创药")),  # 服药先于疗伤
        ("嚼了通天草吃下去", SAFE(action_type=ActionType.USE, item_used="通天草")),  # 能不能吃由规则判（NO_USE）
        ("吃了一惊，盯着龚光杰", SAFE(action_type=ActionType.TALK, target_entity="龚光杰")),  # 吃惊不是服药，也没点名东西
        ("冲龚光杰撂下一句「后会有期，咱们走着瞧」，转身离开", SAFE(action_type=ActionType.MOVE)),  # 撂话离场：话不进 topic
        ("对左子穆说「我要去无量山」", SAFE(action_type=ActionType.TALK, target_entity="左子穆")),  # 引号里的去处是话，不是动作
        ("趁龚光杰不备偷袭他", SAFE(action_type=ActionType.ATTACK, target_entity="龚光杰")),  # 偷袭是出手，潜行不合表
        ("设计诱龚光杰出手，再虚晃一招击他", SAFE(action_type=ActionType.ATTACK, target_entity="龚光杰", approach=Approach.GUILE)),
    ],
)
async def test_heuristic_reads_approach_and_aim(text: str, expected: PlayerIntent) -> None:
    assert await HeuristicIntentParser().parse(text, hall()) == expected


async def test_heuristic_grounds_topic_through_the_rules() -> None:
    """打探的话题按 rules.ground 落地：神农帮只在见闻正文里——知情人不在场，见闻不进快照，话题落不了地即退回闲谈。"""
    without = hall().model_copy(update={"facts": ()})
    assert await HeuristicIntentParser().parse("向左子穆打听神农帮", without) == SAFE(
        action_type=ActionType.TALK, target_entity="左子穆")


async def test_heuristic_never_emits_an_approach_outside_the_table() -> None:
    texts = ["偷偷向左子穆打听神农帮", "骗木婉清传我晓风拂柳", "悄悄顺手赠钟灵金创药", "哄龚光杰离开", "偷学晓风拂柳",
             "借左子穆之名拾起金创药", "看在左先生的份上，向龚光杰赔罪", "威胁龚光杰去剑湖宫"]
    for text in texts:
        intent = await HeuristicIntentParser().parse(text, hall())
        assert intent.approach in MOVES[row_of(intent.action_type, held=True)], (text, intent)
        assert intent.aim is None or intent.aim in AIMS.get(intent.action_type, frozenset()), (text, intent)


# ============================================================
#  大模型路径
# ============================================================
def reply(**fields: object) -> str:
    return json.dumps({"narrative_style": "", **fields}, ensure_ascii=False)


async def test_llm_parser_passes_approach_aim_and_topic_through() -> None:
    from tests.conftest import ScriptedLLM

    llm = ScriptedLLM(reply(action_type="TALK", target_entity="左子穆", approach="言辞", aim="打探", topic="神农帮"))
    intent = await LLMIntentParser(llm).parse("拱手向左掌门请教神农帮的来历", hall())
    assert intent == SAFE(action_type=ActionType.TALK, target_entity="左子穆", approach=Approach.WORDS, aim=Aim.PROBE,
                          topic="神农帮")
    system, _, schema = llm.calls[0]
    assert system == INTENT_SYSTEM and schema == INTENT_SCHEMA
    used = ScriptedLLM(reply(action_type="USE", item_used="金创药", approach="寻常", aim=None, topic=None))
    assert await LLMIntentParser(used).parse("把金创药敷上", hall()) == SAFE(action_type=ActionType.USE, item_used="金创药")


async def test_guard_rechecks_the_topic() -> None:
    """话题同是解析出的指称：大模型把「问问怎么修仙」规整进 topic，照样拦下，且只花一次调用。"""
    from tests.conftest import ScriptedLLM

    llm = ScriptedLLM(reply(action_type="TALK", target_entity="左子穆", approach="言辞", aim="打探", topic="修仙之法"))
    intent = await LLMIntentParser(llm).parse("向左掌门讨教长生之道", hall())
    assert intent.action_type is ActionType.INVALID and "修仙" in (intent.reason or "") and len(llm.calls) == 1
