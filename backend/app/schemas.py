"""
[INPUT]: 依赖 pydantic 的 BaseModel / StringConstraints / model_validator
[OUTPUT]: 对外提供 WorldState、Options、InteractRequest、DirectorOutput、InteractResponse、NewSessionResponse
[POS]: app 的前后端协议与大模型输出契约，是全系统唯一的数据形状来源（前端 types.ts 与之镜像）
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, model_validator

# 语义标签：去空白、非空、封顶长度 —— 既挡住客户端塞入超长 Prompt，也约束大模型的输出漂移
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
OptionText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


# ============================================================
#  世界状态 —— 没有数值，只有标签
# ============================================================
class WorldState(BaseModel):
    location: Tag
    time: Tag
    weather: Tag
    physical_state: Tag

    def status_bar(self) -> str:
        """ui_status_bar 是 next_state 的纯投影，由服务端确定性渲染，绝不交给大模型生成。"""
        return (
            f"【位置：{self.location}】 | 【时辰：{self.time}】 | "
            f"【天气：{self.weather}】 | 【状态：{self.physical_state}】"
        )


class Options(BaseModel):
    A: OptionText  # 浅层：旁观 / 搜刮
    B: OptionText  # 中层：试探 / 解谜
    C: OptionText  # 深层：铤而走险 / 破局


# ============================================================
#  请求 —— Frontend -> Backend
# ============================================================
class InteractRequest(BaseModel):
    """
    current_state 是客户端快照：会话在服务端存在时以服务端为准（防篡改），
    仅当服务端丢失会话（如进程重启）时用它冷启动恢复。
    """

    session_id: UUID
    current_state: WorldState
    action_type: Literal["choice", "custom"]
    action_text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


# ============================================================
#  大模型契约 —— 导演必须产出的结构（不含 ui_status_bar）
# ============================================================
class DirectorOutput(BaseModel):
    scene_description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=800)]
    game_over: bool
    options: Options | None = None
    next_state: WorldState

    @model_validator(mode="after")
    def _alive_needs_options(self) -> "DirectorOutput":
        if not self.game_over and self.options is None:
            raise ValueError("玩家存活时必须给出 A/B/C 三个选项")
        return self


# ============================================================
#  响应 —— Backend -> Frontend
# ============================================================
class InteractResponse(BaseModel):
    ui_status_bar: str
    scene_description: str
    game_over: bool
    options: Options | None = Field(description="死者没有选择：game_over 为 true 时恒为 null")
    next_state: WorldState

    @classmethod
    def from_director(cls, out: DirectorOutput) -> "InteractResponse":
        return cls(
            ui_status_bar=out.next_state.status_bar(),
            scene_description=out.scene_description,
            game_over=out.game_over,
            options=None if out.game_over else out.options,
            next_state=out.next_state,
        )


class NewSessionResponse(InteractResponse):
    session_id: UUID
