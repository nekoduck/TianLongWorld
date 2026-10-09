"""
[INPUT]: 依赖 rules/base 的 Rule / Approval / Rejection / Verdict / resolve / names / present / ground，依赖 rules/parley 的 parley，
         依赖 domain/approach 的 Route / cell_of / infer_aim，依赖 domain/events 的 Conversed / PlacesLearned / ItemTransferred / RelationChanged / DomainEvent，
         依赖 domain/models 的 Attitude，依赖 domain/stakes 的 AnyStakes / Ruling，依赖 domain/intent 的 PlayerIntent，
         依赖 domain/snapshot 的 LocalSnapshot；PlayerState 仅作类型标注
[OUTPUT]: 对外提供 TalkRule（与在场之人交谈：寻常即闲谈 Conversed（带落了地的话题；话题是此地或去处即问路，另出 PlacesLearned），威逼 / 言辞 / 人情 / 套话 / 借势即交涉）/
          directions()（问路得知的去处：此地相邻去处 ∪ 话题地点，减去已认得的）/
          GiveRule（赠物；物归原主直升信赖——东西不是从物主本人手里拿来的才算）、TRUST_RESTORED（物归原主的缘由类别）
[POS]: rules 包里管"人与人"的那几条：寻常交谈是一条 Conversed，赠物是一次易手；
       问路是寻常交谈的一种：话题落在此地或某个去处上，对方便把四下还不认得的去处指给你（PlacesLearned，指路人即交谈对象），从此它们不再是「未知区域」；
       带了手段的交谈按兼容表走交涉区间（所图缺省推断：威逼或带话题 → 打探，对方敌视或戒备 → 化解，其余 → 结交；威逼图不来结交 / 化解 / 求艺），定案由门面经 parley.settled 落为 Parleyed 及其附带的事件。
       信赖的来路是一张封闭清单，P1 只有一条——物归原主：对物主的态度低于信赖时直接升到信赖（阶梯「每次至多一档」的例外，与「翻脸直落敌视」对称），
       没有它，「二流及以上须信赖」会打断逻辑死线（一阳指是一流武学）。物归原主只认真正的「归」：东西若是从物主本人手里到你身上的
       （PlayerState.taken_from：偷来、骗来、讨来、夺来），还回去只是易手——否则弱者偷了再还，就能把仇人刷成信赖、换来二流以上的传功
[PROTOCOL]: 变更时更新此头部，然后检查 rules/CLAUDE.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.approach import Route, cell_of, infer_aim
from app.domain.events import Conversed, DomainEvent, ItemTransferred, PlacesLearned, RelationChanged
from app.domain.intent import PlayerIntent
from app.domain.models import Attitude
from app.domain.rules.base import Approval, Rejection, Rule, Verdict, ground, names, present, resolve
from app.domain.rules.parley import parley
from app.domain.snapshot import LocalSnapshot
from app.domain.stakes import AnyStakes, Ruling

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState

TRUST_RESTORED = "物归原主"


class TalkRule(Rule):
    def adjudicate(self, intent: PlayerIntent, state: PlayerState, snap: LocalSnapshot) -> Verdict:
        who = present(intent.target_entity, snap)
        if isinstance(who, Rejection):
            return who
        topic = ground(intent.topic, snap)
        if cell_of(intent).route is Route.FIXED:
            return Approval(intent, target=who.id, topic=topic)
        return Approval(intent, target=who.id, topic=topic, route=Route.SOCIAL, aim=infer_aim(intent, attitude=who.attitude))

    def stakes(self, ok: Approval, state: PlayerState, snap: LocalSnapshot) -> AnyStakes | None:
        return parley(ok, state, snap) if ok.route is Route.SOCIAL else None

    def consequences(
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: Ruling | None
    ) -> list[DomainEvent]:
        assert ok.target and ok.route is Route.FIXED  # 交涉的定案由门面经 parley.settled 落为事件
        events: list[DomainEvent] = [Conversed(npc_id=ok.target, topic_id=ok.topic)]
        if learned := directions(ok.topic, state, snap):
            events.append(PlacesLearned(location_ids=learned, source_id=ok.target))
        return events


def directions(topic: str | None, state: PlayerState, snap: LocalSnapshot) -> tuple[str, ...]:
    """
    问路：寻常攀谈的话题落在此地或某个去处上，本地人便把四下的去处说给你听——此地所有相邻去处 ∪ 话题地点，
    减去已经认得的（亲历、问过路、远远望得见、天下皆知：快照出路的 discovery 已按它们算好），按 id 排序；话题不是地点或没什么可说即空。
    """
    places = {snap.location.id, *(e.to_id for e in snap.exits)}
    if topic not in places:
        return ()
    known = state.visited | state.heard | {e.to_id for e in snap.exits if e.known}
    return tuple(sorted((places - {snap.location.id} | {topic}) - known))


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
        self, ok: Approval, state: PlayerState, snap: LocalSnapshot, ruling: Ruling | None
    ) -> list[DomainEvent]:
        assert ok.target and ok.item
        events: list[DomainEvent] = [
            ItemTransferred(item_id=ok.item, from_holder=state.player_id, to_holder=ok.target)
        ]
        thing = snap.item(ok.item)
        if (
            thing is not None and thing.owner_id == ok.target
            and state.taken_from.get(ok.item) != ok.target  # 从物主手里偷来、讨来、夺来再还：只是易手
            and state.attitude_of(ok.target).rank < Attitude.TRUSTED.rank
        ):
            events.append(RelationChanged(
                character_id=ok.target, attitude=Attitude.TRUSTED, cause=TRUST_RESTORED, basis=TRUST_RESTORED
            ))
        return events
