"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / Field，依赖 hashlib 的 sha1
[OUTPUT]: 对外提供 因果线（契诃夫的枪）的名词——KarmaKind（结仇 / 放生 / 拾遗 / 欺瞒）、KarmaStatus（未决 / 回响）、
          KarmaThread（kar:<10hex>、种类、事件源 source、牵涉实体 subject_ids、事发之地 origin_id、立线之刻、未决状态 unresolved、
          玩家当时的外显特质 signature、那件事的消息 token_id、状态、领了它作议程的 NPC echoes）、karma_id()、KARMA_MAX、KARMA_CHARS
[POS]: domain 的编剧本体（只有名词；种子、闸门、交集与了结在 domain/screenplay，以免与 events 成环）：
       一条因果线是玩家已经做下、尚未了结的一件事——结了仇、放了人、拾了来历不明的东西、骗过了谁。它不改变任何物理事实，
       只是记下「这里有一把枪」：此后某位 NPC 的行止与它在物理上相交（亲历、听闻、路过事发之地），编剧把它注入那人的议程动机，枪才会响。
       因果线属于玩家的平行世界：由事件折叠进 PlayerState，投影进图谱覆盖层 (:Karma)-[:IMPLICATES]->实体
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import hashlib
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

KARMA_MAX = 6  # 悬着的因果线至多六条：满了请走最旧的
KARMA_CHARS = 24


class KarmaKind(StrEnum):
    FEUD = "结仇"  # 伤了谁、让谁记恨
    MERCY = "放生"  # 制住了谁却放他走
    RELIC = "拾遗"  # 拾取、夺来来历不明的东西（它有原主）
    DECEIT = "欺瞒"  # 骗过、偷过，对方还蒙在鼓里


class KarmaStatus(StrEnum):
    OPEN = "未决"  # 枪挂在墙上
    ECHOED = "回响"  # 有人因它立了议程，正在路上


class KarmaThread(BaseModel):
    """
    一条悬着的因果线。source 是事件源的白描（「在练武厅打伤龚光杰」），unresolved 是未决之处（「龚光杰怀恨未消」），
    subject_ids 是牵涉的人、物、地（落了地的 id，至多四个），signature 是玩家当时外露的特质——日后有人凭它认人。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(pattern=r"^kar:[0-9a-f]{10}$")
    kind: KarmaKind
    source: str = Field(min_length=2, max_length=KARMA_CHARS)
    subject_ids: tuple[str, ...] = Field(min_length=1, max_length=4)
    origin_id: str = Field(pattern=r"^loc:.+")
    opened_tick: int = Field(ge=0)
    unresolved: str = Field(min_length=2, max_length=KARMA_CHARS)
    signature: tuple[str, ...] = ()
    token_id: str | None = None
    status: KarmaStatus = KarmaStatus.OPEN
    echoes: tuple[str, ...] = ()


def karma_id(kind: KarmaKind, subject_ids: tuple[str, ...], tick: int) -> str:
    """同一刻、同一件事、同一班人只立一条线。"""
    raw = f"{kind.value}|{'|'.join(sorted(subject_ids))}|{tick}"
    return "kar:" + hashlib.sha1(raw.encode()).hexdigest()[:10]
