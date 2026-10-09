"""
[INPUT]: 依赖 application/briefs/combat 的 safe，依赖 application/chronicle 的 titled，
         依赖 domain/social 的 SocialStakes / SocialRuling / effects，依赖 domain/events 的 RelationChanged，依赖 domain/outcomes 的 SocialOutcome，
         依赖 domain/intent 的 Aim / Approach，依赖 domain/snapshot 的 LocalSnapshot
[OUTPUT]: 对外提供 social_stakes()（交涉一路的 <stakes> 段：所图、标的、手段、筹码）、meaning()（某结局在这一次交涉里意味着什么：如愿随所图而变）、
          MANNER（威逼 / 套话这两种说法）
[POS]: application/briefs 的「交」：对方的身份、性情、态度与恩怨、外显人设都在 <people> 里（scene.py），这里只写这一番交涉赌的是什么。
       见闻只露玩家已知者（known=True）的正文——打探的标的是一条玩家尚不知道的见闻，简报只说「此人所知的一桩事」，绝不写出正文
       （否则地下城主的微观事实会把它泄给玩家）。含义与 domain/social.effects 的效果表逐条对应：推演写成什么样，定案就是什么样
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from app.application.briefs.combat import safe
from app.application.chronicle import titled
from app.domain.events import RelationChanged
from app.domain.intent import Aim, Approach
from app.domain.outcomes import SocialOutcome
from app.domain.snapshot import LocalSnapshot
from app.domain.social import SocialRuling, SocialStakes, effects

S = SocialOutcome
MANNER = {Approach.FORCE: "威逼", Approach.GUILE: "套话"}  # 这两种手段在交涉里另有说法（兼容表的格子）

_AIM_GRANTED = {
    Aim.PROBE: "他把所知的一桩事告诉了你——只写他开口相告，不写所说的内容",
    Aim.ASK: "他把{subject}交到了你手里",
    Aim.LEARN: "他松口答应传授{subject}，但今日并不传功",
    Aim.BEFRIEND: "他对你生出好感",
    Aim.DEFUSE: "他放下了几分敌意",
    Aim.WARN: "他把你的话听了进去",
}


def _subject(stakes: SocialStakes, scene: LocalSnapshot) -> str:
    """标的名：武学与物品写名字；见闻（打探的标的）恒是玩家尚不知道的，绝不写出正文。"""
    sid = stakes.subject_id
    if not sid:
        return ""
    if sid.startswith("fact:"):
        return "此人所知的一桩事（你尚不知其详）"
    if art := scene.skill(sid):
        need = f"，须他对你{stakes.need.value}才肯传" if stakes.need else ""
        return f"{art.name}（{art.tier.value}{need}）"
    return scene.label(sid)


def meaning(stakes: SocialStakes, outcome: SocialOutcome, scene: LocalSnapshot) -> str:
    if outcome is S.GRANTED:
        sid = stakes.subject_id or ""
        art = scene.skill(sid)
        # 见闻的 label 就是正文：打探的模板里没有 {subject}，这里仍把 fact: 挡在外面，免得哪天模板一改就泄了底
        name = art.name if art else ("那样东西" if not sid or sid.startswith("fact:") else scene.label(sid))
        base = _AIM_GRANTED.get(stakes.aim, "他答应了你的所求").format(subject=name)
        return f"慑于你的威势，{base}，心中却不服" if stakes.approach is Approach.FORCE else base
    if outcome is S.SOFTENED:
        # 松动升不升人情，直接问效果表（求艺至多升到门槛下一档、威逼与诡计只换来口风）：含义与定案同出一源
        ruled = effects(stakes, SocialRuling(outcome=outcome, adopted=True), scene.player_id)
        warmer = any(isinstance(e, RelationChanged) and e.attitude.rank > stakes.attitude.rank for e in ruled)
        return "他口风已松、对你多了几分好感，却未当场答应" if warmer else "他口风已松，却未当场答应"
    return {
        S.NOTHING: "他不置可否，此事没有进展",
        S.REBUFFED: "你被他顶了回来，交情如旧",
        S.FALLOUT: "他当场翻脸，从此视你为敌（只是翻脸，不动手）",
    }[outcome]


def _levers(stakes: SocialStakes, scene: LocalSnapshot) -> list[str]:
    """筹码：已知见闻写正文（只认 known=True——领域本就只选已知者，这里再守一道），靠山写称呼、对你的态度与他俩的关系。"""
    out = []
    for lever in stakes.leverage_ids:
        fact = next((f for f in scene.facts if f.id == lever), None)
        if fact is not None:
            if fact.known:
                out.append(f"- 你知道的事：{fact.text}")
            continue
        if (patron := scene.character(lever)) is not None:
            npc = scene.character(stakes.npc_id)
            bond = (patron.bond_with(npc.id) or npc.bond_with(patron.id)) if npc else None
            out.append(f"- 靠山：{titled(patron)}（对你{patron.attitude.value}，与对方：{bond.value if bond else '有旧'}）")
    return out


def social_stakes(stakes: SocialStakes, scene: LocalSnapshot) -> list[str]:
    """交涉的赌注：对象、所图与标的、手段（威逼 / 套话另有说法）、筹码。交涉永不动武、永不伤人。每行逐值转义。"""
    npc = scene.character(stakes.npc_id)
    manner = MANNER.get(stakes.approach)
    subject = _subject(stakes, scene)
    levers = _levers(stakes, scene)
    lines = [
        f"对象：{npc.name if npc else scene.label(stakes.npc_id)}｜对你{stakes.attitude.value}｜性情{stakes.disposition.value}",
        f"所图：{stakes.aim.value}" + (f"｜标的：{subject}" if subject else ""),
        f"手段：{stakes.approach.value}" + (f"（{manner}）" if manner else ""),
        "筹码：" + ("" if levers else "无"),
        *levers,
    ]
    return [safe(line) for line in lines]
