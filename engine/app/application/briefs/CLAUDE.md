# briefs/
> L2 | 父级: engine/app/application/CLAUDE.md

地下城主的简报包——语义物理引擎的输入层（绝对事实）与输出层（结构化契约）。三路赌注与结果已定之事共用一份 XML 简报：
<intent> 玩家意图（按此情此景规整）/ <player_input> 原话 / <time> <scene> <exits> <people> <crowds> <things> <activities> <traces> <rumors> <clocks> <emerged> <known> 物理快照 / <player> 玩家状态 / <stakes> 这一招赌的是什么 / <physics> 物理边界。
简报是地下城主唯一的世界——只用快照里的 T=0 事实：foreshadow 从不进快照也就从不进简报，未知见闻的正文同样不进；时钟不露 clk: id，act: / trc: / swm: / tok: id 同样不露；每个插值逐值转义。全局事件流不进简报：在场之人知道玩家做过什么，只凭传到此地的消息与亲眼所见（局部认知）。
产出永远只是提议，定案归 domain/resolution.settle。

成员清单
__init__.py: 门面——brief(env, scene, state, intent, said) 拼出整份简报（赌注细节按 rules.stakes 重算，路线或对象与 env 不符即不写）、schema(env, scene) 转自 physics；转出 MEANING / ROUTE / safe / join
scene.py: 输入层共用段——intent_section（动作中文说法（含沉思）、手段、所图、对象、武学、物品、话题 + 原话）、world_section（<time> 时辰与昼夜；此地、出路与去处有无仇人、在场之人：本名称号别名 / 门派 / 境界 / 性情 / 态度与恩怨 / 外显人设 / 是否被制住 / 身负武学 / 与在场者的羁绊 / T=0 描述，对象标【对象】；<crowds> 此地的人群：名｜约数｜此刻在做什么或溃散逃离｜胆量（惊惧阈值经 courage() 只写胆小 ≤3 / 寻常 / 胆大 ≥7，不写数字）；可见之物：在谁手里、不可携带、用法、原物主，不写险性；<activities> 此地的往事按先后（「交手｜你、龚光杰｜已结束」）与 <traces> 痕迹（还剩多久）；<rumors> 传到此地的消息正文，没有时明写「在场之人一无所知」；时钟：名称｜种类｜挂处｜进度 / 阈值｜满则如何；此世细节；已知见闻正文——各段空时写占位，act: / trc: / swm: / tok: id 不露）、player_section（境界按火候折算、武学火候、伤势与气血数值、名望标签与点数、行囊，此行所为 PlayerState.motivation 非空才写）
physics.py: <physics> 物理边界——路线、对象与其态度、每种可裁结局的含义 / 软硬 / 属性写法（output_for 现推）/ 欠几格代价（与闸门同一套等价交换记账）/ 确定性裁决标记、strain、可用属性键、可挂之处；FIXED 只许动时钟、事实、名望；safe_band() 是闸门认得回来的气血带（剔掉相邻气血带共用的端点），简报与气运共用；schema()：字段顺序即推理顺序（collision → severity → cost → convergence → deltas → clock_mutations → new_facts → action_trigger），deltas 为 [{key, value}] 且 key 枚举取 delta_keys，clock_mutations 的 op / kind / maximum 枚举、anchor 枚举取 clock_anchors，action_trigger 枚举（死亡判定只在区间含毙命时；FIXED 只有「无」），枚举去重
combat.py: 「战」——MEANING（v3 原文，现为可裁结局的含义）、combat_stakes（对手、双方境界、性情、所用武学火候、兵器、夺物标的）；持有全包共用的 safe / join
social.py: 「交」——social_stakes（对象、所图与标的、手段（威逼 / 套话）、筹码：已知见闻正文 / 靠山）、meaning（如愿随所图而变，松动升不升人情问效果表）；打探的标的只写「此人所知的一桩事」
covert.py: 「暗」——covert_stakes（失主、所取之物不写险性、手段（骗取 / 偷取）、境界差与情势）、meaning

依赖说明
- 包内单向：combat ← social / covert ← physics ← scene ← __init__；resolution_agent 认门面与 physics.safe_band；scene 另借 application/narrator 的心跳写法（when / crowd / activity / trace / chronological），与说书人同一种说法
- 只读 domain：rules（normalized / stakes / player_tier）、resolution（Envelope / output_for / delta_keys / clock_anchors / hard / 等价交换记账）、三路赌注与结局、snapshot；不写任何东西

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
