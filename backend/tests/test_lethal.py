"""
[INPUT]: 依赖 app.director.lethal 的 judge，依赖 conftest 的 world 夹具
[OUTPUT]: 致死预判的规则单测
[POS]: tests 中守护"敌意 ∧ 点名 ∧ 在场 ∧ 无绝学"四要素判定的用例集
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from app.director.lethal import judge

MEMORY = "邻桌一条魁梧大汉独据一桌——那便是丐帮帮主乔峰。"


def test_provoking_present_grandmaster_is_lethal(world):
    verdict = judge("掀翻乔峰的酒桌，大骂他是契丹狗", world, MEMORY)
    assert verdict.lethal and verdict.killer.name == "萧峰"


def test_alias_resolves_to_same_grandmaster(world):
    assert judge("偷袭北乔峰", world, "北乔峰正在饮酒").killer.name == "萧峰"


def test_benign_compound_words_are_not_hostile(world):
    assert not judge("向小二打听乔峰的来历", world, MEMORY).lethal
    assert not judge("偷偷打量乔峰", world, MEMORY).lethal


def test_absent_grandmaster_cannot_kill(world):
    assert not judge("我要去少林寺挑战扫地僧", world, MEMORY).lethal


def test_hostility_without_named_target_is_left_to_llm(world):
    assert not judge("拔刀砍向那大汉", world, MEMORY).lethal


def test_mastery_lifts_the_death_sentence(world):
    master = world.model_copy(update={"physical_state": "身负北冥神功"})
    assert not judge("挑战乔峰", master, MEMORY).lethal
