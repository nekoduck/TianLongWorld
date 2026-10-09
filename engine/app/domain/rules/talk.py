"""
[INPUT]: 依赖 rules/base 的 Rule / Approval / Rejection / Verdict / resolve / names / present，依赖 domain/events 的 Conversed / ItemTransferred /
         RelationChanged / DomainEvent，依赖 domain/models 的 Attitude，依赖 domain/combat 的 CombatRuling，依赖 domain/intent 的 PlayerIntent，
         依赖 domain/snapshot 的 LocalSnapshot；PlayerState 仅作类型标注
[OUTPUT]: 对外提供 TalkRule（与在场之人交谈）/ GiveRule（赠物；物归原主直升信赖）、TRUST_RESTORED（物归原主的缘由类别）
[POS]: rules 包里管"人与人"的确定性那几条：交谈是一条 Conversed，赠物是一次易手。
       信赖的来路是一张封闭清单，P1 只有一条——物归原主：对物主的态度低于信赖时直接升到信赖（阶梯「每次至多一档」的例外，与「翻脸直落敌视」对称），
       没有它，「二流及以上须信赖」会打断逻辑死线（一阳指是一流武学）
[PROTOCOL]: 变更时更新此头部，然后检查 rules/CLAUDE.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.combat import CombatRuling
from app.domain.events import Conversed, DomainEvent, ItemTransferred, RelationChanged
from app.domain.intent import PlayerIntent
from app.domain.models import Attitude
from app.domain.rules.base import Approval, Rejection, Rule, Verdict, names, present, resolve
from app.domain.snapshot import LocalSnapshot

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState

TRUST_RESTORED = "物归原主"


class TalkRule(Rule):
    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        who = present(intent.target_entity, snap)
        return who if isinstance(who, Rejection) else Approval(intent, target=who.id)

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: CombatRuling | None
    ) -> list[DomainEvent]:
        assert ok.target
        return [Conversed(npc_id=ok.target)]


class GiveRule(Rule):
    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        who = present(intent.target_entity, snap)
        if isinstance(who, Rejection):
            return who
        if not intent.item_used:
            return Rejection("NO_ITEM", "须说明赠予何物。")
        thing = resolve(intent.item_used, snap.inventory, names)
        if thing is None:
            return Rejection("NOT_CARRIED", f"你身上并无「{intent.item_used}」。")
        return Approval(intent, target=who.id, item=thing.id)

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: CombatRuling | None
    ) -> list[DomainEvent]:
        assert ok.target and ok.item
        events: list[DomainEvent] = [
            ItemTransferred(item_id=ok.item, from_holder=state.player_id, to_holder=ok.target)
        ]
        thing = snap.item(ok.item)
        if thing is not None and thing.owner_id == ok.target and state.attitude_of(ok.target).rank < Attitude.TRUSTED.rank:
            events.append(RelationChanged(
                character_id=ok.target, attitude=Attitude.TRUSTED, cause=TRUST_RESTORED, basis=TRUST_RESTORED
            ))
        return events
