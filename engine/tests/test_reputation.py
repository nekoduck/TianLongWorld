"""
[INPUT]: 依赖 app.domain.reputation 的 ripple / kinship，依赖 app.domain.rules 的 decide，依赖 app.domain.snapshot 的 BondView，
         依赖 app.domain.models 的 Attitude / Era / RelationKind，依赖 tests/test_rules 的 scene / act / recast
[OUTPUT]: 名声的单测：受害者敌视（basis 交手）、与之休戚与共的在场目睹者敌视且缘由写明称谓（上首见徒弟挨打是「你打伤其得意门徒」，反之是「师父」）、
          开篇的仇家升一档（敌人之敌，至多友善，敌视者只升到戒备）、将至 / 后文的羁绊一概不动、被制住的目睹者与不在场者不动、已是那一档的不重复入账；
          快照只有对方那头的边时把上首翻过来
[POS]: tests 的人情涟漪基线：T=0 的人不提前记起未来的恩怨
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from app.domain.events import RelationChanged
from app.domain.intent import ActionType
from app.domain.models import Attitude, Era, RelationKind
from app.domain.reputation import kinship, ripple
from app.domain.rules import decide
from app.domain.snapshot import BondView, LocalSnapshot
from tests.test_rules import act, recast, scene


def bonds(snap: LocalSnapshot, cid: str, *views: BondView) -> LocalSnapshot:
    return recast(snap, cid, bonds=views)


def changes(events: list[object]) -> dict[str, tuple[Attitude, str, str]]:
    return {e.character_id: (e.attitude, e.cause, e.basis) for e in events if isinstance(e, RelationChanged)}


async def test_the_master_sees_his_favourite_disciple_struck() -> None:
    """实录回合 1 的复现：打龚光杰，左子穆（师徒关系的上首）的缘由写明「你打伤其得意门徒」。"""
    state, snap = await scene("loc:无量山")
    snap = bonds(snap, "chr:左子穆", BondView(other_id="chr:龚光杰", kind=RelationKind.MENTOR, lead=True),
                 BondView(other_id="chr:辛双清", kind=RelationKind.ENEMY, lead=True))
    snap = bonds(snap, "chr:龚光杰", BondView(other_id="chr:左子穆", kind=RelationKind.MENTOR, lead=False))
    got = changes(decide(act(ActionType.ATTACK, target_entity="龚光杰"), state, snap))
    assert got == {
        "chr:龚光杰": (Attitude.HOSTILE, "遭你出手相攻", "交手"),
        "chr:左子穆": (Attitude.HOSTILE, "你打伤其得意门徒龚光杰", "师徒"),
    }  # 辛双清与龚光杰之间没有羁绊：不动
    hit_master = changes(decide(act(ActionType.ATTACK, target_entity="左子穆"), state, snap))
    assert hit_master["chr:龚光杰"] == (Attitude.HOSTILE, "你打伤其师父左子穆", "师徒")
    assert hit_master["chr:辛双清"] == (Attitude.FRIENDLY, "你出手教训其仇家左子穆", "仇敌")  # 敌人之敌


async def test_only_bonds_of_the_opening_ripple() -> None:
    state, snap = await scene("loc:无量山")
    for era in (Era.IMMINENT, Era.LATER):
        later = bonds(snap, "chr:左子穆", BondView(other_id="chr:龚光杰", kind=RelationKind.MENTOR, lead=True, era=era),
                      BondView(other_id="chr:辛双清", kind=RelationKind.ENEMY, era=era))
        later = bonds(later, "chr:龚光杰", BondView(other_id="chr:左子穆", kind=RelationKind.MENTOR, era=era))
        later = bonds(later, "chr:辛双清", BondView(other_id="chr:左子穆", kind=RelationKind.ENEMY, era=era))
        got = changes(decide(act(ActionType.ATTACK, target_entity="左子穆"), state, later))
        assert set(got) == {"chr:左子穆"}  # 还没结下的师徒与仇怨，T=0 的人记不起来


async def test_the_enemy_of_your_enemy_moves_one_step_and_no_further() -> None:
    hostile = RelationChanged(character_id="chr:辛双清", attitude=Attitude.HOSTILE, cause="c")
    state, snap = await scene("loc:无量山", hostile)
    assert changes(decide(act(ActionType.ATTACK, target_entity="左子穆"), state, snap))["chr:辛双清"][0] is Attitude.WARY
    friendly = RelationChanged(character_id="chr:辛双清", attitude=Attitude.FRIENDLY, cause="c")
    state, snap = await scene("loc:无量山", friendly)
    assert "chr:辛双清" not in changes(decide(act(ActionType.ATTACK, target_entity="左子穆"), state, snap))


async def test_subdued_absent_and_already_hostile_witnesses_stay_put() -> None:
    state, snap = await scene("loc:无量山")
    foe = snap.character("chr:左子穆")
    assert foe is not None
    out = changes(ripple(foe, state, recast(snap, "chr:龚光杰", subdued=True)))
    assert "chr:龚光杰" not in out  # 被制住的人顾不上记恨
    grudge = RelationChanged(character_id="chr:龚光杰", attitude=Attitude.HOSTILE, cause="旧怨")
    state, snap = await scene("loc:无量山", grudge)
    foe = snap.character("chr:左子穆")
    assert foe is not None and "chr:龚光杰" not in changes(ripple(foe, state, snap))
    state, snap = await scene("loc:大理城")
    duan = snap.character("chr:段正淳")
    assert duan is not None and set(changes(ripple(duan, state, snap))) == {"chr:段正淳", "chr:段誉"}  # 乔峰远在无锡，不知此事


async def test_a_one_sided_bond_is_read_from_the_witness_side() -> None:
    _, snap = await scene("loc:无量山")
    snap = bonds(snap, "chr:左子穆")  # 快照只在徒弟那头留了边
    snap = bonds(snap, "chr:龚光杰", BondView(other_id="chr:左子穆", kind=RelationKind.MENTOR, lead=False))
    master, disciple = snap.character("chr:左子穆"), snap.character("chr:龚光杰")
    assert master is not None and disciple is not None
    assert kinship(master, disciple) == (RelationKind.MENTOR, "得意门徒")
    assert kinship(disciple, master) == (RelationKind.MENTOR, "师父")
