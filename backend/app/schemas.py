"""
[INPUT]: 依赖 pydantic 的 BaseModel / ConfigDict / Field / StringConstraints / model_validator
[OUTPUT]: 对外提供 Frozen 基类；协议模型 PlayerSnapshot、PlayerState、WorldEvent、WorldState、GameState、Options、
          NewSessionRequest、InteractRequest、InteractResponse、NewSessionResponse；
          大模型契约 TagDelta、PlayerDelta、LocalDelta、DirectorOutput 与 DIRECTOR_SCHEMA；
          常量 LEDGERS、Ledger、MAX_TAGS、NO_TAG_CHANGE、NO_PLAYER_CHANGE、NO_LOCAL_CHANGE
[POS]: app 的数据契约唯一来源：前后端协议（前端 types.ts 逐字段镜像）与大模型结构化输出契约同处一处；
       所有模型冻结且禁止多余字段，集合一律用元组——数据不可变，状态只能经由纯函数产生新值
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


class Frozen(BaseModel):
    """全系统数据模型的基类：冻结（不可变）+ 禁止多余字段（严格契约，schema 带 additionalProperties: false）。"""

    model_config = ConfigDict(frozen=True, extra="forbid")


# 语义标签：去空白、非空、封顶长度 —— 既挡住超长输入，也约束大模型的输出漂移
Tag = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]  # 人名、物品、武学、状态
OptionText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
EventDesc = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
Scene = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=800)]
ActionText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
ActionType = Literal["choice", "custom"]

MAX_TAGS = 16
Labels = Annotated[tuple[Label, ...], Field(max_length=MAX_TAGS)]

# 由服务端记账的四本标签账：快照之外的一切玩家清单都在这里登记
Ledger = Literal["buffs_debuffs", "social_traits", "inventory", "martial_arts"]
LEDGERS: tuple[Ledger, ...] = ("buffs_debuffs", "social_traits", "inventory", "martial_arts")


# ============================================================
#  玩家状态 —— 没有数值，只有标签
#  快照由大模型每回合整体提议、经运行时校验后采纳；四本标签账只认增减
# ============================================================
class PlayerSnapshot(Frozen):
    location: Tag
    time: Tag  # 十二时辰之一，运行时校验
    weather: Tag
    health_status: Tag  # 生命体征：健康、轻伤、重伤濒死……


class PlayerState(PlayerSnapshot):
    buffs_debuffs: Labels = ()  # 中毒、内力枯竭、致盲……战斗判定的关键
    social_traits: Labels = ()  # 门派、称号、性格、与核心 NPC 的恩怨
    inventory: Labels = ()
    martial_arts: Labels = ()


# ============================================================
#  世界状态 —— 平行世界的大事记：只追加的原子事实，跨投胎延续
# ============================================================
class WorldEvent(Frozen):
    tags: Annotated[
        tuple[Label, ...],
        Field(min_length=1, max_length=6, description="实体标签：发生地点与涉及的人物、门派，供检索路由"),
    ]
    event_desc: Annotated[EventDesc, Field(description="一句话不可逆的原子事实，如：聚贤庄被玩家付之一炬")]


class WorldState(Frozen):
    major_events: tuple[WorldEvent, ...] = ()  # 无上限：靠 JIT 检索控制上下文，而非销毁历史


class GameState(Frozen):
    """current_state / next_state 的完整树：玩家 + 世界。"""

    player_state: PlayerState
    world_state: WorldState = WorldState()

    def status_bar(self) -> str:
        """ui_status_bar 是状态树的纯投影，由服务端确定性渲染，绝不交给大模型生成。"""
        p = self.player_state
        return " | ".join((
            f"【位置：{p.location}】",
            f"【时辰：{p.time}】",
            f"【身份：{', '.join(p.social_traits) or '无名小卒'}】",
            f"【状态：{', '.join((p.health_status, *p.buffs_debuffs))}】",
            f"【武学：{', '.join(p.martial_arts) or '不会武功'}】",
            f"【行囊：{', '.join(p.inventory) or '空无一物'}】",
        ))


class Options(Frozen):
    A: Annotated[OptionText, Field(description="浅层交互：旁观、观察、搜刮")]
    B: Annotated[OptionText, Field(description="中层交互：试探、交涉、解谜")]
    C: Annotated[OptionText, Field(description="深层交互：铤而走险、破局")]

    @model_validator(mode="after")
    def _three_dimensions(self) -> "Options":
        if len({self.A, self.B, self.C}) < 3:
            raise ValueError("A/B/C 必须是三个不同的选项")
        return self


# ============================================================
#  请求 —— Frontend -> Backend
# ============================================================
class NewSessionRequest(Frozen):
    world_id: UUID | None = None  # 携带则在该世界重新投胎（世界大事延续）；缺省则开辟新世界


class InteractRequest(Frozen):
    session_id: UUID
    current_state: GameState  # 客户端视图回显：服务端以事件日志投影为唯一权威，从不采信
    action_type: ActionType
    action_text: ActionText


# ============================================================
#  大模型契约 —— 导演只是意图推演器：提议快照、上报增减，一切写入由运行时裁决
#  所有字段必填、禁止多余字段：结构化输出从采样层面杜绝格式漂移
# ============================================================
class TagDelta(Frozen):
    add: Annotated[tuple[Label, ...], Field(max_length=8, description="本回合新增的标签；清单里已有的不要重复写")]
    remove: Annotated[
        tuple[Label, ...], Field(max_length=8, description="本回合确实失去的标签，名称照抄清单；没写进来的一律保留")
    ]


NO_TAG_CHANGE = TagDelta(add=(), remove=())


class PlayerDelta(Frozen):
    buffs_debuffs: Annotated[TagDelta, Field(description="中毒、受伤致残、内力枯竭写 add；痊愈解毒写 remove")]
    social_traits: Annotated[TagDelta, Field(description="拜入门派、得到称号、结下恩怨写 add；被逐、和解写 remove")]
    inventory: Annotated[TagDelta, Field(description="获得写 add；用掉、遗失、被夺、赠予、丢弃、损毁写 remove")]
    martial_arts: Annotated[TagDelta, Field(description="学会写 add；被废、遗忘写 remove")]


NO_PLAYER_CHANGE = PlayerDelta(
    buffs_debuffs=NO_TAG_CHANGE, social_traits=NO_TAG_CHANGE, inventory=NO_TAG_CHANGE, martial_arts=NO_TAG_CHANGE
)


class LocalDelta(Frozen):
    arrived: Annotated[
        tuple[Label, ...],
        Field(max_length=12, description="本回合进入视野、有名有姓的人物真实姓名（叙述含蓄也要写破）；换地图后写新地点的全部在场者"),
    ]
    departed: Annotated[tuple[Label, ...], Field(max_length=12, description="本回合离开视野的人物，名称照抄局部环境")]


NO_LOCAL_CHANGE = LocalDelta(arrived=(), departed=())


class DirectorOutput(Frozen):
    scene_description: Scene
    game_over: bool
    options: Options | None
    next_state: Annotated[PlayerSnapshot, Field(description="提议的快照：地点、时辰、天气、生命体征")]
    player_delta: PlayerDelta
    local_delta: LocalDelta
    world_events: Annotated[
        tuple[WorldEvent, ...],
        Field(max_length=3, description="玩家本回合引发的不可逆大事（只追加的原子事实）；没有则为空数组"),
    ]

    @model_validator(mode="after")
    def _alive_needs_options(self) -> "DirectorOutput":
        if not self.game_over and self.options is None:
            raise ValueError("玩家存活时必须给出 A/B/C 三个选项")
        return self


# 导演契约的 JSON Schema：各厂商客户端据此配置严格的结构化输出约束
DIRECTOR_SCHEMA = DirectorOutput.model_json_schema()


# ============================================================
#  响应 —— Backend -> Frontend（双轨：UI 数据 + 文学描写）
# ============================================================
class InteractResponse(Frozen):
    ui_status_bar: str
    scene_description: str
    game_over: bool
    options: Options | None = Field(description="死者没有选择：game_over 为 true 时恒为 null")
    next_state: GameState

    @classmethod
    def render(cls, state: GameState, scene: str, options: Options | None, game_over: bool) -> "InteractResponse":
        """响应是投影后状态树的渲染：内部意图（增减、在场变动）一律不外泄。"""
        return cls(
            ui_status_bar=state.status_bar(),
            scene_description=scene,
            game_over=game_over,
            options=None if game_over else options,
            next_state=state,
        )


class NewSessionResponse(InteractResponse):
    session_id: UUID
    world_id: UUID
