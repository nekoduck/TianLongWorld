"""
[INPUT]: 依赖 pydantic 的 BaseModel / StringConstraints / model_validator
[OUTPUT]: 对外提供 StateSnapshot、WorldState、MAX_ITEMS、Options、InteractRequest、DirectorOutput（含 DIRECTOR_SCHEMA）、InteractResponse、NewSessionResponse
[POS]: app 的前后端协议与大模型输出契约，是全系统唯一的数据形状来源（前端 types.ts 与之镜像）
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, model_validator

# 语义标签：去空白、非空、封顶长度 —— 既挡住客户端塞入超长 Prompt，也约束大模型的输出漂移
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
OptionText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]  # 人名、物品名

MAX_ITEMS = 16


# ============================================================
#  世界状态 —— 没有数值，只有标签
#  两种生命周期分开存放：快照由大模型每回合整体重写；随身物品由服务端记账，只认增减
# ============================================================
class StateSnapshot(BaseModel):
    location: Tag
    time: Tag
    weather: Tag
    physical_state: Tag  # 身体状况：伤病、饥寒、疲惫；不含物品


class WorldState(StateSnapshot):
    inventory: list[Label] = Field(default_factory=list, max_length=MAX_ITEMS)

    def status_bar(self) -> str:
        """ui_status_bar 是 next_state 的纯投影，由服务端确定性渲染，绝不交给大模型生成。"""
        return (
            f"【位置：{self.location}】 | 【时辰：{self.time}】 | "
            f"【天气：{self.weather}】 | 【状态：{self.physical_state}】 | "
            f"【行囊：{', '.join(self.inventory) or '空无一物'}】"
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
    next_state: StateSnapshot  # 不含 inventory：大模型从结构上就无法整体改写随身物品
    # 物品只以增减上报，服务端据此记账；未上报失去的物品一律保留——遗漏不等于失去
    items_gained: list[Label] = Field(
        default_factory=list,
        max_length=8,
        description="本回合新得到的物品；随身清单里已有的不要重复写；没有则为空数组",
    )
    items_lost: list[Label] = Field(
        default_factory=list,
        max_length=8,
        description="本回合确实离身的物品（用尽、被夺、丢弃、赠予、损毁），名称照抄随身清单；没有则为空数组",
    )
    # 服务端内部字段，不进前端协议：叙事可以只写"那魁梧大汉"，此处必须写"乔峰"，致死预判据此判定在场
    present: list[Label] = Field(
        default_factory=list,
        max_length=12,
        description="此刻在场、有名有姓的人物真实姓名，即使叙述中未点破身份也要写出；无人则为空数组",
    )

    @model_validator(mode="after")
    def _alive_needs_options(self) -> "DirectorOutput":
        if not self.game_over and self.options is None:
            raise ValueError("玩家存活时必须给出 A/B/C 三个选项")
        return self


# 导演契约的 JSON Schema：支持结构化输出的大模型据此从采样层面约束格式
DIRECTOR_SCHEMA = DirectorOutput.model_json_schema()


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
    def of(cls, state: WorldState, out: DirectorOutput) -> "InteractResponse":
        """state 是服务端记账后的完整状态，out 只贡献叙事与选项；内部字段（present、物品增减）不外泄。"""
        return cls(
            ui_status_bar=state.status_bar(),
            scene_description=out.scene_description,
            game_over=out.game_over,
            options=None if out.game_over else out.options,
            next_state=state,
        )


class NewSessionResponse(InteractResponse):
    session_id: UUID
