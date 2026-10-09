"""
[INPUT]: 依赖 domain/rules 的 Approval（修习的措辞随凭借而变），依赖 domain/progression 的 Guidance，依赖 domain/snapshot 的 LocalSnapshot
[OUTPUT]: 对外提供 TEMPLATES（措辞键 → 2~3 个模板变体）、Slots（槽位：名 → 专名）、render()（按意图摘要确定性挑一个变体并填槽）、
          learn_phrase()（修习的措辞键与槽位：求教 / 参悟 / 随师精研 / 参照典籍 / 闭门苦练，只读 Approval.guidance / source）、bare()（去掉槽位的模板骨架）
[POS]: options 包的措辞表：选项标签的唯一来源。每个键 2~3 个变体，按意图哈希挑选——同一个意图（同一个世界）永远是同一句，
       不同的人与物自然换着说法，菜单不再是一副腔调。模板只写动作与手段的说法，绝不含实体名（人、物、功、地都经槽位填入）；
       去掉槽位后 ≤10 字，槽位缺一即抛错（宁可测试里炸，也不下发半截标签）。打探的标签只有对象，从不带见闻正文——见闻是要打听的东西，不是菜单上的字
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

import re

from app.domain import rules
from app.domain.progression import Guidance
from app.domain.snapshot import LocalSnapshot

type Slots = tuple[tuple[str, str], ...]

# 槽位：exit 出路名、place 去处、npc 对象、item 物、art 武学、src 凭借（师父或典籍）
TEMPLATES: dict[str, tuple[str, ...]] = {
    # —— 出路与自身 ——
    "move": ("沿「{exit}」前往{place}", "取道「{exit}」去{place}", "经「{exit}」往{place}"),
    "observe": ("静观四周", "按兵不动，静观其变", "留神打量四下"),
    "rest": ("盘膝运功疗伤", "觅处静坐调息", "调息疗伤"),  # 静观与调息的意图恒定：实际只露出一句（静观四周 / 调息疗伤），与 P0 同字
    "use.heal": ("以{item}疗伤", "取出{item}疗伤"),
    # —— 修习：键随凭借而变（learn_phrase）——
    "learn.ask": ("向{src}求教{art}", "请{src}指点{art}", "拜请{src}传授{art}"),
    "learn.self": ("参悟{src}，修习{art}", "对照{src}参悟{art}", "研读{src}，揣摩{art}"),
    "learn.teacher": ("随{src}精研{art}", "请{src}点拨{art}", "跟{src}再练{art}"),
    "learn.manual": ("参照{src}苦练{art}", "对着{src}勤练{art}"),
    "learn.alone": ("闭门苦练{art}", "独自勤练{art}", "反复揣摩{art}"),
    "plead": ("恳请{npc}传授{art}", "好言求{npc}传{art}"),
    "plead.favor": ("以人情求{npc}传{art}", "托人情请{npc}授{art}"),
    # —— 与人交谈 ——
    "talk": ("与{npc}攀谈", "上前和{npc}搭话", "同{npc}闲叙几句"),
    "befriend": ("好言结交{npc}", "向{npc}示好结纳", "与{npc}套套交情"),
    "befriend.favor": ("以人情笼络{npc}", "卖{npc}一个人情"),
    "befriend.lever": ("借势结交{npc}", "搬出靠山结纳{npc}"),
    "defuse": ("好言劝解{npc}", "向{npc}赔话释怨", "设法与{npc}化解嫌隙"),
    "defuse.favor": ("以人情化解{npc}的敌意", "托人情向{npc}说和"),
    "defuse.lever": ("借势与{npc}说和", "搬出靠山劝{npc}罢手"),
    "probe": ("向{npc}打听", "向{npc}探问消息", "找{npc}打听打听"),
    "probe.guile": ("套{npc}的话", "旁敲侧击套{npc}的话"),
    "probe.favor": ("凭交情向{npc}打听", "托人情问{npc}内情"),
    "probe.force": ("逼问{npc}", "喝令{npc}吐实"),
    "probe.lever": ("借势逼{npc}开口", "搬出靠山向{npc}问话"),
    # —— 出手与赠物 ——
    "attack": ("以{art}向{npc}出手", "使{art}攻向{npc}", "以{art}与{npc}动手"),
    "attack.bare": ("向{npc}出手", "徒手攻向{npc}", "与{npc}动起手来"),
    "give": ("将{item}交还{npc}", "把{item}还给{npc}", "奉还{npc}的{item}"),
    # —— 取物：地上、被制住者身上、他人手中（按手段各一种说法）——
    "take.ground": ("捡起{item}", "将{item}收入囊中", "拾起{item}"),
    "take.subdued": ("从{npc}身上取走{item}", "搜走{npc}身上的{item}"),
    "take.ask": ("向{npc}讨要{item}", "开口向{npc}求取{item}"),
    "take.favor": ("凭交情向{npc}讨{item}", "托人情求{npc}的{item}"),
    "take.lever": ("借势向{npc}讨{item}", "搬出靠山向{npc}索{item}"),
    "take.steal": ("趁{npc}不备偷取{item}", "悄悄摸走{npc}的{item}"),
    "take.swindle": ("设计骗取{npc}的{item}", "使计诓来{npc}的{item}"),
    "take.seize": ("出手夺{npc}的{item}", "强夺{npc}手中的{item}"),
}

_SLOT = re.compile(r"\{(\w+)\}")


def bare(template: str) -> str:
    """模板骨架：去掉全部槽位。验收以它数「去专名后的模板」，也以它量 ≤10 字。"""
    return _SLOT.sub("", template)


def render(key: str, slots: Slots, digest: str) -> str:
    """按意图摘要（十六进制）确定性挑一个变体并填槽；缺槽位即抛 KeyError——半截标签绝不下发。"""
    variants = TEMPLATES[key]
    template = variants[int(digest, 16) % len(variants)]
    return template.format_map(dict(slots))


def learn_phrase(art: str, ok: rules.Approval, snap: LocalSnapshot) -> tuple[str, Slots]:
    """
    修习的措辞随凭借而变：凭借由 LearnRule 裁定（guidance / source），这里只负责把它说成人话。
    言辞 / 人情相求而师父不肯（route 交）的选项不走这里，它们在候选源里就定了键（恳请某人传授某功）。
    """
    source = snap.label(ok.source)
    match ok.guidance:
        case Guidance.ENTRY if ok.target:  # 入门且有人传授：target 即师父
            return "learn.ask", (("src", source), ("art", art))
        case Guidance.ENTRY:
            return "learn.self", (("src", source), ("art", art))
        case Guidance.TEACHER:
            return "learn.teacher", (("src", source), ("art", art))
        case Guidance.MANUAL:
            return "learn.manual", (("src", source), ("art", art))
    return "learn.alone", (("art", art),)
