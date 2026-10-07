"""
[INPUT]: 依赖 app.director.lethal 的 judge，依赖 pytest 的 parametrize，依赖 conftest 的 game 夹具
[OUTPUT]: 致死预判的规则单测（含情报隔离：内心念头不是冒犯，只审表面行为）
[POS]: tests 中守护"敌意 ∧ 点名 ∧ 在场 ∧ 无绝学"四要素判定的用例集
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import pytest

from app.director.lethal import judge

MEMORY = "邻桌一条魁梧大汉独据一桌——那便是丐帮帮主乔峰。"


def test_provoking_present_grandmaster_is_lethal(game):
    verdict = judge("掀翻乔峰的酒桌，大骂他是契丹狗", game.player_state, MEMORY)
    assert verdict.lethal and verdict.killer.name == "萧峰"


def test_alias_resolves_to_same_grandmaster(game):
    assert judge("偷袭北乔峰", game.player_state, "北乔峰正在饮酒").killer.name == "萧峰"


def test_benign_compound_words_are_not_hostile(game):
    assert not judge("向小二打听乔峰的来历", game.player_state, MEMORY).lethal
    assert not judge("偷偷打量乔峰", game.player_state, MEMORY).lethal


def test_absent_grandmaster_cannot_kill(game):
    assert not judge("我要去少林寺挑战扫地僧", game.player_state, MEMORY).lethal


def test_hostility_without_named_target_is_left_to_llm(game):
    assert not judge("拔刀砍向那大汉", game.player_state, MEMORY).lethal


def test_mastery_lifts_the_death_sentence(game):
    master = game.player_state.model_copy(update={"martial_arts": ["北冥神功"]})
    assert not judge("挑战乔峰", master, MEMORY).lethal


@pytest.mark.parametrize("thought", ["心里暗骂乔峰是个莽夫", "低头喝酒，暗自盘算日后如何打败乔峰", "默默在心中咒骂乔峰"])
def test_private_thoughts_are_not_provocation(game, thought: str):
    # 情报隔离：乔峰听不见玩家心里的话，规则层也不替他读心
    assert not judge(thought, game.player_state, MEMORY).lethal


@pytest.mark.parametrize("action", ["心想管他的，一拳打在乔峰脸上", "心下一横，拔刀砍向乔峰"])
def test_visible_provocation_after_a_thought_still_kills(game, action: str):
    assert judge(action, game.player_state, MEMORY).killer.name == "萧峰"


def test_naming_counts_only_in_visible_behaviour(game):
    # 心里骂的是乔峰，手上打的是段誉：乔峰不会因为一个念头出手
    assert not judge("心里暗骂乔峰，转身一拳打向段誉", game.player_state, MEMORY).lethal


def test_covert_attack_is_still_visible(game):
    # "暗自"只是不声张，刀砍下去对方看得见：它不是心理动词，不能被当作念头剥掉
    assert judge("暗自拔刀砍向乔峰", game.player_state, MEMORY).killer.name == "萧峰"
