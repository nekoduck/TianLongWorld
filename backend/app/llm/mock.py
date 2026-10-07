"""
[INPUT]: 依赖 director/prompts.py 的 read_section / read_directive，依赖 director/lore.py 的 SHICHEN / GRANDMASTERS，依赖 llm/base.py 的 JsonSchema，依赖 app.schemas 的 DirectorOutput / WorldState / Options
[OUTPUT]: 对外提供 MockLLM —— 实现 LLMClient 协议的离线导演
[POS]: llm 包的零密钥替身：像真实大模型一样"阅读"提示词协议标签并产出合规 JSON，让整条管线无需 API Key 即可端到端运行
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

import asyncio
import random

from app.director import prompts
from app.director.lore import GRANDMASTERS, SHICHEN
from app.llm.base import JsonSchema
from app.schemas import DirectorOutput, Options, WorldState

# ============================================================
#  素材库
# ============================================================
_WEATHERS = ("晴", "阴", "微雨", "大雾", "疾风", "细雪")

_SCENES = (
    "{location}的风忽然停了，四下静得只听得见自己的心跳。暗处似有一道目光掠过你的后颈，转瞬即逝。"
    "脚边泥地里半埋着一枚铁牌，刻着古怪的纹路，泛着冷光——像是某个门派的信物，又像是一道催命符。",
    "动作未完，远处传来一阵急促的马蹄声，几名江湖客打马而过，其中一人回头深深看了你一眼，嘴角挂着冷笑。"
    "尘土落定，路边茶棚的老汉压低声音道：“客官，这几日{location}不太平，入夜莫要乱走。”",
    "一切比预想的顺利，却顺利得有些古怪。{location}的人群里，一个戴斗笠的瘦高汉子始终与你隔着十来步，"
    "你停他也停，你走他也走。他腰间的刀鞘是空的，刀却不知藏在何处。",
    "天色骤变，{weather}之中，前方破庙里亮起一点火光，隐约有人在争吵，提到了“易筋经”三个字。"
    "随即一声闷响，争吵戛然而止，火光也灭了。你分明闻到了一丝血腥气。",
)

_OPTIONS = (
    Options(A="躲在暗处，静观其变", B="旁敲侧击，打探消息", C="孤注一掷，抢先出手"),
    Options(A="拾起地上之物细看", B="跟上去，看个究竟", C="拦住来人，当面质问"),
    Options(A="寻个角落歇脚，恢复体力", B="向路人讨教此地门道", C="闯入险地，搏一场机缘"),
)

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
        state = WorldState.model_validate_json(prompts.read_section(user, "world_state"))

        match directive.get("kind"):
            case "opening":
                out = self._opening(state, prompts.read_section(user, "opening_seed"))
            case "lethal":
                out = self._execution(state, directive["killer"], directive["signature"])
            case _:
                out = self._wander(state, _read_present(user))
        return out.model_dump_json()

    # ------------------------------------------------------------------
    def _opening(self, state: WorldState, premise: str) -> DirectorOutput:
        # 像真实导演一样写出在场名单：开局种子里点到的绝顶高手即在场
        present = [m.name for m in GRANDMASTERS if m.mentioned_in(premise)]
        return DirectorOutput(
            scene_description=premise,
            game_over=False,
            options=self._rng.choice(_OPTIONS),
            next_state=state,
            present=present,
        )

    def _execution(self, state: WorldState, killer: str, signature: str) -> DirectorOutput:
        scene = _EXECUTION.format(killer=killer, signature=signature)
        dead = state.model_copy(update={"physical_state": f"中{signature}，气绝身亡"})
        return DirectorOutput(scene_description=scene, game_over=True, options=None, next_state=dead)

    def _wander(self, state: WorldState, present: list[str]) -> DirectorOutput:
        weather = self._rng.choice(_WEATHERS)
        scene = self._rng.choice(_SCENES).format(location=state.location, weather=weather)
        next_state = state.model_copy(update={"time": _next_shichen(state.time), "weather": weather})
        return DirectorOutput(
            scene_description=scene,
            game_over=False,
            options=self._rng.choice(_OPTIONS),
            next_state=next_state,
            present=present,  # Mock 世界里无人离场
        )


def _read_present(prompt: str) -> list[str]:
    names = prompts.read_section(prompt, "present")
    return [] if names in ("", "（无）") else names.split("、")


def _next_shichen(time: str) -> str:
    return SHICHEN[(SHICHEN.index(time) + 1) % len(SHICHEN)] if time in SHICHEN else time
