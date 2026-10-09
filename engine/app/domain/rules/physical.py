"""
[INPUT]: 依赖 rules/base 的 Rule / Approval / Rejection / Verdict / resolve / names / menace，依赖 domain/events 的 Moved / ItemTransferred /
         HealthChanged / ItemConsumed / DomainEvent，依赖 domain/combat 的 CombatRuling，依赖 domain/progression 的 MAX_HP / REST_GAIN，
         依赖 domain/intent 的 PlayerIntent，依赖 domain/snapshot 的 LocalSnapshot；PlayerState 仅作类型标注
[OUTPUT]: 对外提供 ObserveRule（静观，无事件）/ MoveRule（只沿 CONNECTS_TO）/ TakeRule（地上之物与被制住者身上之物）/
          UseRule（服用敷用随身之物：ItemConsumed + HealthChanged(source="item")）/ RestRule（调息：有伤且无仇人在侧，source="rest"）/
          InvalidRule（违背世界观永不获准）
[POS]: rules 包里管"身体与地理"的那几条：出口是否相连、物在谁手、伤要不要疗——只看快照的物理事实与玩家的气血。
       服药每档药力回 REST_GAIN 的气血（钳在上限内）；用掉之物从此不在行囊，这一折叠在阶段 B 随 consumed 补上
[PROTOCOL]: 变更时更新此头部，然后检查 rules/CLAUDE.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.combat import CombatRuling
from app.domain.events import DomainEvent, HealthChanged, ItemConsumed, ItemTransferred, Moved
from app.domain.intent import PlayerIntent
from app.domain.progression import MAX_HP, REST_GAIN
from app.domain.rules.base import Approval, Rejection, Rule, Verdict, menace, names, resolve
from app.domain.snapshot import LocalSnapshot

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState


class ObserveRule(Rule):
    """静观是纯查询：没有事件，世界不因你看了一眼而改变。"""

    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        return Approval(intent)

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: CombatRuling | None
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
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: CombatRuling | None
    ) -> list[DomainEvent]:
        assert ok.target and ok.exit_label
        return [Moved(from_location_id=state.location_id, to_location_id=ok.target, exit_label=ok.exit_label)]


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
        holder = snap.character(thing.holder_id)
        if holder is not None and not holder.subdued:
            return Rejection("HELD_BY_OTHER", f"「{thing.name}」在{holder.name}手中，须先胜过此人，或待其相赠。")
        return Approval(intent, target=thing.id, source=thing.holder_id)

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: CombatRuling | None
    ) -> list[DomainEvent]:
        assert ok.target and ok.source
        return [ItemTransferred(item_id=ok.target, from_holder=ok.source, to_holder=state.player_id)]


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
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: CombatRuling | None
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
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: CombatRuling | None
    ) -> list[DomainEvent]:
        return [HealthChanged(delta=min(REST_GAIN, MAX_HP - state.hp), cause="调息疗伤", source="rest")]


class InvalidRule(Rule):
    """违背世界观的操作永不获准：热兵器、法术、元游戏指令在这里撞上死线。"""

    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        return Rejection("INVALID", intent.reason or "此举不合江湖天道。")

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: CombatRuling | None
    ) -> list[DomainEvent]:
        raise AssertionError("INVALID 永不获准")
