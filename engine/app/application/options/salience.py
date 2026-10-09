"""
[INPUT]: 依赖 domain/rules 的 Approval，依赖 domain/approach 的 Route / TacticalAxis，依赖 domain/intent 的 ActionType，依赖 domain/progression 的 Vitality，
         依赖 domain/threads 的 Thread / pursuing，依赖 options/sources 的 ActionOption；PlayerState 仅作类型标注
[OUTPUT]: 对外提供 Scored（打过分的候选：选项 + 裁决 + 分数 + 命中的焦点位次 + 接续的线索；subjects 牵涉的实体、anchor 对象、axis 战术轴、similarity、rank MMR 排序键）、
          score()（底分 + 焦点 + 伤势 + 心事线索）与各项权重常量
[POS]: options 包的显著性：一个候选值多少分，只由 (状态, 快照, 裁决) 决定。P0 的打分保留（出路的两项随移动归导航而去）：
         底分：物归原主 3、修习 2、取物与交谈 1、出手 0；他人手中之物（讨要 / 暗取 / 强夺）不是唾手可得的机缘，底分 0；
               师父不肯而开口恳求（求艺走交涉）是一次交谈，底分同交谈 1；疗伤之药同取物 1；
         焦点（PlayerState.focus，近来亲手打过交道的人与物）按新旧 +5/+4/+3/+2；调息与疗伤之药在重伤 / 奄奄一息时 +3。
       P1 加两项：接续心事线索（Thread 源，对象仍在场）+3；同一对象、同一所图、同一手段已经试过而线索未了（纠缠不休）−2。
       MMR 的相似度：同动作、同对象、同战术轴各记一分，与已选各席的相似度累加、每分折 2 分（P0 取最大，P1 改累加），
       近似的选项不扎堆、退路菜单尽量铺开不同的轴；平手一律按 id
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.application.options.sources import ActionOption
from app.domain import rules
from app.domain.approach import Route, TacticalAxis
from app.domain.intent import ActionType
from app.domain.progression import Vitality
from app.domain.threads import Thread, pursuing

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState

PRIOR: dict[ActionType, int] = {  # 底分：物归原主是有指向的一步，修习与取物是机缘，交谈是寻常的一步
    ActionType.GIVE: 3, ActionType.LEARN: 2, ActionType.TAKE: 1, ActionType.TALK: 1, ActionType.USE: 1,
}
FOCUS_GAIN = (5, 4, 3, 2)  # 焦点按新旧加分：上回合的对象最要紧
HEAL_GAIN = 3  # 重伤 / 奄奄一息时的调息与疗伤之药
THREAD_GAIN = 3  # 心事未了：对象仍在眼前，换个手段
STALE_COST = 2  # 纠缠不休：同一对象、同一所图、同一手段已经试过
SIMILARITY_COST = 2  # MMR：与已选项同动作、同对象、同战术轴，每项折 2 分


@dataclass(frozen=True, slots=True)
class Scored:
    option: ActionOption
    approval: rules.Approval
    score: int
    focus: int | None  # 命中的焦点位次（0 最新），没命中为 None
    thread: Thread | None = None  # 接续的心事线索（Thread 源）

    @property
    def action(self) -> ActionType:
        return self.option.intent.action_type

    @property
    def axis(self) -> TacticalAxis:
        return self.option.tactical_axis

    @property
    def subjects(self) -> frozenset[str]:
        """选项牵涉的实体：对象、物品、凭借（师父或典籍）、武学。"""
        ok = self.approval
        return frozenset(x for x in (ok.target, ok.item, ok.source, ok.skill) if x)

    @property
    def anchor(self) -> str:
        """这一招冲着谁（同一对象至多两席的那个「对象」）：牵涉的人在先；没有人就是物、去处、凭借或武学本身。"""
        ok = self.approval
        person = next((x for x in (ok.target, ok.source) if x and x.startswith("chr:")), None)
        return person or ok.target or ok.source or ok.skill or self.option.id

    def similarity(self, other: Scored) -> int:
        return int(self.action is other.action) + int(bool(self.subjects & other.subjects)) + int(self.axis is other.axis)

    def rank(self, picked: list[Scored] | None = None) -> tuple[int, str]:
        """
        排序键（越小越先）：MMR 分数取负，平手按 id。冗余按已选各席累加（P1；P0 取最大）：第三招同类比第二招更冗余——
        菜单对眼前的人与事多给几种招，且尽量落在不同的战术轴上。
        """
        penalty = sum(self.similarity(p) for p in picked or ()) * SIMILARITY_COST
        return -(self.score - penalty), self.option.id


def _stale(ok: rules.Approval, state: PlayerState) -> bool:
    """同一对象、同一所图、同一手段已经试过而线索未了：再来一遍只是纠缠不休（领域的交涉分数同样扣分）。"""
    who = ok.target if ok.target and ok.target.startswith("chr:") else ok.source
    if not who or ok.aim is None:
        return False
    t = pursuing(state.threads, who, ok.aim)
    return t is not None and ok.intent.approach in t.tried


def _prior(action: ActionType, ok: rules.Approval) -> int:
    """底分。他人手中之物（讨要 / 暗取 / 强夺）不是唾手可得的机缘：0；师父不肯而开口恳求是一次交谈，不是修习的机缘：同交谈。"""
    if ok.route is not Route.FIXED and action is ActionType.TAKE:
        return 0
    if ok.route is not Route.FIXED and action is ActionType.LEARN:
        return PRIOR[ActionType.TALK]
    return PRIOR.get(action, 0)


def score(option: ActionOption, ok: rules.Approval, state: PlayerState, thread: Thread | None) -> Scored:
    action = option.intent.action_type
    points = _prior(action, ok)
    probe = Scored(option, ok, 0, None)
    hit = next((i for i, x in enumerate(state.focus[: len(FOCUS_GAIN)]) if x in probe.subjects), None)
    if hit is not None:
        points += FOCUS_GAIN[hit]
    if action in (ActionType.REST, ActionType.USE) and state.vitality.rank >= Vitality.WOUNDED.rank:
        points += HEAL_GAIN
    if thread is not None:
        points += THREAD_GAIN
    elif _stale(ok, state):
        points -= STALE_COST
    return Scored(option, ok, points, hit, thread)
