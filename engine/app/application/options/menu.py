"""
[INPUT]: 依赖 options/sources 的 ActionOption，依赖 domain/combat / outcomes 的结局枚举、domain/resolution 的 hard（硬结局即结局字眼），
         依赖 domain/progression 的 Vitality（伤势字眼）；narrator 的 MenuPicks 仅作类型标注（运行期只按结构读 .picks / .key / .flavor，不反向依赖叙事）
[OUTPUT]: 对外提供 compose()（说书人的挑选过闸 → 本回合下发的 3~4 席）、flavor()（一句风味文案的闸门：合格即返回整理后的文案，否则 None）、
          MIN_SEATS / MAX_SEATS / FLAVOR_MIN / FLAVOR_MAX 与 VERDICT_WORDS（结局与状态字眼）
[POS]: options 包的意图风味封装闸门：说书人在叙事的同一次调用里从可供性目录（catalogue，编号 m1…）挑 3~4 招、各配一句武侠风味，
       这里决定哪些算数——大模型只挑招、只写文案，挑什么都落在引擎给定的目录里，执行的永远是那一招的 underlying_command。
       key 须在目录里且不重复（大小写与空白宽容）；flavor 2~24 字、一行中文、无英文与数字（全角亦然）与标记（〈〉<>｜等）、
       无结局与状态字眼（得手 制住 毙命 重伤 如愿 翻脸 败露 ……）、不点场景之外的原著名字（先剔掉场景里叫得出的名字、长名先剔，
       剩下的再撞原著名录即拒；单字名不作数）；合格的换上 flavor_text，不合格的仍保留这一招（说书人可能已在正文里为它铺垫）、文案退回朴素标签，
       与已用文案重复的同样退回。按 id 去重、至多 MAX_SEATS 席；挑中的不足 MIN_SEATS 席时用退路菜单（generate）按次序补（朴素标签）；
       一招都没挑中（离线、降级、解析不了）即原样下发退路菜单。scene_names 应只含玩家此刻叫得出的名字（迷雾里的未知去处不算，
       intent_parser.scene_names 即是此口径），canon_names 取正典蓝图的全部名录
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

from app.application.options.sources import ActionOption
from app.domain.combat import CombatOutcome
from app.domain.outcomes import CovertOutcome, SocialOutcome
from app.domain.progression import Vitality
from app.domain.resolution import hard

if TYPE_CHECKING:
    from app.application.narrator import MenuPicks

MIN_SEATS, MAX_SEATS = 3, 4
FLAVOR_MIN, FLAVOR_MAX = 2, 24

# 结局与状态字眼：硬结局（得手 / 重伤 / 毙命 / 如愿 / 翻脸 / 无痕 / 败露 / 失手）由领域枚举推出，伤势档与改变归属生死的字眼另列——
# 风味只写这一招的姿态与意图，写了结果就是替骰子说话
VERDICT_WORDS: tuple[str, ...] = tuple(sorted(
    {o.value for o in (*CombatOutcome, *SocialOutcome, *CovertOutcome) if hard(o)}
    | {v.value for v in Vitality if v is not Vitality.HALE}
    | {"制住", "擒住", "气绝", "身死", "死了", "杀死", "杀了", "到手", "夺下", "夺得", "夺走", "学会", "成功", "失败"},
    key=lambda w: (-len(w), w),
))
_VERDICT = re.compile("|".join(map(re.escape, VERDICT_WORDS)))
_JUNK = re.compile(r"[A-Za-z0-9Ａ-Ｚａ-ｚ０-９<>〈〉{}\[\]【】`|｜#*_~\\/=+@$%^&\n\r\t]")
_HAN = re.compile(r"[一-鿿]")
_QUOTES = "「」『』“”\"'‘’ 　"


def flavor(text: str, scene_names: Iterable[str], canon_names: Iterable[str]) -> str | None:
    """一句风味文案过闸：去掉首尾空白、引号与句末句号后，合格即返回，否则 None。"""
    line = text.strip().strip(_QUOTES).rstrip("。．.").strip()
    if not FLAVOR_MIN <= len(line) <= FLAVOR_MAX or not _HAN.search(line) or _JUNK.search(line) or _VERDICT.search(line):
        return None
    rest = line
    for name in sorted({n for n in scene_names if n}, key=len, reverse=True):  # 长名先剔：「无量剑法」不能被「无量剑」剔成「法」
        rest = rest.replace(name, "　")
    if any(len(n) >= 2 and n in rest for n in canon_names):
        return None
    return line


def compose(
    catalogue: tuple[ActionOption, ...],
    picks: MenuPicks | None,
    fallback: tuple[ActionOption, ...],
    *,
    scene_names: Iterable[str],
    canon_names: Iterable[str],
) -> tuple[ActionOption, ...]:
    """说书人的挑选过闸，得出本回合下发的 3~4 席（每席的 flavor_text 是过了闸的风味，否则是朴素标签）。"""
    keyed = {f"m{i}": option for i, option in enumerate(catalogue, start=1)}
    here = tuple(scene_names)
    canon = frozenset(n for n in canon_names if len(n) >= 2)  # 单字名不作数：一个「风」字到处都是
    chosen: list[ActionOption] = []
    ids: set[str] = set()
    used: set[str] = set()  # 已用的文案
    for pick in picks.picks if picks is not None else ():
        option = keyed.get(str(pick.key).strip().lower())
        if option is None or option.id in ids or len(chosen) >= MAX_SEATS:
            continue
        line = flavor(str(pick.flavor), here, canon)
        line = line if line is not None and line not in used else option.label
        used.add(line)
        chosen.append(option.model_copy(update={"flavor_text": line}))
        ids.add(option.id)
    if not chosen:
        return tuple(o.model_copy(update={"flavor_text": o.label}) for o in fallback[:MAX_SEATS])
    for option in fallback:
        if len(chosen) >= MIN_SEATS:
            break
        if option.id not in ids:
            chosen.append(option.model_copy(update={"flavor_text": option.label}))
            ids.add(option.id)
    return tuple(chosen)


__all__ = ["FLAVOR_MAX", "FLAVOR_MIN", "MAX_SEATS", "MIN_SEATS", "VERDICT_WORDS", "compose", "flavor"]
