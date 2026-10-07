"""
[INPUT]: 依赖 app.schemas 的 DirectorOutput / Options / PlayerSnapshot / LocalDelta / NO_PLAYER_CHANGE，
         依赖 app.engine 的 snapshot_of，依赖 app.lore 的 OpeningSeed
[OUTPUT]: 对外提供 DEFAULT_OPTIONS、STALLED_SCENE、seed_opening()、execution()
[POS]: director 的确定性兜底，容错链的最后一环（约束解码 → 宽容解析 → 带错重采样 → 这里）：
       开局退回种子原文，必死回合退回确定性处决，普通回合由 pipeline 以 STALLED_SCENE 原地停顿、不追加任何事件。
       兜底产物与大模型输出同为 DirectorOutput，走同一条裁决与投影路径——没有第二套落库逻辑；llm/mock.py 复用 execution()
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from app.engine import snapshot_of
from app.lore import OpeningSeed
from app.schemas import NO_PLAYER_CHANGE, DirectorOutput, LocalDelta, Options, PlayerSnapshot

DEFAULT_OPTIONS = Options(A="静观其变，留意四周", B="向旁人打听此地情形", C="孤注一掷，闯出一条路")

# 普通回合的大模型持续幻觉时，世界原地不动：不追加事件、不推进时辰，玩家可以原样再出一招
STALLED_SCENE = "天机混沌，此招未能落定——风声依旧，江湖仍是方才的模样。"

_EXECUTION = (
    "你话音未落，{killer}连眼皮都未抬，只随手一挥——{signature}！"
    "一股排山倒海的劲力当胸撞来，你眼前一黑，整个人如断线纸鸢般倒飞出去，重重摔在地上，再也没能起来。"
    "江湖很大，可惜你的故事，到此为止。"
)


def seed_opening(seed: OpeningSeed) -> DirectorOutput:
    """开局兜底：种子原文即第一幕，开场白里点到的高手即在场者。"""
    return DirectorOutput(
        scene_description=seed.premise,
        game_over=False,
        options=DEFAULT_OPTIONS,
        next_state=snapshot_of(seed.player),
        player_delta=NO_PLAYER_CHANGE,
        local_delta=LocalDelta(arrived=seed.present, departed=()),
        world_events=(),
    )


def execution(snapshot: PlayerSnapshot, killer: str, signature: str) -> DirectorOutput:
    """必死兜底：规则层已判死，大模型失败或抗命都不改变结局，只是换成确定性的处决叙事。"""
    dead = PlayerSnapshot(
        location=snapshot.location,
        time=snapshot.time,
        weather=snapshot.weather,
        health_status=f"中{signature}，气绝身亡",
    )
    return DirectorOutput(
        scene_description=_EXECUTION.format(killer=killer, signature=signature),
        game_over=True,
        options=None,
        next_state=dead,
        player_delta=NO_PLAYER_CHANGE,
        local_delta=LocalDelta(arrived=(), departed=()),
        world_events=(),
    )
