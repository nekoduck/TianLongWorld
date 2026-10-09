# options/
> L2 | 父级: engine/app/application/CLAUDE.md

动态选项生成器 + 意图风味封装：一招 = 引擎可读的标准指令（underlying_command，rules.command 算出）+ 战术维度（approach.axis_of：激化 / 诡道 / 化解 / 旁观）+ 两层文案（label 朴素标签、flavor_text 玩家看见的那句）。
候选沿快照的合法边枚举，按 domain/approach 的兼容表展开手段，全部经 rules.adjudicate 过滤——合法不等于安全，风险档只说最坏能坏到哪一步，不露结局。
三个出口同出一个打过分的池子：affordances（全部可供之招，点选核验）⊇ catalogue（给说书人的可供性目录）∪ generate（退路菜单）；说书人在叙事的同一次调用里从目录挑 3~4 招配上武侠风味，menu.compose 过闸后下发。移动不在这里：导航是 application/navigation。
一切都是 (玩家状态, 快照) 的纯函数，世界不变则逐字不变（措辞变体按意图哈希挑），点选时按当前快照重算即可核验，从不经大模型；P1 由单文件拆成包以守住 800 行上限，门面不变。

成员清单
__init__.py: 门面，转出 ActionOption / OptionCategory / OptionGenerator / compose
sources.py: OptionCategory（战斗 / 交涉 / 探索 / 修习 / 取物 / 休养）、ActionOption（id = 方向 + 规整后指令意图的摘要（此行所为不进摘要）、flavor_text 玩家看见的文案（缺省等于 label）、label 确定性的朴素标签、tactical_axis 战术维度（axis_of 查表，不由措辞定）、category、why 上榜缘由、risk 风险档、underlying_command 只在服务端的标准指令，只读属性 intent；of() 手搭选项可只给意图，耗时按命令耗时表）、digest() 意图摘要、Candidate（方向、意图、措辞键与槽位、所接续的线索、须走的路线）与四类候选源：Thread（对象仍在场的未了之事，按所图列出兼容表允许而未试过的手段，借势须有筹码）、Person（攀谈、言辞结交或化解（友善以上不出）、打探（仅当 parley.fact_to_learn 选得出此人知情而你未知的见闻，标签从不带见闻正文）、武力出手、求艺与恳请传功（后者只在师父不肯、改走交涉时成立，不向仇人恳求）、他身上之物（被制住即取走，否则言辞讨要 / 潜行偷取 / 武力夺物各一条）、物归原主、借势（仅当 parley.leverage 选得出筹码）、问路（尚有未知去处时向不敌视你的在场者里人情最好的一位——寻常攀谈、话题是此地，规则据此记 PlacesLearned；一份菜单只出一条））、Ground（地上之物，物性闸门由规则把关；疗伤之药，解毒之药 P1 不上菜单）、Self（静观、调息、无人可教的修习）；Exit 源已移除——出路归导航
phrasing.py: TEMPLATES 朴素标签的措辞表（每键 2~3 个变体，模板不含实体名、去槽位 ≤10 字；出路的措辞已去——标签常带地名，未知去处不得露名；问路 ask.way 只有对象）、render（按意图摘要确定性挑变体、缺槽即抛错）、learn_phrase（修习措辞随 Approval.guidance / source：求教 / 参悟 / 随师精研 / 参照典籍 / 闭门苦练）、bare（模板骨架，验收数模板用）
salience.py: Scored（选项 + 裁决 + 分数 + 焦点位次 + 线索；subjects、anchor 对象、axis 战术轴、similarity、rank）与 score()：P0 打分保留（底分物归原主 3 / 修习 2 / 取物与交谈 1 / 出手 0、焦点 +5/+4/+3/+2、重伤以上调息与疗伤之药 +3；出路两项随移动归导航而去）；他人手中之物底分 0、恳请传功底分同交谈；心事线索 +3、纠缠不休（同一对象同一所图同一手段已试）−2；MMR 相似度 = 同动作 + 同对象 + 同战术轴，与已选各席累加、每分折 2 分
slate.py: OptionGenerator(max_options=4, min_options=3, per_target=2, catalogue_size=12)，同一候选池（候选 → 裁决过滤 → rules.command 定标准指令 → 按规整后意图去重、每条线索只留第一条获准的 → 措辞 → 打分）的三个出口：affordances（全部获准的非移动之招，按分排序、不封顶——点选核验）、catalogue（至多 12 招，按战术轴轮转取各轴高分者，每根有招的轴都在，按分排序编号 m1…）、generate（退路菜单：调养席 / 跟进席 → MMR 取满（尽量铺开不同的轴）→ 不足 min 席才补调息、疗伤之药、静观；可供之招本就不足三招时有几招给几招；同一对象至多 per_target 席；脱身席随移动归了导航的 retreat）；经 preview 盖上 why 与风险档
preview.py: why()（物归原主 → 心事线索「换个手段 / 心事未了」→ 人情与焦点 → 这一类举动本身的理由，P0 原句保留，新招各一句，问路「前路未明」；出路的缘由已去）、risk()（risk_of(rules.stakes(意图))，确定之事稳妥）
menu.py: compose(catalogue, picks: MenuPicks | None, fallback, *, scene_names, canon_names) 说书人挑选的过闸——编号须在目录里且不重复（大小写与空白宽容），flavor() 风味闸门（2~24 字、一行中文、无英文与数字（全角亦然）与标记 〈〉<>｜、无结局与状态字眼 VERDICT_WORDS（硬结局由领域枚举推出 + 伤势档 + 制住 / 到手 / 学会……）、剔掉场景名字（长名先剔）后不点原著名录里的名字、单字名不作数）；合格的换上 flavor_text，不合格或与已用文案重复的招照留、文案退回朴素标签；按 id 去重、至多 4 席，挑中的不足 3 席用退路按次序补，一招没挑中即原样下发退路菜单；scene_names 应只含玩家叫得出的名字（intent_parser.scene_names 的口径：未知去处只剩标签）；运行期只按结构读 MenuPicks，不反向依赖叙事

依赖说明
- 包内单向：phrasing ← sources ← salience ← preview ← slate；menu 只依赖 sources；__init__ 转出 slate / sources / menu
- 包外只读 domain：rules（adjudicate / command / stakes / best_skill，rules.parley 的 fact_to_learn / leverage）、approach（Route / TacticalAxis / axis_of）、commands（Command / time_cost）、stakes.Risk / risk_of、resolution.hard 与结局枚举（风味闸门的字眼）、threads、snapshot；PlayerState 只在类型标注里出现；narrator 的 MenuPicks 只在类型标注里出现
- handlers 先算 catalogue / generate / navigation，说书人从目录挑招配风味，compose 过闸后下发；点选按当前状态与快照重算 affordances ∪ navigation 按 id 取回 underlying_command；协议层只下发 id / flavor_text / tactical_axis / category / why / risk（label 与指令永不下发）
- 有毒的地上之物风险档仍是「稳妥」：显示「有险」会泄露玩家尚未打听到的险性（HAZARD 见闻），到手即伤由规则按物性追加

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
