"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / field_validator，依赖 enum 的 StrEnum
[OUTPUT]: 对外提供 ActionType（含 REST 调息、USE 使用随身之物与 INVALID 的系统指令集）、Approach 手段（寻常 / 武力 / 言辞 / 人情 / 计谋 / 潜行 / 借势）、
          Aim 所图（制人 / 夺物 / 脱身 / 求艺 / 打探 / 结交 / 化解 / 讨要 / 示警）、PlayerIntent（结构化意图：动作 + 指称 + 手段 + 所图 + 话题）
[POS]: domain 的命令语言——玩家的华丽描写被降维后的唯一形状。定义在 domain 而非 application：
       裁决规则（rules/）与聚合根消费它，领域层不得反向依赖应用层；application/intent_parser.py 负责产出它。
       手段与所图只是"玩家想怎么做、图什么"的说法：(动作, 手段) 合不合兼容表、所图缺省怎么推断，由 domain/approach.py 规整（rules 门面在裁决前调用）；
       topic 与其余指称一样保留原话、超长截断、空串置空，落不了地由规则置之不理
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

_REF_CHARS = 24
_STYLE_CHARS = 24
_REASON_CHARS = 80


class ActionType(StrEnum):
    MOVE = "MOVE"  # 沿出口移动（CONNECTS_TO）
    OBSERVE = "OBSERVE"  # 静观：纯查询，不产生事件
    TALK = "TALK"  # 与在场之人交谈
    ATTACK = "ATTACK"  # 出手（可带武学与兵器）
    TAKE = "TAKE"  # 取物：地上之物，或已被制住之人身上之物
    GIVE = "GIVE"  # 赠物：把随身之物交给在场之人
    LEARN = "LEARN"  # 修习武学：未入门则求门径（师传 / 自悟），已入门则精进（名师点拨 / 参照典籍 / 闭门苦练）
    REST = "REST"  # 调息疗伤：恢复气血
    USE = "USE"  # 使用随身之物：服药、敷药
    INVALID = "INVALID"  # 违背世界观或无法落地的操作


class Approach(StrEnum):
    """手段：同一个动作可以怎么做。寻常即不带特别手段（旧意图一律如此）。"""

    PLAIN = "寻常"
    FORCE = "武力"
    WORDS = "言辞"
    FAVOR = "人情"
    GUILE = "计谋"
    STEALTH = "潜行"
    LEVERAGE = "借势"


class Aim(StrEnum):
    """所图：这一举想换来什么。None 即没说，由 approach.py 按动作与人情推断。"""

    SUBDUE = "制人"
    SEIZE = "夺物"
    ESCAPE = "脱身"
    LEARN = "求艺"
    PROBE = "打探"
    BEFRIEND = "结交"
    DEFUSE = "化解"
    ASK = "讨要"
    WARN = "示警"


class PlayerIntent(BaseModel):
    """
    意图只是"玩家想做什么"的指称，不是"世界里有什么"的断言：target_entity 等字段保留玩家的说法，
    能否落地为图谱实体由裁决规则判定。narrative_style 只影响笔墨，永不影响裁决。
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    action_type: ActionType
    target_entity: str | None = None
    item_used: str | None = None
    skill_used: str | None = None
    narrative_style: str = ""
    reason: str | None = None  # 仅 INVALID：驳回理由，呈现给玩家
    approach: Approach = Approach.PLAIN
    aim: Aim | None = None
    topic: str | None = None  # 话题指称（人 / 物 / 功 / 地 / 见闻），落不了地即置之不理

    @field_validator("target_entity", "item_used", "skill_used", "topic", mode="before")
    @classmethod
    def _ref(cls, value: Any) -> str | None:
        # 大模型偶尔写空串或超长指称：规整而非拒收——拒收会让整回合因一个修饰词失败
        text = str(value).strip() if value is not None else ""
        return text[:_REF_CHARS] or None

    @field_validator("narrative_style", mode="before")
    @classmethod
    def _style(cls, value: Any) -> str:
        return (str(value).strip() if value is not None else "")[:_STYLE_CHARS]

    @field_validator("reason", mode="before")
    @classmethod
    def _reason(cls, value: Any) -> str | None:
        text = str(value).strip() if value is not None else ""
        return text[:_REASON_CHARS] or None

    @classmethod
    def invalid(cls, reason: str) -> "PlayerIntent":
        return cls(action_type=ActionType.INVALID, reason=reason)
