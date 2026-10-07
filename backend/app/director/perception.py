"""
[INPUT]: 依赖标准库 re，依赖 app.lore 的 kin（人物别名），依赖 app.schemas 的 DirectorOutput，依赖 app.session 的 LocalEnvironment
[OUTPUT]: 对外提供 surface()（文本中外人看得见、听得见的部分）与 witnessed()（一回合里外人也看得见的专名：下一回合的检索种子）
[POS]: director 的感知边界：同一条动作文本，导演全读，NPC、规则层与检索只读"表面行为"；同一回合提取的实体，只有外人也看得见的才能牵动检索。
       surface 被 lethal.judge（只有看得见的冒犯才招来杀身之祸）、pipeline 的 RAG 语义检索（内心念头不把历史拽进上下文）与 witnessed 共用，
       "表面行为"在全系统只有这一个定义
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import re
from collections.abc import Sequence

from app.lore import kin
from app.schemas import DirectorOutput
from app.session import LocalEnvironment

# 只收纯心理动词：从标记起到句读为止都是念头。"暗自""暗中""默默"不收——"暗自一掌拍向乔峰后心"是看得见的偷袭
_THOUGHT = re.compile(
    r"(心里|心中|心想|心道|心下|暗想|暗忖|暗骂|寻思|思忖|盘算|琢磨|默念|腹诽)[^，。；！？,.;!?\n]*"
)


def surface(action: str) -> str:
    """剥掉内心念头的从句："心想管他的，一拳打在乔峰脸上" → "，一拳打在乔峰脸上"。"""
    return _THOUGHT.sub("", action)


def witnessed(out: DirectorOutput, local: LocalEnvironment, secrets: Sequence[str]) -> tuple[str, ...]:
    """
    实体可见性闸门：大模型提取的 involved_entities 只留外人也看得见的——此刻在场或就是所在地的一律留下；
    其余须出现在场景的表面叙述里（含别名；"你心里琢磨……"一类念头先剥掉），且不牵涉玩家仍持有的秘密。
    下一回合的关系图以它们为种子：秘密与念头不能牵动世界；秘密一旦当众揭穿就不在账上，它牵涉的人事随之解禁。
    """
    scene = surface(out.scene_description)
    present = {alias for npc in local.present_npcs for alias in kin(npc)}

    def visible(name: str) -> bool:
        aliases = kin(name)
        if name in local.location or not present.isdisjoint(aliases):
            return True
        return any(alias in scene for alias in aliases) and not any(alias in secret for alias in aliases for secret in secrets)

    return tuple(name for name in out.next_state.involved_entities if visible(name))
