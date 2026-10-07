"""
[INPUT]: 依赖 pytest 的 parametrize，依赖 app.director.lethal 的 judge / Verdict，依赖 app.schemas 的 PlayerState，依赖 conftest 的 PLAYER
[OUTPUT]: 致死预判 judge(action, player, present) 的规则单测：在场高手被点名挑衅（含别名）必死且凶手归一为被点名者的本名
          （多位高手同场时不取名录或在场名单首位）、
          无害复合词剔除且不吞掉真敌意、点名者不在场 / 在场名单为空（不回退叙事原文）/ 只说"那大汉"一律交还大模型、
          绝学只读 martial_arts 且只认绝学名录（buffs 里的"北冥神功尽失"不免死）
[POS]: tests 中守护"无绝学 ∧ 敌意 ∧ 点名 ∧ 在场（只读实体账）→ 必死"四要素判定的纯函数用例集，不碰事件库与大模型
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import pytest

from app.director.lethal import Verdict, judge
from app.schemas import PlayerState
from conftest import PLAYER

QIAO = ("萧峰",)  # 局部环境实体账登记的是本名


def _killer(verdict: Verdict) -> str | None:
    return verdict.killer.name if verdict.killer is not None else None


def _player(**ledgers: tuple[str, ...]) -> PlayerState:
    return PlayerState.model_validate(PLAYER.model_dump() | ledgers)


# ============================================================
#  四要素俱全 —— 必死
# ============================================================
@pytest.mark.parametrize(
    ("action", "present", "killer"),
    [
        ("刺杀萧峰", QIAO, "萧峰"),
        ("掀翻乔峰的酒桌，大骂他是契丹狗", QIAO, "萧峰"),
        ("偷袭北乔峰", ("段誉", "萧峰"), "萧峰"),
        ("向乔帮主吐口水", ("乔峰",), "萧峰"),
        # 凶手是被点名的那位，而非名录首位或在场名单首位：处决叙事的杀招据此取材
        ("向星宿老仙吐口水", ("萧峰", "丁春秋"), "丁春秋"),
    ],
    ids=["name", "alias_qiao_feng", "alias_bei_qiao_feng", "alias_in_both", "named_master_not_first_present"],
)
def test_provoking_present_grandmaster_is_lethal(action: str, present: tuple[str, ...], killer: str) -> None:
    assert _killer(judge(action, PLAYER, present)) == killer


# ============================================================
#  敌意 —— 先剔除无害复合词
# ============================================================
@pytest.mark.parametrize("action", ["向小二打听乔峰的来历", "上下打量乔峰", "偷偷跟在乔峰身后"])
def test_benign_compound_words_are_not_hostile(action: str) -> None:
    assert not judge(action, PLAYER, QIAO).lethal


def test_benign_words_do_not_mask_real_hostility() -> None:
    assert _killer(judge("打听到乔峰的下落后偷袭他", PLAYER, QIAO)) == "萧峰"


# ============================================================
#  点名 ∧ 在场 —— 只读结构化实体账，指代交给大模型
# ============================================================
@pytest.mark.parametrize("present", [("段誉", "王语嫣"), ("扫地僧",)], ids=["commoners", "another_grandmaster"])
def test_named_grandmaster_not_present_cannot_kill(present: tuple[str, ...]) -> None:
    assert not judge("挑战乔峰", PLAYER, present).lethal


def test_empty_presence_never_falls_back_to_narration() -> None:
    assert not judge("掀翻乔峰的酒桌", PLAYER, ()).lethal


def test_unnamed_target_is_left_to_llm() -> None:
    assert not judge("拔刀砍向那大汉", PLAYER, QIAO).lethal


# ============================================================
#  绝学 —— 只读 martial_arts，只认绝学名录
# ============================================================
def test_master_art_lifts_the_death_sentence() -> None:
    assert not judge("挑战乔峰", _player(martial_arts=("北冥神功",)), QIAO).lethal


@pytest.mark.parametrize(
    "ledgers",
    [{"buffs_debuffs": ("北冥神功尽失",)}, {"martial_arts": ("太祖长拳",)}],
    ids=["lost_art_in_buffs", "common_art"],
)
def test_only_listed_master_arts_grant_immunity(ledgers: dict[str, tuple[str, ...]]) -> None:
    assert _killer(judge("挑战乔峰", _player(**ledgers), QIAO)) == "萧峰"
