# rules/
> L2 | 父级: engine/app/domain/CLAUDE.md

裁决核心（Decider）：纯函数，不做 IO、不调大模型、不看时钟。物理校验看快照（出口、在场者、物在谁手），逻辑校验看玩家状态与本体（火候、根基、门径、谁肯传授、伤势）；
能力成长、物品获取、人际变化只能由此处从图谱拓扑推导。胜负未定之事（出手）由 Rule.stakes 圈出可裁区间，地下城主的提议经 combat.settle 钳进区间后才成为事件。
每种动作一条 Rule（开闭：新动作 = 新 Rule + 在 __init__.RULES 注册一行；新的模糊动作 = 覆写 stakes 钩子）。人情一律比 Attitude.rank，不比值。
P1 由单文件拆成包以守住 800 行上限；门面不变——拆包前从 app.domain.rules 能导入的一切，拆包后照样能导入。

成员清单
__init__.py: 门面——RULES 注册表（十种动作各一条，含 USE）、adjudicate（合法性）/ stakes（可裁区间，驳回与确定之事为 None）/ decide（意图 → 定案 → 事件：驳回落为 ActionFailed 并带上 unlock 与玩家当时的所图 aim、手段 approach，提议经 settle 定案）；原样转出 base / physical / talk / martial 的公开名字，_retreat 是 retreat 的旧名
base.py: 地基——Rejection（code + reason + unlock 怎样才行）/ Approval（指称已落地：target / item / skill / exit_label / source / guidance）/ Verdict；resolve 名称落地（精确优先、包含须唯一、单字不猜，称号同样落得到本名）；skill_tier / player_tier / best_skill 按火候折算境界；Rule 抽象（adjudicate / stakes 钩子 / consequences）；present 指称落到在场之人；menace 在场、敌视且行动自如的仇人（戒备不算）；names / listed 渲染助手
physical.py: 身体与地理——ObserveRule（静观无事件）、MoveRule（只沿 CONNECTS_TO）、TakeRule（地上之物与被制住者身上之物）、UseRule（服用敷用随身之物：不在行囊 NOT_CARRIED、无用法 NO_USE、疗伤之药无伤可疗 UNHURT；用掉写 ItemConsumed，疗伤另写 HealthChanged(source="item")，每档药力回 REST_GAIN 并钳在上限内）、RestRule（调息须有伤且无仇人在侧，写 HealthChanged(source="rest")）、InvalidRule（违背世界观永不获准）
talk.py: 人与人的确定性两条——TalkRule（Conversed）、GiveRule（赠物即易手；物归原主是信赖封闭清单上唯一的一条：态度低于信赖时直升信赖，RelationChanged.basis = TRUST_RESTORED「物归原主」，阶梯「每次至多一档」的例外）
martial.py: 武的两条——AttackRule（境界差 × 性情 × 伤势圈出可裁区间；定案落为 SkillExecuted + HealthChanged [+ PlayerDied]；人情涟漪沿 HAS_RELATION 一跳、只波及在场目睹者，「敌人之敌 → 好感」暂停待阶段 B 的 reputation 按 era=开篇 恢复；重伤追加沿来路退回的 Moved(fleeing)，逃离过的险地永不作退路；此情此景里没有的武功名只当笔墨）、retreat（重伤逃脱的去处）、LearnRule（入门走获取要求：失传 → 根基 → 典籍 → 地点 → 传承；精进走修炼要求：化境 → 根基 → 名师点拨 / 参照典籍 / 闭门苦练；点名的师父不肯即拒绝而不悄悄改成苦练；门槛全过而仇人在侧即 UNSAFE）、required_regard（求教门槛：三流及不入流须友善，二流及以上须信赖）；驳回理由照实写人情（敌视写明结怨缘由、戒备是提防、漠然是素无交情、友善而功高是交情尚浅），没点名时说的是交情最深的那位，unlock 写明要到哪一档

依赖说明
- 包内单向：base ← physical / talk / martial ← __init__；各模块只在类型标注里引用 aggregates.PlayerState（TYPE_CHECKING），aggregates 运行期导入本包门面，两者互引而不成环
- 裁决只看两样东西：快照（他人与地理的物理事实）与玩家状态（自身的逻辑事实）；回合编排保证二者版本一致后才调用
- 阶段 B 在此加入 social / covert 两路赌注（stakes 钩子返回 AnyStakes）、TakeRule 的物性闸门、LEARN×{言辞, 人情} 遇 UNWILLING 改走交涉、retreat 避开 hostile_ahead

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
