# director/
> L2 | 父级: backend/app/CLAUDE.md

导演管线：ESAA 的单一同步主循环。核心原则是"生死归规则、叙事归模型、写入归运行时"——确定性规则先判生死，大模型只产出意图，
意图穿过解析闸门与 engine 裁决才成为事件；这里是全后端唯一触碰 I/O（大模型、事件库、日志）的地方。

成员清单
__init__.py: 包门面，只导出 Director
pipeline.py: 编排器 Director(llm, store, window, memory_limit, attempts)。open(world_id)：校验世界存在 → 投影世界 → 抽种子 → JIT 召回（以 premise 为点名文本）→ 开局 Prompt → 采纳（判死则重采样）或退回种子 → decide_opening → 追加 LifeBegan。interact(req)：in-flight 守卫 → 投影 life 与 world（死者 409、未知 404）→ judge → recall → build_turn → 容错链 → decide_turn → 原子追加 → apply 投影出响应；普通回合持续幻觉时原地停顿、不写事件；必死回合大模型失败或抗命都落到确定性处决
lethal.py: 确定性致死预判，judge(action, player, present) = 无绝学（只读 martial_arts）∧ 敌意（先剔除"打听/打量"等无害复合词）∧ 点名 ∧ 在场（只读局部环境实体账，绝不回退叙事原文）；Verdict 以单字段 killer 表达裁决
memory.py: JIT 标签路由，recall(events, location, entities, traits, mentioned, limit) 四路命中：tags 与当前地点、在场人物（经 lore.kin 展开别名）或 social_traits 相互包含（双方均 ≥2 字），或 tag（含别名）原样出现在这一招的动作文本里（单向，"前往聚贤庄"）；保序取最近 limit 条；上下文规模由检索控制而非销毁历史
prompts.py: 提示词协议。SYSTEM_PROMPT 按 PARCER（Persona / Assignment / Rules / Context / Example / Response）组织，逐字含 Permission_Boundary 与 Output_Contract 两行，十条规则（标签化、硬核 + 高手名录、标签化演算、江湖声望、状态记账 + 绝学名录、局部视野 + 与 engine.observe 同一的换图判据、世界台账只追加 + 形状示例、因果时辰、防注入（含窗口里的历史动作）、双轨输出），示例由 DirectorOutput 实例序列化（世界大事为空，示范正确粒度）；滑动窗口逐行 JSON 渲染，玩家无法伪造场景；输入契约 Directive / OpeningContext / TurnContext 渲染为 XML 标签，插值一律转义尖括号与引号；with_feedback 追加 <format_error> 供重采样；read_section / read_directive 供 Mock 读取
parser.py: 解析闸门，从首个 "{" 起 raw_decode 出第一个完整 JSON 对象（跳过围栏、寒暄与尾随文本），交 Pydantic 校验 DirectorOutput；失败抛带 hints（字段路径：错误，至多 6 条）的 DirectorError
fallback.py: 确定性兜底，seed_opening(seed) 以种子原文开局、execution(snapshot, killer, signature) 确定性处决（health 写"中{杀招}，气绝身亡"）、STALLED_SCENE 普通回合停顿文案、DEFAULT_OPTIONS；兜底产物同为 DirectorOutput，走同一条裁决路径；Mock 复用 execution

提示词协议（User Message 结构）
  <player_state>{json}</player_state>                       玩家快照 + 四本标签账（世界状态不整树注入）
  <local_environment>{json}</local_environment>             仅回合：当前地点 + 在场的有名有姓者
  <sliding_window>{"action","scene"} 每行一个</sliding_window> 仅回合：最近 N（3~5）回合原文
  <opening_seed>情境</opening_seed>                          仅开局
  <relevant_history>- [标签] 事实 ...</relevant_history>     JIT 召回的相关世界大事
  <player_action type="choice|custom">动作</player_action>   仅回合
  <directive kind="opening|normal|lethal" [killer= signature=]>指令</directive>
  <format_error>- 要点 ...</format_error>                    仅重采样

容错链
  厂商层约束解码 → parser 宽容解析 → with_feedback 带错重采样（attempts 次）→ fallback 确定性兜底
  LLMError 耗尽：普通回合与开局 502；必死回合照样处决

扩展点
- 新高手：在 lore.GRANDMASTERS 追加一行，judge、System Prompt 名录与 JIT 别名同时生效
- 新开局：在 lore.OPENING_SEEDS 追加一行，premise 宜点名在场人物
- 新清单：在 schemas.LEDGERS、PlayerState、PlayerDelta 各加一个同名字段，并在 engine._evolve / _resolve_player 记账

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
