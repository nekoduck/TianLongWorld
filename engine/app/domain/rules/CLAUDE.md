# rules/
> L2 | 父级: engine/app/domain/CLAUDE.md

裁决核心（Decider）：纯函数，不做 IO、不调大模型、不看时钟。物理校验看快照（出口、在场者、物在谁手、物性），逻辑校验看玩家状态与本体（火候、根基、门径、谁肯传授、伤势）；
能力成长、物品获取、人际变化只能由此处从图谱拓扑推导。意图先经 approach.normalize 按兼容表规整，再交给各条 Rule；
胜负未定之事由 Rule.stakes 圈出可裁区间——出手（combat）、交涉（social）、暗中取物（covert）三路之一，地下城主的提议经 stakes.settle_any 钳进区间后才成为事件。
每种动作一条 Rule（开闭：新动作 = 新 Rule + 在 __init__.RULES 注册一行；新的模糊动作 = 覆写 stakes 钩子并在 Approval.route 标明哪一路）。人情一律比 Attitude.rank，不比值。
P1 由单文件拆成包以守住 800 行上限；门面不变——拆包前从 app.domain.rules 能导入的一切，拆包后照样能导入。

成员清单
__init__.py: 门面——RULES 注册表（十种动作各一条，含 USE）、normalized（按此情此景规整意图：TAKE 的物在谁手、话题能否落地）、adjudicate（规整 + 合法性）/ stakes（可裁区间：三路赌注之一，驳回与确定之事为 None）/ decide（意图 → 定案 → 事件：驳回落为 ActionFailed 并带上 unlock、玩家当时的所图 aim、手段 approach 与落了地的对象 / 标的 target_id / subject_id；提议经 settle_any 定案；交与暗两路由 parley.settled 落为事件；险物到手经 physical.handled 追加一次留一口气的伤）；原样转出 base / physical / talk / martial / parley 的公开名字，_retreat 是 retreat 的旧名
base.py: 地基——Rejection（code + reason + unlock 给玩家看的「怎样才行」+ target_id / subject_id 落了地的对象与标的：UNWILLING 是师父与武学、HELD_BY_OTHER 是持有人与物、NOT_PORTABLE 是物）/ Approval（指称已落地：target / item / skill / exit_label / source / guidance，route 走哪一路、aim 推断后的所图、topic 落了地的话题 id）/ Verdict；resolve 名称落地（精确优先、包含须唯一、单字不猜，称号同样落得到本名）；ground 话题落地（在场者、可见之物、可知武学、此地与去处、见闻正文、名称表）；skill_tier / player_tier / best_skill 按火候折算境界；Rule 抽象（adjudicate / stakes 钩子返回 AnyStakes / consequences 收 Ruling）；present 指称落到在场之人；menace 在场、敌视且行动自如的仇人（戒备不算）；names / listed 渲染助手
physical.py: 身体与地理——ObserveRule（静观无事件）、MoveRule（只沿 CONNECTS_TO）、TakeRule（物性闸门：不可携带即 NOT_PORTABLE，unlock「就地察看」；地上之物与被制住者身上之物伸手即得；自由人手中之物按兼容表分路：寻常驳回 HELD_BY_OTHER 并提示「强夺、讨要或暗取」、武力 → 夺物（与出手同一区间，得手即易手）、言辞 / 人情 / 借势 → 讨要、计谋 / 潜行 → 骗取 / 偷取）、handled（这一批里易手到玩家的险物各追加一次 HealthChanged(source="blow", source_id=物)，HAZARD_HURT 封顶、留一口气、已身死不追加）、UseRule（服用敷用随身之物：不在行囊 NOT_CARRIED、无用法 NO_USE、疗伤之药无伤可疗 UNHURT；用掉写 ItemConsumed，疗伤另写 HealthChanged(source="item")，每档药力回 REST_GAIN 并钳在上限内）、RestRule（调息须有伤且无仇人在侧，写 HealthChanged(source="rest")）、InvalidRule（违背世界观永不获准）
talk.py: 人与人——TalkRule（寻常即闲谈 Conversed，带落了地的话题 topic_id；威逼 / 言辞 / 人情 / 套话 / 借势即交涉，所图缺省推断：威逼或带话题打探、敌视戒备化解、其余结交；威逼图不来结交 / 化解 / 求艺）、GiveRule（赠物即易手；物归原主是信赖封闭清单上唯一的一条：态度低于信赖、且那件东西不是从物主本人手里到你身上的（PlayerState.taken_from：偷来、骗来、讨来、夺来、从被制住的他身上取来，转手第三人也洗不白）时直升信赖，RelationChanged.basis = TRUST_RESTORED「物归原主」，阶梯「每次至多一档」的例外）
martial.py: 武——AttackRule（境界差 × 性情 × 伤势圈出可裁区间；计谋出手不越级）、strike_stakes / strike（出手与夺物共用：定案落为 SkillExecuted（带手段）+ HealthChanged [+ PlayerDied]，夺物得手追加易手；人情涟漪交给 reputation.ripple；重伤追加夺路而逃的 Moved(fleeing)）、retreat（先走来路，其次去处没有仇人 hostile_ahead=False 的出路，逃离过的险地永不作退路，条条不通才留在原地）、LearnRule（入门走获取要求：失传 → 根基 → 典籍 → 地点 → 传承；精进走修炼要求：化境 → 根基 → 名师点拨 / 参照典籍 / 闭门苦练；点名的师父不肯即拒绝而不悄悄改成苦练；以言辞 / 人情相求而师父不肯（UNWILLING）改走交涉·求艺——Parleyed 可附人情 +1，从不传功；门槛全过而仇人在侧即 UNSAFE，交涉不受此限）；驳回理由照实写人情（敌视写明结怨缘由、戒备是提防、漠然是素无交情、友善而功高是交情尚浅），没点名时说的是交情最深的那位，unlock 写明要到哪一档
parley.py: 交与暗两路的接线——parley（获准的交涉 → SocialStakes：打探预先选定此人所知、你未知（known=False）、unlock 落得了地的见闻（有话题只取牵涉它的）；讨要须他手里真有那件可携之物，物主所珍难度 +1；求艺看 required_regard 与武学境界的难度，一档之内够不上门槛即无如愿；同一手段求过同一件事算纠缠）、filch（获准的暗取 → CovertStakes：玩家境界对失主境界、失主态度、是否被制住）、settled（交与暗的定案 → 事件）、leverage（筹码由领域确定性选出：借势 = 在场友善以上、与此人有开篇羁绊的靠山 + 已知 LEVERAGE 见闻；言辞 / 人情 = 已知 MOTIVE 见闻；威逼 = 已知 LEVERAGE 见闻；见闻只认快照里 known=True 者——知情人不在场照样能用）、fact_to_learn、required_regard（求教门槛：三流及不入流须友善，二流及以上须信赖）

依赖说明
- 包内单向：base ← parley ← martial ← physical，base / parley ← talk，全部 ← __init__；各模块只在类型标注里引用 aggregates.PlayerState（TYPE_CHECKING），aggregates 运行期导入本包门面，两者互引而不成环
- 包外：approach（兼容表与所图推断）、stakes（三路赌注与 settle_any）、social / covert（区间与效果，只吃纯数值）、reputation（人情涟漪）、threads（纠缠判据读 PlayerState.threads）
- 裁决只看两样东西：快照（他人与地理的物理事实）与玩家状态（自身的逻辑事实）；回合编排保证二者版本一致后才调用
- 交涉与暗中永不致死：它们的定案不产出 PlayerDied，险物到手的伤由 handled 统一追加且留一口气

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
