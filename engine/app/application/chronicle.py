"""
[INPUT]: 依赖 domain/events 的全部领域事件，依赖 domain/ambient 的 Activity / ActivityKind，依赖 domain/combat 的 CombatOutcome，依赖 domain/outcomes 的 SocialOutcome / CovertOutcome，
         依赖 domain/intent 的 ActionType / Approach，依赖 domain/models 的 Attitude / EntityKind / kind_of，
         依赖 domain/snapshot 的 LocalSnapshot / CharacterView
[OUTPUT]: 对外提供 describe(event, labels, player_name) —— 一条领域事件的确定性白描（一句话；不出声的事件为空串，调用方一律滤掉；
          含时钟四事件、微观事实 FactEmerged、名望 RenownChanged 与世界心跳七事件）；
          titled(character) —— 「段延庆（恶贯满盈）」式的称呼；known_arts(snapshot) —— 「北冥神功（略有小成）」式的武学与火候
[POS]: application 的事实渲染器：把事件与快照翻成人话。describe 供三处消费——回合结果帧里的 facts、叙事 Prompt 里的 <settled_facts>、
       长线记忆的向量语料。它只读事件与名称表，不经大模型：记忆里存的是这里的白描而非大模型的散文，幻觉因此进不了记忆；
       也不写数值——熟练度与气血的涨落只说"有所精进""受了伤"，到了哪一步由快照里的火候与伤势去说。
       人情五档各有措辞（敌视「心生敌意」… 信赖「深为信赖」），交涉、见闻、用物、暗中取物各有一句白描。
       服药是两条事件（ItemConsumed + HealthChanged(source="item")）一句话：后者不出声（空串），免得「以金创药疗伤」之后再来一句「伤势有所好转」。
       语义物理引擎的六条事件：时钟挂上「暗流：钟灵的戒心（1/4）」、推进「…渐深（3/4）」/ 回退「…稍解（1/4）」、
       坍缩「…满了：识破你的手脚」、化解「…烟消云散」、微观事实照录原文、名望「某某的名声更响了 / 坏了几分（缘由）」；
       时钟事件自带名称与进度，这里从不回查时钟表、从不露 clk: id；零步的推进与零点的名望不出声。
       世界心跳的七条事件里只有人群溃散出声「某某惊惶四散，一哄而逃。」（人群名取名称表：快照的 labels 覆盖 swm: id）；
       时间流逝、交手的往事、痕迹、消息的生成与扩散、风化、顺手牵羊一律空串——它们经快照被感知（此地的痕迹、传到此地的消息），
       不被宣告：别处发生的事玩家本不该知道，白描一旦写出就进了 turn_resolved、<settled_facts> 与记忆，成了全知视角。
       titled / known_arts 是称呼与火候的唯一写法：状态栏、叙事 Hard Prompt 与地下城主的战况简报共用，三处说法不会各执一词
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Mapping

from app.domain.ambient import Activity, ActivityKind
from app.domain.combat import CombatOutcome
from app.domain.events import (
    ActionFailed,
    ActivityStarted,
    ClockAdvanced,
    ClockCleared,
    ClockCollapsed,
    ClockStarted,
    Conversed,
    DomainEvent,
    FactEmerged,
    FactLearned,
    FactTokenSpawned,
    HealthChanged,
    ItemConsumed,
    ItemDecayed,
    ItemPilfered,
    ItemTransferred,
    Maneuvered,
    Moved,
    Parleyed,
    PlayerDied,
    PlayerSpawned,
    RelationChanged,
    RenownChanged,
    RumorSpread,
    SkillExecuted,
    SkillPracticed,
    TimePassed,
    TraceLeft,
)
from app.domain.intent import ActionType, Approach
from app.domain.models import Attitude, EntityKind, kind_of
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.snapshot import CharacterView, LocalSnapshot

_OUTCOME = {
    CombatOutcome.SUCCESS: "将其制住",
    CombatOutcome.STALEMATE: "两人斗了个旗鼓相当，谁也奈何不了谁",
    CombatOutcome.MINOR_WOUND: "吃了点亏，带着轻伤退开",
    CombatOutcome.SEVERE_WOUND: "身受重伤，拼死逃脱",
    CombatOutcome.DEATH: "反被一招毙命",
}
_ATTITUDE = {
    Attitude.HOSTILE: "心生敌意",
    Attitude.WARY: "心存戒备",
    Attitude.NEUTRAL: "不再放在心上",
    Attitude.FRIENDLY: "生出好感",
    Attitude.TRUSTED: "深为信赖",
}
_PARLEY = {
    SocialOutcome.GRANTED: "如愿以偿",
    SocialOutcome.SOFTENED: "对方口风已松",
    SocialOutcome.NOTHING: "无果而终",
    SocialOutcome.REBUFFED: "碰了一鼻子灰",
    SocialOutcome.FALLOUT: "对方当场翻脸",
}
_COVERT = {
    CovertOutcome.CLEAN: "得手，神不知鬼不觉",
    CovertOutcome.EXPOSED: "得手，却被察觉",
    CovertOutcome.FOILED: "未能得手，好在无人察觉",
    CovertOutcome.CAUGHT: "失手，当场被撞破",
}
_ACTION = {
    ActionType.MOVE: "前往他处",
    ActionType.OBSERVE: "静观",
    ActionType.THINK: "沉思",
    ActionType.TALK: "与人交谈",
    ActionType.ATTACK: "出手",
    ActionType.TAKE: "取物",
    ActionType.GIVE: "赠物",
    ActionType.LEARN: "修习武学",
    ActionType.REST: "调息疗伤",
    ActionType.USE: "使用随身之物",
    ActionType.INVALID: "行非常之事",
}


_UNDERCURRENT = "那股暗流"  # 旧账里没带名字的时钟事件：宁可含糊，也不露 clk: id
_STOPS = ("。", "！", "？", "…", "」")


def _by(approach: Approach) -> str:
    return "" if approach is Approach.PLAIN else f"以{approach.value}"


def describe(event: DomainEvent, labels: Mapping[str, str], player_name: str) -> str:
    def name(any_id: str | None) -> str:
        if any_id is None:
            return ""
        return labels.get(any_id) or any_id.split(":", 1)[-1]

    me = player_name
    match event:
        case PlayerSpawned(location_id=loc):
            return f"{me}初入江湖，现身于{name(loc)}。"
        case Moved(from_location_id=src, to_location_id=dst, exit_label=label, fleeing=fleeing):
            return f"{me}经「{label}」{'夺路逃离' if fleeing else '离开'}{name(src)}，来到{name(dst)}。"
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
        case HealthChanged(source="item"):  # ItemConsumed 那一句已说了服药疗伤
            return ""
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
        case Parleyed(npc_id=npc, aim=aim, approach=approach, outcome=outcome):
            return f"{me}{_by(approach)}向{name(npc)}{aim.value}，{_PARLEY[outcome]}。"
        case FactLearned(fact_id=fact, source_id=src):
            return f"{me}从{name(src)}处打听到「{name(fact)}」。"
        case ItemConsumed(item_id=item, effect=effect):
            return f"{me}以{name(item)}{effect}。"
        case Maneuvered(item_id=item, target_id=target, approach=approach, outcome=outcome):
            return f"{me}{_by(approach)}暗取{name(target)}的{name(item)}——{_COVERT[outcome]}。"
        # 语义物理引擎的六条：时钟事件自带名称与进度，白描不回查时钟表，也从不露出 clk: id
        case ClockStarted(clock=clock):
            return f"暗流：{clock.name}（{clock.progress}/{clock.maximum}）。"
        case ClockAdvanced(steps=0):
            return ""
        case ClockAdvanced(name=title, steps=steps, progress=progress, maximum=maximum):
            gauge = f"（{progress}/{maximum}）" if maximum else ""
            return f"{title or _UNDERCURRENT}{'渐深' if steps > 0 else '稍解'}{gauge}。"
        case ClockCollapsed(name=title, consequence=consequence):
            then = consequence.rstrip("。！")
            return f"{title}满了：{then}。" if then else f"{title}满了。"
        case ClockCleared(name=title):
            return f"{title or _UNDERCURRENT}烟消云散。"
        case FactEmerged(text=text):
            return text if text.endswith(_STOPS) else f"{text}。"
        case RenownChanged(delta=0):
            return ""
        case RenownChanged(delta=delta, cause=cause):
            why = f"（{cause}）" if cause else ""
            return f"{me}的名声{'更响了' if delta > 0 else '坏了几分'}{why}。"
        # 世界心跳：只有眼前人群的溃散出声；其余经快照被感知、不被宣告——别处发生的事，玩家本不该知道
        case ActivityStarted(activity=Activity(kind=ActivityKind.ROUT, participants=crowd)):
            return f"{'、'.join(name(s) for s in crowd)}惊惶四散，一哄而逃。"
        case TimePassed() | ActivityStarted() | TraceLeft() | FactTokenSpawned() | RumorSpread() | ItemDecayed() | ItemPilfered():
            return ""
    raise TypeError(f"未知的领域事件：{type(event).__name__}")


# ============================================================
#  称呼与火候 —— 快照里的身份与渐进式状态，只有这一种写法
# ============================================================
def titled(character: CharacterView) -> str:
    """本名在前、称号随后：「段延庆（恶贯满盈）」。别名不入称呼——它是玩家指称时的线索，不是此人的名号。"""
    return character.name + (f"（{'、'.join(character.titles)}）" if character.titles else "")


def known_arts(snap: LocalSnapshot) -> tuple[str, ...]:
    """玩家已会的武学及其火候：「北冥神功（略有小成）」。火候由快照的熟练度与悟性现算，与聚合根同出一套 progression。"""
    return tuple(
        f"{art.name}（{mastery.value}）" if (mastery := snap.mastery(art.id)) else art.name for art in snap.known_skills
    )
