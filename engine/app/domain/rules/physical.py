"""
[INPUT]: 依赖 rules/base 的 Rule / Approval / Rejection / Verdict / resolve / names / menace / best_skill，依赖 rules/martial 的 strike / strike_stakes（夺物），
         依赖 rules/parley 的 parley / filch（讨要与暗取），依赖 domain/approach 的 Route / cell_of / infer_aim，
         依赖 domain/events 的 Moved / ItemTransferred / HealthChanged / ItemConsumed / DomainEvent，依赖 domain/combat 的 CombatRuling，
         依赖 domain/stakes 的 AnyStakes / Ruling，依赖 domain/progression 的 MAX_HP / REST_GAIN，
         依赖 domain/intent 的 PlayerIntent，依赖 domain/snapshot 的 LocalSnapshot / ItemView；PlayerState 仅作类型标注
[OUTPUT]: 对外提供 ObserveRule（静观，无事件）/ MoveRule（只沿 CONNECTS_TO）/ TakeRule（地上之物与被制住者身上之物定案；
          他人手中之物按手段分路：武力 → 战·夺物、言辞 / 人情 / 借势 → 交·讨要、计谋 / 潜行 → 暗·骗取 / 偷取、寻常 → 驳回并提示换手段；
          不可携带之物一律驳回 NOT_PORTABLE）/ HAZARD_HURT 与 handled()（险物取到手即受伤，留一口气）/
          UseRule（服用敷用随身之物：ItemConsumed + HealthChanged(source="item")）/ RestRule（调息：有伤且无仇人在侧，source="rest"）/
          InvalidRule（违背世界观永不获准）
[POS]: rules 包里管"身体与地理"的那几条：出口是否相连、物在谁手、伤要不要疗——只看快照的物理事实与玩家的气血。
       取物先过物性闸门（不可携带者不论用什么手段都拿不走），再看物在谁手：地上或被制住者身上即确定易手，自由人手中的则按兼容表分路；
       有险性之物（hazard）不论经哪一路到手，门面都追加一次 HealthChanged(source="blow", source_id=物)，永不致死。
       服药每档药力回 REST_GAIN 的气血（钳在上限内）；用掉之物从此不在行囊（PlayerState.consumed）
[PROTOCOL]: 变更时更新此头部，然后检查 rules/CLAUDE.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.approach import Route, cell_of, infer_aim
from app.domain.combat import CombatRuling
from app.domain.events import DomainEvent, HealthChanged, ItemConsumed, ItemTransferred, Moved, PlayerDied
from app.domain.intent import PlayerIntent
from app.domain.progression import MAX_HP, REST_GAIN
from app.domain.rules.base import Approval, Rejection, Rule, Verdict, best_skill, menace, names, resolve
from app.domain.rules.martial import strike, strike_stakes
from app.domain.rules.parley import filch, parley
from app.domain.snapshot import LocalSnapshot
from app.domain.stakes import AnyStakes, Ruling

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState


class ObserveRule(Rule):
    """静观是纯查询：没有事件，世界不因你看了一眼而改变。"""

    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        return Approval(intent)

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: Ruling | None
    ) -> list[DomainEvent]:
        return []


class MoveRule(Rule):
    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        if not intent.target_entity:
            return Rejection("NO_TARGET", "欲往何处？须说出去向。")
        way = resolve(intent.target_entity, snap.exits, names)
        if way is None:
            roads = "、".join(f"{e.label}（{e.to_name}）" for e in snap.exits) or "无路可走"
            return Rejection("NO_PATH", f"此处并无通往「{intent.target_entity}」的路。可行之路：{roads}。")
        return Approval(intent, target=way.to_id, exit_label=way.label)

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: Ruling | None
    ) -> list[DomainEvent]:
        assert ok.target and ok.exit_label
        return [Moved(from_location_id=state.location_id, to_location_id=ok.target, exit_label=ok.exit_label)]


HAZARD_HURT = 15  # 险物取到手的伤：轻伤一档的下沿，绝不致死
HELD_HINT = "强夺、讨要或暗取"


class TakeRule(Rule):
    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        wanted = intent.target_entity or intent.item_used
        if not wanted:
            return Rejection("NO_TARGET", "欲取何物？")
        reachable = [i for i in snap.items if i.holder_id != state.player_id]
        thing = resolve(wanted, reachable, names)
        if thing is None:
            mine = resolve(wanted, snap.inventory, names)
            if mine is not None:
                return Rejection("ALREADY_CARRIED", f"「{mine.name}」已在你身上。")
            return Rejection("NOT_PRESENT", f"此处不见「{wanted}」。")
        if not thing.portable:
            return Rejection("NOT_PORTABLE", f"「{thing.name}」无法随身带走。", unlock="就地察看")
        holder = snap.character(thing.holder_id)
        if holder is None or holder.subdued:  # 地上之物、被制住者身上之物：伸手即得
            return Approval(intent, target=thing.id, source=thing.holder_id)
        cell = cell_of(intent, held=True)
        aim = infer_aim(intent, attitude=holder.attitude, held=True)
        match cell.route:
            case Route.COMBAT:  # 夺物即出手：点名的功夫会就用它，否则看家本领（与 AttackRule 同一口径）
                named = resolve(intent.skill_used, snap.known_skills, names)
                skill = named or best_skill(state, snap)
                return Approval(intent, target=holder.id, item=thing.id, source=holder.id,
                                skill=skill.id if skill else None, route=Route.COMBAT, aim=aim)
            case Route.SOCIAL | Route.COVERT:
                return Approval(intent, target=holder.id, item=thing.id, source=holder.id, route=cell.route, aim=aim)
        return Rejection("HELD_BY_OTHER", f"「{thing.name}」在{holder.name}手中，须先胜过此人，或待其相赠。", unlock=HELD_HINT)

    def stakes(self, ok: Approval, state: PlayerState, snap: LocalSnapshot) -> AnyStakes | None:
        match ok.route:
            case Route.COMBAT:
                return strike_stakes(ok, state, snap, seize=ok.item)
            case Route.SOCIAL:
                return parley(ok, state, snap)
            case Route.COVERT:
                return filch(ok, state, snap)
        return None

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: Ruling | None
    ) -> list[DomainEvent]:
        if ok.route is Route.COMBAT:
            assert isinstance(ruling, CombatRuling)
            return strike(ok, state, snap, ruling, seize=ok.item)
        assert ok.route is Route.FIXED and ok.target and ok.source  # 讨要与暗取的定案由门面经 parley.settled 落为事件
        return [ItemTransferred(item_id=ok.target, from_holder=ok.source, to_holder=state.player_id)]


def handled(events: list[DomainEvent], state: PlayerState, snap: LocalSnapshot) -> list[DomainEvent]:
    """
    险物取到手即受伤：这一批事件里每一件易手到玩家行囊、带 hazard 的物品，追加一次 HealthChanged(source="blow")。
    扣减钳到留一口气——取物永不致死；这一批里已身死就不再追加。
    """
    if any(isinstance(e, PlayerDied) for e in events):
        return []
    hp = min(MAX_HP, max(0, state.hp + sum(e.delta for e in events if isinstance(e, HealthChanged))))
    out: list[DomainEvent] = []
    for e in events:
        if not (isinstance(e, ItemTransferred) and e.to_holder == state.player_id):
            continue
        thing = snap.item(e.item_id)
        if thing is None or not thing.hazard or (hurt := max(-HAZARD_HURT, 1 - hp)) >= 0:
            continue
        out.append(HealthChanged(delta=hurt, cause=f"触到{thing.name}，{thing.hazard}", source_id=thing.id))
        hp += hurt
    return out


class UseRule(Rule):
    """使用随身之物：须在行囊里、须有用法；疗伤之药须有伤可疗。用掉即 ItemConsumed，疗伤另写 HealthChanged(source="item")。"""

    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        wanted = intent.item_used or intent.target_entity
        if not wanted:
            return Rejection("NO_ITEM", "欲用何物？")
        thing = resolve(wanted, snap.inventory, names)
        if thing is None:
            return Rejection("NOT_CARRIED", f"你身上并无「{wanted}」。")
        if thing.use is None:
            return Rejection("NO_USE", f"「{thing.name}」不能服用，也不能敷用。")
        if thing.use.effect == "疗伤" and state.hp >= MAX_HP:
            return Rejection("UNHURT", "你气血充盈，无伤可疗。")
        return Approval(intent, item=thing.id)

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: Ruling | None
    ) -> list[DomainEvent]:
        assert ok.item
        thing = snap.item(ok.item)
        assert thing is not None and thing.use is not None
        events: list[DomainEvent] = [ItemConsumed(item_id=thing.id, effect=thing.use.effect)]
        if thing.use.effect == "疗伤":
            healed = min(thing.use.potency * REST_GAIN, MAX_HP - state.hp)
            events.append(HealthChanged(delta=healed, cause=f"服用{thing.name}", source_id=thing.id, source="item"))
        return events


class RestRule(Rule):
    """调息疗伤：气血不满才有伤可疗；敌视你的人就在一旁且行动自如时，没人能安心闭目运气。"""

    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        if state.hp >= MAX_HP:
            return Rejection("UNHURT", "你气血充盈，无伤可疗。")
        if foe := menace(snap):
            return Rejection("UNSAFE", f"{foe.name}在侧虎视眈眈，你无法安心调息。")
        return Approval(intent)

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: Ruling | None
    ) -> list[DomainEvent]:
        return [HealthChanged(delta=min(REST_GAIN, MAX_HP - state.hp), cause="调息疗伤", source="rest")]


class InvalidRule(Rule):
    """违背世界观的操作永不获准：热兵器、法术、元游戏指令在这里撞上死线。"""

    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        return Rejection("INVALID", intent.reason or "此举不合江湖天道。")

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: Ruling | None
    ) -> list[DomainEvent]:
        raise AssertionError("INVALID 永不获准")
