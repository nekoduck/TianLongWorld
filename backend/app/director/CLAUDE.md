# director/
> L2 | 父级: backend/app/CLAUDE.md

导演管线：把一个玩家动作变成一次经过裁决与校验的世界推演。核心原则是"生死归规则、叙事归模型"——确定性规则先判定致死，大模型只被允许叙述结果，且其输出必须穿过解析闸门才能落地。

成员清单
__init__.py: 包门面，只导出 Director
pipeline.py: 编排核心 Director(llm, store, memory_limit, attempts)，open() 抽开局种子生成第一幕（evolve(种子状态, 裁决) 继承种子物品；种子点名的高手按大模型写出的开局地点先登记在场，地点措辞漂移也清不掉，再 observe 大模型补写的到场者），interact() 串联 守卫 → judge(presence) → recall 记忆过滤（含动作点名）→ build_turn → LLM(附 DIRECTOR_SCHEMA) → parse（失败重采样，默认 2 次）→ 必死封印 → advance
lethal.py: 确定性致死预判，judge(action, player, presence) 判定 无绝学（读 martial_arts 与 buffs_debuffs）∧ 敌意（先剔除"打听/打量"等无害复合词）∧ 点名（敌意与点名都只审 perception.surface 后的表面行为，心里骂乔峰不算冒犯）∧ 在场（Session.presence()：局部环境 present_npcs 优先，为空时退回上一幕原文）；Verdict 以单字段 killer 表达裁决
perception.py: 感知边界，surface(action) 剥掉以纯心理动词（心里/心想/暗骂/盘算/琢磨……；"暗自""暗中"不算——暗中出手看得见）起头的念头从句，只留外人看得见、听得见的部分；"玩家的表面行为"在全系统只有这一个定义，lethal 与 JIT 点名检索共用
memory.py: 记忆拦截与过滤器（Memory Filter Layer），recall(events, location, present_npcs, traits, mentioned, limit) 只放行 tags 与当前地点、在场 NPC（经 lore.kin 展开别名）或 social_traits 相互包含的世界大事（双方均 ≥2 字："聚贤庄废墟"↔「聚贤庄」、"丐帮弟子"↔「丐帮」），外加规格之外的第四路：tag（含别名）原样出现在这一招动作的表面部分里（调用方先经 perception.surface 剥去内心念头；单向，"潜回聚贤庄"抵达当回合即见那里的过往）；保序取最近 limit 条；纯函数，全量台账永不进 Prompt；检索键只取场景里公开可感知的东西，玩家的 secrets 与内心念头都不参与检索（以秘密为钥匙召回历史，等于每回合提醒导演玩家藏着什么）
prompts.py: 提示词协议，SYSTEM_PROMPT 是静态世界法则（可被厂商缓存）：高手名录 / 【情报隔离铁律】secrets 与未示人之物对 NPC 绝对不可见、NPC 只凭自身认知 + 玩家表面行为 + 公开世事、禁止因秘密找上门或生预感、窗口是玩家亲历而非 NPC 共同记忆 / 【克制生成原则】不凭空制造宿命与巧合、平淡一回合合法 / 【标签化演算】/ 【江湖声望】NPC 认得出玩家（见过、自报家门、服色、信物、画像）才按 social_traits 与台账旧事对待他，仇家不会凭空找上门；social_traits 只记公开名声、隐藏身份进 secrets / 【状态记账】五本账只报 player_delta 增减 + 私密与公开分界（私密情报推入 secrets、暗中所为在台账只写旁观者视角的后果、当众揭穿即 remove）/ 【局部视野】local_delta 到场离场（真名或门派身份群体）与换图判据 / 【世界台账】<relevant_history> 是相关历史、重大变故以 {"tags","event_desc"} 追加进 next_state.major_events、每回合至多 3 条、tags 只写实体名词、对原著人物的不可逆影响也算、不抄旧事 / 叙事要求（危机只能来自玩家行为的合理后果、闲逛平淡白描合格、选项不为机缘设局）/ JSON 契约；build_opening(seed) / build_turn(session, memories, ...) 以 XML 标签组装 User Message：secrets 从 <player_state> 剥出、以 SECRETS_PREAMBLE 单独成段，普通回合指令附 _SELF_CHECK 落笔前自查（NPC 凭什么知道、有没有凭空巧合），HISTORY_PREAMBLE"这是与当前场景/人物相关的世界历史记录："领起相关大事；插值转义尖括号与引号，窗口与相关历史都逐行 JSON（伪造不出条目，抄回原文不变、去重生效）；read_section / read_directive 供 Mock 读取
parser.py: 解析闸门，截取首 "{" 至末 "}" 剥离围栏与寒暄，交 Pydantic 校验 DirectorOutput，失败抛 DirectorError

提示词协议（User Message 结构，每一项都有上限：Prompt 长度与游戏进度、台账长度无关）
  <player_state>{json}</player_state>                              玩家快照与四本标签账（不含 secrets），世界台账从不整树注入
  <secrets>以下情报只有玩家自己知道……NPC 不可见：
  "信封里是丐帮副帮主的谋反密信"</secrets>                        私密情报单独成段，逐行 JSON 字符串，无则"（无）"
  <local_environment>{"location","present_npcs"}</local_environment> 服务端记账的局部环境
  <recent_history>{"action","scene"} 每行一个</recent_history>      仅回合：滑动窗口，至多 HISTORY_TURNS（3~5）回合
  <relevant_history>这是与当前场景/人物相关的世界历史记录：
  {"tags":["聚贤庄","游氏双雄"],"event_desc":"玩家在聚贤庄大战中烧毁了正厅……"}</relevant_history>  仅回合：memory.recall 放行的至多 MEMORY_LIMIT 条，逐行 JSON，无则"（无）"
  <opening_seed>情境</opening_seed>                                 仅开局
  <player_action type="choice|custom">动作</player_action>                仅回合
  <directive kind="opening|normal|lethal" [killer= signature=]>指令</directive>

扩展点
- 新高手：在 app/lore.GRANDMASTERS 追加一行，judge、System Prompt 名录与 kin 别名检索同时生效
- 新开局：在 app/lore.OPENING_SEEDS 追加一行，premise 点名的高手开局即登记在场
- 新清单：在 schemas.LEDGERS 与 PlayerState / PlayerDelta 各加一个同名字段，evolve 自动按增减记账（规则不同于标签账的，在 session._BOOKKEEPERS 登记专属记账函数）；
  若是私密类账本，还须在 prompts._player 中剥出、单独成段，并且不作 JIT 检索键——否则它会原样进入 <player_state>

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
