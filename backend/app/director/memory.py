"""
[INPUT]: 依赖 app.lore 的 kin（人物别名展开），依赖 app.schemas 的 WorldEvent
[OUTPUT]: 对外提供 recall() —— 记忆拦截与过滤器（Memory Filter Layer）：地点 / 在场 NPC / 身份三路 + 动作点名一路
[POS]: director 的 JIT 记忆层：调用大模型之前，从无上限的世界台账里只挑出与"此时此地此人"相关的几条注入 Prompt，
       全量 major_events 永远不进上下文——台账越长，喂给大模型的仍是一个封顶的常数。纯函数，被 pipeline.py 每回合调用
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Sequence

from app.lore import kin
from app.schemas import WorldEvent

_MIN_KEY = 2  # 单字标签或单字键（"刀""庄"）匹配面太宽，只会把无关历史灌进上下文


def recall(
    events: Sequence[WorldEvent],
    *,
    location: str,
    present_npcs: Sequence[str],
    traits: Sequence[str],
    mentioned: str = "",
    limit: int,
) -> list[WorldEvent]:
    """
    相关 = 事件的某个 tag 与当前地点、在场 NPC（含全部别名）或玩家的 social_traits 相互包含：
    "聚贤庄废墟"命中 tag「聚贤庄」，在场的"丐帮弟子"命中 tag「丐帮」，在场的「萧峰」命中 tag「乔峰」。
    另有一路：tag（含别名）原样出现在 mentioned（这一招动作的表面部分，由调用方经 perception.surface 剥去内心念头）里——
    "潜回松鹤楼""去找乔峰"在落笔写抵达场景之前
    就要看见那里、那人的过往；只做单向检索，长动作文本不能反过来"包含"一切短标签。
    相关事件按发生先后保留最近的 limit 条——越近的大事越可能左右眼前的局面，条数封顶使 Prompt 长度与台账长度无关。
    检索键只取场景里公开可感知的东西：玩家的 secrets 从不参与检索，免得秘密每回合把相关历史拽进上下文、诱导导演加戏。
    """
    keys = (location, *(alias for npc in present_npcs for alias in kin(npc)), *traits)
    relevant = [
        event
        for event in events
        if any(_hit(tag, key) for tag in event.tags for key in keys) or any(_named(tag, mentioned) for tag in event.tags)
    ]
    return relevant[-limit:] if limit > 0 else []  # 切片 [-0:] 会返回全量，必须显式拦下


def _hit(tag: str, key: str) -> bool:
    return len(tag) >= _MIN_KEY and len(key) >= _MIN_KEY and (tag in key or key in tag)


def _named(tag: str, text: str) -> bool:
    return len(tag) >= _MIN_KEY and any(alias in text for alias in kin(tag))
