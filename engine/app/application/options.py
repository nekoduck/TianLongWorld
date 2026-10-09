"""
[INPUT]: 依赖 domain/rules 的 adjudicate / Approval / best_skill，依赖 domain/intent 的 ActionType / PlayerIntent，依赖 domain/progression 的 Guidance / Vitality，
         依赖 domain/models 的 Attitude（认出仇人在侧），依赖 domain/snapshot 的 LocalSnapshot，PlayerState 仅作类型标注（读 focus / focus_fresh / came_from / fled_from / vitality）
[OUTPUT]: 对外提供 OptionCategory（战斗 / 交涉 / 探索 / 修习 / 取物 / 休养）、ActionOption（id + 标签 + 方向 + why 上榜缘由 + 服务端持有的意图）、
          OptionGenerator（(玩家状态, 快照) → 3~4 个跟着剧情走的合法行动选项）
[POS]: application 的动态选项生成器（显著性菜单 Slate）：遍历快照里的合法边——出路（CONNECTS_TO）、在场之人（LOCATED_IN）、
       可及之物（HELD_BY / canon_holder）、可学之功（KNOWS_SKILL / REQUIRES）、自身的伤——枚举候选意图，用裁决规则的 adjudicate 滤掉不合法的，
       再按显著性打分、按席位与 MMR 挑出 3~4 个：
         打分：焦点（PlayerState.focus，近来亲手打过交道的人与物）按新旧 +5/+4/+3/+2；仇人（敌视且行动自如）在侧时出路 +4；
               不走回头路的出路 +1，逃离过的险地两样都不加；调息在重伤 / 奄奄一息时 +3，轻伤只作补位（凑不足 3 个才上），安然无恙不给；
               物归原主 +3、修习 +2、取物与攀谈 +1 是底分，静观只作补位。
         席位：伤重而四下无敌设调养席；仇人在侧设脱身席（与 rules._retreat 同理：先走来路，绝不逃回险地）；
               上一招的对象（focus[0] 且 focus_fresh）仍在场设跟进席（牵涉他的最显著一条）；
               其余席位按 MMR 取：分数减去与已选项的相似度（同动作、同对象各记一分，每分折 2 分），近似的选项不扎堆；平手一律按 id。
       没有版本号取模：同一个世界（状态与快照除版本外都相同）必得同一份菜单，逐字不变。
       每个选项带一句确定性的 why（≤12 字：「仇人在侧，先脱身」「方才打过交道」「伤重宜调息」），只读分数来源，不泄露胜负。
       修习不止入门：已入门而未登峰造极的武学同样给出精进的选项，标签随裁决给出的凭借而变（求教 / 参悟 / 随师精研 / 参照典籍 / 闭门苦练），
       凭借只读 Approval.guidance / source，这里绝不重判一遍。
       选项从不经大模型，因而不可能出现图谱里不存在的东西；它是 (状态, 快照) 的纯函数：服务端在玩家点选时按当前快照重算一遍即可核验，
       无需缓存、天然防伪造。"合法"不等于"安全"：向绝顶高手出手照样是选项，后果由规则与地下城主裁定，选项不泄露胜负
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from app.domain import rules
from app.domain.intent import ActionType, PlayerIntent
from app.domain.models import Attitude
from app.domain.progression import Guidance, Vitality
from app.domain.snapshot import LocalSnapshot

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState


class OptionCategory(StrEnum):
    COMBAT = "战斗"
    SOCIAL = "交涉"
    EXPLORE = "探索"
    CULTIVATE = "修习"
    ACQUIRE = "取物"
    RECOVER = "休养"


class ActionOption(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    label: str
    category: OptionCategory
    why: str = ""  # 为什么上榜：≤12 字的确定性短语，随选项下发
    intent: PlayerIntent  # 只在服务端：前端只拿到 id、标签、方向与 why

    @classmethod
    def of(cls, category: OptionCategory, label: str, intent: PlayerIntent, why: str = "") -> ActionOption:
        digest = hashlib.sha1(intent.model_dump_json().encode()).hexdigest()[:8]
        return cls(id=f"{category.name.lower()}-{digest}", label=label, category=category, why=why, intent=intent)


# 候选：方向、意图、标签。标签可以是一个函数——有些措辞取决于裁决给出的凭借（修习凭的是谁、是什么）
type _Label = str | Callable[[rules.Approval], str]
type _Candidate = tuple[OptionCategory, PlayerIntent, _Label]


def _learn_label(art: str, snap: LocalSnapshot) -> Callable[[rules.Approval], str]:
    """修习的措辞随凭借而变。凭借由 LearnRule 裁定（guidance / source），这里只负责把它说成人话。"""

    def label(ok: rules.Approval) -> str:
        source = snap.label(ok.source)
        match ok.guidance:
            case Guidance.ENTRY if ok.target:  # 入门且有人传授：target 即师父
                return f"向{source}求教{art}"
            case Guidance.ENTRY:
                return f"参悟{source}，修习{art}"
            case Guidance.TEACHER:
                return f"随{source}精研{art}"
            case Guidance.MANUAL:
                return f"参照{source}苦练{art}"
        return f"闭门苦练{art}"

    return label


# ============================================================
#  显著性 —— 底分 + 焦点 + 仇人 + 伤势；MMR 的相似度惩罚
# ============================================================
_PRIOR: dict[ActionType, int] = {  # 底分：物归原主是有指向的一步，修习与取物是机缘，攀谈是寻常的一步
    ActionType.GIVE: 3, ActionType.LEARN: 2, ActionType.TAKE: 1, ActionType.TALK: 1,
}
_FOCUS_GAIN = (5, 4, 3, 2)  # 焦点按新旧加分：上回合的对象最要紧
_FLIGHT_GAIN = 4  # 仇人在侧时的出路（逃离过的险地除外）
_FRESH_GAIN = 1  # 不走回头路
_HEAL_GAIN = 3  # 重伤 / 奄奄一息时的调息
_SIMILARITY_COST = 2  # MMR：与已选项同动作、同对象，每项折 2 分


@dataclass(frozen=True, slots=True)
class _Scored:
    option: ActionOption
    approval: rules.Approval
    score: int
    focus: int | None  # 命中的焦点位次（0 最新），没命中为 None

    @property
    def action(self) -> ActionType:
        return self.option.intent.action_type

    @property
    def subjects(self) -> frozenset[str]:
        """选项牵涉的实体：对象、物品、凭借（师父或典籍）、武学。"""
        ok = self.approval
        return frozenset(x for x in (ok.target, ok.item, ok.source, ok.skill) if x)

    def similarity(self, other: _Scored) -> int:
        return int(self.action is other.action) + int(bool(self.subjects & other.subjects))

    def rank(self, picked: list[_Scored] | None = None) -> tuple[int, str]:
        """排序键（越小越先）：MMR 分数取负，平手按 id。"""
        penalty = max((self.similarity(p) for p in picked or ()), default=0) * _SIMILARITY_COST
        return -(self.score - penalty), self.option.id


class OptionGenerator:
    def __init__(self, *, max_options: int = 4, min_options: int = 3) -> None:
        self._max = max_options
        self._min = min_options

    def generate(self, state: PlayerState, snap: LocalSnapshot) -> tuple[ActionOption, ...]:
        if not state.alive:
            return ()
        foes = {c.id for c in snap.characters if c.attitude is Attitude.HOSTILE and not c.subdued}
        main: list[_Scored] = []
        filler: list[_Scored] = []  # 只在凑不足 min 个时补位：轻伤时的调息、静观
        for category, intent, label in self._candidates(state, snap):
            verdict = rules.adjudicate(intent, state, snap)
            if not isinstance(verdict, rules.Approval):
                continue
            text = label if isinstance(label, str) else label(verdict)
            scored = self._score(ActionOption.of(category, text, intent), verdict, state, foes)
            match intent.action_type:
                case ActionType.REST if state.vitality is Vitality.HALE:
                    continue  # 安然无恙：气血差几分也不值得占一席
                case ActionType.REST if state.vitality is Vitality.HURT:
                    filler.append(scored)
                case ActionType.OBSERVE:
                    filler.append(scored)
                case _:
                    main.append(scored)

        picked: list[_Scored] = []
        seats: dict[str, str] = {}  # 选项 id → 席位缘由
        heal = [s for s in main if s.action is ActionType.REST]  # 能进 main 的调息必是重伤以上；仇人在侧时规则已驳回
        if heal:  # 调养席：伤重而四下无敌，调息永远看得见
            picked.append(heal[0])
            seats[heal[0].option.id] = "伤重宜调息"
        flight = [s for s in main if s.action is ActionType.MOVE and s.approval.target not in state.fled_from] if foes else []
        if flight:  # 脱身席：仇人在侧，退路永远看得见——与 rules._retreat 同理，先走来路，绝不逃回险地
            best = min(flight, key=lambda s: (s.approval.target != state.came_from, *s.rank()))
            picked.append(best)
            seats[best.option.id] = "仇人在侧，先脱身"
        lead = state.focus[0] if state.focus and state.focus_fresh else None
        if lead and (snap.character(lead) or snap.item(lead)):  # 跟进席：上一招的对象仍在眼前
            follow = [s for s in main if lead in s.subjects and s not in picked]
            if follow:
                picked.append(min(follow, key=lambda s: s.rank()))
        rest = [s for s in main if s not in picked]
        while len(picked) < self._max and rest:  # 其余席位：MMR，近似的选项不扎堆
            best = min(rest, key=lambda s: s.rank(picked))
            picked.append(best)
            rest.remove(best)
        for extra in sorted(filler, key=lambda s: (s.action is not ActionType.REST, s.option.id)):
            if len(picked) >= self._min:
                break
            picked.append(extra)
        return tuple(
            s.option.model_copy(update={"why": seats.get(s.option.id) or self._why(s, state, snap, foes)})
            for s in picked
        )

    # ============================================================
    #  打分与缘由
    # ============================================================
    @staticmethod
    def _score(option: ActionOption, ok: rules.Approval, state: PlayerState, foes: set[str]) -> _Scored:
        action = option.intent.action_type
        score = _PRIOR.get(action, 0)
        probe = _Scored(option, ok, 0, None)
        hit = next((i for i, x in enumerate(state.focus[: len(_FOCUS_GAIN)]) if x in probe.subjects), None)
        if hit is not None:
            score += _FOCUS_GAIN[hit]
        match action:
            case ActionType.MOVE if ok.target in state.fled_from:
                pass  # 逃离过的险地：仇人不会挪窝，既不是退路也不算新去处
            case ActionType.MOVE:
                score += _FLIGHT_GAIN if foes else 0
                score += _FRESH_GAIN if ok.target != state.came_from else 0
            case ActionType.REST if state.vitality.rank >= Vitality.WOUNDED.rank:
                score += _HEAL_GAIN
        return _Scored(option, ok, score, hit)

    @staticmethod
    def _why(s: _Scored, state: PlayerState, snap: LocalSnapshot, foes: set[str]) -> str:
        """上榜缘由：先看人情与焦点，再看这一类举动本身的理由。每句都只由状态与快照决定。"""
        ok, action = s.approval, s.action
        if action is ActionType.GIVE:
            return "物归原主"
        if s.focus is not None:
            anchor = state.focus[s.focus]
            if anchor.startswith("chr:"):
                if anchor in foes:
                    return "仇怨未了"
                if state.attitude_of(anchor) is Attitude.FRIENDLY:
                    return "与你有交情"
                return "方才打过交道" if s.focus == 0 and state.focus_fresh else "先前打过交道"
            if action is ActionType.LEARN:
                return "典籍在手，趁热打铁" if s.focus == 0 and state.focus_fresh else "典籍在手"
            return "新近经手之物"
        match action:
            case ActionType.REST:
                return "伤重宜调息" if state.vitality.rank >= Vitality.WOUNDED.rank else "略作调养"
            case ActionType.MOVE if ok.target in state.fled_from:
                return "曾在此遇险"
            case ActionType.MOVE:
                return "原路折返" if ok.target == state.came_from else "换个去处"
            case ActionType.ATTACK:
                return "仇人当面" if ok.target in foes else "以武相见"
            case ActionType.TALK:
                return "可探口风"
            case ActionType.TAKE:
                return "唾手可得" if ok.source == snap.location.id else "其人已被制住"
            case ActionType.LEARN:
                return {
                    Guidance.ENTRY: "有人肯传授" if ok.target else "典籍在手",
                    Guidance.TEACHER: "名师在侧",
                    Guidance.MANUAL: "典籍在手",
                }.get(ok.guidance or Guidance.ALONE, "勤能补拙")
        return "看清形势"

    @staticmethod
    def _candidates(state: PlayerState, snap: LocalSnapshot) -> Iterator[_Candidate]:
        intent = PlayerIntent
        for way in snap.exits:
            yield (OptionCategory.EXPLORE, intent(action_type=ActionType.MOVE, target_entity=way.label),
                   f"沿「{way.label}」前往{way.to_name}")
        yield OptionCategory.EXPLORE, intent(action_type=ActionType.OBSERVE), "静观四周"
        yield OptionCategory.RECOVER, intent(action_type=ActionType.REST), "调息疗伤"  # 无伤、或敌视者在侧，规则自会滤掉

        skill = rules.best_skill(state, snap)
        for who in snap.characters:
            yield OptionCategory.SOCIAL, intent(action_type=ActionType.TALK, target_entity=who.name), f"与{who.name}攀谈"
            yield (
                OptionCategory.COMBAT,
                intent(action_type=ActionType.ATTACK, target_entity=who.name, skill_used=skill.name if skill else None),
                f"以{skill.name}向{who.name}出手" if skill else f"向{who.name}出手",
            )
            for thing in snap.inventory:
                if thing.owner_id == who.id:
                    yield (OptionCategory.SOCIAL,
                           intent(action_type=ActionType.GIVE, target_entity=who.name, item_used=thing.name),
                           f"将{thing.name}交还{who.name}")

        for thing in snap.items:
            if thing.holder_id == snap.player_id:
                continue
            where = "拾起" if thing.holder_id == snap.location.id else f"从{snap.label(thing.holder_id)}身上取走"
            yield (OptionCategory.ACQUIRE, intent(action_type=ActionType.TAKE, target_entity=thing.name),
                   f"{where}{thing.name}")

        # 入门与精进是同一个 LEARN：不点名师父，由 LearnRule 自己找肯教之人、典籍或闭门苦练；已臻化境、根基不足者自会被滤掉
        for art in snap.skills:
            yield (OptionCategory.CULTIVATE, intent(action_type=ActionType.LEARN, skill_used=art.name),
                   _learn_label(art.name, snap))
