"""
[INPUT]: 依赖 domain/events 的全部领域事件，依赖 domain/combat 的 CombatOutcome，依赖 domain/intent 的 ActionType，依赖 domain/models 的 Attitude / EntityKind / kind_of
[OUTPUT]: 对外提供 describe(event, labels, player_name) —— 一条领域事件的确定性白描（一句话）
[POS]: application 的事实渲染器：把事件翻成人话，供三处消费——回合结果帧里的 facts、叙事 Prompt 里的 <settled_facts>、
       长线记忆的向量语料。它只读事件与名称表，不经大模型：记忆里存的是这里的白描而非大模型的散文，幻觉因此进不了记忆；
       也不写数值——熟练度与气血的涨落只说"有所精进""受了伤"，到了哪一步由快照里的火候与伤势去说
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Mapping

from app.domain.combat import CombatOutcome
from app.domain.events import (
    ActionFailed,
    Conversed,
    DomainEvent,
    HealthChanged,
    ItemTransferred,
    Moved,
    PlayerDied,
    PlayerSpawned,
    RelationChanged,
    SkillExecuted,
    SkillPracticed,
)
from app.domain.intent import ActionType
from app.domain.models import Attitude, EntityKind, kind_of

_OUTCOME = {
    CombatOutcome.SUCCESS: "将其制住",
    CombatOutcome.STALEMATE: "两人斗了个旗鼓相当，谁也奈何不了谁",
    CombatOutcome.MINOR_WOUND: "吃了点亏，带着轻伤退开",
    CombatOutcome.SEVERE_WOUND: "身受重伤，拼死逃脱",
    CombatOutcome.DEATH: "反被一招毙命",
}
_ATTITUDE = {Attitude.HOSTILE: "心生敌意", Attitude.FRIENDLY: "生出好感", Attitude.NEUTRAL: "不再放在心上"}
_ACTION = {
    ActionType.MOVE: "前往他处",
    ActionType.OBSERVE: "静观",
    ActionType.TALK: "与人交谈",
    ActionType.ATTACK: "出手",
    ActionType.TAKE: "取物",
    ActionType.GIVE: "赠物",
    ActionType.LEARN: "修习武学",
    ActionType.REST: "调息疗伤",
    ActionType.INVALID: "行非常之事",
}


def describe(event: DomainEvent, labels: Mapping[str, str], player_name: str) -> str:
    def name(any_id: str | None) -> str:
        if any_id is None:
            return ""
        return labels.get(any_id) or any_id.split(":", 1)[-1]

    me = player_name
    match event:
        case PlayerSpawned(location_id=loc):
            return f"{me}初入江湖，现身于{name(loc)}。"
        case Moved(from_location_id=src, to_location_id=dst, exit_label=label):
            return f"{me}经「{label}」离开{name(src)}，来到{name(dst)}。"
        case ItemTransferred(item_id=item, from_holder=src, to_holder=dst):
            if kind_of(dst) is EntityKind.PLAYER:
                where = "地上拾得" if kind_of(src) is EntityKind.LOCATION else "身上取走"
                return f"{me}在{name(src)}{where}{name(item)}。"
            if kind_of(src) is EntityKind.PLAYER:
                return f"{me}将{name(item)}交给{name(dst)}。"
            return f"{name(item)}自{name(src)}易手至{name(dst)}。"
        case SkillPracticed(skill_id=skill, source_id=None):
            return f"{me}闭门苦练{name(skill)}，功力略有长进。"
        case SkillPracticed(skill_id=skill, source_id=str(src)) if kind_of(src) is EntityKind.ITEM:
            return f"{me}参照{name(src)}修习{name(skill)}，功力有所精进。"
        case SkillPracticed(skill_id=skill, source_id=src):
            return f"{me}得{name(src)}点拨，修习{name(skill)}，功力有所精进。"
        case SkillExecuted(skill_id=skill, target_id=target, item_id=item, outcome=outcome):
            how = f"以{name(skill)}" if skill else "徒手"
            weapon = f"，手持{name(item)}" if item else ""
            return f"{me}{how}向{name(target)}出手{weapon}——{_OUTCOME[outcome]}。"
        case HealthChanged(delta=delta, cause=cause) if delta < 0:
            return f"{me}受了伤（{cause}）。"
        case HealthChanged(cause=cause):
            return f"{me}{cause}，伤势有所好转。"
        case Conversed(npc_id=npc):
            return f"{me}与{name(npc)}交谈。"
        case RelationChanged(character_id=who, attitude=attitude, cause=cause):
            return f"{name(who)}对{me}{_ATTITUDE[attitude]}（{cause}）。"
        case ActionFailed(action=action, reason=reason):
            return f"{me}欲{_ACTION[action]}，未果：{reason}"
        case PlayerDied(cause=cause):
            return f"{me}殒命：{cause}。"
    raise TypeError(f"未知的领域事件：{type(event).__name__}")
