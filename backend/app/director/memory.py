"""
[INPUT]: 依赖 app.lore 的 kin（人物别名展开），依赖 app.schemas 的 WorldEvent
[OUTPUT]: 对外提供 recall() —— JIT 标签路由检索
[POS]: director 的记忆路由层：调用大模型前，从只追加、无上限的世界大事记中只挑出与"此时此地此人"相关的条目注入 Prompt——
       上下文规模由检索控制，而不是靠销毁历史；纯函数，被 pipeline.py 在开局与每回合调用
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Sequence

from app.lore import kin
from app.schemas import WorldEvent

_MIN_KEY = 2  # 单字标签（如"刀"）匹配面太宽，只会把无关历史灌进上下文


def recall(
    events: Sequence[WorldEvent],
    *,
    location: str,
    entities: Sequence[str],
    traits: Sequence[str],
    limit: int,
) -> tuple[WorldEvent, ...]:
    """
    仅当事件的某个 tag 与当前地点、在场人物（含全部别名）或玩家的 social_traits 相互包含时才算相关；
    相关事件按发生先后保留最近的 limit 条——越近的大事越可能左右眼前的局面。
    """
    keys = (location, *(alias for entity in entities for alias in kin(entity)), *traits)
    relevant = [event for event in events if any(_hit(tag, key) for tag in event.tags for key in keys)]
    return tuple(relevant[-limit:])


def _hit(tag: str, key: str) -> bool:
    return len(tag) >= _MIN_KEY and len(key) >= _MIN_KEY and (tag in key or key in tag)
