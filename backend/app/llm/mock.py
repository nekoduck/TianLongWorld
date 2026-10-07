"""
[INPUT]: 依赖 director/prompts.py 的 read_section / read_directive，依赖 app.lore 的 SHICHEN / GRANDMASTERS，依赖 llm/base.py 的 JsonSchema，
         依赖 app.schemas 的 PlayerState / NextState / DirectorOutput / PlayerDelta / TagDelta / SecretDelta / LocalDelta / WorldEvent / Options
[OUTPUT]: 对外提供 MockLLM —— 实现 LLMClient 协议的离线导演
[POS]: llm 包的零密钥替身：像真实大模型一样只"阅读"提示词协议标签（player_state / opening_seed / player_action / directive）并产出合规 JSON，让整条管线无需 API Key 即可端到端运行；"偷听"上报一条私密情报，"烧/毁"上报一条以当前地点为标签、旁观者视角的世界大事，走通情报隔离与 JIT 记忆；
       回合场景与选项是被动沙盒的样板——只有环境的自然反馈，没有冲着玩家来的目光与巧合
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import asyncio
import random

from app.director import prompts
from app.lore import GRANDMASTERS, SHICHEN
from app.llm.base import JsonSchema
from app.schemas import (
    DirectorOutput,
    LocalDelta,
    NextState,
    Options,
    PlayerDelta,
    PlayerState,
    SecretDelta,
    TagDelta,
    WorldEvent,
)

# ============================================================
#  素材库
# ============================================================
_WEATHERS = ("晴", "阴", "微雨", "大雾", "疾风", "细雪")

# 被动沙盒的样板：只有环境的自然反馈与各忙各的路人，没有冲着玩家来的目光、尾随与恰好听见的秘闻
_SCENES = (
    "{location}的风轻轻吹过，四下只有虫鸣与远处的犬吠。路边茶棚的老汉支着下巴打盹，"
    "几个挑担的货郎各走各路，谁也没多看你一眼。脚边泥地里半埋着一块锈迹斑斑的铁片。",
    "你在{location}慢慢转了一圈。街角的面摊冒着热气，摊主正跟熟客抱怨今年的米价；"
    "墙根下两个孩童蹲着斗蟋蟀，吵得不可开交，见你走近，抱起竹笼便跑开了。",
    "{weather}之中，{location}行人稀少。一辆牛车吱呀呀碾过泥路，车夫哼着走调的小曲，"
    "转过山坳便不见了。道旁的野草被车辙压弯，又慢慢直了起来。",
    "天色渐变，{location}的炊烟一缕缕升起。远处传来几声更鼓，近处只有你自己的脚步声，"
    "和偶尔从檐下扑棱飞起的麻雀。",
)

_OPTIONS = (
    Options(A="四下看看，留意动静", B="找人搭话，打听近况", C="离开此地，换个去处"),
    Options(A="拾起地上之物细看", B="向摊主讨碗热汤", C="趁天色未晚赶路"),
    Options(A="寻个角落歇脚，恢复体力", B="向路人讨教此地门道", C="去更远处闯荡一番"),
)

# 三条确定性规则，让离线模式也走通三种记账：说"拾/捡"就捡到铁牌（标签账），说"偷听"就听到一桩秘密（私密情报账），
# 说"烧/毁"就毁掉此地（世界台账）——私密与公开各归其位
_FOUND_ITEM = "锈蚀铁牌"
_FOUND_WORDS = ("拾", "捡")
_SECRET = "听见有人约在三更的杏子林碰头"
_SECRET_WORDS = ("偷听",)
_RAZE_WORDS = ("烧", "毁")

_EXECUTION = (
    "你话音未落，{killer}连眼皮都未抬，只随手一挥——{signature}！"
    "一股排山倒海的劲力当胸撞来，你眼前一黑，整个人如断线纸鸢般倒飞出去，重重摔在地上，再也没能起来。"
    "江湖很大，可惜你的故事，到此为止。"
)


class MockLLM:
    def __init__(self, *, latency: float = 0.0, rng: random.Random | None = None):
        self._latency = latency  # 模拟推演耗时，让前端的缓冲提示可见
        self._rng = rng or random.Random()

    async def complete(self, system: str, user: str, schema: JsonSchema | None = None) -> str:
        if self._latency:
            await asyncio.sleep(self._latency)
        directive = prompts.read_directive(user)
        # 只取玩家快照：标签账与世界台账由服务端记账，导演（含 Mock）只上报增减与新增大事
        player = PlayerState.model_validate_json(prompts.read_section(user, "player_state"))
        state = NextState(
            location=player.location, time=player.time, weather=player.weather, health_status=player.health_status
        )

        match directive.get("kind"):
            case "opening":
                out = self._opening(state, prompts.read_section(user, "opening_seed"))
            case "lethal":
                out = self._execution(state, directive["killer"], directive["signature"])
            case _:
                out = self._wander(state, prompts.read_section(user, "player_action"))
        return out.model_dump_json()

    # ------------------------------------------------------------------
    def _opening(self, state: NextState, premise: str) -> DirectorOutput:
        # 像真实导演一样写破在场者：开局种子里点到的绝顶高手即进入视野（系统已登记，重复写出也只记一次）
        arrived = [m.name for m in GRANDMASTERS if m.mentioned_in(premise)]
        return DirectorOutput(
            scene_description=premise,
            game_over=False,
            options=self._rng.choice(_OPTIONS),
            next_state=state,
            local_delta=LocalDelta(arrived=arrived),
        )

    def _execution(self, state: NextState, killer: str, signature: str) -> DirectorOutput:
        scene = _EXECUTION.format(killer=killer, signature=signature)
        dead = state.model_copy(update={"health_status": f"中{signature}，气绝身亡"})
        return DirectorOutput(scene_description=scene, game_over=True, options=None, next_state=dead)

    def _wander(self, state: NextState, action: str) -> DirectorOutput:
        found = any(word in action for word in _FOUND_WORDS)
        overheard = any(word in action for word in _SECRET_WORDS)
        razed = any(word in action for word in _RAZE_WORDS)
        weather = self._rng.choice(_WEATHERS)
        scene = self._rng.choice(_SCENES).format(location=state.location, weather=weather)
        place = state.location[:20]  # 标签是 Label（≤20 字）
        # 旁观者视角：世人看得见的是这场火，看不见是谁放的——真相若是暗中所为，属于玩家的 secrets
        events = [WorldEvent(tags=[place], event_desc=f"{place}突起大火，烧成一片焦土")] if razed else []
        next_state = state.model_copy(
            update={"time": _next_shichen(state.time), "weather": weather, "major_events": events}
        )
        return DirectorOutput(
            scene_description=scene,
            game_over=False,
            options=self._rng.choice(_OPTIONS),
            next_state=next_state,
            player_delta=PlayerDelta(
                inventory=TagDelta(add=[_FOUND_ITEM] if found else []),
                secrets=SecretDelta(add=[_SECRET] if overheard else []),
            ),
            # local_delta 缺省为空：Mock 世界里无人进出，在场者由服务端照旧记着
        )


def _next_shichen(time: str) -> str:
    return SHICHEN[(SHICHEN.index(time) + 1) % len(SHICHEN)] if time in SHICHEN else time
