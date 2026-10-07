"""
[INPUT]: 依赖 app.lore 的 Grandmaster / GRANDMASTERS / MASTER_ARTS，依赖 app.schemas 的 PlayerState
[OUTPUT]: 对外提供 Verdict 裁决、SAFE 常量、judge() 致死预判
[POS]: director 的确定性规则层，在大模型之前拦截"无绝学挑衅在场绝顶高手"；它裁定生死，大模型只负责叙述——
       判死回合即使大模型失败或抗命，编排器也会以确定性处决落定
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Sequence
from dataclasses import dataclass

from app.lore import GRANDMASTERS, MASTER_ARTS, Grandmaster
from app.schemas import PlayerState


@dataclass(frozen=True)
class Verdict:
    """killer 非空即必死：单字段表达裁决，杜绝"必死却无凶手"的不可能状态。"""

    killer: Grandmaster | None = None

    @property
    def lethal(self) -> bool:
        return self.killer is not None


SAFE = Verdict()

# ------------------------------------------------------------------
#  敌意词典：单字匹配宽，先剔除无害复合词再判定（"打听" ≠ "打"）
# ------------------------------------------------------------------
_HOSTILE = (
    "杀", "刺", "砍", "劈", "斩", "打", "揍", "踢", "踹", "扇", "耳光",
    "偷袭", "暗算", "行刺", "下毒", "出手", "动手", "拔刀", "拔剑",
    "挑战", "挑衅", "单挑", "叫阵", "决斗", "比武", "过招",
    "骂", "辱", "嘲笑", "讥讽", "吐口水", "掀", "抢", "夺", "偷",
)
_BENIGN = (
    "打听", "打量", "打坐", "打探", "打招呼", "打尖", "打酒", "打水", "打扮", "打算", "打扰",
    "刺探", "偷看", "偷听", "偷偷",
)


def _is_hostile(action: str) -> bool:
    for word in _BENIGN:
        action = action.replace(word, "")
    return any(word in action for word in _HOSTILE)


def judge(action: str, player: PlayerState, present: Sequence[str]) -> Verdict:
    """
    必死 = 玩家无绝学 ∧ 动作带敌意 ∧ 动作点名某绝顶高手 ∧ 此人在场。
    - 在场只读结构化的局部环境实体账，绝不回退到叙事原文：传闻里提到的高手不能隔空处决玩家
    - 绝学只读 martial_arts：buffs 里"北冥神功尽失"之类的负面标签不再反向赐予免死
    """
    if any(art in learned for learned in player.martial_arts for art in MASTER_ARTS):
        return SAFE
    if not _is_hostile(action):
        return SAFE
    presence = "、".join(present)
    for master in GRANDMASTERS:
        if master.mentioned_in(action) and master.mentioned_in(presence):
            return Verdict(killer=master)
    return SAFE
