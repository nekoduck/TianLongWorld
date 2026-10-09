"""
[INPUT]: 依赖 application/bus 的 Bond / Pursuit / ClockInfo，依赖 domain/aggregates 的 PlayerState，依赖 domain/threads 的 Thread，
         依赖 domain/intent 的 Aim，依赖 domain/models 的 Attitude，依赖 domain/outcomes 的 SocialOutcome / CovertOutcome，
         依赖 domain/snapshot 的 LocalSnapshot
[OUTPUT]: 对外提供 bonds(state, snap, names)（人情一栏）、pursuits(state, names)（心事一栏）、referenced(state)（两栏需要取名的 id）、
          clocks(snap)（眼前的暗流，至多 CLOCKS_SHOWN 只）、renown(state)（名望的语义标签）、BONDS_MAX / PURSUITS_MAX / CLOCKS_SHOWN
[POS]: application 的状态栏附栏：把聚合根里的人情与心事线索翻成玩家看得懂的几行字。纯函数，只读状态与名字表，不查图、不经大模型；
       名字由 handlers 据 referenced() 向 WorldReader.labels 取来——一回合至多一次 labels、两次调用，世界不变则逐字不变。
       情报隔离：打探线索的标的是见闻（fact:），见闻正文是玩家还不知道的事——这里从不为它取名，标签只写对象「打探 · 左子穆」。
       时钟只取快照召回的（挂在眼前之物上的），凶险的在前、离坍缩近的在前；名称自带语义，不取名、不露 clk: id。名望只露语义标签，点数不下发
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from collections.abc import Mapping

from app.application.bus import Bond, ClockInfo, Pursuit
from app.domain.aggregates import PlayerState
from app.domain.intent import Aim
from app.domain.models import Attitude
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.snapshot import LocalSnapshot
from app.domain.threads import Thread

BONDS_MAX = 6
PURSUITS_MAX = 3
CLOCKS_SHOWN = 4
_FACT = "fact:"
_PROGRESS = {  # 线索的进展：最近一次的结局或驳回码 → 一句人话（如愿、得手即了结，不会留在线索里）
    SocialOutcome.SOFTENED.value: "口风已松",
    SocialOutcome.NOTHING.value: "尚无眉目",
    SocialOutcome.REBUFFED.value: "碰了钉子",
    SocialOutcome.FALLOUT.value: "对方已翻脸",
    CovertOutcome.FOILED.value: "未能得手",
    CovertOutcome.CAUGHT.value: "失手被撞破",
    "UNWILLING": "对方不肯",
}


def _name(any_id: str, names: Mapping[str, str]) -> str:
    return names.get(any_id) or any_id.split(":", 1)[-1]


def _shown(thread: Thread) -> str:
    """标签上写谁：有标的写标的（武学、物件），打探与见闻一律写对象——见闻正文绝不上状态栏。"""
    subject = thread.subject
    if subject and thread.aim is not Aim.PROBE and not subject.startswith(_FACT):
        return subject
    return thread.target


def referenced(state: PlayerState) -> set[str]:
    """两栏需要取名的 id：态度不是漠然的人、前 PURSUITS_MAX 条线索的对象与标的（见闻除外，免得正文被取进名字表）。"""
    ids = {who for who, regard in state.attitudes.items() if regard is not Attitude.NEUTRAL}
    for thread in state.threads[:PURSUITS_MAX]:
        ids |= {thread.target, _shown(thread)}
    return ids


def bonds(state: PlayerState, snap: LocalSnapshot, names: Mapping[str, str]) -> tuple[Bond, ...]:
    """态度不是漠然的人：在场者优先，再按人情的轻重（|rank|）降序，再按 id；至多 BONDS_MAX 条，缘由取 attitude_causes。"""
    present = {c.id: c.name for c in snap.characters}
    regarded = [(who, regard) for who, regard in state.attitudes.items() if regard is not Attitude.NEUTRAL]
    regarded.sort(key=lambda pair: (pair[0] not in present, -abs(pair[1].rank), pair[0]))
    return tuple(
        Bond(name=present.get(who) or _name(who, names), attitude=regard.value, cause=state.attitude_causes.get(who, ""))
        for who, regard in regarded[:BONDS_MAX]
    )


def _note(thread: Thread) -> str:
    """进展；已试的手段；求艺还缺的人情档。三段以「；」相连，缺哪段就省哪段。"""
    parts = [
        _PROGRESS.get(thread.progress, ""),
        f"已试：{'、'.join(a.value for a in thread.tried)}" if thread.tried else "",
        f"须{thread.need.value}" if thread.need else "",
    ]
    return "；".join(filter(None, parts))


def pursuits(state: PlayerState, names: Mapping[str, str]) -> tuple[Pursuit, ...]:
    """未了的所图，新者在前、至多 PURSUITS_MAX 条：「求艺 · 白虹贯日」+「对方不肯；已试：寻常；须友善」。"""
    return tuple(
        Pursuit(label=f"{t.aim.value} · {_name(_shown(t), names)}", note=_note(t)) for t in state.threads[:PURSUITS_MAX]
    )


def clocks(snap: LocalSnapshot) -> tuple[ClockInfo, ...]:
    """眼前的暗流：凶险的（疑心 / 敌意 / 危机）在前，再按离坍缩还差几格升序，再按 id；至多 CLOCKS_SHOWN 只。世界不变则逐字不变。"""
    shown = sorted(snap.clocks, key=lambda c: (not c.kind.threat, c.remaining, c.id))[:CLOCKS_SHOWN]
    return tuple(ClockInfo(name=c.name, kind=c.kind.value, progress=c.progress, maximum=c.maximum) for c in shown)


def renown(state: PlayerState) -> str:
    """名望的语义标签：江湖上怎么说你。"""
    return state.renown.value
