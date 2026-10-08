"""
[INPUT]: 依赖 application/ports 的 LLMClient，依赖 domain/intent 的 ActionType / PlayerIntent，依赖 domain/snapshot 的 LocalSnapshot
[OUTPUT]: 对外提供 WorldviewGuard（违背世界观的确定性词表守卫）、IntentParser 抽象（模板方法：守卫 → 解读 → 再守卫）、
          LLMIntentParser（结构化输出 + 重采样 + 兜底 INVALID）、HeuristicIntentParser（离线关键词解析）、INTENT_SYSTEM / scene_vocabulary()
[POS]: application 的命令侧入口（CQRS 的 Command 解析）：把玩家的华丽武侠描写降维为系统可识别的 PlayerIntent。
       三道防线拦截热兵器与法术：①词表守卫在调用大模型之前直接判 INVALID（省钱且不可被话术绕过）；
       ②提示词要求大模型对违背世界观的内容判 INVALID；③即便大模型被说服，裁决规则也只认图谱里存在的实体——AK47 不在任何人的行囊里。
       解析器只产出"想做什么"，从不判断"能不能做"：后者是 domain/rules 的职权。
       场景词表随渐进式状态而丰富：人物带称号与别名（喊「恶贯满盈」也能规整为段延庆），已会武学带火候；
       LEARN 一个动作涵盖入门与精进，REST 调息疗伤是独立动作——"练功疗伤"以疗伤为准
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Protocol

from pydantic import ValidationError

from app.application.ports import LLMClient
from app.domain.intent import ActionType, PlayerIntent
from app.domain.snapshot import LocalSnapshot

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
    def __init__(self, lexicon: Iterable[str] = ANACHRONISMS) -> None:
        self._lexicon = tuple(word.lower() for word in lexicon)

    def violation(self, *texts: str | None) -> str | None:
        """返回第一个越界的词；全部合乎世界观返回 None。"""
        for text in texts:
            lowered = (text or "").lower()
            if hit := next((word for word in self._lexicon if word in lowered), None):
                return hit
        return None


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
        if word := self._guard.violation(text):
            return PlayerIntent.invalid(f"「{word}」不属于这个江湖。")
        intent = await self._interpret(text, scene)
        if word := self._guard.violation(intent.target_entity, intent.item_used, intent.skill_used):
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

    known = set(scene.player_skills)
    lines = [
        f"所在：{scene.location.name}",
        f"出路：{'、'.join(f'{e.label}→{e.to_name}' for e in scene.exits) or '无'}",
        f"在场之人：{names(scene.characters)}",
        f"可见之物：{names(i for i in scene.items if i.holder_id != scene.player_id)}",
        f"随身之物：{names(scene.inventory)}",
        f"你已会的武学：{practiced()}",
        f"此地可闻的武学：{names(s for s in scene.skills if s.id not in known)}",
    ]
    return _safe("\n".join(lines))


INTENT_SCHEMA = PlayerIntent.model_json_schema()

INTENT_SYSTEM = """你是《天龙八部》文字世界的意图解析器。玩家会用华丽的武侠笔墨描述自己的举动，你要把它降维为一条系统指令，只输出 JSON。

action_type 只能取以下之一：
- MOVE：沿某条出路去往别处。target_entity 写出路名或目的地名。
- OBSERVE：观望、等待、倾听、闭目养神等不改变任何事物的举动。
- TALK：与某人说话、打听、拜见。target_entity 写那人。
- ATTACK：向某人出手。target_entity 写那人；skill_used 只在玩家点名所用武功时填写；item_used 只在玩家点名所用兵器时填写。
- TAKE：拿取某物。target_entity 写那件东西。
- GIVE：把随身之物交给某人。target_entity 写那人，item_used 写那件东西。
- LEARN：修习、求教、参悟、练功、苦练某门武功——初学乍练与已会之后的精进都算。skill_used 写那门武功；若向某人求教，target_entity 写那人。
- REST：调息、疗伤、打坐、运功疗伤、歇息养伤，为的是恢复伤势。一句话里既练功又疗伤，以疗伤为准。
- INVALID：举动违背这个北宋武侠世界的世界观——热兵器与现代器物（枪械、炸弹、手机、汽车……）、魔法法术、
  修仙异能、元游戏指令（存档、作弊、修改数值、索要无敌）——或根本无法理解。reason 用一句话写明为何不合天道。

解析铁律：
1. 你只解析"想做什么"，不判断"做不做得成"。玩家想打绝顶高手，就是 ATTACK；成败由世界引擎裁决。
2. 指称以 <scene_vocabulary> 为准：玩家用代称或绰号指场上的人、物、路时，规整为表中的正名；
   玩家点名的东西不在表中，照抄玩家的原话，绝不替换成表里别的东西，更不编造新名字。
3. 一句话里有多个动作，取最主要、最先发生的那一个。
4. narrative_style 用二到八个字概括玩家的笔墨风格（如「潇洒从容」「阴狠毒辣」「恭敬谦卑」），它只影响叙事，不影响裁决。
5. 只输出符合 schema 的 JSON 对象，不要解释。

示例：
玩家：「我施展凌波微步，飘然向北而去」（出路有 北上→无量山）
{"action_type": "MOVE", "target_entity": "北上", "item_used": null, "skill_used": null, "narrative_style": "飘逸潇洒", "reason": null}
玩家：「掏出手枪对准那大汉扣动扳机」
{"action_type": "INVALID", "target_entity": null, "item_used": null, "skill_used": null, "narrative_style": "", "reason": "北宋江湖没有手枪"}
玩家：「恭恭敬敬向段王爷请教一阳指」
{"action_type": "LEARN", "target_entity": "段正淳", "item_used": null, "skill_used": "一阳指", "narrative_style": "恭敬谦卑", "reason": null}
玩家：「寻个僻静处盘膝坐下，运功疗伤」
{"action_type": "REST", "target_entity": null, "item_used": null, "skill_used": null, "narrative_style": "沉静", "reason": null}"""


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
#  离线解析器 —— 无大模型时的确定性兜底：动词 + 场景词表
# ============================================================
_VERBS: dict[ActionType, tuple[str, ...]] = {
    ActionType.REST: ("调息", "疗伤", "养伤", "打坐"),  # 不收「歇息」「养神」：歇一歇只是静观，不是疗伤
    ActionType.LEARN: ("学", "求教", "参悟", "修习", "研读", "练", "精研"),
    ActionType.GIVE: ("给", "赠", "交还", "送", "奉还", "归还", "递"),
    ActionType.ATTACK: ("攻", "杀", "击", "出手", "偷袭", "刺", "砍", "劈", "揍", "殴打"),  # 不收单字「打」：打听不是动手
    ActionType.TAKE: ("拿", "取", "捡", "拾", "夺"),
    ActionType.MOVE: ("去", "往", "走", "前往", "赶", "进入", "回到", "离开"),
}


def _after_verb(text: str, verbs: Iterable[str]) -> str:
    hits = [(text.find(v), v) for v in verbs if v in text]
    if not hits:
        return ""
    pos, verb = min(hits)
    return text[pos + len(verb) :].strip(" 　，。！？,.!?")[:12]


def _mentioned[T](text: str, views: Iterable[T]) -> T | None:
    return next((v for v in views if any(n in text for n in v.names)), None)  # type: ignore[attr-defined]


class HeuristicIntentParser(IntentParser):
    async def _interpret(self, text: str, scene: LocalSnapshot) -> PlayerIntent:
        def has(action: ActionType) -> bool:
            return any(verb in text for verb in _VERBS[action])

        person = _mentioned(text, scene.characters)
        thing = _mentioned(text, scene.items)
        art = _mentioned(text, scene.skills)
        way = _mentioned(text, scene.exits)
        carried = _mentioned(text, scene.inventory)

        def rest(action: ActionType) -> str | None:
            return _after_verb(text, _VERBS[action]) or None  # 点名的东西不在场景里：照抄原话，交给规则驳回

        if has(ActionType.REST):  # 先于修习判定：「练功疗伤」的本意是疗伤
            return PlayerIntent(action_type=ActionType.REST)
        if has(ActionType.LEARN):
            return PlayerIntent(action_type=ActionType.LEARN, skill_used=art.name if art else rest(ActionType.LEARN),
                                target_entity=person.name if person else None)
        if has(ActionType.GIVE) and person and carried:
            return PlayerIntent(action_type=ActionType.GIVE, target_entity=person.name, item_used=carried.name)
        if has(ActionType.ATTACK):
            known = art.name if art and art.id in scene.player_skills else None
            return PlayerIntent(action_type=ActionType.ATTACK, skill_used=known,
                                target_entity=person.name if person else rest(ActionType.ATTACK))
        if has(ActionType.TAKE):
            return PlayerIntent(action_type=ActionType.TAKE, target_entity=thing.name if thing else rest(ActionType.TAKE))
        if way:
            return PlayerIntent(action_type=ActionType.MOVE, target_entity=way.label)
        if has(ActionType.MOVE):
            return PlayerIntent(action_type=ActionType.MOVE, target_entity=rest(ActionType.MOVE))
        if person:
            return PlayerIntent(action_type=ActionType.TALK, target_entity=person.name)
        return PlayerIntent(action_type=ActionType.OBSERVE)
