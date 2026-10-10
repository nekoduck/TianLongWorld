"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / Field，依赖 domain/progression 的 Vitality / vitality，依赖 hashlib 的 sha256；
         PlayerState 与 LocalSnapshot 只在类型标注里出现（本模块被 events / agenda 导入，运行期不得反向依赖它们）
[OUTPUT]: 对外提供 命格特质与外显签名——TraitKind（相貌 / 口音 / 装束 / 印记）与 TRAITS 封闭词表、KIND_OF（特质 → 种类）、SPOKEN（开口才露的种类：口音）、
          SCAR「面有刀疤」与 FATED_MARKS、fated(player_id)（命格：由 id 确定性抽出，写进 PlayerSpawned 后即成事实）、trait_errors(traits)（特质组合的闸门）、
          CONSPICUOUS_KINDS / ARMS_KINDS 与 conspicuous(kind)（一眼看得见的物件种类：兵器、坐骑、灵兽……；书信、药物、饰物贴身藏得住）、
          TargetSignature（议程携带的目标签名：特质与物件，至少一项）、Signature（此刻旁人眼里的你：外露的特质与物件、是否持械、是否一身血污）与 digest、
          signature(state, snap, spoke=)（外显签名：口音只在开口时露出，藏着的物件不露）、matches(sig, target)（签名匹配：物件一件对上即中，特质须对上两项（只写一项的对上即中））
[POS]: domain 的「命格特质引力场」本体：玩家不再是幽灵——他长什么样、说什么口音、穿什么、身上带着什么看得见的东西，都是图谱可以匹配的事实。
       NPC 认人只凭这些外显之物（见过、听人描述过、议程里记着要找的那副模样）；藏在怀里的东西（PlayerState.hidden_items）不露，出示（ItemShown）之后才露。
       特质是封闭词表（不收自由文本）：命格由 id 定、写进投胎事件；刀疤是挨刀挨出来的（TraitAcquired），不是天生
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.progression import Vitality, vitality

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState
    from app.domain.snapshot import LocalSnapshot


class TraitKind(StrEnum):
    LOOKS = "相貌"
    ACCENT = "口音"
    ATTIRE = "装束"
    MARK = "印记"


TRAITS: dict[TraitKind, tuple[str, ...]] = {
    TraitKind.LOOKS: ("面如冠玉", "貌不惊人", "身形魁梧", "瘦削精悍"),
    TraitKind.ACCENT: ("大理口音", "中原口音", "江南口音", "蜀中口音", "关外口音"),
    TraitKind.ATTIRE: ("书生青衫", "粗布短打", "江湖劲装", "锦衣华服"),
    TraitKind.MARK: ("眉心朱痣", "左颊胎记", "面有刀疤"),
}
KIND_OF: dict[str, TraitKind] = {trait: kind for kind, values in TRAITS.items() for trait in values}
SPOKEN = frozenset({TraitKind.ACCENT})  # 开口才露出来的：不说话的人，旁人听不出他是哪里人
SCAR = "面有刀疤"  # 重伤于刀兵之下才有的印记（TraitAcquired）
FATED_MARKS = ("眉心朱痣", "左颊胎记")  # 天生的印记；三人里约有一人带着
MARKS_MAX = 2  # 印记至多两处（天生的一处 + 后来的刀疤），其余种类各一

# 一眼看得见的物件：拿在手里、牵在身边、背在背上。书信、药物、饰物、秘籍贴身藏得住
CONSPICUOUS_KINDS = frozenset({"兵器", "坐骑", "灵兽", "雕像", "奇石", "家具", "乐器", "护具"})
ARMS_KINDS = frozenset({"兵器"})  # 持械：暗器藏在袖里，不算


def conspicuous(kind: str) -> bool:
    return kind in CONSPICUOUS_KINDS


def fated(player_id: str) -> tuple[str, ...]:
    """命格：由玩家 id 确定性地抽出相貌、口音、装束，三分之一的人另带一处天生的印记。写进 PlayerSpawned 后，日后改了词表老玩家也不变。"""
    digest = hashlib.sha256(f"命格|{player_id}".encode()).digest()
    picks = [TRAITS[kind][digest[i] % len(TRAITS[kind])] for i, kind in enumerate((TraitKind.LOOKS, TraitKind.ACCENT, TraitKind.ATTIRE))]
    if digest[3] < 86:
        picks.append(FATED_MARKS[digest[4] % len(FATED_MARKS)])
    return tuple(picks)


def trait_errors(traits: tuple[str, ...]) -> list[str]:
    """特质组合的闸门：只收词表里的、不重复、相貌口音装束各至多一项、印记至多两处。"""
    errors = [f"不认识的特质：{t}" for t in traits if t not in KIND_OF]
    if len(set(traits)) != len(traits):
        errors.append("特质有重复")
    for kind in TraitKind:
        count = sum(1 for t in traits if KIND_OF.get(t) is kind)
        if count > (MARKS_MAX if kind is TraitKind.MARK else 1):
            errors.append(f"{kind.value}过多：{count}")
    return errors


class TargetSignature(BaseModel):
    """议程携带的目标签名：NPC 要找的那个人身上的特质与物件——只能取他亲眼见过、听人描述过或丢在那人手里的（npc.admit 守）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    traits: tuple[str, ...] = Field(default=(), max_length=4)
    items: tuple[str, ...] = Field(default=(), max_length=4)

    @model_validator(mode="after")
    def _grounded(self) -> Self:
        if not self.traits and not self.items:
            raise ValueError("目标签名至少要有一项特质或物件")
        if bad := [t for t in self.traits if t not in KIND_OF]:
            raise ValueError(f"目标签名里有不认识的特质：{'、'.join(bad)}")
        if bad := [i for i in self.items if not i.startswith("itm:")]:
            raise ValueError(f"目标签名里的物件须是 itm: id：{'、'.join(bad)}")
        return self


@dataclass(frozen=True, slots=True)
class Signature:
    """此刻旁人眼里的你：外露的特质（口音只在开口时）与外露的物件、是否持械、是否一身血污（重伤及以下）。"""

    traits: frozenset[str]
    items: frozenset[str]
    armed: bool = False
    bloodied: bool = False

    @property
    def digest(self) -> str:
        """看见的样子一变（亮出一件兵器、添了刀疤、开了口），旁人就会重新打量你一回。"""
        raw = "|".join((*sorted(self.traits), "#", *sorted(self.items), str(self.armed), str(self.bloodied)))
        return hashlib.sha1(raw.encode()).hexdigest()[:10]


def signature(state: PlayerState, snap: LocalSnapshot, *, spoke: bool = False) -> Signature:
    """外显签名：命格里看得见的特质（spoke 才算口音）、行囊里没藏起来的物件（种类取快照的 ItemView.kind）、持械、血污。"""
    traits = frozenset(t for t in state.traits if KIND_OF.get(t) not in SPOKEN or spoke)
    shown = state.inventory - state.hidden_items
    kinds = {i.id: i.kind for i in snap.items}
    armed = any(kinds.get(item, "") in ARMS_KINDS for item in shown)
    bloodied = vitality(state.hp).rank >= Vitality.WOUNDED.rank
    return Signature(traits=traits, items=frozenset(shown), armed=armed, bloodied=bloodied)


def matches(sig: Signature, target: TargetSignature) -> bool:
    """签名匹配：物件一件对上即中；特质须对上两项（签名只写了一项特质的，对上那一项即中）。"""
    if set(target.items) & sig.items:
        return True
    need = min(2, len(target.traits))
    return need > 0 and len(set(target.traits) & sig.traits) >= need
