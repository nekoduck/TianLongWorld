"""
[INPUT]: 依赖 domain/events 的 Parleyed / Maneuvered / ActionFailed / ItemTransferred / SkillPracticed / FactLearned / RelationChanged / DomainEvent，
         依赖 domain/intent 的 Aim / Approach，依赖 domain/models 的 Attitude，依赖 domain/outcomes 的 SocialOutcome / CovertOutcome
[OUTPUT]: 对外提供 Thread（一条未了的所图：对象 id、所图、所求之物 / 之功 / 之见闻、已试手段、进展、所需人情、尝试次数）、THREADS_MAX、
          fold()（纯函数：一条事件 + 折叠前的人情 → 新的线索表）、pursuing()（对某人某所图的那条线索）
[POS]: domain 的心事线索：玩家想要而还没到手的东西。只由事件折叠（聚合根 evolve 调用），不读快照、不读剧本，重放即重算。
       一律按落了地的 id 立键：对象是 chr:，标的是 art: / itm: / fact:。
       开启 / 推进：交涉不如愿（Parleyed，带 subject_id）、暗中取物未得手（Maneuvered 未遂 / 失手）、
       求教被拒（ActionFailed UNWILLING 带 target_id / subject_id：unlock 解析为所需人情；旧账没有 target_id，不开线索）。
       不开：图的是一样东西（打探 / 讨要 / 求艺）却没有落了地的标的——无从追索；结交 / 化解而人情早已达成——无可再图。
       了结：如愿（Parleyed 如愿、Maneuvered 无痕 / 败露）；标的到手（那件东西易手到你、那门武学修习了一次——不论凭谁、凭什么、那条见闻已知）；
       结交到友善、化解到漠然（RelationChanged）。同一句驳回再来一遍，线索与次序都原样不动。至多 THREADS_MAX 条，新者在前，满了请走最旧的。
       P1 没有 NPC 身故的事件：「对象身故即了结」随 P2 的 NpcFell 补上
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from app.domain.events import (
    ActionFailed,
    DomainEvent,
    FactLearned,
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
_AIMED = frozenset({Aim.PROBE, Aim.ASK, Aim.LEARN, Aim.SEIZE})  # 图的是一样东西：没有落了地的标的就无从追索，不开线索


@dataclass(frozen=True, slots=True)
class Thread:
    """
    target 是对象的 id（chr:）：交涉的对方、暗取的失主、不肯传功的师父；subject 是所求之物、之功、之见闻的 id。
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
    """开启或推进一条线索并挪到最前。attempt 为假（驳回）时不计次数：同一句驳回再来一遍，线索原样不动、次序也不动——世界不变，菜单也不该变。"""
    old = pursuing(threads, target, aim)
    base = old or Thread(target=target, aim=aim)
    tried = base.tried if approach in base.tried else (*base.tried, approach)
    new = replace(base, subject=subject or base.subject, tried=tried, progress=progress, need=need or base.need,
                  attempts=base.attempts + (1 if attempt else 0))
    if new == old:
        return threads
    return (new, *(t for t in threads if t.key != new.key))[:THREADS_MAX]


def _close(threads: tuple[Thread, ...], keep: Callable[[Thread], bool]) -> tuple[Thread, ...]:
    return tuple(t for t in threads if keep(t))


def _met(aim: Aim, regard: Attitude) -> bool:
    """结交 / 化解的目标人情是否已达成：已达成就没有什么可图的了。"""
    return aim in _GOAL and regard.rank >= _GOAL[aim].rank


def fold(
    threads: tuple[Thread, ...], event: DomainEvent, player_id: str,
    attitudes: Mapping[str, Attitude] | None = None,
) -> tuple[Thread, ...]:
    """attitudes 是折叠这条事件之前的人情（PlayerState.attitudes）：结交 / 化解的目标已达成时不开线索。"""
    regard = attitudes or {}
    match event:
        case Parleyed(npc_id=npc, aim=aim, outcome=SocialOutcome.GRANTED):
            return _close(threads, lambda t: t.key != (npc, aim))
        case Parleyed(npc_id=npc, aim=aim, subject_id=subject) if (
            (aim in _AIMED and subject is None) or _met(aim, regard.get(npc, Attitude.NEUTRAL))
        ):
            return _close(threads, lambda t: t.key != (npc, aim))
        case Parleyed(npc_id=npc, aim=aim, approach=approach, outcome=outcome, subject_id=subject):
            return _touch(threads, npc, aim, approach, outcome.value, subject=subject)
        case Maneuvered(target_id=who, item_id=item, outcome=outcome) if outcome in _GOT:
            return _close(threads, lambda t: not (t.target == who and t.aim is Aim.SEIZE) and t.subject != item)
        case Maneuvered(target_id=who, item_id=item, approach=approach, outcome=outcome):
            return _touch(threads, who, Aim.SEIZE, approach, outcome.value, subject=item)
        case ActionFailed(reason_code="UNWILLING", target_id=str(who), subject_id=subject, aim=aim, approach=approach,
                          unlock=unlock):
            need = Attitude(unlock) if unlock in _REGARDS else None
            return _touch(threads, who, aim or Aim.LEARN, approach, "UNWILLING", subject=subject, need=need, attempt=False)
        case ItemTransferred(item_id=item, to_holder=taker) if taker == player_id:
            return _close(threads, lambda t: t.subject != item)
        case SkillPracticed(skill_id=skill):
            return _close(threads, lambda t: not (t.aim is Aim.LEARN and t.subject == skill))
        case FactLearned(fact_id=fact):
            return _close(threads, lambda t: not (t.aim is Aim.PROBE and t.subject == fact))
        case RelationChanged(character_id=who, attitude=attitude):
            return _close(threads, lambda t: not (t.target == who and _met(t.aim, attitude)))
    return threads
