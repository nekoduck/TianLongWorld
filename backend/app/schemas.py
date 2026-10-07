"""
[INPUT]: 依赖 pydantic 的 BaseModel / StringConstraints / model_validator
[OUTPUT]: 对外提供 PlayerSnapshot、PlayerState、LEDGERS、WorldEvent、WorldState、GameState、MAX_TAGS、
          Options、InteractRequest、TagDelta / PlayerDelta / LocalDelta / NextState、DirectorOutput（含 DIRECTOR_SCHEMA）、
          InteractResponse、NewSessionResponse
[POS]: app 的前后端协议与大模型输出契约，是全系统唯一的数据形状来源（前端 types.ts 与之镜像）
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, model_validator

# 语义标签：去空白、非空、封顶长度 —— 既挡住客户端塞入超长 Prompt，也约束大模型的输出漂移
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
OptionText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]  # 人名、物品、武学、状态
EventDesc = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]

MAX_TAGS = 16  # 每本玩家标签账的上限

TagList = Annotated[list[Label], Field(max_length=MAX_TAGS)]


# ============================================================
#  玩家状态 —— 没有数值，只有标签
#  两种生命周期分开存放：快照由大模型每回合整体重写；四本标签账由服务端记账，只认增减
# ============================================================
class PlayerSnapshot(BaseModel):
    location: Tag
    time: Tag
    weather: Tag
    health_status: Tag  # 生命体征：健康、轻伤、重伤濒死……


class PlayerState(PlayerSnapshot):
    buffs_debuffs: TagList = []  # 中毒、内力枯竭、致盲……战斗判定的关键
    social_traits: TagList = []  # 门派、称号、性格、与核心 NPC 的恩怨
    inventory: TagList = []
    martial_arts: TagList = []


# 由服务端记账的四本标签账：快照之外的一切玩家清单都在这里登记
LEDGERS = ("buffs_debuffs", "social_traits", "inventory", "martial_arts")


# ============================================================
#  世界状态 —— 平行世界的大事记：带实体标签的原子事实，只增不删、不设上限
#  台账可以无限增长，喂给大模型的却永远只是按标签筛出的几条（见 director/memory.py）
# ============================================================
class WorldEvent(BaseModel):
    tags: list[Label] = Field(
        min_length=1,
        max_length=6,
        description="事件涉及的实体名词：地点、人物、门派、物品，如 聚贤庄、游氏双雄、丐帮；检索靠它们命中",
    )
    event_desc: EventDesc = Field(description="一句话原子事实，如：玩家在聚贤庄大战中烧毁了正厅，游氏双雄战死")


class WorldState(BaseModel):
    major_events: list[WorldEvent] = []


class GameState(BaseModel):
    """current_state / next_state 的完整树：玩家 + 世界。"""

    player_state: PlayerState
    world_state: WorldState = WorldState()

    def status_bar(self) -> str:
        """ui_status_bar 是 next_state 的纯投影，由服务端确定性渲染，绝不交给大模型生成。"""
        p = self.player_state
        return " | ".join((
            f"【位置：{p.location}】",
            f"【时辰：{p.time}】",
            f"【身份：{', '.join(p.social_traits) or '无名小卒'}】",
            f"【状态：{', '.join([p.health_status, *p.buffs_debuffs])}】",
            f"【武学：{', '.join(p.martial_arts) or '不会武功'}】",
            f"【行囊：{', '.join(p.inventory) or '空无一物'}】",
        ))


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
    current_state: GameState
    action_type: Literal["choice", "custom"]
    action_text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


# ============================================================
#  大模型契约 —— 导演必须产出的结构（不含 ui_status_bar）
#  一切清单只以增减上报，服务端据此记账：遗漏不等于失去；世界大事只追加
# ============================================================
class TagDelta(BaseModel):
    add: list[Label] = Field(default=[], max_length=8, description="本回合新增的标签；清单里已有的不要重复写")
    remove: list[Label] = Field(default=[], max_length=8, description="本回合确实失去的标签，名称照抄清单；没写进来的一律保留")


class PlayerDelta(BaseModel):
    buffs_debuffs: TagDelta = Field(default=TagDelta(), description="中毒、受伤致残、内力枯竭写 add；痊愈解毒写 remove")
    social_traits: TagDelta = Field(default=TagDelta(), description="拜入门派、得到称号、结下恩怨写 add；被逐、和解写 remove")
    inventory: TagDelta = Field(default=TagDelta(), description="获得写 add；用掉、遗失、被夺、赠予、丢弃、损毁写 remove")
    martial_arts: TagDelta = Field(default=TagDelta(), description="学会写 add；被废、遗忘写 remove")


class LocalDelta(BaseModel):
    """局部环境（在场 NPC）的增减：同一地图内遗漏不等于离场；换了地图由服务端强制清空，只留新地点的到场者。"""

    arrived: list[Label] = Field(
        default=[],
        max_length=12,
        description="本回合进入视野、有名有姓者的真实姓名（叙事含蓄也要写破，如 乔峰）；换了地图则写出新地点的全部在场者",
    )
    departed: list[Label] = Field(default=[], max_length=12, description="本回合离开视野者，名字照抄 local_environment")


class NextState(PlayerSnapshot):
    """导演提议的下一刻：快照四字段整体重写；major_events 只写本回合新发生的大事，由服务端追加进世界台账。"""

    major_events: list[WorldEvent] = Field(
        default=[],
        max_length=3,
        description="仅本回合新发生的重大变故（NPC 死亡、地标被毁、剧情节点）；没有则为空数组，绝不抄写已有的历史",
    )


class DirectorOutput(BaseModel):
    scene_description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=800)]
    game_over: bool
    options: Options | None = None
    # 快照四字段 + 本回合新增大事：大模型从结构上就无法整体改写标签账，也无法改写或删除已有的世界台账
    next_state: NextState
    player_delta: PlayerDelta = PlayerDelta()
    # 服务端内部字段，不进前端协议：叙事可以只写"那魁梧大汉"，此处必须写"乔峰"，致死预判据此判定在场
    local_delta: LocalDelta = LocalDelta()

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
    next_state: GameState

    @classmethod
    def of(cls, state: GameState, out: DirectorOutput) -> "InteractResponse":
        """state 是服务端记账后的完整状态，out 只贡献叙事与选项；内部字段（局部环境、增减）不外泄。"""
        return cls(
            ui_status_bar=state.status_bar(),
            scene_description=out.scene_description,
            game_over=out.game_over,
            options=None if out.game_over else out.options,
            next_state=state,
        )


class NewSessionResponse(InteractResponse):
    session_id: UUID
