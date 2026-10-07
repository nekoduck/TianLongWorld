"""
[INPUT]: 依赖 pytest 的 parametrize，依赖 app.director.memory 的 recall，依赖 app.schemas 的 WorldEvent，依赖 conftest 的 MEMORY_LIMIT
[OUTPUT]: JIT 标签路由检索 recall() 的纯函数单测
[POS]: tests 中守护"只把与此时此地此人此招相关的世界大事注入上下文"的用例集：地点相互包含、在场人物经 kin 别名、social_traits、
       动作点名（目的地、要找的人，经 kin 别名）单向命中，单字标签防误伤、无关大事排除、保持发生先后、limit 只取最近
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Sequence

import pytest

from app.director.memory import recall
from app.schemas import WorldEvent
from conftest import MEMORY_LIMIT

# 缺省地点与下列所有标签都不相交：命中只能来自用例显式给出的那一路
ELSEWHERE = "雁门关外"


def _event(desc: str, *tags: str) -> WorldEvent:
    return WorldEvent(tags=tags, event_desc=desc)


def _recall(
    events: Sequence[WorldEvent],
    *,
    location: str = ELSEWHERE,
    entities: Sequence[str] = (),
    traits: Sequence[str] = (),
    mentioned: str = "",
    limit: int = MEMORY_LIMIT,
) -> tuple[WorldEvent, ...]:
    return recall(events, location=location, entities=entities, traits=traits, mentioned=mentioned, limit=limit)


# ============================================================
#  三路命中 —— 地点、在场人物（含别名）、玩家身份
# ============================================================
@pytest.mark.parametrize(
    ("tag", "location"),
    [("松鹤楼", "无锡松鹤楼"), ("无锡松鹤楼", "松鹤楼")],
    ids=["tag_inside_location", "location_inside_tag"],
)
def test_location_matches_by_mutual_containment(tag: str, location: str) -> None:
    burned = _event("松鹤楼被付之一炬", tag)
    assert _recall([burned], location=location) == (burned,)


def test_present_entity_matches_through_kin_aliases() -> None:
    insulted = _event("玩家当众辱骂乔峰", "乔峰")
    assert _recall([insulted], entities=["萧峰"]) == (insulted,)


def test_social_trait_matches() -> None:
    feud = _event("丐帮长老围攻聚贤庄", "丐帮")
    assert _recall([feud], traits=["丐帮弟子"]) == (feud,)


# ============================================================
#  过滤 —— 单字防误伤、无关排除
# ============================================================
@pytest.mark.parametrize(
    ("tag", "location", "traits"),
    [("楼", "无锡松鹤楼", ()), ("丐帮", ELSEWHERE, ("丐",))],
    ids=["single_char_tag", "single_char_key"],
)
def test_single_character_never_matches(tag: str, location: str, traits: tuple[str, ...]) -> None:
    assert _recall([_event("一桩不相干的旧事", tag)], location=location, traits=traits) == ()


def test_unrelated_events_are_excluded() -> None:
    related = _event("玩家在松鹤楼摔碎了酒坛", "无锡松鹤楼")
    unrelated = _event("丁春秋毒杀了星宿派大弟子", "星宿海", "丁春秋")
    assert _recall([unrelated, related], location="无锡松鹤楼", entities=["萧峰"], traits=["丐帮弟子"]) == (related,)


# ============================================================
#  截取 —— 保序、只留最近 limit 条
# ============================================================
def test_preserves_chronological_order() -> None:
    first = _event("玩家初到松鹤楼", "松鹤楼")
    noise = _event("鸠摩智闯入天龙寺", "天龙寺", "鸠摩智")
    second = _event("萧峰在松鹤楼与段誉斗酒", "萧峰", "段誉")
    third = _event("松鹤楼掌柜关门歇业", "无锡松鹤楼")
    assert _recall([first, noise, second, third], location="无锡松鹤楼", entities=["乔峰"]) == (first, second, third)


def test_limit_keeps_only_the_most_recent() -> None:
    events = [_event(f"松鹤楼第{n}桩大事", "松鹤楼") for n in range(5)]
    assert _recall(events, location="无锡松鹤楼", limit=2) == tuple(events[-2:])


# ============================================================
#  第四路 —— 动作点名：此招要去的地方、要找的人
# ============================================================
@pytest.mark.parametrize(
    ("tag", "action"),
    [("聚贤庄", "连夜赶往聚贤庄"), ("萧峰", "去雁门关外找乔峰")],
    ids=["destination", "named_person_by_alias"],
)
def test_action_naming_a_tag_recalls_it(tag: str, action: str) -> None:
    event = _event("那里早已物是人非", tag)
    assert _recall([event], mentioned=action) == (event,)


def test_action_mention_is_one_way() -> None:
    # 长动作文本不能反过来"包含"短标签之外的一切：标签必须原样出现在动作里，单字标签不算
    events = [_event("丐帮长老围攻聚贤庄", "丐帮长老会"), _event("刀光一闪", "刀")]
    assert _recall(events, mentioned="拔刀向丐帮弟子讨个说法") == ()
