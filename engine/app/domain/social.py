"""
[INPUT]: 依赖 domain/outcomes 的 SocialOutcome，依赖 domain/events 的 Parleyed / FactLearned / ItemTransferred / RelationChanged / DomainEvent，
         依赖 domain/intent 的 Aim / Approach，依赖 domain/models 的 Attitude / Disposition
[OUTPUT]: 对外提供 SocialStakes（一次交涉的赌注：可裁区间 + 确定性裁决 + 定案所需的一切）、assess_social()（圈区间）、
          SocialRuling 与 settle_social()（出界取确定性裁决）、effects()（定案 → 事件）、LEARN_DIFFICULTY（求艺按武学境界的难度）、CAP 交涉的人情上限
[POS]: domain 的交涉硬轨，与 combat.py 对称：言辞 / 人情 / 借势 / 威逼 / 套话不再是一句闲谈，而是一个由领域圈定的可裁区间，
       地下城主只能在区间里挑结局。分数只看图谱与聚合给的事实：人情 rank、性情、境界差（只用于威逼）、筹码（见闻与靠山）、所图的难度、纠缠不休。
       硬约束与「越级取胜不在区间里」同等地位：
         如愿须够得着（打探须有可说的见闻、讨要须他手里真有那件东西、求艺须一档之内够上传功门槛）；
         人情阶梯每次至多一档，例外只有翻脸直落敌视；言辞 / 人情 / 借势 / 套话至多把人推到友善——信赖只来自封闭清单（物归原主）；
         只有武力 / 计谋 / 借势会激人翻脸，仁厚者永不翻脸；交涉永不致死（不产出任何气血涨落）；
         求艺从不传功——如愿只是对方松口（人情够上门槛），下一回合再求才走修习的两道门
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from dataclasses import dataclass

from app.domain.events import DomainEvent, FactLearned, ItemTransferred, Parleyed, RelationChanged
from app.domain.intent import Aim, Approach
from app.domain.models import Attitude, Disposition
from app.domain.outcomes import SocialOutcome

S = SocialOutcome
ORDER: tuple[SocialOutcome, ...] = tuple(SocialOutcome)  # 由对玩家最有利到最不利
CAP = Attitude.FRIENDLY  # 交涉至多把人推到这一档：信赖只来自行动（物归原主）
HARSH = frozenset({Approach.FORCE, Approach.GUILE})  # 威逼与诡计：区间更宽，也可能激人翻脸
_PROVOKING = HARSH | {Approach.LEVERAGE}  # 只有这几种手段会激人翻脸；言辞、人情不会（P1 没有可机读的「忌」）
_TEMPER = {Disposition.MERCIFUL: 1, Disposition.NEUTRAL: 0, Disposition.RUTHLESS: -1}
LEARN_DIFFICULTY = {"不入流": 0, "三流": 0, "二流": 1, "一流": 2, "绝顶": 3}  # 求艺的难度按武学境界

_CAUSE_UP = {
    Approach.WORDS: "为你言辞所动",
    Approach.FAVOR: "承你人情",
    Approach.LEVERAGE: "碍于情面",
    Approach.GUILE: "被你说得动了心",
    Approach.PLAIN: "与你相谈投机",
}
_CAUSE_FALLOUT = {
    Approach.FORCE: "被你威逼，当场翻脸",
    Approach.GUILE: "识破你的诡计，当场翻脸",
    Approach.LEVERAGE: "恼你借势压人，当场翻脸",
}


@dataclass(frozen=True, slots=True)
class SocialStakes:
    """
    一次交涉的赌注。admissible 由对玩家最有利到最不利排列，canonical 是其中的确定性裁决；
    subject_id 是打探的见闻（fact:）/ 讨要之物（itm:）/ 求艺之功（art:），need 是求艺所需的人情档。score 只供测试与简报，不下发。
    """

    npc_id: str
    aim: Aim
    approach: Approach
    attitude: Attitude
    disposition: Disposition
    score: int
    admissible: tuple[SocialOutcome, ...]
    canonical: SocialOutcome
    subject_id: str | None = None
    leverage_ids: tuple[str, ...] = ()
    need: Attitude | None = None

    @property
    def target_id(self) -> str:
        return self.npc_id

    @property
    def contested(self) -> bool:
        return len(self.admissible) > 1


@dataclass(frozen=True, slots=True)
class SocialRuling:
    outcome: SocialOutcome
    adopted: bool  # 采纳了地下城主的结局；未采纳时它的速写作废
    hp_change: int = 0  # 交涉永不伤人：恒为 0，只为与 CombatRuling 同形


def _envelope(score: int, approach: Approach, disposition: Disposition) -> tuple[tuple[SocialOutcome, ...], SocialOutcome]:
    if score >= 2:
        return (S.GRANTED, S.SOFTENED), S.GRANTED
    if score == 1:
        return (S.GRANTED, S.SOFTENED, S.NOTHING), S.SOFTENED
    if score == 0:
        return (S.SOFTENED, S.NOTHING, S.REBUFFED), S.NOTHING
    harsh = approach in HARSH
    if score == -1:
        return ((S.NOTHING, S.REBUFFED, S.FALLOUT) if harsh else (S.NOTHING, S.REBUFFED)), S.REBUFFED
    ruthless_provoked = harsh and disposition is Disposition.RUTHLESS
    return (S.REBUFFED, S.FALLOUT), (S.FALLOUT if ruthless_provoked else S.REBUFFED)


def _nearest(admissible: tuple[SocialOutcome, ...], wanted: SocialOutcome) -> SocialOutcome:
    """被硬约束剔掉的确定性裁决，落到区间里离它最近的一格（同样近取对玩家更有利的那格）。"""
    return min(admissible, key=lambda o: (abs(ORDER.index(o) - ORDER.index(wanted)), ORDER.index(o)))


def assess_social(
    *,
    npc_id: str,
    aim: Aim,
    approach: Approach,
    attitude: Attitude,
    disposition: Disposition,
    edge: int = 0,
    subdued: bool = False,
    leverage_ids: tuple[str, ...] = (),
    difficulty: int = 0,
    repeated: bool = False,
    reachable: bool = True,
    subject_id: str | None = None,
    need: Attitude | None = None,
) -> SocialStakes:
    """
    分数（纯函数，输入全来自快照与聚合）：
      交情 rank（威逼不看交情，看境界差 edge = 你的境界 − 他的境界，钳在 ±2；对方已被制住再 +2）
      + 性情（仁厚 +1、中庸 0、狠辣 −1）+ 筹码 min(2, 见闻与靠山的件数) − 所图的难度 − 1（同一手段求过同一件事：纠缠不休只会更糟）
      − 1（借势而无势可借：虚张声势）。
    区间：≥2 如愿·松动（如愿）；1 如愿·松动·无果（松动）；0 松动·无果·碰壁（无果）；−1 无果·碰壁[+翻脸 若威逼 / 诡计]（碰壁）；
          ≤−2 碰壁·翻脸（碰壁；狠辣者遭威逼或识破诡计时翻脸）。
    硬约束随后剔格：够不着则无如愿；言辞 / 人情不激人翻脸；仁厚者不翻脸；剔掉的确定性裁决落到最近的一格。
    """
    score = (max(-2, min(2, edge)) + (2 if subdued else 0)) if approach is Approach.FORCE else attitude.rank
    score += _TEMPER[disposition] + min(2, len(leverage_ids)) - difficulty - (1 if repeated else 0)
    if approach is Approach.LEVERAGE and not leverage_ids:
        score -= 1
    admissible, canonical = _envelope(score, approach, disposition)
    banned = set()
    if not reachable:
        banned.add(S.GRANTED)
    if approach not in _PROVOKING or disposition is Disposition.MERCIFUL:
        banned.add(S.FALLOUT)
    kept = tuple(o for o in admissible if o not in banned)
    if not kept:  # 剔空了（够不着又只剩如愿一格之类）：退到相邻那格
        kept = (S.SOFTENED,) if S.GRANTED in admissible else (S.REBUFFED,)
    return SocialStakes(
        npc_id=npc_id, aim=aim, approach=approach, attitude=attitude, disposition=disposition, score=score,
        admissible=kept, canonical=_nearest(kept, canonical), subject_id=subject_id,
        leverage_ids=tuple(sorted(set(leverage_ids))), need=need,
    )


def settle_social(stakes: SocialStakes, outcome: object | None = None) -> SocialRuling:
    """提议的结局在区间里即采纳，否则（出界、缺席、别的路线的结局）取确定性裁决。交涉的提议只有结局，没有扣减。"""
    if isinstance(outcome, SocialOutcome) and outcome in stakes.admissible:
        return SocialRuling(outcome=outcome, adopted=True)
    return SocialRuling(outcome=stakes.canonical, adopted=False)


def _up(stakes: SocialStakes, ceiling: Attitude = CAP) -> Attitude:
    """上一档，钳在 ceiling 之下；已在其上则不动（交涉从不把信赖之人往下拉）。"""
    att = stakes.attitude
    return att.step(1) if att.rank < ceiling.rank else att


def effects(stakes: SocialStakes, ruling: SocialRuling, player_id: str) -> list[DomainEvent]:
    """
    定案 → 事件。每次交涉入账一条 Parleyed（带所图的标的 subject_id，心事线索靠它折叠与立名），再按所图与结局附上：
      如愿：打探 → FactLearned（领域预先选定、知情人正是此人的那条见闻）；讨要 → ItemTransferred（他 → 你）；
            结交 / 化解 → 升一档（至多友善）；求艺 → 升一档（恰够上传功门槛，不传功）；威逼得逞 → 他照办却降一档（不低于戒备）：畏而不服；
      松动：升一档（至多友善；求艺至多到门槛下一档）；威逼与诡计只换来口风，不升人情；
      无果、碰壁：人情不变；翻脸：直落敌视。从不产出 HealthChanged 或 PlayerDied。
    """
    o, aim, approach, npc = ruling.outcome, stakes.aim, stakes.approach, stakes.npc_id
    events: list[DomainEvent] = [
        Parleyed(npc_id=npc, aim=aim, approach=approach, outcome=o, leverage_ids=stakes.leverage_ids,
                 subject_id=stakes.subject_id)
    ]
    regard, cause = stakes.attitude, ""
    if o is S.GRANTED:
        if aim is Aim.PROBE and stakes.subject_id:
            events.append(FactLearned(fact_id=stakes.subject_id, source_id=npc))
        if aim is Aim.ASK and stakes.subject_id:
            events.append(ItemTransferred(item_id=stakes.subject_id, from_holder=npc, to_holder=player_id))
        if approach is Approach.FORCE:
            if stakes.attitude.rank > Attitude.WARY.rank:
                regard, cause = stakes.attitude.step(-1), "被你威逼，畏而不服"
        elif aim in (Aim.BEFRIEND, Aim.DEFUSE, Aim.LEARN):
            regard, cause = _up(stakes), _CAUSE_UP.get(approach, "")
    elif o is S.SOFTENED and approach not in HARSH:
        ceiling = CAP
        if aim is Aim.LEARN and stakes.need is not None and stakes.need.rank - 1 < ceiling.rank:
            ceiling = stakes.need.step(-1)
        regard, cause = _up(stakes, ceiling), _CAUSE_UP.get(approach, "")
    elif o is S.FALLOUT:
        regard, cause = Attitude.HOSTILE, _CAUSE_FALLOUT.get(approach, "当场翻脸")
    if regard is not stakes.attitude:
        events.append(RelationChanged(character_id=npc, attitude=regard, cause=cause, basis=o.value))
    return events
