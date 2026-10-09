"""
[INPUT]: 依赖 domain/events 的 RelationChanged，依赖 domain/models 的 Attitude / Era / RelationKind，依赖 domain/snapshot 的 LocalSnapshot / CharacterView / BondView；
         PlayerState 仅作类型标注（避免与 aggregates 成环）
[OUTPUT]: 对外提供 ripple()（出手之后的人情涟漪：受害者、与之休戚与共的目睹者、其仇家）、kinship()（目睹者眼里受害者是他的什么人）、STRUCK（受害者的缘由类别）
[POS]: domain 的名声：取代 rules 里的 _ripple。人情的涟漪只沿图谱的 HAS_RELATION 走一跳、只波及在场且行动自如的目睹之人，
       而且只认结于开篇（era=开篇）的羁绊——将至、后文才结下的关系在 T=0 还不存在，沿它翻转态度就是让人提前记起未来的恩怨。
       在开篇的范围内恢复「敌人之敌」：受害者的仇家对你升一档（至多友善，阶梯每次至多一档）；与受害者休戚与共者直落敌视。
       缘由写明称谓与其人：师徒、主仆按上首（lead）分——目睹者是师父时写「你打伤其得意门徒某某」，反之「你打伤其师父某某」；
       亲族不分长幼统称「至亲」（亲族边里有父子也有夫妻、兄弟，方向靠不住）……
       RelationChanged.basis 记下称谓所据的关系（「师徒」）或受害者本人的「交手」
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.events import RelationChanged
from app.domain.models import Attitude, Era, RelationKind
from app.domain.snapshot import BondView, CharacterView, LocalSnapshot

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState

STRUCK = "交手"

# 目睹者眼里，受害者是他的什么人：(关系, 目睹者是否上首) → 称谓
_KIN: dict[tuple[RelationKind, bool], str] = {
    (RelationKind.MENTOR, True): "得意门徒",
    (RelationKind.MENTOR, False): "师父",
    (RelationKind.KIN, True): "至亲",  # 亲族不分长幼：蓝图的亲族边里有父子，也有夫妻、兄弟，边的方向说不清谁是长辈
    (RelationKind.KIN, False): "至亲",
    (RelationKind.FELLOW, True): "同门",
    (RelationKind.FELLOW, False): "同门",
    (RelationKind.SWORN, True): "结义兄弟",
    (RelationKind.SWORN, False): "结义兄弟",
    (RelationKind.SERVES, True): "属下",
    (RelationKind.SERVES, False): "主人",
    (RelationKind.LOVER, True): "意中人",
    (RelationKind.LOVER, False): "意中人",
    (RelationKind.ENEMY, True): "仇家",
    (RelationKind.ENEMY, False): "仇家",
}


def _bond(witness: CharacterView, foe: CharacterView) -> BondView | None:
    """目睹者与受害者之间那条开篇的羁绊，lead 按目睹者的立场：快照两头都有边时取目睹者自己那头，只有对方那头时把上首翻过来。"""
    own = next((b for b in witness.bonds if b.other_id == foe.id and b.era is Era.OPENING), None)
    if own is not None:
        return own
    theirs = next((b for b in foe.bonds if b.other_id == witness.id and b.era is Era.OPENING), None)
    return theirs.model_copy(update={"other_id": foe.id, "lead": not theirs.lead}) if theirs is not None else None


def kinship(witness: CharacterView, foe: CharacterView) -> tuple[RelationKind, str] | None:
    """目睹者眼里受害者是他的什么人（只认开篇的羁绊）：(关系, 称谓)。"""
    bond = _bond(witness, foe)
    return (bond.kind, _KIN[bond.kind, bond.lead]) if bond is not None else None


def ripple(foe: CharacterView, state: PlayerState, snap: LocalSnapshot) -> list[RelationChanged]:
    """出手之后谁对你变了脸色：受害者敌视；与之休戚与共的目睹者敌视；其仇家升一档（至多友善）。态度没变的不入账。"""
    out: list[RelationChanged] = []
    if state.attitude_of(foe.id) is not Attitude.HOSTILE:
        out.append(RelationChanged(character_id=foe.id, attitude=Attitude.HOSTILE, cause="遭你出手相攻", basis=STRUCK))
    for witness in snap.characters:
        if witness.id == foe.id or witness.subdued or (tie := kinship(witness, foe)) is None:
            continue
        kind, title = tie
        regard = state.attitude_of(witness.id)
        if kind is RelationKind.ENEMY:
            if regard.rank < Attitude.FRIENDLY.rank:
                out.append(RelationChanged(character_id=witness.id, attitude=regard.step(1),
                                           cause=f"你出手教训其{title}{foe.name}", basis=kind.value))
        elif regard is not Attitude.HOSTILE:
            out.append(RelationChanged(character_id=witness.id, attitude=Attitude.HOSTILE,
                                       cause=f"你打伤其{title}{foe.name}", basis=kind.value))
    return out
