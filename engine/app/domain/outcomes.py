"""
[INPUT]: 依赖 enum 的 StrEnum
[OUTPUT]: 对外提供 SocialOutcome（交涉的结局：如愿 / 松动 / 无果 / 碰壁 / 翻脸）、CovertOutcome（暗中行事的结局：无痕 / 未遂 / 败露 / 失手，按后果由轻到重）
[POS]: domain 的结局词汇（与 combat.CombatOutcome 并列的另两路）。单独成模块是为了断环：events.py 的 Parleyed / Maneuvered 要用它们，
       而 social.py / covert.py（阶段 B 的可裁区间与定案）又要从 events 导入事件——词汇放在两者之下，谁都能导入而不成环。
       两枚举都由对玩家最有利到最不利排列，与 CombatOutcome 同一口径：可裁区间按这个次序切片，risk_of 只看最坏的一端
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from enum import StrEnum


class SocialOutcome(StrEnum):
    """交涉（言辞 / 人情 / 借势 / 威逼 / 套话）的结局，由对玩家最有利到最不利。交涉永不致死。"""

    GRANTED = "如愿"  # 对方答应所求
    SOFTENED = "松动"  # 口风已松，未当场答应
    NOTHING = "无果"
    REBUFFED = "碰壁"  # 被顶了回来，人情不变
    FALLOUT = "翻脸"  # 当场翻脸：直落敌视


class CovertOutcome(StrEnum):
    """
    暗中行事（计谋 / 潜行取物）的结局：得手与否 × 察觉与否。暗中行事永不致死。
    由好到坏按「后果」排，不按「收获」：未遂什么也没惹出来，败露虽到手却结了仇——被察觉才是暗中行事真正的代价，
    FortuneResolver「绝不比确定性裁决更重」与 risk_of「只看最坏一端」都按这个次序。
    """

    CLEAN = "无痕"  # 得手，未被察觉
    FOILED = "未遂"  # 未得手，未被察觉
    EXPOSED = "败露"  # 得手，被察觉
    CAUGHT = "失手"  # 未得手，被察觉
