"""
[INPUT]: 依赖 domain/combat 的 CombatOutcome / Stakes，依赖 domain/snapshot 的 LocalSnapshot
[OUTPUT]: 对外提供 MEANING（出手每种结局在故事里意味着什么）、combat_stakes()（出手一路的 <stakes> 段：对手、双方境界、所用武学火候、兵器、夺物）、
          safe()（逐值转义）与 join()（顿号连缀，空则「无」）——全部简报共用的两件笔墨工具
[POS]: application/briefs 的「战」：MEANING 的文字原样沿用 v3（经 2026-10 真实 Gemini 选型实测：只给「得手」二字，模型会把制住写成"退开半步"），
       如今它是 <physics> 里可裁结局的含义；攻方境界写领域按火候折算过的值，不让大模型二次折算；兵器不改境界。
       对手的身份、性情、态度与随身之物在 <people> / <things> 里（scene.py），这里只写这一招的赌注本身
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Iterable

from app.domain.combat import CombatOutcome, Stakes
from app.domain.snapshot import LocalSnapshot

# 每种结局在故事里意味着什么：简报逐条写明，推演才写得成那个样子
MEANING = {
    CombatOutcome.SUCCESS: "对手被你制住",
    CombatOutcome.STALEMATE: "谁也奈何不了谁，各自退开",
    CombatOutcome.MINOR_WOUND: "你吃了亏，带着轻伤退开",
    CombatOutcome.SEVERE_WOUND: "你身受重伤，拼死逃脱、保住性命",
    CombatOutcome.DEATH: "你当场毙命",
}


def safe(text: str) -> str:
    return text.replace("<", "＜").replace(">", "＞")  # 图谱描述与玩家原话都不能闭合或伪造协议标签


def join(parts: Iterable[str]) -> str:
    return "、".join(parts) or "无"


def combat_stakes(stakes: Stakes, scene: LocalSnapshot) -> list[str]:
    """出手的赌注：对手、双方境界（攻方已按火候折算）、对手性情、所用武学与火候、兵器、夺物的标的。每行逐值转义。"""
    foe = scene.character(stakes.defender_id)
    art = scene.skill(stakes.skill_id) if stakes.skill_id else None
    mastery = scene.mastery(art.id) if art else None
    used = f"{art.name}（{mastery.value}）" if art and mastery else (art.name if art else "徒手")
    weapon = scene.label(stakes.item_id) if stakes.item_id else "无"
    lines = [
        f"对手：{foe.name if foe else scene.label(stakes.defender_id)}｜境界{stakes.defender_tier.value}｜性情{stakes.disposition.value}",
        f"你：境界{stakes.attacker_tier.value}（已按火候折算，不要再因火候压低）｜所用：{used}｜兵器：{weapon}（兵器不改境界）",
    ]
    if stakes.seize_id:
        lines.append(f"所图：夺下{scene.label(stakes.seize_id)}（得手即易手，由规则结算）")
    return [safe(line) for line in lines]
