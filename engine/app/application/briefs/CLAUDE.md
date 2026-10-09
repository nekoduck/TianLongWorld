# briefs/
> L2 | 父级: engine/app/application/CLAUDE.md

地下城主的简报包——语义物理引擎的输入层（绝对事实）与输出层（结构化契约）。三路赌注与结果已定之事共用一份 XML 简报：
<intent> 玩家意图（按此情此景规整）/ <player_input> 原话 / <time> <scene> <exits> <people> <crowds> <things> <activities> <traces> <rumors> <clocks> <emerged> <known> 物理快照 / <player> 玩家状态 / <stakes> 这一招赌的是什么 / <physics> 物理边界。
探索迷雾：<exits> 与说书人同一种写法（narrator.way「方位｜去处｜交通方式｜路程」），玩家不认得的去处只写「未知区域」，出口标签不进；在场者带议程而来的，人物行附「来意：…」（handlers 交来的玩家状态已先过 veil，迷雾里的地名只剩「别处」）。
简报是地下城主唯一的世界——只用快照里的 T=0 事实：foreshadow 从不进快照也就从不进简报，未知见闻的正文同样不进；时钟不露 clk: id，act: / trc: / swm: / tok: id 同样不露；每个插值逐值转义。全局事件流不进简报：在场之人知道玩家做过什么，只凭传到此地的消息与亲眼所见（局部认知）。
产出永远只是提议，定案归 domain/resolution.settle；H-Agent 另有两份简报（agenda.py）：宏观议程（每位核心 NPC 的人设、执念、恩怨、此地情报与可去之处）与相撞的裁决（撞见沿用本包的物理快照与结果已定的物理边界，前面加一段 <encounter>；狭路相逢写两人、仇怨、此地传闻与可裁结局）——NPC 只知道传到他所在之处的消息，玩家身在何处不进议程简报。

成员清单
__init__.py: 门面——brief(env, scene, state, intent, said) 拼出整份简报（赌注细节按 rules.stakes 重算，路线或对象与 env 不符即不写）、schema(env, scene) 转自 physics；转出 MEANING / ROUTE / safe / join；简报同样守探索迷雾与来意（world_section），撞见的判官（npc_agent）借同一份 world_section
scene.py: 输入层共用段——intent_section（动作中文说法（含沉思）、手段、所图、对象、武学、物品、话题 + 原话）、world_section（<time> 时辰与昼夜；此地；<exits> 每行「方位｜去处｜交通方式｜路程」（narrator.way，未知去处写「未知区域」，出口标签不进）+ 去处有无仇人；在场之人：本名称号别名 / 门派 / 境界 / 性情 / 态度与恩怨 / 来意（PlayerState.agendas 的议程意图，带议程者才写）/ 外显人设 / 是否被制住 / 身负武学 / 与在场者的羁绊 / T=0 描述，对象标【对象】；<crowds> 此地的人群：名｜约数｜此刻在做什么或溃散逃离｜胆量（惊惧阈值经 courage() 只写胆小 ≤3 / 寻常 / 胆大 ≥7，不写数字）；可见之物：在谁手里、不可携带、用法、原物主，不写险性；<activities> 此地的往事按先后（「交手｜你、龚光杰｜已结束」）与 <traces> 痕迹（还剩多久）；<rumors> 传到此地的消息正文，没有时明写「在场之人一无所知」；时钟：名称｜种类｜挂处｜进度 / 阈值｜满则如何；此世细节；已知见闻正文——各段空时写占位，act: / trc: / swm: / tok: id 不露）、player_section（境界按火候折算、武学火候、伤势与气血数值、名望标签与点数、行囊，此行所为 PlayerState.motivation 非空才写）
physics.py: <physics> 物理边界——路线、对象与其态度、每种可裁结局的含义 / 软硬 / 属性写法（output_for 现推）/ 欠几格代价（与闸门同一套等价交换记账）/ 确定性裁决标记、strain、可用属性键、可挂之处；FIXED 只许动时钟、事实、名望；safe_band() 是闸门认得回来的气血带（剔掉相邻气血带共用的端点），简报与气运共用；schema()：字段顺序即推理顺序（collision → severity → cost → convergence → deltas → clock_mutations → new_facts → action_trigger），deltas 为 [{key, value}] 且 key 枚举取 delta_keys，clock_mutations 的 op / kind / maximum 枚举、anchor 枚举取 clock_anchors，action_trigger 枚举（死亡判定只在区间含毙命时；FIXED 只有「无」），枚举去重
combat.py: 「战」——MEANING（v3 原文，现为可裁结局的含义）、combat_stakes（对手、双方境界、性情、所用武学火候、兵器、夺物标的）；持有全包共用的 safe / join
social.py: 「交」——social_stakes（对象、所图与标的、手段（威逼 / 套话）、筹码：已知见闻正文 / 靠山）、meaning（如愿随所图而变，松动升不升人情问效果表）；打探的标的只写「此人所知的一桩事」
covert.py: 「暗」——covert_stakes（失主、所取之物不写险性、手段（骗取 / 偷取）、境界差与情势）、meaning
agenda.py: H-Agent 的简报——Brief(text, schema, names：此景出现过的名字，事实预筛先剔掉它们)；agenda_brief(state, atlas, npcs, personas, relations, cause)（<time> / <cause> / <player> 只写玩家名（他身在何处不写）+ 每位能领议程的核心 NPC 一段 <npc>：本名称号｜门派｜境界｜性情、此刻所在（居所）、执念、好恶、对玩家的态度与恩怨、开篇仇敌（带缘由）与羁绊（后文结的不算）、眼下议程、此地情报（传到他所在之处的消息，新者在前至多 RUMORS_MAX=4 条，没有即明写此人一无所知）、可去之处与路程（三跳以内、路程近者在前至多 DESTINATIONS_MAX=24 处）；契约 agendas 数组，npc 枚举这几位的本名、target 枚举他们可去之处 ∪「留守」、intent maxLength 16、priority 1~3）；veil(text, state, scene, atlas)（探索迷雾守到来意里：来意是大模型写的、常带目标地名，玩家叫不出名的地方——没到过、没问过路、不是眼前认得的去处、也不是名胜——都抹成「别处」，处所的简称（「·」右侧两字以上）同样抹掉，长名先配、认得的名字原样留下（「大理城」不会被「大理」吃掉一半）；说书人的 errands 与撞见的判官共用）；encounter_brief(encounter, env, scene, state, agenda, atlas)（<encounter> 写明不是玩家的举动、来者、来意（此地正是去处 / 途经此地正往某处去，来意与去处都过 veil——判官写下的细节会进玩家的回合）、对你的态度与恩怨，其后是 world_section / player_section / physics_section 的 FIXED，契约即 physics.schema）；skirmish_brief(stakes, state, atlas, personas, relations)（<encounter> 地点与时辰、<people> 来者与在此者（境界带伤折一档、性情、执念、来意）、<feud> 开篇仇怨、<rumors> 此地传闻、<physics> 可裁结局的含义 SKIRMISH_MEANING 与确定性裁决标记；契约 reasoning → outcome（枚举可裁结局）→ fact）；distance(ticks)（一个时辰内 / 约两个时辰 / 约半日 / 约三日）；不露任何 id，每个插值逐值转义

依赖说明
- 包内单向：combat ← social / covert ← physics ← scene ← __init__，agenda 借 combat 的 safe / join、scene 的 world_section / player_section 与 physics 的 physics_section / schema（门面不转出 agenda，npc_agent 与 handlers（veil）直接导入）；resolution_agent 认门面与 physics.safe_band；scene 另借 application/narrator 的心跳写法（when / crowd / activity / trace / chronological）与出路写法 way，与说书人同一种说法——narrator 的 way 取 application/navigation 的 duration_label，出路的路程与导航按钮也是同一种说法
- 只读 domain：rules（normalized / stakes / player_tier）、resolution（Envelope / output_for / delta_keys / clock_anchors / hard / 等价交换记账）、三路赌注与结局、snapshot；不写任何东西；agenda 另读 domain/npc（STAY / SkirmishStakes / wounded）、domain/agenda、domain/heartbeat（Atlas / position）与 domain/commands 的时辰写法

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
