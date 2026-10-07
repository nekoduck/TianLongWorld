# director/
> L2 | 父级: backend/app/CLAUDE.md

导演管线：把一个玩家动作变成一次经过裁决与校验的世界推演。核心原则是"生死归规则、叙事归模型"——确定性规则先判定致死，大模型只被允许叙述结果，且其输出必须穿过解析闸门才能落地。

成员清单
__init__.py: 包门面，只导出 Director
pipeline.py: 编排核心 Director(llm, store, memory_limit, attempts)，open() 抽开局种子生成第一幕（evolve(种子状态, 裁决) 继承种子物品；种子点名的高手先登记在场，再 observe 大模型补写的到场者），interact() 串联 守卫 → judge(presence) → recall 记忆过滤 → build_turn → LLM(附 DIRECTOR_SCHEMA) → parse（失败重采样，默认 2 次）→ 必死封印 → advance
lethal.py: 确定性致死预判，judge(action, player, presence) 判定 无绝学（读 martial_arts 与 buffs_debuffs）∧ 敌意（先剔除"打听/打量"等无害复合词）∧ 点名 ∧ 在场（Session.presence()：局部环境 present_npcs 优先，为空时退回上一幕原文）；Verdict 以单字段 killer 表达裁决
memory.py: 记忆拦截与过滤器（Memory Filter Layer），recall(events, location, present_npcs, traits, limit) 只放行 tags 与当前地点、在场 NPC（经 lore.kin 展开别名）或 social_traits 相互包含的世界大事（双方均 ≥2 字："聚贤庄废墟"↔「聚贤庄」、"丐帮弟子"↔「丐帮」），保序取最近 limit 条；纯函数，全量台账永不进 Prompt
prompts.py: 提示词协议，SYSTEM_PROMPT 是静态世界法则（可被厂商缓存）：高手名录 / 【标签化演算】/ 【江湖声望】/ 【状态记账】四本账只报 player_delta 增减 / 【局部视野】local_delta 到场离场与换图判据 / 【世界台账】<relevant_history> 是相关历史、重大变故以 {"tags","event_desc"} 追加进 next_state.major_events、tags 只写实体名词、不抄旧事 / 叙事要求 / JSON 契约；build_opening(seed) / build_turn(session, memories, ...) 以 XML 标签组装 User Message，HISTORY_PREAMBLE"这是与当前场景/人物相关的世界历史记录："领起相关大事；插值转义尖括号与引号、窗口逐行 JSON 防伪造；read_section / read_directive 供 Mock 读取
parser.py: 解析闸门，截取首 "{" 至末 "}" 剥离围栏与寒暄，交 Pydantic 校验 DirectorOutput，失败抛 DirectorError

提示词协议（User Message 结构，每一项都有上限：Prompt 长度与游戏进度、台账长度无关）
  <player_state>{json}</player_state>                              只有玩家状态，世界台账从不整树注入
  <local_environment>{"location","present_npcs"}</local_environment> 服务端记账的局部环境
  <recent_history>{"action","scene"} 每行一个</recent_history>      仅回合：滑动窗口，至多 HISTORY_TURNS（3~5）回合
  <relevant_history>这是与当前场景/人物相关的世界历史记录：
  - [聚贤庄、游氏双雄] 玩家在聚贤庄大战中烧毁了正厅……</relevant_history>  仅回合：memory.recall 放行的至多 MEMORY_LIMIT 条，无则"（无）"
  <opening_seed>情境</opening_seed>                                 仅开局
  <player_action type="choice|custom">动作</player_action>
  <directive kind="opening|normal|lethal" [killer= signature=]>指令</directive>

扩展点
- 新高手：在 app/lore.GRANDMASTERS 追加一行，judge、System Prompt 名录与 kin 别名检索同时生效
- 新开局：在 app/lore.OPENING_SEEDS 追加一行，premise 点名的高手开局即登记在场
- 新清单：在 schemas.LEDGERS 与 PlayerState / PlayerDelta 各加一个同名字段，evolve 自动按增减记账

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
