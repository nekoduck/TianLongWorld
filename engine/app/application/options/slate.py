"""
[INPUT]: 依赖 domain/rules 的 adjudicate / Approval，依赖 domain/intent 的 ActionType，依赖 domain/models 的 Attitude（仇人只认敌视），
         依赖 domain/progression 的 Vitality，依赖 domain/snapshot 的 LocalSnapshot，依赖 options/sources 的 ActionOption / candidates，
         依赖 options/phrasing 的 render / learn_phrase，依赖 options/salience 的 Scored / score，依赖 options/preview 的 why / risk；PlayerState 仅作类型标注
[OUTPUT]: 对外提供 OptionGenerator（(玩家状态, 快照) → 3~4 个跟着剧情走的合法「招」，每个带 why 与风险档）
[POS]: options 包的菜单（Slate）：候选源 → rules.adjudicate 过滤 → 措辞 → 显著性 → 席位与 MMR → 预览。P0 的席位全部保留：
         伤重而四下无敌设调养席；仇人在侧设脱身席（与 rules._retreat 同理：先走来路，绝不逃回险地）；
         上一招的对象（focus[0] 且 focus_fresh）仍在场设跟进席（牵涉他的最显著一条）；其余席位按 MMR 取满（同动作、同对象各折 2 分，与已选各席累加），
         凑不足 min 席才补调息（轻伤）与静观；平手一律按 id。P1 加一道 MMR 之外的硬上限：同一对象（anchor）至多 per_target 席（缺省 2），
         免得一个人的七种招挤满菜单。去重按意图：同一意图只留先到的候选（线索的「换个手段」先于寻常版本）；每条线索只留第一条获准的手段。
       没有版本号取模：同一个世界（状态与快照除版本外都相同）必得同一份菜单，逐字不变——措辞变体也按意图哈希挑。
       选项从不经大模型，因而不可能出现图谱里不存在的东西；它是 (状态, 快照) 的纯函数：服务端在玩家点选时按当前快照重算一遍即可核验，
       无需缓存、天然防伪造。「合法」不等于「安全」：向绝顶高手出手照样是选项，风险档只说最坏能坏到哪一步，不泄露胜负
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from app.application.options import preview
from app.application.options.phrasing import learn_phrase, render
from app.application.options.salience import Scored, score
from app.application.options.sources import ActionOption, candidates
from app.domain import rules
from app.domain.intent import ActionType, Aim
from app.domain.models import Attitude
from app.domain.progression import Vitality
from app.domain.snapshot import LocalSnapshot

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState

class OptionGenerator:
    def __init__(self, *, max_options: int = 4, min_options: int = 3, per_target: int = 2) -> None:
        self._max = max_options
        self._min = min_options
        self._per_target = per_target

    def generate(self, state: PlayerState, snap: LocalSnapshot) -> tuple[ActionOption, ...]:
        if not state.alive:
            return ()
        foes = {c.id for c in snap.characters if c.attitude is Attitude.HOSTILE and not c.subdued}
        main, filler = self._gather(state, snap, foes)
        picked: list[Scored] = []
        seats: dict[str, str] = {}  # 选项 id → 席位缘由
        held: Counter[str] = Counter()  # 对象 → 已占席位

        def take(s: Scored, reason: str = "") -> None:
            picked.append(s)
            held[s.anchor] += 1
            if reason:
                seats[s.option.id] = reason

        def open_(s: Scored) -> bool:
            return s not in picked and held[s.anchor] < self._per_target

        heal = [s for s in main if s.action is ActionType.REST]  # 能进 main 的调息必是重伤以上；仇人在侧时规则已驳回
        if heal:  # 调养席：伤重而四下无敌，调息永远看得见
            take(heal[0], "伤重宜调息")
        flight = [s for s in main if s.action is ActionType.MOVE and s.approval.target not in state.fled_from] if foes else []
        if flight:  # 脱身席：仇人在侧，退路永远看得见——与 rules._retreat 同理，先走来路，绝不逃回险地
            take(min(flight, key=lambda s: (s.approval.target != state.came_from, *s.rank())), "仇人在侧，先脱身")
        lead = state.focus[0] if state.focus and state.focus_fresh else None
        if lead and (snap.character(lead) or snap.item(lead)):  # 跟进席：上一招的对象仍在眼前
            follow = [s for s in main if lead in s.subjects and open_(s)]
            if follow:
                take(min(follow, key=lambda s: s.rank()))
        while len(picked) < self._max:  # 其余席位：MMR，近似的选项不扎堆；同一对象至多 per_target 席
            rest = [s for s in main if open_(s)]
            if not rest:
                break
            take(min(rest, key=lambda s: s.rank(picked)))
        for extra in sorted(filler, key=lambda s: (s.action is ActionType.OBSERVE, s.action is not ActionType.REST, s.option.id)):
            if len(picked) >= self._min:
                break
            if open_(extra):
                take(extra)
        return tuple(
            s.option.model_copy(update={
                "why": seats.get(s.option.id) or preview.why(s, state, snap, foes),
                "risk": preview.risk(s.option.intent, state, snap),
            })
            for s in picked
        )

    @staticmethod
    def _gather(state: PlayerState, snap: LocalSnapshot, foes: set[str]) -> tuple[list[Scored], list[Scored]]:
        """候选 → 裁决过滤 → 措辞 → 打分；分进正席（main）与补位（filler：轻伤时的调息与疗伤之药、静观）。"""
        main: list[Scored] = []
        filler: list[Scored] = []
        seen: set[str] = set()  # 意图哈希：同一意图只留先到的候选
        pursued: set[tuple[str, Aim]] = set()  # 已出过「换个手段」的线索
        for cand in candidates(state, snap):
            digest = ActionOption.digest(cand.intent)
            if digest in seen or (cand.thread is not None and cand.thread.key in pursued):
                continue
            verdict = rules.adjudicate(cand.intent, state, snap)
            if not isinstance(verdict, rules.Approval) or (cand.route is not None and verdict.route is not cand.route):
                continue
            seen.add(digest)
            if cand.thread is not None:
                pursued.add(cand.thread.key)
            key, slots = learn_phrase(dict(cand.slots)["art"], verdict, snap) if cand.key == "learn" else (cand.key, cand.slots)
            option = ActionOption.of(cand.category, render(key, slots, digest), cand.intent)
            scored = score(option, verdict, state, foes, cand.thread)
            match cand.intent.action_type:
                case ActionType.REST | ActionType.USE if state.vitality is Vitality.HALE:
                    continue  # 安然无恙：气血差几分也不值得占一席，更不值得吃掉一份药
                case ActionType.REST | ActionType.USE if state.vitality is Vitality.HURT:
                    filler.append(scored)
                case ActionType.OBSERVE:
                    filler.append(scored)
                case _:
                    main.append(scored)
        return main, filler


__all__ = ["OptionGenerator"]
