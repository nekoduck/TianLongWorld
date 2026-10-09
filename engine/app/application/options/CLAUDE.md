# options/
> L2 | 父级: engine/app/application/CLAUDE.md

动态选项生成器：菜单是 (玩家状态, 快照) 的纯函数，世界不变则逐字不变（措辞变体也按意图哈希挑），点选时按当前快照重算即可核验，从不经大模型。
一个选项就是一「招」= (动作, 手段)：候选沿快照的合法边枚举，按 domain/approach 的兼容表展开手段，全部经 rules.adjudicate 过滤——合法不等于安全，风险档只说最坏能坏到哪一步，不露结局。
P1 由单文件拆成包以守住 800 行上限；门面不变。handlers 先算菜单再叙事，「标签（why）」作端倪进 <hooks>。

成员清单
__init__.py: 门面，只转出 ActionOption / OptionCategory / OptionGenerator
sources.py: OptionCategory（战斗 / 交涉 / 探索 / 修习 / 取物 / 休养）、ActionOption（id = 方向 + 意图哈希（此行所为不进哈希），digest 同时决定措辞变体；why 上榜缘由、risk 风险档、意图只留服务端）、Candidate（方向、意图、措辞键与槽位、所接续的线索、须走的路线）与五类候选源：Thread（对象仍在场的未了之事，按所图列出兼容表允许而未试过的手段，借势须有筹码）、Person（攀谈、言辞结交或化解（友善以上不出）、打探（仅当 parley.fact_to_learn 选得出此人知情而你未知的见闻，标签从不带见闻正文）、武力出手、求艺与恳请传功（后者只在师父不肯、改走交涉时成立，不向仇人恳求）、他身上之物（被制住即取走，否则言辞讨要 / 潜行偷取 / 武力夺物各一条）、物归原主、借势（仅当 parley.leverage 选得出筹码））、Ground（地上之物，物性闸门由规则把关；疗伤之药，解毒之药 P1 不上菜单）、Self（静观、调息、无人可教的修习）、Exit（出路）
phrasing.py: TEMPLATES 措辞表（每键 2~3 个变体，模板不含实体名、去槽位 ≤10 字）、render（按意图摘要确定性挑变体、缺槽即抛错）、learn_phrase（修习措辞随 Approval.guidance / source：求教 / 参悟 / 随师精研 / 参照典籍 / 闭门苦练）、bare（模板骨架，验收数模板用）
salience.py: Scored（选项 + 裁决 + 分数 + 焦点位次 + 线索；subjects、anchor 对象、similarity、rank）与 score()：P0 打分全保留（底分物归原主 3 / 修习 2 / 取物与交谈 1 / 出手 0、焦点 +5/+4/+3/+2、仇人在侧出路 +4、不走回头路 +1、重伤以上调息与疗伤之药 +3）；他人手中之物底分 0、恳请传功底分同交谈；心事线索 +3、纠缠不休（同一对象同一所图同一手段已试）−2；MMR 冗余与已选各席累加、每分折 2 分
slate.py: OptionGenerator(max_options=4, min_options=3, per_target=2)：候选 → 裁决过滤（按意图去重、每条线索只留第一条获准的）→ 措辞 → 打分 → 调养席 / 脱身席 / 跟进席 → MMR 取满 → 不足 min 席才补调息、疗伤之药、静观；同一对象至多 per_target 席（MMR 之外的硬上限）；最后经 preview 盖上 why 与风险档
preview.py: why()（物归原主 → 心事线索「换个手段 / 心事未了」→ 人情与焦点 → 这一类举动本身的理由，P0 原句保留，新招各一句）、risk()（risk_of(rules.stakes(意图))，确定之事稳妥）

依赖说明
- 包内单向：phrasing ← sources ← salience ← preview ← slate ← __init__
- 包外只读 domain：rules（adjudicate / stakes / best_skill，rules.parley 的 fact_to_learn / leverage）、approach.Route、stakes.Risk / risk_of、threads、snapshot；PlayerState 只在类型标注里出现
- handlers 与 container 只认门面；协议层只下发 id / label / category / why / risk（意图永不下发）
- 有毒的地上之物风险档仍是「稳妥」：显示「有险」会泄露玩家尚未打听到的险性（HAZARD 见闻），到手即伤由规则按物性追加

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
