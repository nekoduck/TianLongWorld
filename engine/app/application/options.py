"""
[INPUT]: 依赖 domain/rules 的 adjudicate / Approval / best_skill，依赖 domain/intent 的 ActionType / PlayerIntent，依赖 domain/models 的 Attitude，
         依赖 domain/snapshot 的 LocalSnapshot，PlayerState 仅作类型标注
[OUTPUT]: 对外提供 OptionCategory（战斗 / 交涉 / 探索 / 修习 / 取物）、ActionOption（id + 标签 + 方向 + 服务端持有的意图）、
          OptionGenerator（快照 → 3~4 个方向各异的合法行动选项）
[POS]: application 的动态选项生成器（Affordances）：遍历快照里的合法边——出路（CONNECTS_TO）、在场之人（LOCATED_IN）、
       可及之物（HELD_BY / canon_holder）、可学之功（KNOWS_SKILL / REQUIRES）——枚举候选意图，再用裁决规则的 adjudicate 滤掉不合法的。
       选项从不经大模型，因而不可能出现图谱里不存在的东西；它是快照的纯函数：服务端在玩家点选时按当前快照重算一遍即可核验，
       无需缓存、天然防伪造。"合法"不等于"安全"：向绝顶高手出手照样是选项，后果由规则裁定，选项不泄露胜负
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from app.domain import rules
from app.domain.intent import ActionType, PlayerIntent
from app.domain.models import Attitude
from app.domain.snapshot import LocalSnapshot

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState


class OptionCategory(StrEnum):
    COMBAT = "战斗"
    SOCIAL = "交涉"
    EXPLORE = "探索"
    CULTIVATE = "修习"
    ACQUIRE = "取物"


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


# 稀缺的机缘排在前面；寻常的三类按快照版本轮换起点，让相邻回合的选项不总是同一副面孔
_RARE = (OptionCategory.CULTIVATE, OptionCategory.ACQUIRE)
_COMMON = (OptionCategory.EXPLORE, OptionCategory.SOCIAL, OptionCategory.COMBAT)


class OptionGenerator:
    def __init__(self, *, max_options: int = 4, min_options: int = 3) -> None:
        self._max = max_options
        self._min = min_options

    def generate(self, state: PlayerState, snap: LocalSnapshot) -> tuple[ActionOption, ...]:
        if not state.alive:
            return ()
        pools: dict[OptionCategory, list[ActionOption]] = {c: [] for c in OptionCategory}
        for candidate in self._candidates(state, snap):
            if isinstance(rules.adjudicate(candidate.intent, state, snap), rules.Approval):
                pools[candidate.category].append(candidate)

        shift = snap.version % len(_COMMON)
        order = [*_RARE, *_COMMON[shift:], *_COMMON[:shift]]
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
    def _candidates(state: PlayerState, snap: LocalSnapshot) -> Iterator[ActionOption]:
        intent = PlayerIntent
        for way in snap.exits:
            yield ActionOption.of(OptionCategory.EXPLORE, f"沿「{way.label}」前往{way.to_name}",
                                  intent(action_type=ActionType.MOVE, target_entity=way.label))
        yield ActionOption.of(OptionCategory.EXPLORE, "静观四周", intent(action_type=ActionType.OBSERVE))

        skill = rules.best_skill(state, snap)
        for who in snap.characters:
            yield ActionOption.of(OptionCategory.SOCIAL, f"与{who.name}攀谈",
                                  intent(action_type=ActionType.TALK, target_entity=who.name))
            yield ActionOption.of(
                OptionCategory.COMBAT,
                f"以{skill.name}向{who.name}出手" if skill else f"向{who.name}出手",
                intent(action_type=ActionType.ATTACK, target_entity=who.name, skill_used=skill.name if skill else None),
            )
            for thing in snap.inventory:
                if thing.owner_id == who.id:
                    yield ActionOption.of(OptionCategory.SOCIAL, f"将{thing.name}交还{who.name}",
                                          intent(action_type=ActionType.GIVE, target_entity=who.name,
                                                 item_used=thing.name))

        for thing in snap.items:
            if thing.holder_id == snap.player_id:
                continue
            where = "拾起" if thing.holder_id == snap.location.id else f"从{snap.label(thing.holder_id)}身上取走"
            yield ActionOption.of(OptionCategory.ACQUIRE, f"{where}{thing.name}",
                                  intent(action_type=ActionType.TAKE, target_entity=thing.name))

        for art in snap.skills:
            if art.id in state.skills:
                continue
            teachers = sorted((c for c in snap.characters if art.id in c.skill_ids),
                              key=lambda c: c.attitude is not Attitude.FRIENDLY)  # 肯教的人排在前面
            mentor = teachers[0] if teachers else None
            text = art.acquisition.items[0] if art.acquisition.items else None
            label = f"向{mentor.name}求教{art.name}" if mentor else f"参悟{snap.label(text)}，修习{art.name}"
            yield ActionOption.of(OptionCategory.CULTIVATE, label,
                                  intent(action_type=ActionType.LEARN, skill_used=art.name,
                                         target_entity=mentor.name if mentor else None))
