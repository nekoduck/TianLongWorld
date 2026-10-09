"""
[INPUT]: 依赖 domain/events 的 Parleyed / Maneuvered / ActionFailed / ItemTransferred / SkillPracticed / RelationChanged / DomainEvent，
         依赖 domain/intent 的 Aim / Approach，依赖 domain/models 的 Attitude，依赖 domain/outcomes 的 SocialOutcome / CovertOutcome
[OUTPUT]: 对外提供 Thread（一条未了的所图：对象、所图、所求之物、已试手段、进展、所需人情、尝试次数）、THREADS_MAX、
          fold()（纯函数：一条事件 → 新的线索表）、pursuing()（对某人某所图的那条线索）
[POS]: domain 的心事线索：玩家想要而还没到手的东西。只由事件折叠（聚合根 evolve 调用），不读快照、不读剧本，重放即重算。
       开启 / 推进：交涉不如愿（Parleyed）、暗中取物未得手（Maneuvered 未遂 / 失手）、求教被拒（ActionFailed UNWILLING：unlock 解析为所需人情）。
       了结：如愿（Parleyed 如愿、Maneuvered 无痕 / 败露）；物已到手（任何途径从此人手里易手到你）；得其点拨（SkillPracticed 的 source 正是此人）；
       结交到友善、化解到漠然（RelationChanged）。至多 THREADS_MAX 条，新者在前，满了请走最旧的。
       P1 没有 NPC 身故的事件：「对象身故即了结」随 P2 的 NpcFell 补上
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Callable
from dataclasses import dataclass, replace

from app.domain.events import (
    ActionFailed,
    DomainEvent,
    ItemTransferred,
    Maneuvered,
    Parleyed,
    RelationChanged,
    SkillPracticed,
)
from app.domain.intent import Aim, Approach
from app.domain.models import Attitude
from app.domain.outcomes import CovertOutcome, SocialOutcome

THREADS_MAX = 6
_GOT = (CovertOutcome.CLEAN, CovertOutcome.EXPOSED)
_REGARDS = frozenset(a.value for a in Attitude)  # UNWILLING 的 unlock 是人情档的名字
_GOAL = {Aim.BEFRIEND: Attitude.FRIENDLY, Aim.DEFUSE: Attitude.NEUTRAL}  # 人情够到这一档，这条线索就了了


@dataclass(frozen=True, slots=True)
class Thread:
    """
    target 是对象：交涉与暗中取物是人物 id（chr:）；求教被拒是驳回入账的原话（ActionFailed.target 只存玩家的说法）。
    progress 是最近一次的结局（如愿以外的 SocialOutcome / CovertOutcome 的值）或驳回码；need 是求艺所需的人情档；
    attempts 只数有赌注的尝试（交涉、暗取），驳回不算。
    """

    target: str
    aim: Aim
    subject: str | None = None
    tried: tuple[Approach, ...] = ()
    progress: str = ""
    need: Attitude | None = None
    attempts: int = 0

    @property
    def key(self) -> tuple[str, Aim]:
        return self.target, self.aim


def pursuing(threads: tuple[Thread, ...], target: str, aim: Aim) -> Thread | None:
    return next((t for t in threads if t.key == (target, aim)), None)


def _touch(
    threads: tuple[Thread, ...], target: str, aim: Aim, approach: Approach, progress: str,
    *, subject: str | None = None, need: Attitude | None = None, attempt: bool = True,
) -> tuple[Thread, ...]:
    """开启或推进一条线索并挪到最前。attempt 为假（驳回）时不计次数：同一句驳回再来一遍，线索原样不动——世界不变，菜单也不该变。"""
    old = pursuing(threads, target, aim) or Thread(target=target, aim=aim)
    tried = old.tried if approach in old.tried else (*old.tried, approach)
    new = replace(old, subject=subject or old.subject, tried=tried, progress=progress, need=need or old.need,
                  attempts=old.attempts + (1 if attempt else 0))
    return (new, *(t for t in threads if t.key != new.key))[:THREADS_MAX]


def _close(threads: tuple[Thread, ...], keep: Callable[[Thread], bool]) -> tuple[Thread, ...]:
    return tuple(t for t in threads if keep(t))


def fold(threads: tuple[Thread, ...], event: DomainEvent, player_id: str) -> tuple[Thread, ...]:
    match event:
        case Parleyed(npc_id=npc, aim=aim, outcome=SocialOutcome.GRANTED):
            return _close(threads, lambda t: t.key != (npc, aim))
        case Parleyed(npc_id=npc, aim=aim, approach=approach, outcome=outcome):
            return _touch(threads, npc, aim, approach, outcome.value)
        case Maneuvered(target_id=who, item_id=item, outcome=outcome) if outcome in _GOT:
            return _close(threads, lambda t: not (t.target == who and t.aim is Aim.SEIZE) and t.subject != item)
        case Maneuvered(target_id=who, item_id=item, approach=approach, outcome=outcome):
            return _touch(threads, who, Aim.SEIZE, approach, outcome.value, subject=item)
        case ActionFailed(reason_code="UNWILLING", target=target, aim=aim, approach=approach, unlock=unlock):
            need = Attitude(unlock) if unlock in _REGARDS else None
            return _touch(threads, target or "", aim or Aim.LEARN, approach, "UNWILLING", need=need, attempt=False)
        case ItemTransferred(item_id=item, from_holder=giver, to_holder=taker) if taker == player_id:
            return _close(threads, lambda t: t.subject != item and not (t.target == giver and t.aim in (Aim.ASK, Aim.SEIZE)))
        case SkillPracticed(skill_id=skill, source_id=source):
            return _close(threads, lambda t: t.subject != skill and not (t.target == source and t.aim is Aim.LEARN))
        case RelationChanged(character_id=who, attitude=attitude):
            return _close(threads, lambda t: not (
                t.target == who and t.aim in _GOAL and attitude.rank >= _GOAL[t.aim].rank
            ))
    return threads

