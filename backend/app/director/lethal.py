"""
[INPUT]: 依赖 director/lore.py 的 Grandmaster / GRANDMASTERS，依赖 app.schemas 的 WorldState
[OUTPUT]: 对外提供 Verdict 裁决、SAFE 常量、judge() 致死预判
[POS]: director 的确定性规则层，在大模型之前拦截"无武功挑衅在场绝顶高手"；它裁定生死，大模型只负责叙述
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from dataclasses import dataclass

from app.director.lore import GRANDMASTERS, Grandmaster
from app.schemas import WorldState


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

# 身负这些绝学者已非无名小卒，生死交还大模型按常识裁量
_MASTERY = (
    "北冥神功", "六脉神剑", "降龙十八掌", "易筋经", "小无相功", "天山六阳掌",
    "斗转星移", "一阳指", "凌波微步", "内力深厚", "绝世武功", "身负绝学",
)


def _is_hostile(action: str) -> bool:
    for word in _BENIGN:
        action = action.replace(word, "")
    return any(word in action for word in _HOSTILE)


def judge(action: str, state: WorldState, presence: str) -> Verdict:
    """
    必死 = 玩家无绝学 ∧ 动作带敌意 ∧ 动作点名某绝顶高手 ∧ 此人在场（presence 含其任一称呼）。
    在场条件防止"我要去少林挑战扫地僧"这种远在天边的狠话被当场处决。
    """
    if any(mark in state.physical_state for mark in _MASTERY):
        return SAFE
    if not _is_hostile(action):
        return SAFE
    for master in GRANDMASTERS:
        if master.mentioned_in(action) and master.mentioned_in(presence):
            return Verdict(killer=master)
    return SAFE
