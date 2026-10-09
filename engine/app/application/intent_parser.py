"""
[INPUT]: 依赖 application/ports 的 LLMClient，依赖 domain/intent 的 ActionType / Approach / Aim / PlayerIntent，依赖 domain/approach 的 MOVES / AIMS / Row / row_of（兼容表），
         依赖 domain/rules/base 的 ground（话题落地），依赖 domain/snapshot 的 LocalSnapshot / ExitView / CharacterView（handle 方位把手），
         依赖 domain/geography 的 Direction / UNKNOWN_PLACE，依赖 domain/models 的 WorldBlueprint（for_canon）/ Attitude
[OUTPUT]: 对外提供 WorldviewGuard（违背世界观的确定性词表守卫，先剔除场景正名与 for_canon 收录的原著撞词正名）、scene_names()（玩家此刻叫得出的名字，迷雾里的出路不算）、
          IntentParser 抽象（模板方法：守卫 → 解读 → 清空非移动的此行所为 → 再守卫）、
          LLMIntentParser（结构化输出 + 重采样 + 兜底 INVALID）、HeuristicIntentParser（离线关键词解析：动作 + 手段 + 所图 + 话题 + 沉思 + 此行所为 + 方位 + 问路）、
          INTENT_SYSTEM / INTENT_SCHEMA / scene_vocabulary()
[POS]: application 的命令侧入口（CQRS 的 Command 解析）：把玩家的华丽武侠描写降维为系统可识别的 PlayerIntent（动作 × 手段 × 所图 × 对象 × 话题 × 此行所为）。
       三道防线拦截热兵器与法术：①词表守卫在调用大模型之前直接判 INVALID（省钱且不可被话术绕过）；
       ②提示词要求大模型对违背世界观的内容判 INVALID；③即便大模型被说服，裁决规则也只认图谱里存在的实体——AK47 不在任何人的行囊里。
       解析器只产出"想做什么、怎么做、图什么"，从不判断"能不能做"：后者是 domain/rules 的职权（兼容表外的组合由 approach.normalize 退回寻常）。
       守卫的原文检查与字段再检查（target / item / skill / topic / motivation）都先剔除场景正名（scene_names）与原著里撞上禁词的正名（for_canon，不在眼前也算）再查禁词：
       原著的「金针渡劫」不被「渡劫」误杀，「渡劫飞升」照拦。
       INTENT_SCHEMA 只有形状（domain/intent 不写 docstring）；说明全在 INTENT_SYSTEM：USE、七种手段与九种所图各一句判据（「用于 / 见于」由兼容表生成）、
       话题指称、撂话离场是 MOVE（话只化进笔墨）。离线解析器认得手段与所图的关键词（引号里的话不算动作），打探的话题须经 rules.ground 落得了地，
       输出的手段先经 _fit 按兼容表退回寻常——偷袭是出手，不是潜行。
       场景词表随渐进式状态而丰富：人物带称号与别名（喊「恶贯满盈」也能规整为段延庆），已会武学带火候；
       LEARN 一个动作涵盖入门与精进，REST 调息疗伤是独立动作——"练功疗伤"以疗伤为准，"服药疗伤"以 USE 为准。
       THINK 是沉思（回想、盘算、权衡，不动手不开口，只花时间）；motivation 是 MOVE 的此行所为（去找谁、去做什么，≤24 字，没说即空），
       别的动作写了也由模板方法清空——它随 Moved 入账，跨进新地方时与眼前所见对照，写出预期落差。离线解析：沉思的关键词只在别的动作都没命中时判 THINK；
       此行所为按结构切分——用出口名定位去处，取其后到句末的一段（「去大理城找段正淳」→「找段正淳」），去处后紧跟「的 / 之」的那一处只是修饰、不足两字不算；
       点了出路且「去」在一切别的动作之前即是动身，后面的取物、出手、打探都是此行所为（「去大理城取玉璧」不是在此地取物）；
       找移动动词前先等长遮掉「去 / 往 / 赶」的非移动义（过去的事、失去、望去、往事、赶紧），「想想过去的事」是沉思而不是挪步。
       探索迷雾：场景词表的出路写「方位把手（标签）→去处」，未知的去处只写「把手→未知区域」——标签（常带地名）与名字都不给；
       scene_names 只收认得的出路（守卫与菜单风味的闸门共用此口径）。INTENT_SYSTEM 教 MOVE 写方位把手（往某方位走就写那个把手，未知区域只能写把手、不猜名字），
       问路（打听去处、前路，这路通向哪里）是寻常的 TALK、topic 写此地之名，示例各一条。离线解析认方位：先等长遮掉此景一切名字（「南海鳄神」不是往南），
       「往」+ 四方四隅即挪步，「向 / 朝」与上下内外须跟着挪步的字，「北上」「南下」「东行」「西去」照认；这个方位上恰有一条出路即落到它的把手，
       不止一条或没有就照写方位交给规则驳回（多义不猜），其后到句末是此行所为；没点出路名字时才看方位，点了的照旧。
       问路先于一切判定（问的话常在引号里，故看原话）：问点了名的人，没点名就问不敌视你的在场者里人情最好的一位（平手取快照次序，与菜单的问路同一人），
       四下无人可问就不是问路；问路恒是寻常手段、话题是此地——规则据此记下 PlacesLearned
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Protocol, Self

from pydantic import ValidationError

from app.application.ports import LLMClient
from app.domain.approach import AIMS, MOVES, Row, row_of
from app.domain.geography import UNKNOWN_PLACE, Direction
from app.domain.intent import ActionType, Aim, Approach, PlayerIntent
from app.domain.models import Attitude, WorldBlueprint
from app.domain.rules.base import ground
from app.domain.snapshot import CharacterView, ExitView, LocalSnapshot

logger = logging.getLogger(__name__)


# ============================================================
#  第一道防线：世界观词表
#  刻意不收单字「枪」「炮」「剑气」：长枪、枪法、石炮都是这个江湖里的东西，误杀比漏网更伤玩家
# ============================================================
ANACHRONISMS: tuple[str, ...] = (
    "手枪", "步枪", "机枪", "冲锋枪", "狙击枪", "霰弹枪", "来复枪", "猎枪", "枪械", "子弹", "弹匣", "手榴弹",
    "炸弹", "导弹", "火箭筒", "核弹", "原子弹", "激光", "机甲", "坦克", "飞机", "汽车", "摩托", "手机", "电话",
    "电脑", "互联网", "无人机", "魔法", "法术", "魔杖", "咒语", "火球术", "传送门", "召唤术", "异能", "超能力",
    "修仙", "渡劫", "金丹", "元婴", "外挂", "作弊", "存档", "读档", "无敌模式", "系统面板",
    "ak47", "ak-47", "gun", "pistol", "rifle", "magic", "spell", "laser",
)


class WorldviewGuard:
    def __init__(self, lexicon: Iterable[str] = ANACHRONISMS, *, canon: Iterable[str] = ()) -> None:
        self._lexicon = tuple(word.lower() for word in lexicon)
        self._canon = tuple(canon)  # 原著里撞上禁词的正名（「金针渡劫」）：不在眼前也照样是江湖里的东西

    @classmethod
    def for_canon(cls, blueprint: WorldBlueprint | None) -> Self:
        """从正典里挑出撞上禁词的全部叫法（人、功、物、地，含称号与别名）：玩家在别处打听它，也不该被当成修仙。"""
        if blueprint is None:
            return cls()
        lexicon = tuple(word.lower() for word in ANACHRONISMS)
        named = (*blueprint.locations, *blueprint.martial_arts, *blueprint.items)
        names = (*(n for e in named for n in e.names), *(n for c in blueprint.characters for n in c.names))
        return cls(canon=sorted({n for n in names if any(word in n.lower() for word in lexicon)}))

    def violation(self, *texts: str | None, spared: Iterable[str] = ()) -> str | None:
        """
        返回第一个越界的词；全部合乎世界观返回 None。
        spared 是此情此景的正名（scene_names）：先把撞上禁词的那些剔掉再查词表——原著的「金针渡劫」不该被「渡劫」误杀。
        只剔撞词的正名（其余正名剔了也无益，反倒可能拆碎禁词），剔除处留一个分隔符，免得前后两段拼成新的禁词。
        """
        names = sorted(
            {n.lower() for n in (*spared, *self._canon) if any(word in n.lower() for word in self._lexicon)},
            key=len, reverse=True,
        )
        for text in texts:
            lowered = (text or "").lower()
            for name in names:
                lowered = lowered.replace(name, "｜")
            if hit := next((word for word in self._lexicon if word in lowered), None):
                return hit
        return None


def scene_names(scene: LocalSnapshot) -> tuple[str, ...]:
    """
    此情此景玩家叫得出的全部正名与叫法（人、功、物、所在、认得的出路）：守卫查禁词之前先剔掉它们，菜单风味的闸门也以它为「此景之名」。
    迷雾里的出路一个名字也不算——未知去处的名字与出口标签（常带地名）都不是玩家此刻叫得出的。
    """
    views = (*scene.characters, *scene.skills, *scene.items)
    return (
        *(n for v in views for n in v.names),
        scene.location.name,
        *(n for e in scene.exits if e.known for n in e.names),
    )


# ============================================================
#  解析器抽象 —— 模板方法：守卫是不可绕过的固定步骤，子类只负责"解读"
# ============================================================
class IntentParser(ABC):
    def __init__(self, guard: WorldviewGuard | None = None) -> None:
        self._guard = guard or WorldviewGuard()

    async def parse(self, text: str, scene: LocalSnapshot) -> PlayerIntent:
        text = text.strip()
        if not text:
            return PlayerIntent.invalid("你什么也没有做。")
        spared = scene_names(scene)  # 两道检查都先剔除场景正名：「向左子穆求教金针渡劫」不是修仙
        if word := self._guard.violation(text, spared=spared):
            return PlayerIntent.invalid(f"「{word}」不属于这个江湖。")
        intent = await self._interpret(text, scene)
        if intent.motivation and intent.action_type is not ActionType.MOVE:  # 此行所为只属于移动：别的动作写了也不算数
            intent = intent.model_copy(update={"motivation": ""})
        # 话题与此行所为同是解析出的说法：「打听渡劫之法」「去大理城修仙」照拦
        fields = (intent.target_entity, intent.item_used, intent.skill_used, intent.topic, intent.motivation)
        if word := self._guard.violation(*fields, spared=spared):
            return PlayerIntent.invalid(f"「{word}」不属于这个江湖。")
        return intent

    @abstractmethod
    async def _interpret(self, text: str, scene: LocalSnapshot) -> PlayerIntent: ...


def _safe(text: str) -> str:
    return text.replace("<", "＜").replace(">", "＞")  # 玩家输入不能闭合或伪造协议标签


class _Nameable(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def names(self) -> tuple[str, ...]: ...


def scene_vocabulary(scene: LocalSnapshot) -> str:
    """此情此景可指称之物：让解析器能把"那个乞丐头子""恶贯满盈"规整为在场者的正名，而不是凭空造名。"""

    def names(views: Iterable[_Nameable]) -> str:
        parts = []
        for v in views:
            others = v.names[1:]  # 正名之外的一切叫法：人物是称号 + 别名，其余是别名
            parts.append(v.name + (f"（{'、'.join(others)}）" if others else ""))
        return "、".join(parts) or "无"

    def practiced() -> str:
        parts = []
        for s in scene.known_skills:
            mastery = scene.mastery(s.id)
            notes = [*([mastery.value] if mastery else []), *(f"又名{a}" for a in s.aliases)]
            parts.append(s.name + (f"（{'；'.join(notes)}）" if notes else ""))
        return "、".join(parts) or "无"

    def way(e: ExitView) -> str:  # 迷雾：未知的去处只给方位把手，标签与名字都不给
        handle = scene.handle(e)
        if not e.known:
            return f"{handle}→{UNKNOWN_PLACE}"
        return f"{handle}{f'（{e.label}）' if e.label != handle else ''}→{e.to_name}"

    known = set(scene.player_skills)
    lines = [
        f"所在：{scene.location.name}",
        f"出路（方位把手→去处）：{'、'.join(map(way, scene.exits)) or '无'}",
        f"在场之人：{names(scene.characters)}",
        f"可见之物：{names(i for i in scene.items if i.holder_id != scene.player_id)}",
        f"随身之物：{names(scene.inventory)}",
        f"你已会的武学：{practiced()}",
        f"此地可闻的武学：{names(s for s in scene.skills if s.id not in known)}",
    ]
    return _safe("\n".join(lines))


INTENT_SCHEMA = PlayerIntent.model_json_schema()  # 只有形状：domain/intent 的枚举与模型不写 docstring，说明全在 INTENT_SYSTEM

# 手段与所图的判据：一句话一条，给大模型看。适用的动作不手写，由 domain/approach 的兼容表（MOVES / AIMS）生成——表一改，提示词跟着改
_APPROACH_CUES: dict[Approach, str] = {
    Approach.PLAIN: "没有特别的手段，照直去做（缺省）",
    Approach.FORCE: "以武相逼——动手强夺、厉声威胁、逼问、喝问",
    Approach.WORDS: "凭言语打动对方——恳请、请教、相求、劝说、打听、赔罪、开口讨要",
    Approach.FAVOR: "凭交情恩义相求——「看在……的份上」「念在旧情」、讨个人情、投其所好地赠物",
    Approach.GUILE: "设局或说谎——骗、诓、哄、设计、虚晃一招、套话",
    Approach.STEALTH: "瞒着对方暗中下手——偷、窃、顺手牵羊、趁其不备摸走",
    Approach.LEVERAGE: "搬出靠山或把柄压人——「借某人之名」「搬出某人」、拿对方的短处说事",
}
_AIM_CUES: dict[Aim, str] = {
    Aim.SUBDUE: "把对方打倒、制住",
    Aim.SEIZE: "把对方手里的东西弄到手（强夺、偷、骗）",
    Aim.ESCAPE: "摆脱纠缠、离开险地",
    Aim.LEARN: "求对方传授武功",
    Aim.PROBE: "打听消息、问起某人某事——问的是什么写进 topic",
    Aim.BEFRIEND: "示好、攀交情",
    Aim.DEFUSE: "平息对方的怒气或仇怨——劝解、说和、赔罪",
    Aim.ASK: "开口向对方要东西",
    Aim.WARN: "提醒对方提防某人某事",
}


def _row_label(row: Row) -> str:
    return "TAKE（他人之物）" if row is Row.TAKE_HELD else row.value


def _criteria() -> str:
    approaches = [
        f"- {a.value}：{_APPROACH_CUES[a]}"
        + ("" if a is Approach.PLAIN else f"。用于 {'、'.join(_row_label(r) for r, row in MOVES.items() if a in row)}")
        for a in Approach
    ]
    aims = [f"- {m.value}：{_AIM_CUES[m]}。见于 {'、'.join(t.value for t in ActionType if m in AIMS.get(t, ()))}" for m in Aim]
    return "approach（手段）只能取以下之一，动作不在「用于」之列一律写「寻常」：\n" + "\n".join(approaches) + "\n\naim（所图）只能取以下之一，没说清就写 null（世界引擎按动作与人情推断）：\n" + "\n".join(aims)


INTENT_SYSTEM = """你是《天龙八部》文字世界的意图解析器。玩家会用华丽的武侠笔墨描述自己的举动，你要把它降维为一条系统指令，只输出 JSON。

action_type 只能取以下之一：
- MOVE：沿某条出路去往别处。target_entity 写 <scene_vocabulary>「出路」里那条路的方位把手（「北」「东·二」），去处已知时也可写去处名；
  往某个方位走（向北而去、往东、顺着小路南下）就写那个方位的把手。去处是「未知区域」的只能写方位把手，不替它猜名字。
  玩家说了此去找谁、做什么，写进 motivation。撂下一句话就走（「后会有期」后拂袖而去）也是 MOVE：那句话只化进 narrative_style，不写进 topic。
- OBSERVE：观望、等待、倾听、闭目养神等不改变任何事物的举动。
- THINK：沉思、回想、盘算、权衡——只在心里过一遍，不动手、不开口、不挪步。
- TALK：与某人说话、打听、拜见、劝解、威逼、套话。target_entity 写说话的对象（不是谈起的那人）。
  问路（打听去处、前路，问这条路通向哪里）也是 TALK：target_entity 写问的那人，topic 写此地之名（<scene_vocabulary> 的「所在」），approach「寻常」，aim null。
- ATTACK：向某人出手。target_entity 写那人；skill_used 只在玩家点名所用武功时填写；item_used 只在玩家点名所用兵器时填写。
- TAKE：拿取某物——地上之物，或别人身上之物（偷、骗、讨、夺都是 TAKE，怎么拿写进 approach）。target_entity 写那件东西。
- GIVE：把随身之物交给某人。target_entity 写那人，item_used 写那件东西。
- LEARN：修习、求教、参悟、练功、苦练某门武功——初学乍练与已会之后的精进都算。skill_used 写那门武功；若向某人求教，target_entity 写那人。
- REST：调息、疗伤、打坐、运功疗伤、歇息养伤，为的是恢复伤势。一句话里既练功又疗伤，以疗伤为准。
- USE：服药、敷药、吃下或涂抹随身之物。item_used 写那件东西。服药疗伤以 USE 为准。
- INVALID：举动违背这个北宋武侠世界的世界观——热兵器与现代器物（枪械、炸弹、手机、汽车……）、魔法法术、
  修仙异能、元游戏指令（存档、作弊、修改数值、索要无敌）——或根本无法理解。reason 用一句话写明为何不合天道。

""" + _criteria() + """

topic（话题）：玩家谈起、打听、提醒或拿来说事的那个人、物、武功、地方或见闻，按 <scene_vocabulary> 规整为正名，
不在表中照抄原话（世界引擎落不了地即置之不理）；没有话题写 null。说话的对象写 target_entity，不写进 topic。

motivation（此行所为）：只在 MOVE 时填写——玩家说了此去找谁、去做什么，照原话简写，至多二十四字（「去大理城找段正淳」写「找段正淳」，去处本身写进 target_entity）；
玩家没说就写空串 ""，不替玩家编一个来由。别的动作一律写空串：此行所为不是这一步要做的事。

解析铁律：
1. 你只解析"想做什么"，不判断"做不做得成"。玩家想打绝顶高手，就是 ATTACK；成败由世界引擎裁决。
2. 指称以 <scene_vocabulary> 为准：玩家用代称或绰号指场上的人、物、路时，规整为表中的正名（路规整为方位把手）；
   玩家点名的东西不在表中，照抄玩家的原话，绝不替换成表里别的东西，更不编造新名字。
3. 一句话里有多个动作，取最主要、最先发生的那一个。
4. 手段与所图照玩家的原话判，不替玩家加戏：没有特别手段就是「寻常」，所图没说清就是 null。
5. narrative_style 用二到八个字概括玩家的笔墨风格（如「潇洒从容」「阴狠毒辣」「恭敬谦卑」），它只影响叙事，不影响裁决。
6. 只输出符合 schema 的 JSON 对象，不要解释。

示例：
玩家：「我施展凌波微步，飘然向北而去」（出路有 北（北上）→无量山）
{"action_type": "MOVE", "target_entity": "北", "item_used": null, "skill_used": null, "narrative_style": "飘逸潇洒", "reason": null, "approach": "寻常", "aim": null, "topic": null, "motivation": ""}
玩家：「连夜赶去大理城，找段正淳问个明白」（出路有 南（南下）→大理城）
{"action_type": "MOVE", "target_entity": "南", "item_used": null, "skill_used": null, "narrative_style": "风尘仆仆", "reason": null, "approach": "寻常", "aim": null, "topic": null, "motivation": "找段正淳问个明白"}
玩家：「掏出手枪对准那大汉扣动扳机」
{"action_type": "INVALID", "target_entity": null, "item_used": null, "skill_used": null, "narrative_style": "", "reason": "北宋江湖没有手枪", "approach": "寻常", "aim": null, "topic": null, "motivation": ""}
玩家：「恭恭敬敬向段王爷请教一阳指」
{"action_type": "LEARN", "target_entity": "段正淳", "item_used": null, "skill_used": "一阳指", "narrative_style": "恭敬谦卑", "reason": null, "approach": "言辞", "aim": "求艺", "topic": null, "motivation": ""}
玩家：「趁那小姑娘不备，悄悄摸走她的闪电貂」（在场之人有 钟灵）
{"action_type": "TAKE", "target_entity": "闪电貂", "item_used": null, "skill_used": null, "narrative_style": "鬼鬼祟祟", "reason": null, "approach": "潜行", "aim": "夺物", "topic": null, "motivation": ""}
玩家：「向左掌门打听神农帮为何上门寻仇」（在场之人有 左子穆）
{"action_type": "TALK", "target_entity": "左子穆", "item_used": null, "skill_used": null, "narrative_style": "客气", "reason": null, "approach": "言辞", "aim": "打探", "topic": "神农帮", "motivation": ""}
玩家：「沿着溪边的小径往东走」（出路有 东→未知区域）
{"action_type": "MOVE", "target_entity": "东", "item_used": null, "skill_used": null, "narrative_style": "从容", "reason": null, "approach": "寻常", "aim": null, "topic": null, "motivation": ""}
玩家：「向那小姑娘打听这条路通向哪里」（所在：剑湖宫·练武厅，在场之人有 钟灵）
{"action_type": "TALK", "target_entity": "钟灵", "item_used": null, "skill_used": null, "narrative_style": "客气", "reason": null, "approach": "寻常", "aim": null, "topic": "剑湖宫·练武厅", "motivation": ""}
玩家：「冷笑一声：『咱们走着瞧！』拂袖而去」（出路有 外部（出厅）→剑湖宫）
{"action_type": "MOVE", "target_entity": "外部", "item_used": null, "skill_used": null, "narrative_style": "冷傲撂话", "reason": null, "approach": "寻常", "aim": null, "topic": null, "motivation": ""}
玩家：「掏出金创药敷在伤处」
{"action_type": "USE", "target_entity": null, "item_used": "金创药", "skill_used": null, "narrative_style": "咬牙忍痛", "reason": null, "approach": "寻常", "aim": null, "topic": null, "motivation": ""}
玩家：「寻个僻静处盘膝坐下，运功疗伤」
{"action_type": "REST", "target_entity": null, "item_used": null, "skill_used": null, "narrative_style": "沉静", "reason": null, "approach": "寻常", "aim": null, "topic": null, "motivation": ""}
玩家：「倚着廊柱，把方才那一剑的来路在心里过了一遍」
{"action_type": "THINK", "target_entity": null, "item_used": null, "skill_used": null, "narrative_style": "若有所思", "reason": null, "approach": "寻常", "aim": null, "topic": null, "motivation": ""}"""


class LLMIntentParser(IntentParser):
    def __init__(self, llm: LLMClient, *, attempts: int = 2, guard: WorldviewGuard | None = None) -> None:
        super().__init__(guard)
        self._llm = llm
        self._attempts = attempts

    async def _interpret(self, text: str, scene: LocalSnapshot) -> PlayerIntent:
        user = f"<scene_vocabulary>\n{scene_vocabulary(scene)}\n</scene_vocabulary>\n<player_input>{_safe(text)}</player_input>"
        for attempt in range(1, self._attempts + 1):
            raw = await self._llm.complete(INTENT_SYSTEM, user, INTENT_SCHEMA)
            try:
                start, end = raw.find("{"), raw.rfind("}")
                return PlayerIntent.model_validate(json.loads(raw[start : end + 1]))
            except (ValueError, ValidationError) as exc:
                logger.warning("意图解析第 %d 次输出不合契约：%s", attempt, exc)
        return PlayerIntent.invalid("天机未明：无法领会此举，请换个说法。")


# ============================================================
#  离线解析器 —— 无大模型时的确定性兜底：动词 + 手段与所图的关键词 + 场景词表
# ============================================================
_VERBS: dict[ActionType, tuple[str, ...]] = {
    ActionType.REST: ("调息", "疗伤", "养伤", "打坐"),  # 不收「歇息」「养神」：歇一歇只是静观，不是疗伤
    ActionType.LEARN: ("学", "求教", "参悟", "修习", "研读", "练", "精研", "传我", "传授", "请教", "指点"),
    ActionType.GIVE: ("给", "赠", "交还", "送", "奉还", "归还", "递"),
    ActionType.ATTACK: ("攻", "杀", "击", "出手", "偷袭", "刺", "砍", "劈", "揍", "殴打", "打向", "打去", "一拳", "一掌", "一脚", "踢"),
    # 不收单字「打」：打听不是动手；「一拳打向」「一脚踢去」才是
    ActionType.TAKE: ("拿", "取", "捡", "拾", "夺", "抢", "偷", "窃", "摸走", "顺走", "借我", "借给我", "借来", "给我", "讨要", "要来", "讨来"),
    # 「借」只收「借我 / 借来」：「借左掌门之名」是借势，不是取物
    ActionType.MOVE: ("去", "往", "走", "前往", "赶", "进入", "回到", "离开"),
    ActionType.THINK: ("想想", "寻思", "沉思", "盘算", "回想", "思索"),  # 只在别的动作都没命中时才算：「寻思片刻，南下而去」是移动
}
_ASKING = ("借我", "借给我", "借来", "给我", "讨要", "要来", "讨来")  # 开口要：取他人之物而别无手段，即言辞讨要

# 服 / 敷 / 吃 + 行囊之物 → USE；「说服」「佩服」「吃惊」不是服药
_USE = re.compile(r"(?<![说佩降制驯屈信折心衣征收克臣舒])服|敷|吃(?![惊力亏紧])|涂抹|涂上|吞下|喝下|饮下")
# 手段关键词，按优先级排列：借势与人情是「凭什么」，压过「怎么说」；潜行的「偷」在 ATTACK 一行不合表（偷袭只是出手），由 _fit 退回寻常
_APPROACH_WORDS: tuple[tuple[Approach, re.Pattern[str]], ...] = (
    (Approach.LEVERAGE, re.compile(r"借.{1,8}?(之名|的名头|的名号|的面子)|搬出|打着.{1,8}?旗号")),
    (Approach.FAVOR, re.compile(r"人情|看在.{0,12}?(份上|面上)|念在")),
    (Approach.FORCE, re.compile(r"威胁|威逼|逼问|喝问|胁迫|夺|抢")),
    (Approach.GUILE, re.compile(r"骗|诓|哄|设计|诈|套.{0,6}?话|套出")),
    (Approach.STEALTH, re.compile(r"偷|窃|顺手|摸走|顺走")),
    (Approach.WORDS, re.compile(r"恳请|请教|相求|劝|说和|赔罪|赔礼|求情|好言")),
)
_BACKER = re.compile(r"借(.{1,8}?)(?:之名|的名头|的名号|的面子)|搬出(.{1,8})|打着(.{1,8}?)旗号")  # 借势的靠山：不是说话的对象
_PROBE = ("打听", "打探", "询问", "问起", "探问")  # 打探：话题落得了地才算（「打听消息」只是闲谈）
_DEFUSE = ("劝", "说和", "赔罪", "赔礼")
_COERCE = ("威胁", "威逼", "逼问", "喝问", "胁迫")  # 威逼是 TALK×武力：动口不动手
_FILLER = re.compile(r"^(一下|一番|一些|些|关于|有关)")
_QUOTED = re.compile(r"「[^」]*」|『[^』]*』|“[^”]*”|\"[^\"]*\"")  # 撂下的话是笔墨，不是动作：动词与手段只在引号之外找
# 此行所为：去处之后那一段说法，截到句末；开头的标点、连词与趋向补语（「，再去」「而去」）不是所为
_CLAUSE_END = re.compile(r"[。！？；!?;…]")
_PURPOSE_LEAD = re.compile(r"^[\s，,、:：]*(?:然后|而后|再|便|就|好|并|而)?[去来]?")
_MODIFIER = ("的", "之")  # 去处后紧跟「的 / 之」：那个名字只是修饰（「崖下的藤蔓」），不是去处
# 「去 / 往 / 赶」的非移动义：过去的事、失去、放眼望去、往事、以往、赶紧——不是挪步。等长遮掉再找移动动词，位置与原句对齐
_NOT_GOING = re.compile(r"过去(?=[的之种那这所])|[失除死望看瞧]去|去[世年]|往(?=[日事常昔年往])|[以既过]往|赶(?=[紧快忙])")
_DOINGS = (*_PROBE, *_COERCE, *_DEFUSE)  # 开口之事的关键词：与别的动作动词一起，跟「去」比谁在句中先出现
# 方位：「往东」「向北走」「朝南而去」「一路南下」「往上爬」「往里走」。往 + 四方四隅本身就是挪步；向 / 朝，以及上下内外，须跟着挪步的字
_GO = r"(?:而去|(?:走|行|奔|赶|闯|进|飞|掠|爬|攀|跳|跃|游|跑)[去来]?|去)"  # 「奔去」「走来」整个算进去：其后才是此行所为
_HEADING = re.compile(
    rf"往着?(?P<a>东北|东南|西北|西南|东|南|西|北)(?:边|面|方)?{_GO}?"
    rf"|(?:往|向|朝)着?(?P<b>东北|东南|西北|西南|东|南|西|北|上|下|里|内|外)(?:边|面|方|头)?{_GO}"
    r"|(?P<c>东北|东南|西北|西南|东|南|西|北)(?:行|去)|(?P<d>北)上|(?P<e>南)下"
)
_BEARING: dict[str, Direction] = {
    **{d.value: d for d in (Direction.NORTHEAST, Direction.SOUTHEAST, Direction.NORTHWEST, Direction.SOUTHWEST,
                            Direction.EAST, Direction.SOUTH, Direction.WEST, Direction.NORTH, Direction.UP, Direction.DOWN)},
    "里": Direction.INSIDE, "内": Direction.INSIDE, "外": Direction.OUTSIDE,
}
# 问路：打听去处、前路，问这条路通向哪里——话题是此地，规则据此把四下还不认得的去处指给你
_ASK_WAY = re.compile(
    r"问路|问个路|问问路|指路|指点(?:一下)?(?:去处|前路|路径|道路|出路)|打听(?:一下)?(?:去处|前路|路径|道路|出路|路)"
    r"|通(?:向|往|到)(?:哪|何)|(?:怎么|怎样|如何)走|往哪(?:里|儿|边)?(?:走|去)|去往何处"
)


def _after_verb(text: str, verbs: Iterable[str], *, seen: str | None = None) -> str:
    """最先出现的动词之后那几个字；seen 是遮掉非移动义的等长句子（在它里面找动词，从原句里取字）。"""
    seen = text if seen is None else seen
    hits = [(seen.find(v), v) for v in verbs if v in seen]
    if not hits:
        return ""
    pos, verb = min(hits)
    return text[pos + len(verb) :].strip(" 　，。！？,.!?")[:12]


def _first(text: str, words: Iterable[str]) -> int:
    """最先出现的那个词在句中的位置；一个都没出现即句长。"""
    return min((i for w in words if (i := text.find(w)) >= 0), default=len(text))


class _Called(Protocol):
    @property
    def names(self) -> tuple[str, ...]: ...


def _mentioned[T: _Called](text: str, views: Iterable[T], *, skip: Iterable[str] = ()) -> T | None:
    """最先被点名的那一个（同一处取最长的叫法）：「向左子穆打听钟灵」说话的对象是左子穆。skip 是不作此用的叫法（借势的靠山）。"""
    skip = tuple(skip)
    best: tuple[int, int, T] | None = None
    for v in views:
        hits = [(text.find(n), -len(n)) for n in v.names if n and n in text and n not in skip]
        if hits and (best is None or min(hits) < best[:2]):
            best = (*min(hits), v)
    return best[2] if best else None


def _purpose(text: str, way: _Called) -> str:
    """
    MOVE 的此行所为：按结构切分——用出口名（出路名或去处名）定位去处，取它之后到句末的那一段：「去大理城找段正淳」→「找段正淳」。
    去处后紧跟「的 / 之」的那一处只是修饰（「剑湖宫的匾额」），跳过它看下一处；剥掉开头的标点、连词与趋向补语后不足两字的（「而去」「走」）不算。
    """
    spots = sorted(
        (m.start(), -len(n)) for n in {n for n in way.names if n} for m in re.finditer(re.escape(n), text)
    )
    for pos, minus in spots:
        tail = text[pos - minus :]
        if tail.startswith(_MODIFIER):
            continue
        return _clause(tail)
    return ""


def _clause(tail: str) -> str:
    """去处（或方位说法）之后到句末的那一段，剥掉开头的标点、连词与趋向补语；不足两字不算。"""
    clause = _PURPOSE_LEAD.sub("", _CLAUSE_END.split(tail, 1)[0]).rstrip(" 　，,、")
    return clause if len(clause) >= 2 else ""


def _heading(text: str, scene: LocalSnapshot) -> tuple[str, int, int] | None:
    """
    方位说法 → (去向, 起, 止)：先等长遮掉此景的一切名字（「南海鳄神」「东宗弟子」不是方位）再找；
    这个方位上恰有一条出路即它的把手（「东」「东·二」），不止一条或一条也没有就照写方位，交给规则驳回——多义不猜。
    """
    named = {n for v in (*scene.characters, *scene.items, *scene.skills) for n in v.names}
    named |= {s.name for s in scene.swarms} | {scene.location.name} | {n for e in scene.exits for n in (e.label, e.to_name)}
    for name in sorted(filter(None, named), key=len, reverse=True):
        text = text.replace(name, "　" * len(name))
    m = _HEADING.search(text)
    if m is None:
        return None
    direction = _BEARING[next(g for g in m.groups() if g)]
    ways = [e for e in scene.exits if e.direction is direction]
    return (scene.handle(ways[0]) if len(ways) == 1 else direction.value), m.start(), m.end()


def _guide(scene: LocalSnapshot) -> CharacterView | None:
    """没点名问谁路：不敌视你的在场者里人情最好的一位（平手取快照次序），与菜单的问路同一个人。"""
    willing = [c for c in scene.characters if c.attitude is not Attitude.HOSTILE]
    return max(willing, key=lambda c: c.attitude.rank, default=None)


def _approach(text: str) -> Approach:
    return next((a for a, cue in _APPROACH_WORDS if cue.search(text)), Approach.PLAIN)


def _fit(action: ActionType, approach: Approach, *, held: bool = False) -> Approach:
    """兼容表外的手段退回寻常（与 approach.normalize 同一口径）：偷袭是出手，不是潜行。"""
    return approach if approach in MOVES[row_of(action, held=held)] else Approach.PLAIN


def _topic(text: str, scene: LocalSnapshot, cues: Iterable[str]) -> str | None:
    """话题：打探之词后面的那几个字，落得了地（rules.ground）才算；先试修饰语之前的中心词——「钟灵的下落」取「钟灵」。"""
    tail = _FILLER.sub("", _after_verb(text, cues))
    for candidate in dict.fromkeys((tail.split("的")[0], tail.split("之")[0], tail)):
        if candidate and ground(candidate, scene) is not None:
            return candidate
    return None


class HeuristicIntentParser(IntentParser):
    async def _interpret(self, text: str, scene: LocalSnapshot) -> PlayerIntent:
        body = _QUOTED.sub("", text).strip() or text
        going = _NOT_GOING.sub(lambda m: "　" * len(m.group()), body)  # 找移动动词只在这里找：「想想过去的事」不是挪步

        def has(words: Iterable[str]) -> bool:
            return any(w in body for w in words)

        named = next((g for m in _BACKER.finditer(body) for g in m.groups() if g), "")
        backer = _mentioned(named, scene.characters)  # 借谁的名头，谁就不是说话的对象：「搬出左子穆来压龚光杰」对的是龚光杰
        person = _mentioned(body, scene.characters, skip=backer.names if backer else ())
        thing = _mentioned(body, scene.items)
        art = _mentioned(body, scene.skills)
        way = _mentioned(body, scene.exits)
        heading = None if way else _heading(going, scene)  # 没点出路的名字，再看说没说方位
        carried = _mentioned(body, scene.inventory)
        approach = _approach(body)

        def rest(action: ActionType) -> str | None:  # 点名的东西不在场景里：照抄原话，交给规则驳回
            return _after_verb(body, _VERBS[action], seen=going if action is ActionType.MOVE else None) or None

        def errand() -> PlayerIntent:
            if way is not None:
                return PlayerIntent(action_type=ActionType.MOVE, target_entity=way.label, motivation=_purpose(body, way))
            assert heading is not None
            target, _, end = heading  # 方位的说法与原句等长对齐：其后到句末即此行所为
            return PlayerIntent(action_type=ActionType.MOVE, target_entity=target, motivation=_clause(body[end:]))

        # 问路先于一切：「向钟灵打听去处」不是打探，「问他往哪走」也不是自己动身；问的话常在引号里，故看原话
        if _ASK_WAY.search(text) and (guide := person or _guide(scene)) is not None:
            return PlayerIntent(action_type=ActionType.TALK, target_entity=guide.name, topic=scene.location.name)

        def talk(aim: Aim | None = None, topic: str | None = None, default: Approach = Approach.PLAIN) -> PlayerIntent:
            assert person is not None
            manner = _fit(ActionType.TALK, approach) if approach is not Approach.PLAIN else default
            return PlayerIntent(action_type=ActionType.TALK, target_entity=person.name, approach=manner, aim=aim, topic=topic)

        # 点了出路且「去」在一切别的动作之前：此行所为，不是此地之事——「去大理城取玉璧」是动身，取玉璧是到了才做的事
        acts = (*_VERBS[ActionType.REST], *_VERBS[ActionType.LEARN], *_VERBS[ActionType.GIVE],
                *_VERBS[ActionType.ATTACK], *_VERBS[ActionType.TAKE], *_DOINGS)
        dosing = m.start() if (m := _USE.search(body)) else len(body)
        setting_off = min(_first(going, _VERBS[ActionType.MOVE]), heading[1] if heading else len(body))
        if (way or heading) and setting_off < min(_first(body, acts), dosing):
            return errand()
        probe = _topic(body, scene, _PROBE) if person and has(_PROBE) else None
        # 服药先于疗伤：「服下金创药疗伤」用的是药；点名的须是此地叫得出名的东西，「吃饭」不是服药
        if _USE.search(body) and (pill := carried or thing):
            return PlayerIntent(action_type=ActionType.USE, item_used=pill.name)
        # 疗伤先于修习：「练功疗伤」的本意是疗伤；但「趁龚光杰调息偷袭他」里的调息说的是对手，「打听疗伤之法」是打探
        if has(_VERBS[ActionType.REST]) and not (person and (has(_VERBS[ActionType.ATTACK]) or probe)):
            return PlayerIntent(action_type=ActionType.REST)
        if probe:  # 打探先于修习：「向左子穆打听无量剑法」问的是来历，不是求艺
            return talk(Aim.PROBE, probe, default=Approach.WORDS)
        if has(_VERBS[ActionType.LEARN]):
            return PlayerIntent(action_type=ActionType.LEARN, skill_used=art.name if art else rest(ActionType.LEARN),
                                target_entity=person.name if person else None, approach=_fit(ActionType.LEARN, approach))
        if has(_VERBS[ActionType.GIVE]) and person and carried:
            return PlayerIntent(action_type=ActionType.GIVE, target_entity=person.name, item_used=carried.name,
                                approach=_fit(ActionType.GIVE, approach))
        if person and has(_COERCE):  # 威逼要东西即讨要（话题是那件东西），否则缺省打探——由规则推断
            asked = thing if thing and thing.holder_id == person.id else None
            return talk(Aim.ASK if asked else None, asked.name if asked else None)
        if person and has(_DEFUSE):
            return talk(Aim.DEFUSE, default=Approach.WORDS)
        if has(_VERBS[ActionType.ATTACK]):
            known = art.name if art and art.id in scene.player_skills else None
            return PlayerIntent(action_type=ActionType.ATTACK, skill_used=known, approach=_fit(ActionType.ATTACK, approach),
                                target_entity=person.name if person else rest(ActionType.ATTACK))
        if has(_VERBS[ActionType.TAKE]):
            held = thing is not None and any(c.id == thing.holder_id for c in scene.characters)
            manner = _fit(ActionType.TAKE, approach, held=held)
            if held and manner is Approach.PLAIN and has(_ASKING):
                manner = Approach.WORDS  # 开口要人家的东西，就是言辞讨要
            return PlayerIntent(action_type=ActionType.TAKE, target_entity=thing.name if thing else rest(ActionType.TAKE),
                                approach=manner)
        if way or heading:
            return errand()
        if any(v in going for v in _VERBS[ActionType.MOVE]):  # 去处不在出路里：照抄原话交给规则驳回，驳回的移动不入账，此行所为也就无从谈起
            return PlayerIntent(action_type=ActionType.MOVE, target_entity=rest(ActionType.MOVE))
        if has(_VERBS[ActionType.THINK]):  # 别的动作都没命中，才是沉思：「回想龚光杰那一剑」不是找他说话
            return PlayerIntent(action_type=ActionType.THINK)
        if person:
            return talk()
        return PlayerIntent(action_type=ActionType.OBSERVE)
