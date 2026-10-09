"""
[INPUT]: 依赖 domain/rules 的 best_skill，依赖 domain/rules/parley 的 fact_to_learn / leverage（打探与借势只在领域选得出见闻与筹码时才出），
         依赖 domain/intent 的 ActionType / Aim / Approach / PlayerIntent，依赖 domain/approach 的 Route，依赖 domain/models 的 Attitude，
         依赖 options/phrasing 的 Slots，
         依赖 domain/stakes 的 Risk，依赖 domain/threads 的 Thread，依赖 domain/snapshot 的 LocalSnapshot / CharacterView；PlayerState 仅作类型标注
[OUTPUT]: 对外提供 OptionCategory（战斗 / 交涉 / 探索 / 修习 / 取物 / 休养）、ActionOption（id + 标签 + 方向 + why + risk 风险档 + 服务端持有的意图）、
          Candidate（一个候选：方向、意图、措辞键与槽位、所属心事线索、须走的路线）、candidates()（五类候选源依次产出：Thread / Person / Ground / Self / Exit）
[POS]: options 包的候选源：沿快照的合法边枚举「招」，不裁决、不打分——合法与否交给 rules.adjudicate，轻重交给 salience。
       Thread：PlayerState.threads 里对象仍在场的每条未了之事，按所图列出兼容表允许而尚未试过的手段（门面只留第一条获准的）；
       Person：每位在场者——攀谈；言辞结交（敌视 / 戒备者化解，人情已达成者不出）；打探（仅当领域选得出此人知情、玩家未知的见闻，标签从不带见闻正文）；
               武力出手；求艺（寻常，由 LearnRule 找肯教之人）与「恳请某人传授某功」（言辞，只在师父不肯、改走交涉时成立；不向仇人恳求）；
               他身上之物：已被制住即伸手取走，否则言辞讨要 / 潜行偷取 / 武力夺物各一条；物归原主；借势（仅当领域选得出筹码）；
       Ground：地上可携之物（物性闸门由规则把关：不可携带者不出）、行囊里的疗伤之药（无伤由规则滤掉；解毒之药 P1 无毒可解，不诱人白白吃掉）；
       Self：静观、调息、无人可教的修习（参悟典籍、参照典籍、闭门苦练——同一个 LEARN，凭借由规则定）；Exit：出路。
       同一意图只算一个候选（门面按 id 去重，先到者胜：线索的「换个手段」压过同一招的寻常版本）
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from app.application.options.phrasing import Slots
from app.domain import rules
from app.domain.approach import Route
from app.domain.intent import ActionType, Aim, Approach, PlayerIntent
from app.domain.models import Attitude
from app.domain.rules.parley import fact_to_learn, leverage
from app.domain.snapshot import CharacterView, LocalSnapshot
from app.domain.stakes import Risk
from app.domain.threads import Thread

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState


class OptionCategory(StrEnum):
    COMBAT = "战斗"
    SOCIAL = "交涉"
    EXPLORE = "探索"
    CULTIVATE = "修习"
    ACQUIRE = "取物"
    RECOVER = "休养"


class ActionOption(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    label: str
    category: OptionCategory
    why: str = ""  # 为什么上榜：≤12 字的确定性短语，随选项下发
    risk: Risk | None = None  # 语义风险档：只看可裁区间最坏的一端，不露结局；确定之事稳妥
    intent: PlayerIntent  # 只在服务端：前端只拿到 id、标签、方向、why 与风险档

    @staticmethod
    def digest(intent: PlayerIntent) -> str:
        """意图哈希：id 与措辞变体都由它确定——同一个意图永远同一个 id、同一句话。"""
        return hashlib.sha1(intent.model_dump_json().encode()).hexdigest()[:8]

    @classmethod
    def of(cls, category: OptionCategory, label: str, intent: PlayerIntent, why: str = "") -> ActionOption:
        return cls(id=f"{category.name.lower()}-{cls.digest(intent)}", label=label, category=category, why=why, intent=intent)


@dataclass(frozen=True, slots=True)
class Candidate:
    """
    一个候选「招」。key 为 "learn" 时措辞随裁决给出的凭借而定（phrasing.learn_phrase），其余键在此就定了。
    thread 是它所接续的心事线索（Thread 源）；route 非空时只认走这一路的获准——「恳请某人传授某功」只在师父不肯、改走交涉时才是一招。
    """

    category: OptionCategory
    intent: PlayerIntent
    key: str
    slots: Slots = ()
    thread: Thread | None = None
    route: Route | None = None


_C, _A, _P, _T = OptionCategory, Aim, Approach, ActionType


def candidates(state: PlayerState, snap: LocalSnapshot) -> Iterator[Candidate]:
    yield from threads(state, snap)
    yield from persons(state, snap)
    yield from grounds(state, snap)
    yield from selves(state, snap)
    yield from exits(snap)


# ============================================================
#  Exit / Self / Ground
# ============================================================
def exits(snap: LocalSnapshot) -> Iterator[Candidate]:
    for way in snap.exits:
        yield Candidate(_C.EXPLORE, PlayerIntent(action_type=_T.MOVE, target_entity=way.label), "move",
                        (("exit", way.label), ("place", way.to_name)))


def _taught_here(snap: LocalSnapshot) -> set[str]:
    """在场者身负的武学：求艺归 Person 源，其余（典籍、自己已会的）归 Self 源——同一个 LEARN 意图只出一次。"""
    return {s for c in snap.characters for s in c.skill_ids}


def selves(state: PlayerState, snap: LocalSnapshot) -> Iterator[Candidate]:
    yield Candidate(_C.EXPLORE, PlayerIntent(action_type=_T.OBSERVE), "observe")
    yield Candidate(_C.RECOVER, PlayerIntent(action_type=_T.REST), "rest")  # 无伤、或敌视者在侧，规则自会滤掉
    taught = _taught_here(snap)
    for art in snap.skills:  # 参悟典籍 / 参照典籍 / 闭门苦练：不点名师父，凭借由 LearnRule 定；已臻化境、根基不足者自会被滤掉
        if art.id not in taught:
            yield Candidate(_C.CULTIVATE, PlayerIntent(action_type=_T.LEARN, skill_used=art.name), "learn",
                            (("art", art.name),))


def grounds(state: PlayerState, snap: LocalSnapshot) -> Iterator[Candidate]:
    for thing in snap.ground_items:
        yield Candidate(_C.ACQUIRE, PlayerIntent(action_type=_T.TAKE, target_entity=thing.name), "take.ground",
                        (("item", thing.name),))
    for thing in snap.inventory:  # 疗伤之药：无伤可疗由 UseRule 驳回；解毒之药 P1 无毒可解，不上菜单（文本照样可以服）
        if thing.use is not None and thing.use.effect == "疗伤":
            yield Candidate(_C.RECOVER, PlayerIntent(action_type=_T.USE, item_used=thing.name), "use.heal",
                            (("item", thing.name),))


# ============================================================
#  Person —— 在场之人身上的每一种「招」
# ============================================================
def _goal(who: CharacterView) -> Aim | None:
    """结交还是化解：敌视 / 戒备者化解，漠然者结交，友善以上已无可再图（交涉至多推到友善）。"""
    if who.attitude.rank < Attitude.NEUTRAL.rank:
        return _A.DEFUSE
    if who.attitude.rank < Attitude.FRIENDLY.rank:
        return _A.BEFRIEND
    return None


_GOAL_KEY = {_A.DEFUSE: "defuse", _A.BEFRIEND: "befriend"}


def persons(state: PlayerState, snap: LocalSnapshot) -> Iterator[Candidate]:
    skill = rules.best_skill(state, snap)
    taught = _taught_here(snap)
    for who in snap.characters:
        npc = (("npc", who.name),)
        talk = PlayerIntent(action_type=_T.TALK, target_entity=who.name)
        yield Candidate(_C.SOCIAL, talk, "talk", npc)
        if (goal := _goal(who)) is not None:
            yield Candidate(_C.SOCIAL, talk.model_copy(update={"approach": _P.WORDS, "aim": goal}), _GOAL_KEY[goal], npc)
            if leverage(who, _P.LEVERAGE, state, snap):  # 借势：领域选得出靠山或把柄才是一招，否则只是虚张声势
                yield Candidate(_C.SOCIAL, talk.model_copy(update={"approach": _P.LEVERAGE, "aim": goal}),
                                f"{_GOAL_KEY[goal]}.lever", npc)
        if fact_to_learn(who, None, state, snap) is not None:  # 打探：此人知情、你尚未知，标签只说向谁打听
            yield Candidate(_C.SOCIAL, talk.model_copy(update={"approach": _P.WORDS, "aim": _A.PROBE}), "probe", npc)
        attack = PlayerIntent(action_type=_T.ATTACK, target_entity=who.name, approach=_P.FORCE,
                              skill_used=skill.name if skill else None)
        yield (Candidate(_C.COMBAT, attack, "attack", (("art", skill.name), *npc)) if skill
               else Candidate(_C.COMBAT, attack, "attack.bare", npc))
        for thing in snap.inventory:
            if thing.owner_id == who.id:
                yield Candidate(_C.SOCIAL, PlayerIntent(action_type=_T.GIVE, target_entity=who.name, item_used=thing.name),
                                "give", (("item", thing.name), *npc))
        for thing in snap.items_of(who.id):
            yield from _held(who, thing.name)
        for art in snap.skills if who.attitude is not Attitude.HOSTILE else ():  # 向仇人恳求传功不是一招（线索另论）
            if art.id in who.skill_ids:
                plead = PlayerIntent(action_type=_T.LEARN, target_entity=who.name, skill_used=art.name, approach=_P.WORDS)
                yield Candidate(_C.CULTIVATE, plead, "plead", (*npc, ("art", art.name)), route=Route.SOCIAL)
    for art in snap.skills:  # 求艺（寻常）：不点名师父，由 LearnRule 找肯教之人——同一门功夫只出一条
        if art.id in taught:
            yield Candidate(_C.CULTIVATE, PlayerIntent(action_type=_T.LEARN, skill_used=art.name), "learn",
                            (("art", art.name),))


_HELD: tuple[tuple[Approach, OptionCategory, str], ...] = (
    (_P.WORDS, _C.SOCIAL, "take.ask"),
    (_P.STEALTH, _C.ACQUIRE, "take.steal"),
    (_P.FORCE, _C.COMBAT, "take.seize"),
)


def _held(who: CharacterView, item: str) -> Iterator[Candidate]:
    """他身上之物：被制住即伸手取走（寻常）；否则言辞讨要、潜行偷取、武力夺物各一条——手段不同，标签不同，裁决也走不同的路。"""
    slots = (("npc", who.name), ("item", item))
    take = PlayerIntent(action_type=_T.TAKE, target_entity=item)
    if who.subdued:
        yield Candidate(_C.ACQUIRE, take, "take.subdued", slots)
        return
    for approach, category, key in _HELD:
        yield Candidate(category, take.model_copy(update={"approach": approach}), key, slots)


# ============================================================
#  Thread —— 未了的所图：对象仍在眼前，换个还没试过的手段
# ============================================================
_THREAD_TAKE: tuple[tuple[Approach, OptionCategory, str], ...] = (
    (_P.WORDS, _C.SOCIAL, "take.ask"),
    (_P.STEALTH, _C.ACQUIRE, "take.steal"),
    (_P.GUILE, _C.ACQUIRE, "take.swindle"),
    (_P.FAVOR, _C.SOCIAL, "take.favor"),
    (_P.LEVERAGE, _C.SOCIAL, "take.lever"),
    (_P.FORCE, _C.COMBAT, "take.seize"),
)
_THREAD_PROBE: tuple[tuple[Approach, str], ...] = (
    (_P.WORDS, "probe"), (_P.GUILE, "probe.guile"), (_P.FAVOR, "probe.favor"), (_P.LEVERAGE, "probe.lever"),
    (_P.FORCE, "probe.force"),
)
_THREAD_GOAL: tuple[tuple[Approach, str], ...] = ((_P.WORDS, ""), (_P.FAVOR, ".favor"), (_P.LEVERAGE, ".lever"))


def threads(state: PlayerState, snap: LocalSnapshot) -> Iterator[Candidate]:
    """
    每条线索按偏好次序列出兼容表允许、尚未试过的手段（门面只留第一条获准的）。借势只在领域选得出筹码时才列——没有势可借的借势是虚张声势。
    求艺：言辞 / 人情（LEARN 行仅此两格有退路）；夺物 / 讨要：他人之物那一行除寻常外的六格（东西须仍在他身上）；
    打探：交谈那一行的五种手段；结交 / 化解：言辞 / 人情 / 借势（威逼图不来交情）。
    """
    for t in state.threads:
        who = snap.character(t.target)
        if who is None:
            continue
        npc = (("npc", who.name),)
        lever = bool(leverage(who, _P.LEVERAGE, state, snap))

        def fresh(approach: Approach, t: Thread = t, lever: bool = lever) -> bool:
            return approach not in t.tried and (approach is not _P.LEVERAGE or lever)

        match t.aim:
            case _A.LEARN if t.subject and (art := snap.skill(t.subject)) is not None:
                for approach, key in ((_P.WORDS, "plead"), (_P.FAVOR, "plead.favor")):
                    if fresh(approach):
                        intent = PlayerIntent(action_type=_T.LEARN, target_entity=who.name, skill_used=art.name,
                                              approach=approach)
                        yield Candidate(_C.CULTIVATE, intent, key, (*npc, ("art", art.name)), thread=t)
            case _A.SEIZE | _A.ASK if t.subject and (thing := snap.item(t.subject)) is not None and thing.holder_id == who.id:
                take = PlayerIntent(action_type=_T.TAKE, target_entity=thing.name)
                for approach, category, key in _THREAD_TAKE:
                    if fresh(approach):
                        yield Candidate(category, take.model_copy(update={"approach": approach}), key,
                                        (*npc, ("item", thing.name)), thread=t)
            case _A.PROBE:
                talk = PlayerIntent(action_type=_T.TALK, target_entity=who.name, aim=_A.PROBE)
                for approach, key in _THREAD_PROBE:
                    if fresh(approach):
                        yield Candidate(_C.SOCIAL, talk.model_copy(update={"approach": approach}), key, npc, thread=t)
            case _A.BEFRIEND | _A.DEFUSE:
                talk = PlayerIntent(action_type=_T.TALK, target_entity=who.name, aim=t.aim)
                for approach, suffix in _THREAD_GOAL:
                    if fresh(approach):
                        yield Candidate(_C.SOCIAL, talk.model_copy(update={"approach": approach}),
                                        _GOAL_KEY[t.aim] + suffix, npc, thread=t)
