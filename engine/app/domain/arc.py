"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / Field，依赖 domain/commands 的 SPAWN_TICK，依赖 re
[OUTPUT]: 对外提供 命运弧光与章回主题的名词——ArcPhase（初入江湖 / 杀伐 / 孤绝 / 侠义 / 诡道 / 求道 / 漂泊）、
          Chapter（第几章、弧光阶段、主题 theme 2~8 字、基调 tone 2~6 字、意象 motifs 1~3 个各 ≤4 字、潜台词 subtext 2~30 字、开章之刻）、
          CATALOGUE（每个阶段一份缺省的主题、基调、意象与潜台词——编剧失灵时的确定性退路）、PROLOGUE（第一章：初入江湖）、
          ARC_WINDOW / PHASE_MIN / CHAPTER_MIN_TICKS、phase_of(marks)（近来的弧光标记 → 当下阶段：出现至少 PHASE_MIN 次的阶段里最多者，平手取最近者，否则初入江湖）、
          ChapterProposal（编剧大模型的宽容提议）与 admit_chapter(proposal, phase, no, tick)（章回闸门：逐字段检验，不合格的字段退回 CATALOGUE）
[POS]: domain 的编剧本体之二：玩家近来做了什么（连续杀戮、求助无门、物归原主……）折成一串弧光标记（domain/screenplay.mark，evolve 折叠），
       阶段一变即开新章（ChapterOpened）。章回主题是抽象的潜台词滤镜——它不改变任何事实、不点任何人名地名，
       只要求说书人在描写客观的动作与环境时取贴合主题的意象、基调与笔触，让武侠的宿命感前后一贯
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import re
from collections import Counter
from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.commands import SPAWN_TICK

ARC_WINDOW = 12  # 弧光只看最近十二件要紧的事
PHASE_MIN = 3  # 一个阶段至少出现三次才算成形
CHAPTER_MIN_TICKS = 16  # 两章之间至少隔四个时辰：不为一时的起伏换章


class ArcPhase(StrEnum):
    DAWN = "初入江湖"
    CARNAGE = "杀伐"  # 连番动手、伤人
    FORSAKEN = "孤绝"  # 求助无门：碰壁、翻脸、被拒
    CHIVALRY = "侠义"  # 物归原主、化解、赠予、结交
    GUILE = "诡道"  # 暗取、计谋
    SEEKER = "求道"  # 修习
    DRIFT = "漂泊"  # 一路行走、无所依傍


class Chapter(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    no: int = Field(ge=1)
    phase: ArcPhase
    theme: str = Field(min_length=2, max_length=8)
    tone: str = Field(min_length=2, max_length=6)
    motifs: tuple[str, ...] = Field(min_length=1, max_length=3)
    subtext: str = Field(min_length=2, max_length=30)
    opened_tick: int = Field(ge=0)

    @field_validator("motifs")
    @classmethod
    def _short(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not 1 <= len(m) <= 4 for m in value):
            raise ValueError("意象每个一到四字")
        return value


# 每个阶段一份缺省的主题（编剧失灵时的退路；编剧提议的字段不合格时也逐字段退回这里）
CATALOGUE: dict[ArcPhase, tuple[str, str, tuple[str, ...], str]] = {
    ArcPhase.DAWN: ("初出茅庐", "清朗", ("晨雾", "新竹", "远山"), "前路未明而心怀热望"),
    ArcPhase.CARNAGE: ("暴戾反噬", "肃杀", ("血", "寒鸦", "锈刃"), "杀意终将回噬持刀之人"),
    ArcPhase.FORSAKEN: ("命运虚无", "苍凉", ("孤灯", "冷雨", "空巷"), "天地之大无处容身"),
    ArcPhase.CHIVALRY: ("侠骨柔肠", "温厚", ("暖阳", "清泉", "归燕"), "一诺之重胜过千金"),
    ArcPhase.GUILE: ("暗夜行舟", "阴郁", ("烛影", "薄雾", "暗流"), "瞒过众人终瞒不过自己"),
    ArcPhase.SEEKER: ("问道", "空寂", ("古松", "山泉", "孤峰"), "武道尽头仍是人心"),
    ArcPhase.DRIFT: ("浮萍", "怅惘", ("长路", "斜阳", "飞蓬"), "身似浮萍不知归处"),
}


def _chapter(no: int, phase: ArcPhase, tick: int) -> Chapter:
    theme, tone, motifs, subtext = CATALOGUE[phase]
    return Chapter(no=no, phase=phase, theme=theme, tone=tone, motifs=motifs, subtext=subtext, opened_tick=tick)


PROLOGUE = _chapter(1, ArcPhase.DAWN, SPAWN_TICK)


def phase_of(marks: Sequence[ArcPhase]) -> ArcPhase:
    """近来的弧光标记（新者在前）→ 当下阶段：出现至少 PHASE_MIN 次的阶段里最多者，平手取最近出现者；都不够即初入江湖。"""
    counts = Counter(marks)
    formed = [phase for phase, n in counts.items() if n >= PHASE_MIN and phase is not ArcPhase.DAWN]
    if not formed:
        return ArcPhase.DAWN
    top = max(counts[p] for p in formed)
    return next(m for m in marks if m in formed and counts[m] == top)


_FLAW = re.compile(r"[A-Za-z0-9０-９<>〈〉`{}\[\]\n\r\t「」『』\"'“”]")


class ChapterProposal(BaseModel):
    """编剧大模型的章回提议（宽容：去空白、多余字段忽略、意象可写成一个字符串）。"""

    model_config = ConfigDict(extra="ignore")

    theme: str = ""
    tone: str = ""
    motifs: list[str] = []
    subtext: str = ""

    @field_validator("theme", "tone", "subtext", mode="before")
    @classmethod
    def _text(cls, value: Any) -> str:
        return "" if value is None else str(value).strip().strip("。，、；：！？")

    @field_validator("motifs", mode="before")
    @classmethod
    def _motifs(cls, value: Any) -> list[str]:
        if value is None:
            return []
        items = re.split(r"[、，,\s]+", value) if isinstance(value, str) else list(value)
        return [str(m).strip() for m in items if str(m).strip()]


def _clean(text: str, low: int, high: int) -> bool:
    return low <= len(text) <= high and not _FLAW.search(text)


def admit_chapter(proposal: ChapterProposal | None, phase: ArcPhase, no: int, tick: int, *, names: Sequence[str] = ()) -> Chapter:
    """
    章回闸门：逐字段检验编剧的提议——长度、一行中文、不含英文数字与标记、不点任何专名（names：原著名录与此景之名）；
    不合格的字段各自退回 CATALOGUE[phase]。编剧失灵（proposal 为 None）即整份取缺省。
    """
    theme, tone, motifs, subtext = CATALOGUE[phase]

    def ok(text: str, low: int, high: int) -> bool:
        return _clean(text, low, high) and not any(len(n) >= 2 and n in text for n in names)

    if proposal is not None:
        theme = proposal.theme if ok(proposal.theme, 2, 8) else theme
        tone = proposal.tone if ok(proposal.tone, 2, 6) else tone
        picked = tuple(dict.fromkeys(m for m in proposal.motifs if ok(m, 1, 4)))[:3]
        motifs = picked or motifs
        subtext = proposal.subtext if ok(proposal.subtext, 2, 30) else subtext
    return Chapter(no=no, phase=phase, theme=theme, tone=tone, motifs=motifs, subtext=subtext, opened_tick=tick)
