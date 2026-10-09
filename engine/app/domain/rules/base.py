"""
[INPUT]: 依赖 domain/intent 的 PlayerIntent，依赖 domain/events 的 DomainEvent，依赖 domain/models 的 Tier / Attitude，
         依赖 domain/combat 的 Stakes / CombatRuling，依赖 domain/progression 的 effective_tier，依赖 domain/snapshot 的 LocalSnapshot / CharacterView / SkillView；
         PlayerState 仅作类型标注（避免与 aggregates 成环）
[OUTPUT]: 对外提供 Rejection（驳回：code + reason + unlock 怎样才行）/ Approval（获准：指称已落地）/ Verdict、resolve()（名称 → 实体的唯一匹配）、
          skill_tier() / player_tier() / best_skill()（火候折算后的境界与看家本领）、Rule 抽象（adjudicate / stakes 钩子 / consequences）、
          present()（指称落到在场之人）、menace()（在场、敌视且行动自如的仇人）、names() / listed() 渲染助手
[POS]: rules 包的地基：各条 Rule 共用的裁决结果、名称落地、境界折算与"仇人在侧"的判据。纯函数，不做 IO、不调大模型、不看时钟。
       人情只比 rank：仇人只认敌视（戒备不是仇人）
[PROTOCOL]: 变更时更新此头部，然后检查 rules/CLAUDE.md
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.domain.combat import CombatRuling, Stakes
from app.domain.events import DomainEvent
from app.domain.intent import PlayerIntent
from app.domain.models import Attitude, Tier
from app.domain.progression import Guidance, effective_tier
from app.domain.snapshot import CharacterView, LocalSnapshot, SkillView

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState


# ============================================================
#  裁决结果
# ============================================================
@dataclass(frozen=True, slots=True)
class Rejection:
    code: str
    reason: str
    unlock: str = ""  # 怎样才行（「信赖」……）：随 ActionFailed 入账，供心事线索与选项提示


@dataclass(frozen=True, slots=True)
class Approval:
    """裁决通过：意图里的指称已落地为图谱实体 id。"""

    intent: PlayerIntent
    target: str | None = None
    item: str | None = None
    skill: str | None = None
    exit_label: str | None = None
    source: str | None = None  # TAKE：物品原持有者；LEARN：传功点拨之人或所凭典籍（闭门苦练为 None）
    guidance: Guidance | None = None  # LEARN：入门，或精进凭的是什么


type Verdict = Approval | Rejection


# ============================================================
#  名称落地 —— 精确命中优先；退而求其次的包含匹配必须唯一，多义不猜
# ============================================================
def resolve[T](name: str | None, candidates: Iterable[T], names: Callable[[T], Iterable[str]]) -> T | None:
    if not name:
        return None
    wanted = name.strip()
    pool = list(candidates)
    exact = [c for c in pool if wanted in tuple(names(c))]
    if exact:
        return exact[0] if len(exact) == 1 else None
    if len(wanted) < 2:
        return None
    fuzzy = [c for c in pool if any(len(n) >= 2 and (n in wanted or wanted in n) for n in names(c))]
    return fuzzy[0] if len(fuzzy) == 1 else None


def names(view: object) -> Iterable[str]:
    return view.names  # type: ignore[attr-defined]


def listed(snap: LocalSnapshot, ids: Iterable[str]) -> str:
    return "、".join(f"「{snap.label(i)}」" for i in ids)


# ============================================================
#  境界 —— 一门功夫拿得出几分本事，看火候：境界上限是功夫的，折扣是自己的
# ============================================================
def skill_tier(view: SkillView, state: PlayerState) -> Tier:
    mastery = state.mastery(view.id)
    return effective_tier(view.tier, mastery) if mastery is not None else Tier.NONE


def player_tier(state: PlayerState, snap: LocalSnapshot) -> Tier:
    tiers = [skill_tier(view, state) for view in snap.skills if view.id in state.skills]
    return max(tiers, key=lambda t: t.rank, default=Tier.NONE)


def best_skill(state: PlayerState, snap: LocalSnapshot) -> SkillView | None:
    def strength(s: SkillView) -> tuple[int, int, str]:
        mastery = state.mastery(s.id)
        return -skill_tier(s, state).rank, -(mastery.rank if mastery else -1), s.id

    known = sorted((s for s in snap.skills if s.id in state.skills), key=strength)
    return known[0] if known else None


# ============================================================
#  规则抽象
# ============================================================
class Rule(ABC):
    @abstractmethod
    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict: ...

    def stakes(self, ok: Approval, state: PlayerState, snap: LocalSnapshot) -> Stakes | None:
        """胜负未定之事的可裁区间；结果确定的动作没有赌注（缺省）。"""
        return None

    @abstractmethod
    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: CombatRuling | None
    ) -> list[DomainEvent]: ...


def present(name: str | None, snap: LocalSnapshot) -> CharacterView | Rejection:
    if not name:
        return Rejection("NO_TARGET", "须说明对谁而为。")
    who = resolve(name, snap.characters, names)
    if who is None:
        return Rejection("NOT_PRESENT", f"此处不见「{name}」。")
    return who


def menace(snap: LocalSnapshot) -> CharacterView | None:
    """在场、敌视你且行动自如的人：有他在侧，调息与修习都无从静心（戒备不算仇人，只认敌视）。"""
    return next((c for c in snap.characters if c.attitude is Attitude.HOSTILE and not c.subdued), None)
