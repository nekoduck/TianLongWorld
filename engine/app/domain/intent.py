"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / field_validator，依赖 enum 的 StrEnum
[OUTPUT]: 对外提供 ActionType（含 REST 调息与 INVALID 的系统指令集）、PlayerIntent（结构化意图）
[POS]: domain 的命令语言——玩家的华丽描写被降维后的唯一形状。定义在 domain 而非 application：
       裁决规则（rules.py）与聚合根消费它，领域层不得反向依赖应用层；application/intent_parser.py 负责产出它
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
    INVALID = "INVALID"  # 违背世界观或无法落地的操作


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

    @field_validator("target_entity", "item_used", "skill_used", mode="before")
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
