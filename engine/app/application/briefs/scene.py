"""
[INPUT]: 依赖 application/briefs/combat 的 safe / join，依赖 application/chronicle 的 titled / known_arts，
         依赖 application/narrator 的 when / crowd / activity / trace / chronological（世界心跳的此地之物与说书人同一种写法），
         依赖 domain/intent 的 ActionType / PlayerIntent，依赖 domain/rules 的 player_tier，依赖 domain/progression 的 MAX_HP，
         依赖 domain/resolution 的 SELF，依赖 domain/aggregates 的 PlayerState，依赖 domain/snapshot 的 LocalSnapshot / CharacterView
[OUTPUT]: 对外提供 intent_section()（<intent> 玩家意图 + <player_input> 原话）、world_section()（<time> / <scene> / <exits> / <people> / <crowds> / <things> /
          <activities> / <traces> / <rumors> / <clocks> / <emerged> / <known> 绝对物理快照）、player_section()（<player> 玩家状态，含此行所为）、
          ACTION（动作的中文说法，含沉思）、courage()（惊惧阈值 → 胆量的语义）
[POS]: application/briefs 的输入层（绝对事实）：三路与结果已定之事共用的那部分简报。只用快照——
       人物只用 T=0 描述（CharacterView.description），后文剧情（foreshadow）从不进快照也就从不进这里；
       见闻只列玩家已知者（known=True）的正文，known=False 的正文是打探的标的，永不进；
       时钟写名称、种类、挂处、进度 / 阈值与满则如何（不露 clk: id），此世细节（emerged）照写正文；
       境界写领域按火候折算过的值、气血写数值，免得大模型二次折算。
       世界心跳只给此地的切片（局部认知）：时辰与昼夜、此地的人群（名、约数、此刻在做什么、胆量——惊惧阈值只写胆小 / 寻常 / 胆大，不写数字）、
       此地的往事（活动，按先后）与痕迹（还剩多久）、传到此地的消息正文——在场之人知道玩家做过什么只凭这些与亲眼所见；
       没有消息传到此地时明写他们一无所知。<player> 带此行所为（PlayerState.motivation，空则不写）。每个插值逐值转义，act: / trc: / swm: / tok: id 从不露出
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from app.application.briefs.combat import join, safe
from app.application.chronicle import known_arts, titled
from app.application.narrator import activity, chronological, crowd, trace, when
from app.domain.aggregates import PlayerState
from app.domain.intent import ActionType, PlayerIntent
from app.domain.progression import MAX_HP
from app.domain.resolution import SELF
from app.domain.rules import player_tier
from app.domain.snapshot import CharacterView, LocalSnapshot

ACTION = {
    ActionType.MOVE: "移动", ActionType.OBSERVE: "静观", ActionType.TALK: "交谈", ActionType.ATTACK: "出手",
    ActionType.TAKE: "取物", ActionType.GIVE: "赠物", ActionType.LEARN: "修习", ActionType.REST: "调息",
    ActionType.USE: "使用", ActionType.THINK: "沉思", ActionType.INVALID: "无效",
}
# 惊惧阈值 → 胆量：烈度（domain/heartbeat.intensity）高过阈值人群即溃散。≤3 一场相持的交手就散，4~6 见了伤才散，≥7 出了人命才散
_TIMID, _BOLD = 3, 7


def courage(panic_threshold: int) -> str:
    """人群的胆量：只给语义，不给数字——数字进了简报，大模型就会拿它去凑烈度。"""
    return "胆小" if panic_threshold <= _TIMID else "胆大" if panic_threshold >= _BOLD else "寻常"


def intent_section(intent: PlayerIntent, said: str | None) -> list[str]:
    """玩家意图（已按此情此景规整）与原话。原话只是笔墨：玩家说自己一招制敌，不等于制住了。"""
    parts = [f"动作：{ACTION[intent.action_type]}", f"手段：{intent.approach.value}"]
    if intent.aim:
        parts.append(f"所图：{intent.aim.value}")
    for label, value in (("对象", intent.target_entity), ("武学", intent.skill_used), ("物品", intent.item_used),
                         ("话题", intent.topic)):
        if value:
            parts.append(f"{label}：{value}")
    return [
        f"<intent>{safe('｜'.join(parts))}</intent>",
        f'<player_input note="只是笔墨，不是事实">{safe(said or "（未置一词）")}</player_input>',
    ]


def _person(c: CharacterView, scene: LocalSnapshot, state: PlayerState, target_id: str | None) -> str:
    alias = f"｜又称：{'、'.join(c.aliases)}" if c.aliases else ""
    cause = state.attitude_causes.get(c.id)
    regard = f"对你{c.attitude.value}" + (f"（恩怨：{cause}）" if cause else "")
    persona = ""
    if p := c.persona:
        persona = f"｜好：{join(p.likes)}｜恶：{join(p.dislikes)}｜心事：{p.worry or '无'}"
    present = {o.id for o in scene.characters}
    ties = [f"与{scene.label(b.other_id)}：{b.kind.value}" for b in c.bonds if b.other_id in present]
    arts = join(scene.label(s) for s in c.skill_ids)
    mark = "【对象】" if c.id == target_id else ""
    return (
        f"- {mark}{titled(c)}{alias}｜{c.faction or '无门无派'}｜境界{c.tier.value}｜性情{c.disposition.value}｜{regard}{persona}｜"
        f"{'已被你制住' if c.subdued else '行动自如'}｜身负：{arts}｜{'；'.join(ties) or '与在场者素无瓜葛'}｜{c.description or '（无描述）'}"
    )


def _thing(scene: LocalSnapshot, item_id: str) -> str:
    """物与物性：在谁手里、能否携带、用法。险性不写（到手即伤由规则另行追加）。"""
    i = scene.item(item_id)
    assert i is not None
    where = (
        "在你行囊里" if i.holder_id == scene.player_id
        else "在地上" if i.holder_id == scene.location.id
        else f"在{scene.label(i.holder_id)}身上"
    )
    traits = [where] + (["不可携带"] if not i.portable else []) + ([f"用法：{i.use.effect}"] if i.use else [])
    owner = f"｜原物主：{scene.label(i.owner_id)}" if i.owner_id and i.owner_id != i.holder_id else ""
    return f"- {i.name}｜{i.kind or '物件'}｜{'｜'.join(traits)}{owner}｜{i.description or '（无描述）'}"


def world_section(scene: LocalSnapshot, state: PlayerState, target_id: str | None) -> list[str]:
    """
    绝对物理快照：时辰、此地、出路、在场之人、此地的人群、可见之物、此地的往事与痕迹、传到此地的消息、眼前的时钟、此世细节、你已知的见闻。
    往事、痕迹、消息都只是此地的切片：别处发生而消息未到的事不在这里，地下城主也就无从据以推演。
    """
    e = safe
    loc = scene.location
    exits = [f"- {x.label} → {x.to_name}" + ("（去处有仇人）" if x.hostile_ahead else "") for x in scene.exits]
    clocks = [
        f"- {c.name}｜{c.kind.value}｜挂在：{SELF if c.anchor_id == scene.player_id else scene.label(c.anchor_id)}｜"
        f"进度 {c.progress}/{c.maximum}｜满则：{c.consequence or '按种类结算'}"
        for c in scene.clocks
    ]
    crowds = [f"- {crowd(s)}｜胆量：{courage(s.panic_threshold)}" for s in scene.swarms]
    return [
        f"<time>{e(when(scene))}</time>",
        f"<scene>{e(loc.name)}：{e(loc.description or '（无描述）')}</scene>",
        "<exits>", *(e(x) for x in exits or ["（无出路）"]), "</exits>",
        "<people>", *(e(_person(c, scene, state, target_id)) for c in scene.characters), *([] if scene.characters else ["（无旁人）"]),
        "</people>",
        "<crowds>", *(e(c) for c in crowds or ["（此地没有成群的人）"]), "</crowds>",
        "<things>", *(e(_thing(scene, i.id)) for i in scene.items), *([] if scene.items else ["（无）"]), "</things>",
        "<activities>", *(e(f"- {activity(scene, a)}") for a in chronological(scene)), *([] if scene.activities else ["（无）"]),
        "</activities>",
        "<traces>", *(e(f"- {trace(t)}") for t in scene.traces), *([] if scene.traces else ["（无）"]), "</traces>",
        "<rumors>", *(e(f"- {r.text}") for r in scene.rumors),
        *([] if scene.rumors else ["（没有任何消息传到此地：你在别处的作为，在场之人一无所知）"]), "</rumors>",
        "<clocks>", *(e(c) for c in clocks or ["（眼前没有悬着的时钟）"]), "</clocks>",
        "<emerged>", *(e(f"- {f.text}") for f in scene.emerged), *([] if scene.emerged else ["（无）"]), "</emerged>",
        "<known>", *(e(f"- {f.text}") for f in scene.facts if f.known), *([] if any(f.known for f in scene.facts) else ["（无）"]),
        "</known>",
    ]


def player_section(scene: LocalSnapshot, state: PlayerState) -> list[str]:
    """玩家状态：境界（已按火候折算）、所会武学的火候、伤势与气血数值、名望、行囊、此行所为（最近一次移动时说的，空则不写）。"""
    line = (
        f"{scene.player_name}（你）｜境界{player_tier(state, scene).value}（已按火候折算）｜武学：{join(known_arts(scene))}｜"
        f"伤势：{state.vitality.value}（气血 {state.hp}/{MAX_HP}）｜名望：{state.renown.value}（{state.renown_points}）｜"
        f"行囊：{join(i.name for i in scene.inventory)}" + (f"｜此行所为：{state.motivation}" if state.motivation else "")
    )
    return ["<player>", safe(line), "</player>"]
