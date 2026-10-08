"""
[INPUT]: 依赖 domain/rules 的 adjudicate / Approval / best_skill，依赖 domain/intent 的 ActionType / PlayerIntent，依赖 domain/progression 的 Guidance，
         依赖 domain/snapshot 的 LocalSnapshot，PlayerState 仅作类型标注
[OUTPUT]: 对外提供 OptionCategory（战斗 / 交涉 / 探索 / 修习 / 取物 / 休养）、ActionOption（id + 标签 + 方向 + 服务端持有的意图）、
          OptionGenerator（快照 → 3~4 个方向各异的合法行动选项）
[POS]: application 的动态选项生成器（Affordances）：遍历快照里的合法边——出路（CONNECTS_TO）、在场之人（LOCATED_IN）、
       可及之物（HELD_BY / canon_holder）、可学之功（KNOWS_SKILL / REQUIRES）、自身的伤——枚举候选意图，再用裁决规则的 adjudicate 滤掉不合法的。
       修习不止入门：已入门而未登峰造极的武学同样给出精进的选项，标签随裁决给出的凭借而变（求教 / 参悟 / 随师精研 / 参照典籍 / 闭门苦练），
       凭借只读 Approval.guidance / source，这里绝不重判一遍；有伤且身边安全时给出调息疗伤。
       选项从不经大模型，因而不可能出现图谱里不存在的东西；它是快照的纯函数：服务端在玩家点选时按当前快照重算一遍即可核验，
       无需缓存、天然防伪造。"合法"不等于"安全"：向绝顶高手出手照样是选项，后果由规则与地下城主裁定，选项不泄露胜负
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterator
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from app.domain import rules
from app.domain.intent import ActionType, PlayerIntent
from app.domain.progression import Guidance
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
    intent: PlayerIntent  # 只在服务端：前端只拿到 id 与标签

    @classmethod
    def of(cls, category: OptionCategory, label: str, intent: PlayerIntent) -> ActionOption:
        digest = hashlib.sha1(intent.model_dump_json().encode()).hexdigest()[:8]
        return cls(id=f"{category.name.lower()}-{digest}", label=label, category=category, intent=intent)


# 稀缺的机缘排在前面（疗伤最急）；寻常的三类按快照版本轮换起点，让相邻回合的选项不总是同一副面孔
_RARE = (OptionCategory.RECOVER, OptionCategory.CULTIVATE, OptionCategory.ACQUIRE)
_COMMON = (OptionCategory.EXPLORE, OptionCategory.SOCIAL, OptionCategory.COMBAT)
_RARE_SEATS = 2  # 首轮里稀缺方向最多占的席位：疗伤、修习、取物同时可行时，第三个让位给寻常方向

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


class OptionGenerator:
    def __init__(self, *, max_options: int = 4, min_options: int = 3) -> None:
        self._max = max_options
        self._min = min_options

    def generate(self, state: PlayerState, snap: LocalSnapshot) -> tuple[ActionOption, ...]:
        if not state.alive:
            return ()
        pools: dict[OptionCategory, list[ActionOption]] = {c: [] for c in OptionCategory}
        for category, intent, label in self._candidates(state, snap):
            verdict = rules.adjudicate(intent, state, snap)
            if isinstance(verdict, rules.Approval):
                text = label if isinstance(label, str) else label(verdict)
                pools[category].append(ActionOption.of(category, text, intent))

        shift = snap.version % len(_COMMON)
        rare = [c for c in _RARE if pools[c]]
        order = [*rare[:_RARE_SEATS], *_COMMON[shift:], *_COMMON[:shift], *rare[_RARE_SEATS:]]
        cursors = {c: snap.version % len(pools[c]) if pools[c] else 0 for c in order}
        picked: list[ActionOption] = []
        limit = self._max  # 第一轮每个方向至多一个；仍不足 min 个时，再一轮轮从各方向补到 min 为止
        while True:
            progressed = False
            for category in order:
                if len(picked) >= limit:
                    break
                pool = pools[category]
                fresh = (pool[(cursors[category] + k) % len(pool)] for k in range(len(pool)))
                if (option := next((o for o in fresh if o not in picked), None)) is not None:
                    picked.append(option)
                    progressed = True
            if not progressed or len(picked) >= self._min:
                break
            limit = self._min
        return tuple(picked)

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
