"""
[INPUT]: 依赖 pydantic v2 的 BaseModel / Field，依赖 domain/intent 的 ActionType / PlayerIntent
[OUTPUT]: 对外提供 世界时间的刻度 TICKS_PER_DAY / TICKS_PER_SHICHEN / SPAWN_TICK、TIME_COSTS 各动作耗时表、NEARBY_MOVE / REFUSED_COST、
          Command（意图 + 必填的 time_cost）、time_cost()（动作 × 获准与否 × 去处是否同一处所 → 刻数）、same_place()（两地是否同一处所）、
          day_of() / time_label()（「第一日·辰正」）/ daylight()（卯时至酉时为昼）
[POS]: domain 的「时间是第一物理量」：玩家的每一条命令都花时间——攀谈、静观、沉思也不例外——世界据此走动（application/world_clock 把 time_cost 绑定成心跳）。
       精神时光屋的病根是"行动不花时间"：仇人面前连练十回功、离开一处再回来一切如初、消息从不出门。
       一刻是世界的最小心跳（十五分钟），一日九十六刻；耗时由封闭表定，与措辞无关；被驳回的命令只花一刻——你试过、没成，也花了时间
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from pydantic import BaseModel, ConfigDict, Field

from app.domain.intent import ActionType, PlayerIntent

# ============================================================
#  刻度 —— 一刻十五分钟，一个时辰八刻，一日九十六刻
# ============================================================
TICKS_PER_DAY = 96
TICKS_PER_SHICHEN = 8
SPAWN_TICK = 32  # 投胎在第一日辰正（八点）：原著开篇，比剑正要开场

NEARBY_MOVE = 1  # 同一处所之内走动（剑湖宫 ↔ 剑湖宫·练武厅）
REFUSED_COST = 1  # 被驳回的命令

TIME_COSTS: dict[ActionType, int] = {
    ActionType.MOVE: 4,  # 换一处所，一个时辰的脚程
    ActionType.OBSERVE: 1,
    ActionType.THINK: 1,
    ActionType.TALK: 1,
    ActionType.ATTACK: 1,
    ActionType.TAKE: 1,
    ActionType.GIVE: 1,
    ActionType.USE: 1,
    ActionType.LEARN: 8,  # 修习一回，一个时辰
    ActionType.REST: 8,  # 调息一回，一个时辰
    ActionType.INVALID: REFUSED_COST,
}


class Command(BaseModel):
    """一条已裁定的玩家命令：意图之外必须写明它花多少刻——没有不花时间的命令。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    intent: PlayerIntent
    time_cost: int = Field(ge=1, le=TICKS_PER_DAY)


def same_place(here: str, there: str) -> bool:
    """两地同属一处所：「上级·处所」的上级相同（剑湖宫与剑湖宫·练武厅），或本就同名。"""
    return here.split("·", 1)[0] == there.split("·", 1)[0]


def time_cost(action: ActionType, *, approved: bool = True, here: str = "", there: str | None = None) -> int:
    """耗时查封闭表：驳回只花一刻；移动到同一处所之内只花一刻（there 是去处的名字）。"""
    if not approved:
        return REFUSED_COST
    if action is ActionType.MOVE and there is not None and same_place(here, there):
        return NEARBY_MOVE
    return TIME_COSTS[action]


# ============================================================
#  报时 —— 十二时辰、初正、刻；tick 0 是第一日子正（零点）
# ============================================================
_SHICHEN = "子丑寅卯辰巳午未申酉戌亥"
_KE = ("", "一刻", "二刻", "三刻")
_DIGITS = "零一二三四五六七八九"


def _numeral(n: int) -> str:
    """一百以内的中文数字：一、十、十一、二十、九十九。"""
    tens, ones = divmod(n, 10)
    if tens == 0:
        return _DIGITS[ones]
    return f"{'' if tens == 1 else _DIGITS[tens]}十{_DIGITS[ones] if ones else ''}"


def day_of(tick: int) -> int:
    """第几日（从 0 起）：黎明的生态按它逐日结算。"""
    return tick // TICKS_PER_DAY


def time_label(tick: int) -> str:
    """「第一日·辰正」「第二日·子初三刻」：时辰从前一日的子初（二十三点）起算，每个时辰分初、正，各四刻。"""
    k = (tick % TICKS_PER_DAY + TICKS_PER_SHICHEN // 2) % TICKS_PER_DAY
    within = k % TICKS_PER_SHICHEN
    half = "初" if within < TICKS_PER_SHICHEN // 2 else "正"
    day = day_of(tick) + 1
    return f"第{_numeral(day) if day < 100 else day}日·{_SHICHEN[k // TICKS_PER_SHICHEN]}{half}{_KE[within % 4]}"


def daylight(tick: int) -> bool:
    """卯时到酉时（五点到十九点）为昼：夜里的人与事，叙事要写得出夜色。"""
    hour = (tick % TICKS_PER_DAY) // 4
    return 5 <= hour < 19
