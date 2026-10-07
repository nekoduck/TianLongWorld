# director/
> L2 | 父级: backend/app/CLAUDE.md

导演管线：把一个玩家动作变成一次经过裁决与校验的世界推演。核心原则是"生死归规则、叙事归模型"——确定性规则先判定致死，大模型只被允许叙述结果，且其输出必须穿过解析闸门才能落地。

成员清单
__init__.py: 包门面，只导出 Director
pipeline.py: 编排核心 Director，open() 抽开局种子生成第一幕，interact() 串联 守卫 → judge → build_turn → LLM → parse（失败重采样，默认 2 次）→ 必死封印 → advance
lethal.py: 确定性致死预判，judge() 判定 无绝学 ∧ 敌意（先剔除"打听/打量"等无害复合词）∧ 点名 ∧ 近期场景在场；Verdict 以单字段 killer 表达裁决
prompts.py: 提示词协议，SYSTEM_PROMPT（世界法则/叙事要求/JSON 契约）+ build_opening / build_turn 以 XML 标签组装 User Message；插值文本转义尖括号与引号防注入；read_section / read_directive 供 Mock 读取
parser.py: 解析闸门，截取首 "{" 至末 "}" 剥离围栏与寒暄，交 Pydantic 校验 DirectorOutput，失败抛 DirectorError
lore.py: 静态世界设定，GRANDMASTERS（13 位会下死手的绝顶高手及别名、杀招）、OPENING_SEEDS（6 个开局情境，半数有高手在场）、SHICHEN 十二时辰

提示词协议（User Message 结构）
  <world_state>{json}</world_state>
  <recent_history>「动作」→ 场景 ...</recent_history>     仅回合
  <opening_seed>情境</opening_seed>                      仅开局
  <player_action type="choice|custom">动作</player_action>
  <directive kind="opening|normal|lethal" [killer= signature=]>指令</directive>

扩展点
- 新高手：在 lore.GRANDMASTERS 追加一行，judge 自动生效
- 新开局：在 lore.OPENING_SEEDS 追加一行，premise 宜点名在场人物以便致死预判识别

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
