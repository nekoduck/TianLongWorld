# director/
> L2 | 父级: backend/app/CLAUDE.md

导演管线：把一个玩家动作变成一次经过裁决与校验的世界推演。核心原则是"生死归规则、叙事归模型"——确定性规则先判定致死，记忆仓储备好案头资料，大模型只被允许叙述结果，且其输出必须穿过解析闸门才能落地。

成员清单
__init__.py: 包门面，只导出 Director
pipeline.py: 编排核心 Director(llm, store, memory=MemoryFactory, semantic_top_k, attempts)，open() 抽开局种子生成第一幕（两个参考模块为（无）；evolve(种子状态, 裁决) 继承种子物品；种子点名的高手按大模型写出的开局地点先登记在场，地点措辞漂移也清不掉，再 observe 大模型补写的到场者；开局实体经 witnessed 种下第一回合；情报与大事同样经 _settle 落账），interact() 串联 守卫 → judge(presence) → _context RAG 检索（关系图种子 = 上一回合 involved + 在场 NPC + 所在地 + 公开身份；语义查询 = surface(动作)，多取再与关系网去重，封顶 top_k）→ system_prompt(graph, history) + build_turn → LLM(附 DIRECTOR_SCHEMA) → parse（失败重采样，默认 2 次）→ 必死封印 → advance → _settle（先 retire_secret、再 commit 新知、最后 commit 公开大事：顺序即私密优先的语义）；只认 MemoryService 抽象
lethal.py: 确定性致死预判，judge(action, player, presence) 判定 无绝学（读 martial_arts 与 buffs_debuffs）∧ 敌意（先剔除"打听/打量"等无害复合词）∧ 点名（敌意与点名都只审 perception.surface 后的表面行为，心里骂乔峰不算冒犯）∧ 在场（Session.presence()：局部环境 present_npcs 优先，为空时退回上一幕原文）；Verdict 以单字段 killer 表达裁决
perception.py: 感知边界，surface(action) 剥掉以纯心理动词（心里/心想/暗骂/盘算/琢磨……；"暗自""暗中"不算——暗中出手看得见）起头的念头从句，只留外人看得见、听得见的部分；"玩家的表面行为"在全系统只有这一个定义，lethal 与 RAG 语义检索共用
prompts.py: 提示词协议，SYSTEM_PROMPT 是静态世界法则：高手名录 / 【情报隔离铁律】secrets 与未示人之物对 NPC 绝对不可见、NPC 只凭自身认知 + 玩家表面行为 + 公开世事、禁止因秘密找上门或生预感、窗口与参考模块都不是 NPC 的共同记忆 / 【克制生成原则】不凭空制造宿命与巧合、平淡一回合合法 / 【标签化演算】/ 【江湖声望】NPC 认得出玩家才按 social_traits 与旧事对待他 / 【状态记账】五本账只报 player_delta 增减 + 私密与公开分界 / 【局部视野】/ 【世界台账】两个参考模块怎么读（【台账】已发生不可逆、【原著】开篇关系可被台账改写、【常识】世界规矩）、重大变故以 {"tags","event_desc"} 追加、每回合至多 3 条、不抄旧事 / 【实体提取】逐字含用户规定的提取指令、写进 next_state.involved_entities、至多 12 个专名、只写场面上出现的 / 叙事要求 / JSON 契约；system_prompt(graph, history) = SYSTEM_PROMPT 恒为前缀（厂商前缀缓存照常命中）+ [Graph_Context: 当前实体关系网] + [Semantic_History: 历史相关事件]（HISTORY_PREAMBLE"这是与当前场景/人物相关的世界历史记录："领起），资料逐行 JSON 字符串、无则"（无）"；build_opening(seed) / build_turn(session, action_type, action, verdict) 以 XML 标签组装 User Message：secrets 从 <player_state> 剥出、以 SECRETS_PREAMBLE 单独成段，普通回合指令附 _SELF_CHECK 落笔前自查；插值转义尖括号与引号，窗口逐行 JSON；read_section / read_directive 供 Mock 读取，read_module 读回参考模块条目
parser.py: 解析闸门，截取首 "{" 至末 "}" 剥离围栏与寒暄，交 Pydantic 校验 DirectorOutput，失败抛 DirectorError

提示词协议
System Prompt（静态法则在前，检索资料在后；每一行资料都封顶，长度与台账长度无关）
  {SYSTEM_PROMPT}
  [Graph_Context: 当前实体关系网]
  这是与此刻在场者、所在地、玩家身份及上一回合涉及之人事相关的关系网：
  "【台账】聚贤庄、游氏双雄、丐帮：玩家在聚贤庄大战中烧毁了正厅，游氏双雄战死"     至多 GRAPH_LIMIT 行，大事在前
  "【原著】萧峰（乔峰）是丐帮帮主，威震江湖"
  [Semantic_History: 历史相关事件]
  这是与当前场景/人物相关的世界历史记录：
  "【常识】聚贤庄游氏双雄交游广阔，常邀天下英雄聚会"                               至多 SEMANTIC_TOP_K 行，与关系网去重
User Message（XML 标签，每一项都有上限）
  <player_state>{json}</player_state>                              玩家快照与四本标签账（不含 secrets），世界台账从不整树注入
  <secrets>以下情报只有玩家自己知道……NPC 不可见：
  "信封里是丐帮副帮主的谋反密信"</secrets>                        私密情报单独成段，逐行 JSON 字符串，无则"（无）"
  <local_environment>{"location","present_npcs"}</local_environment> 服务端记账的局部环境
  <recent_history>{"action","scene"} 每行一个</recent_history>      仅回合：滑动窗口，至多 HISTORY_TURNS（3~5）回合
  <opening_seed>情境</opening_seed>                                 仅开局
  <player_action type="choice|custom">动作</player_action>                仅回合
  <directive kind="opening|normal|lethal" [killer= signature=]>指令</directive>

扩展点
- 新高手：在 app/lore.GRANDMASTERS 追加一行，judge、System Prompt 名录与 kin 别名检索同时生效
- 新开局：在 app/lore.OPENING_SEEDS 追加一行，premise 点名的高手开局即登记在场
- 新关系 / 新常识：在 app/lore.RELATIONS / WORLD_RULES 追加一行，关系图与语义检索即刻可用；只收天下皆知之事
- 新记忆后端（图数据库、向量库）：实现 app/memory_service.MemoryService 的四个方法，在 app/main.py 换一个 MemoryFactory，管线一行不改；
  同一条大事在两条检索路径上须渲染为同一行文本，secrets 须只进不出
- 新清单：在 schemas.TAG_LEDGERS 与 PlayerState / PlayerDelta 各加一个同名字段，evolve 自动按增减记账；
  若是私密类账本，须像 secrets 一样经记忆仓储落账、在 prompts._player 中剥出单独成段，并且不作检索键——否则它会原样进入 <player_state>

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
