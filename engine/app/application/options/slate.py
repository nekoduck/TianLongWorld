"""
[INPUT]: 依赖 domain/rules 的 adjudicate / command / Approval，依赖 domain/approach 的 TacticalAxis，依赖 domain/intent 的 ActionType / Aim，
         依赖 domain/models 的 Attitude（仇人只认敌视），依赖 domain/progression 的 Vitality，依赖 domain/snapshot 的 LocalSnapshot，
         依赖 options/sources 的 ActionOption / candidates / digest，依赖 options/phrasing 的 render / learn_phrase，依赖 options/salience 的 Scored / score，
         依赖 options/preview 的 why / risk；PlayerState 仅作类型标注
[OUTPUT]: 对外提供 OptionGenerator：affordances()（全部获准的非移动之招，打过分、按分排序——点选核验用，不封顶）、
          catalogue()（给说书人的可供性目录：至多 catalogue_size 招，按战术轴轮转取各轴高分者，每根有招的轴都在）、
          generate()（退路菜单 3~4 席：说书人没交菜单或交得不够时用，也是 compose 补位的来源）
[POS]: options 包的菜单（Slate）：候选源 → rules.adjudicate 过滤 → rules.command 定标准指令 → 措辞 → 显著性 → 三种出口。
       意图风味封装之后菜单分两层：说书人从 catalogue 里挑 3~4 招、配上武侠风味（options/menu.compose 过闸），挑不出或挑得不够就由 generate 的退路补；
       三者同出一个打过分的池子，catalogue 与 generate 都是 affordances 的子集——玩家点选时服务端按当前状态与快照重算 affordances ∪ 导航，
       按 id 取回 underlying_command，伪造与过期的 id 自然落空，无需缓存。
       退路菜单保留 P0 的席位：伤重而四下无敌设调养席；上一招的对象（focus[0] 且 focus_fresh）仍在场设跟进席（牵涉他的最显著一条）；
       其余席位按 MMR 取满（同动作、同对象、同战术轴各折 2 分，与已选各席累加——退路也尽量铺开不同的轴），凑不足 min 席才补调息（轻伤）与静观。
       脱身席随移动一起归了导航（application/navigation 的 retreat 标记），菜单里不再有出路。
       同一对象至多 per_target 席（MMR 之外的硬上限）；去重按规整后的意图：同一条标准指令只留先到的候选（线索的「换个手段」先于寻常版本），
       每条线索只留第一条获准的手段。没有版本号取模：同一个世界（状态与快照除版本外都相同）必得同一份菜单与同一份目录，逐字不变。
       选项的指令从不经大模型，因而不可能出现图谱里不存在的东西；「合法」不等于「安全」：向绝顶高手出手照样是选项，风险档只说最坏能坏到哪一步
[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from app.application.options import preview
from app.application.options.phrasing import learn_phrase, render
from app.application.options.salience import Scored, score
from app.application.options.sources import ActionOption, candidates, digest
from app.domain import rules
from app.domain.approach import TacticalAxis
from app.domain.intent import ActionType, Aim
from app.domain.models import Attitude
from app.domain.progression import Vitality
from app.domain.snapshot import LocalSnapshot

if TYPE_CHECKING:
    from app.domain.aggregates import PlayerState

CATALOGUE_SIZE = 12  # 可供性目录的上限：说书人只在这些招里挑


class _Pool:
    """一回合的候选池：正席（main）与补位（filler：轻伤时的调息与疗伤之药、静观），都已裁决、定好指令、打过分。"""

    def __init__(self, state: PlayerState, snap: LocalSnapshot) -> None:
        self.state = state
        self.snap = snap
        self.foes = {c.id for c in snap.characters if c.attitude is Attitude.HOSTILE and not c.subdued}
        self.main: list[Scored] = []
        self.filler: list[Scored] = []
        self._gather()

    @property
    def everything(self) -> list[Scored]:
        """全部可供之招，按分排序（分高在前，平手按 id）。"""
        return sorted((*self.main, *self.filler), key=lambda s: s.rank())

    def finish(self, s: Scored, reason: str = "") -> ActionOption:
        """盖上 why（席位的缘由优先）与风险档。"""
        return s.option.model_copy(update={
            "why": reason or preview.why(s, self.state, self.snap, self.foes),
            "risk": preview.risk(s.option.intent, self.state, self.snap),
        })

    def _gather(self) -> None:
        """候选 → 裁决过滤 → 标准指令 → 措辞 → 打分；分进正席与补位。"""
        state, snap = self.state, self.snap
        seen: set[str] = set()  # 规整后意图的摘要：同一条标准指令只留先到的候选
        pursued: set[tuple[str, Aim]] = set()  # 已出过「换个手段」的线索
        for cand in candidates(state, snap):
            if digest(cand.intent) in seen or (cand.thread is not None and cand.thread.key in pursued):
                continue
            verdict = rules.adjudicate(cand.intent, state, snap)
            if not isinstance(verdict, rules.Approval) or (cand.route is not None and verdict.route is not cand.route):
                continue
            command = rules.command(verdict.intent, state, snap)
            key = digest(command.intent)
            if key in seen:
                continue
            seen |= {key, digest(cand.intent)}
            if cand.thread is not None:
                pursued.add(cand.thread.key)
            phrase, slots = learn_phrase(dict(cand.slots)["art"], verdict, snap) if cand.key == "learn" else (cand.key, cand.slots)
            option = ActionOption.of(cand.category, render(phrase, slots, key), command)
            scored = score(option, verdict, state, cand.thread)
            match command.intent.action_type:
                case ActionType.REST | ActionType.USE if state.vitality is Vitality.HALE:
                    continue  # 安然无恙：气血差几分也不值得占一席，更不值得吃掉一份药
                case ActionType.REST | ActionType.USE if state.vitality is Vitality.HURT:
                    self.filler.append(scored)
                case ActionType.OBSERVE:
                    self.filler.append(scored)
                case _:
                    self.main.append(scored)


class OptionGenerator:
    def __init__(
        self, *, max_options: int = 4, min_options: int = 3, per_target: int = 2, catalogue_size: int = CATALOGUE_SIZE
    ) -> None:
        self._max = max_options
        self._min = min_options
        self._per_target = per_target
        self._catalogue = catalogue_size

    # ------------------------------------------------------------
    #  全部可供之招 —— 点选核验的那张表
    # ------------------------------------------------------------
    def affordances(self, state: PlayerState, snap: LocalSnapshot) -> tuple[ActionOption, ...]:
        """全部获准的非移动之招，打过分、按分排序（不封顶）：catalogue 与 generate 都从这里取，点选按 id 在这里核验。"""
        if not state.alive:
            return ()
        pool = _Pool(state, snap)
        return tuple(pool.finish(s) for s in pool.everything)

    # ------------------------------------------------------------
    #  可供性目录 —— 交给说书人挑
    # ------------------------------------------------------------
    def catalogue(self, state: PlayerState, snap: LocalSnapshot) -> tuple[ActionOption, ...]:
        """
        至多 catalogue_size 招：按战术轴轮转，每轮从每根轴各取一招该轴剩下的最高分者（轴按各自最高分排先后，平手按枚举次序），
        取满为止——每根有招的轴都在目录里，说书人才挑得出不同的轴。目录按分排序（m1 最显著）。
        """
        if not state.alive:
            return ()
        pool = _Pool(state, snap)
        queues: dict[TacticalAxis, list[Scored]] = {}
        for s in pool.everything:
            queues.setdefault(s.axis, []).append(s)
        order = sorted(queues, key=lambda axis: (queues[axis][0].rank(), list(TacticalAxis).index(axis)))
        picked: list[Scored] = []
        while len(picked) < self._catalogue and any(queues.values()):
            for axis in order:
                if queues[axis] and len(picked) < self._catalogue:
                    picked.append(queues[axis].pop(0))
        return tuple(pool.finish(s) for s in sorted(picked, key=lambda s: s.rank()))

    # ------------------------------------------------------------
    #  退路菜单 —— 说书人没交菜单时照旧下发
    # ------------------------------------------------------------
    def generate(self, state: PlayerState, snap: LocalSnapshot) -> tuple[ActionOption, ...]:
        if not state.alive:
            return ()
        pool = _Pool(state, snap)
        main, filler = pool.main, pool.filler
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

        heal = sorted((s for s in main if s.action is ActionType.REST), key=lambda s: s.rank())
        if heal:  # 调养席：伤重而四下无敌，调息永远看得见（能进 main 的调息必是重伤以上；仇人在侧时规则已驳回）
            take(heal[0], "伤重宜调息")
        lead = state.focus[0] if state.focus and state.focus_fresh else None
        if lead and (snap.character(lead) or snap.item(lead)):  # 跟进席：上一招的对象仍在眼前
            follow = [s for s in main if lead in s.subjects and open_(s)]
            if follow:
                take(min(follow, key=lambda s: s.rank()))
        while len(picked) < self._max:  # 其余席位：MMR，近似的选项不扎堆、尽量铺开不同的轴；同一对象至多 per_target 席
            rest = [s for s in main if open_(s)]
            if not rest:
                break
            take(min(rest, key=lambda s: s.rank(picked)))
        for extra in sorted(filler, key=lambda s: (s.action is ActionType.OBSERVE, s.action is not ActionType.REST, s.option.id)):
            if len(picked) >= self._min:
                break
            if open_(extra):
                take(extra)
        return tuple(pool.finish(s, seats.get(s.option.id, "")) for s in picked)


__all__ = ["CATALOGUE_SIZE", "OptionGenerator"]
