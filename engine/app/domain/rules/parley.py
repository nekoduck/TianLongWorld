"""
[INPUT]: 依赖 rules/base 的 Approval / player_tier，依赖 domain/social 的 assess_social / SocialStakes / SocialRuling / effects / LEARN_DIFFICULTY / CAP，
         依赖 domain/covert 的 assess_covert / CovertStakes / CovertRuling / effects，依赖 domain/threads 的 pursuing，
         依赖 domain/events 的 DomainEvent，依赖 domain/intent 的 Aim / Approach，依赖 domain/models 的 Attitude / Era / RelationKind / Tier，
         依赖 domain/snapshot 的 LocalSnapshot / CharacterView / FactView；PlayerState 仅作类型标注
[OUTPUT]: 对外提供 parley()（获准的交涉 → SocialStakes）、filch()（获准的暗中取物 → CovertStakes）、settled()（交与暗两路的定案 → 事件）、
          leverage()（此人吃哪些筹码）、fact_to_learn()（打探如愿时领域预先选定的那条见闻）、required_regard()（求教门槛）
[POS]: rules 包里「交」与「暗」两路的接线：从快照与玩家状态里取出 social / covert 需要的一切事实，交给它们圈区间；
       定案后的事件也由它们产出——规则只负责把指称落了地的意图翻成纯数值的输入。
       筹码（leverage_ids）由领域确定性选出，大模型不提议：借势 = 在场、行动自如、对你友善以上、与他有开篇羁绊的靠山 + 已知的 LEVERAGE 见闻；
       言辞 / 人情 = 已知的 MOTIVE 见闻（知其所好）；威逼 = 已知的 LEVERAGE 见闻（把柄）；套话没有筹码。
       见闻只认快照里的：筹码只取 known=True 者（玩家已知，图谱把主体或 unlock 目标在场的已知见闻带进快照——从甲处听来的把柄，
       甲不在场照样能用在乙身上）；打探只取 known=False 且此人正是知情人者（他就站在你面前）、unlock 的目标落得了地（在名称表里）；
       有话题则只取牵涉话题的那几条；按 id 取第一条
[PROTOCOL]: 变更时更新此头部，然后检查 rules/CLAUDE.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain import covert, social
from app.domain.events import DomainEvent
from app.domain.intent import Aim, Approach
from app.domain.models import Attitude, Era, RelationKind, Tier
from app.domain.rules.base import Approval, player_tier
from app.domain.snapshot import CharacterView, FactView, LocalSnapshot, SkillView
from app.domain.threads import pursuing

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState

_LEVER_KINDS: dict[Approach, frozenset[str]] = {
    Approach.LEVERAGE: frozenset({"LEVERAGE"}),
    Approach.WORDS: frozenset({"MOTIVE"}),
    Approach.FAVOR: frozenset({"MOTIVE"}),
    Approach.FORCE: frozenset({"LEVERAGE"}),
}


def required_regard(art: SkillView) -> Attitude:
    """求教的门槛：三流（及不入流）的功夫，友善之人便肯教；二流及以上，非信赖之人不传。"""
    return Attitude.FRIENDLY if art.tier.rank <= Tier.THIRD.rank else Attitude.TRUSTED


def _patrons(npc: CharacterView, state: PlayerState, snap: LocalSnapshot) -> list[str]:
    """在场的靠山：行动自如、对你友善以上、与此人有开篇的休戚与共之谊。"""
    out = []
    for c in snap.characters:
        if c.id == npc.id or c.subdued or c.attitude.rank < Attitude.FRIENDLY.rank:
            continue
        ties = [b for b in (*c.bonds, *npc.bonds) if b.other_id in (npc.id, c.id) and b.era is Era.OPENING]
        if any(b.kind is not RelationKind.ENEMY for b in ties):
            out.append(c.id)
    return out


def leverage(npc: CharacterView, approach: Approach, state: PlayerState, snap: LocalSnapshot) -> tuple[str, ...]:
    kinds = _LEVER_KINDS.get(approach, frozenset())
    facts = [
        f.id for f in snap.facts
        if f.known and f.unlock is not None and f.unlock.kind in kinds and (f.unlock.target_id == npc.id or npc.id in f.subject_ids)
    ]
    patrons = _patrons(npc, state, snap) if approach is Approach.LEVERAGE else []
    return tuple(sorted({*facts, *patrons}))


def fact_to_learn(npc: CharacterView, topic: str | None, state: PlayerState, snap: LocalSnapshot) -> FactView | None:
    candidates = [
        f for f in snap.facts
        if not f.known and npc.id in f.knower_ids and f.id not in state.known_facts
        and (f.unlock is None or f.unlock.target_id in snap.labels)
    ]
    if topic:
        candidates = [f for f in candidates if topic == f.id or topic in f.subject_ids]
    return candidates[0] if candidates else None  # 快照里的见闻恒按 id 排序


def parley(ok: Approval, state: PlayerState, snap: LocalSnapshot) -> social.SocialStakes:
    """获准的交涉 → 可裁区间。ok.target 是交涉对象；aim 已推断；求艺看 ok.skill，讨要看 ok.item（或落在他身上的话题之物）。"""
    assert ok.target and ok.aim
    npc = snap.character(ok.target)
    assert npc is not None
    approach, aim = ok.intent.approach, ok.aim
    subject: str | None = None
    reachable, difficulty, need = True, 0, None
    match aim:
        case Aim.PROBE:
            fact = fact_to_learn(npc, ok.topic, state, snap)
            subject, reachable = (fact.id if fact else None), fact is not None
        case Aim.ASK:
            wanted = snap.item(ok.item or ok.topic or "")
            held = wanted is not None and wanted.holder_id == npc.id and wanted.portable
            subject, reachable = (wanted.id if held and wanted else None), held
            difficulty = 1 if held and wanted and wanted.owner_id == npc.id else 0
        case Aim.LEARN:
            art = snap.skill(ok.skill or ok.topic or "")
            if art is None or art.id not in npc.skill_ids:
                reachable = False
            else:
                need = required_regard(art)
                subject, difficulty = art.id, social.LEARN_DIFFICULTY[art.tier.value]
                reachable = need.rank <= social.CAP.rank and npc.attitude.step(1).rank >= need.rank
        case Aim.BEFRIEND | Aim.DEFUSE:
            reachable = npc.attitude.rank < social.CAP.rank
    tried = pursuing(state.threads, npc.id, aim)
    return social.assess_social(
        npc_id=npc.id, aim=aim, approach=approach, attitude=npc.attitude, disposition=npc.disposition,
        edge=player_tier(state, snap).rank - npc.tier.rank, subdued=npc.subdued,
        leverage_ids=leverage(npc, approach, state, snap), difficulty=difficulty,
        repeated=tried is not None and approach in tried.tried, reachable=reachable, subject_id=subject, need=need,
    )


def filch(ok: Approval, state: PlayerState, snap: LocalSnapshot) -> covert.CovertStakes:
    """获准的暗中取物 → 可裁区间。ok.source 是失主，ok.item 是那件东西。"""
    assert ok.source and ok.item
    holder = snap.character(ok.source)
    assert holder is not None
    return covert.assess_covert(
        target_id=holder.id, item_id=ok.item, approach=ok.intent.approach, attitude=holder.attitude,
        player=player_tier(state, snap), holder=holder.tier, subdued=holder.subdued,
    )


def settled(
    at_stake: social.SocialStakes | covert.CovertStakes, ruling: object, state: PlayerState
) -> list[DomainEvent]:
    if isinstance(at_stake, social.SocialStakes):
        assert isinstance(ruling, social.SocialRuling)
        return social.effects(at_stake, ruling, state.player_id)
    assert isinstance(ruling, covert.CovertRuling)
    return covert.effects(at_stake, ruling, state.player_id)
