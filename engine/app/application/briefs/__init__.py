"""
[INPUT]: 依赖 briefs/scene 的 intent_section / world_section / player_section，依赖 briefs/physics 的 physics_section / schema / ROUTE，
         依赖 briefs/combat / social / covert 的 <stakes> 段与 MEANING / safe / join，依赖 domain/rules 的 normalized / stakes（重算这一招的赌注细节），
         依赖 domain/resolution 的 Envelope，依赖 domain/combat / social / covert 的赌注，依赖 domain/intent 的 PlayerIntent，
         依赖 domain/aggregates 的 PlayerState，依赖 domain/snapshot 的 LocalSnapshot
[OUTPUT]: 对外提供 brief(env, scene, state, intent, said)（输入层：XML 简报）、schema(env, scene)（输出层：结构化输出契约），
          并转出 MEANING / ROUTE / safe / join
[POS]: application 的地下城主简报包（门面）——语义物理引擎的输入层与输出层：
       输入层是绝对事实：<intent> 玩家意图（按此情此景规整过）与 <player_input> 原话、<time> … <known> 物理快照（含此地的人群、往事、痕迹与传到此地的消息）、<player> 玩家状态（含此行所为）、
       <stakes> 这一招赌的是什么（三路之一；按 rules.stakes 重算，与 Envelope 同出一源）、<physics> 物理边界。
       只用快照里的 T=0 事实：Character.foreshadow 从不进快照，也就从不进简报；玩家尚不知道的见闻正文同样不进；
       全局事件流也不进：在场之人知道玩家做过什么，只凭传到此地的消息与亲眼所见（局部认知）。
       探索迷雾同样守在简报里：出路只写「方位｜去处｜交通方式｜路程」，玩家不认得的去处写「未知区域」，出口标签不进；
       带议程而来的在场者附「来意」（PlayerState.agendas 的意图），撞见的判官（npc_agent）借同一份 world_section。
       简报是地下城主唯一的世界：简报之外的人物物功，推演里一字不提
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from app.application.briefs.combat import MEANING, combat_stakes, join, safe
from app.application.briefs.covert import covert_stakes
from app.application.briefs.physics import ROUTE, physics_section, schema
from app.application.briefs.scene import intent_section, player_section, world_section
from app.application.briefs.social import social_stakes
from app.domain import rules
from app.domain.aggregates import PlayerState
from app.domain.approach import Route
from app.domain.combat import Stakes
from app.domain.covert import CovertStakes
from app.domain.intent import PlayerIntent
from app.domain.resolution import Envelope
from app.domain.snapshot import LocalSnapshot
from app.domain.social import SocialStakes
from app.domain.stakes import AnyStakes

__all__ = ["MEANING", "ROUTE", "brief", "join", "safe", "schema"]


def _stakes_section(at_stake: AnyStakes | None, scene: LocalSnapshot, state: PlayerState) -> list[str]:
    if isinstance(at_stake, Stakes):
        body = combat_stakes(at_stake, scene)
    elif isinstance(at_stake, SocialStakes):
        body = social_stakes(at_stake, scene)
    elif isinstance(at_stake, CovertStakes):
        body = covert_stakes(at_stake, scene, state)
    else:
        return []
    return ["<stakes>", *body, "</stakes>"]


def brief(env: Envelope, scene: LocalSnapshot, state: PlayerState, intent: PlayerIntent, said: str | None) -> str:
    """把一招的绝对事实打包成 XML 简报。赌注细节按 rules.stakes 重算（纯函数，与圈出 env 的是同一次推导），路线不符即不写。"""
    plain = rules.normalized(intent, state, scene)
    at_stake = rules.stakes(plain, state, scene) if env.route is not Route.FIXED else None
    if at_stake is not None and at_stake.target_id != env.target_id:
        at_stake = None
    return "\n".join([
        *intent_section(plain, said),
        *world_section(scene, state, env.target_id),
        *player_section(scene, state),
        *_stakes_section(at_stake, scene, state),
        *physics_section(env, at_stake, scene),
    ])
