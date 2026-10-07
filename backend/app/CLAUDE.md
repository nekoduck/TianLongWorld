# app/
> L2 | 父级: backend/CLAUDE.md

后端应用包。分层自上而下单向依赖：api（路由）→ director（编排）→ memory_service（记忆仓储）→ session / llm（状态与模型）→ schemas / errors（契约）。main.py 是唯一把它们装配在一起的组合根，其余模块互不 new 对方。

成员清单
__init__.py: 包标识，仅一行导航注释
main.py: 组合根，create_app(settings, director) 装配 LLM + SessionStore + 记忆仓储工厂 in_memory(graph_limit) + Director(semantic_top_k) + CORS + GameError 统一处理器；记忆存在哪里只在这里决定；模块级 app 供 uvicorn 加载，测试注入替身 director
config.py: 配置唯一入口，pydantic-settings 读取环境变量与 backend/.env（路径锚定于文件而非 cwd），provider 四选一（mock/gemini/openai/anthropic）；history_turns 滑动窗口钉死 3~5、graph_limit 关系网行数 1~30、semantic_top_k 语义检索条数 1~10；get_settings() 进程级单例
schemas.py: 协议唯一来源，GameState = { player_state: PlayerState, world_state: WorldState } 状态树（自带六段 status_bar 投影：位置/时辰/身份/状态/武学/行囊，缺省为无名小卒/健康/不会武功/空无一物）；PlayerSnapshot（大模型每回合重写的 location/time/weather/health_status）⊂ PlayerState（+ LEDGERS 五本账 = TAG_LEDGERS 四本标签账（evolve 记账）+ secrets 私密情报（Secret ≤ SECRET_CHARS=60 字，经记忆仓储落账），各 MAX_TAGS 封顶；secrets 缺省为空，旧客户端兼容）；WorldState.major_events: list[WorldEvent{tags, event_desc}]（只追加、不设上限）；DirectorOutput 大模型契约（NextState = 快照 + 本回合新增 major_events（至多 MAX_NEW_EVENTS=3 条，超额截断记日志而非判整回合非法）+ involved_entities 实体提取（Label ≤ LABEL_CHARS=20 字，至多 MAX_ENTITIES=12 个；重复、超长、超量规整记日志而非 502；只进服务端不进前端协议）、PlayerDelta 五账增减（secrets 走 SecretDelta：条目是一句话而非标签，大模型写长写多时截断记日志而非 502）、LocalDelta 到场/离场（有名有姓者写真名，无名者写门派身份群体），存活必有选项）+ DIRECTOR_SCHEMA / InteractResponse.of(state, out) / NewSessionResponse
api.py: 路由层，POST /api/session 开局、POST /api/interact 出招、GET /api/health；零业务逻辑，Director 经 app.state 注入
session.py: 会话状态层，evolve(state, out) 状态推进 = 快照 + reconcile 四本标签账（只认点名移除，先减后加，唯一包含匹配，多义不动），私密情报账与世界台账原样带过；另备两本账的记账规矩供记忆仓储施行：absorb 私密情报账（移除同样只认点名；新增按相互包含去重、更详尽的说法取而代之；满员请走最早的，新知不丢）、chronicle 世界台账（只追加，event_desc 去重，不合并不删除）、still_secret 私密优先判据（与玩家仍持有的秘密相同或相互包含（短者 ≥6 字）的大事不入账）；LocalEnvironment{location, present_npcs} 与 observe()（同图——新地点包含原地点全称——只认到场/离场，其余一律切换地图强制清空；按 lore.kin 身份认人，离场也认"丐帮帮主乔峰"式修饰称呼；在场者封顶 12，满员时绝顶高手一律留下、其余只留最近到场者）；witnessed(out, local) 实体可见性闸门（involved_entities 只留场景原文出现（含别名）、在场或就是所在地的专名）；Session（state + deque 滑动窗口 Turn 记忆 + local 局部环境 + involved 检索种子 + dead/busy 标志，acting() 回合守卫，presence() 在场素材，advance() 推进）与 SessionStore（OrderedDict LRU，get_or_rehydrate 冷启动恢复，检索种子随之清空）
memory_service.py: 记忆仓储（Repository Pattern，GraphRAG 接口地基），MemoryService 抽象 = query_relational_graph(entity_names) -> str 图检索 + query_semantic_events(action_text, top_k) -> list[str] 语义检索 + commit_event(event_data, is_secret) 统一落账 + retire_secret(secret) 秘密退场；InMemoryMemoryService(session, graph_limit) 以会话状态树为存储（写穿透，单一事实来源）：关系图的边 = 台账大事（排在前）+ lore.RELATIONS（两端命中先于一跳），实体与节点两侧都经 kin 展开别名、相互包含即命中（"醉酒的乔峰"也认得「萧峰」的边），语义 = 字符二元组 Jaccard + 点名加权 1.0，及格线 0.2，同分近事优先，语料是 lore.WORLD_RULES 与公开大事；secrets 只进不出；同一条大事两路同形（【台账】标签：原文，压平空白），调用方据此去重；MemoryFactory / in_memory() 每会话一个实例，命名空间在构造时划定
lore.py: 静态世界设定，GRANDMASTERS（13 位会下死手的绝顶高手及别名、杀招；规则层与 System Prompt 共用）与 kin()（同一人物的全部称呼）、is_grandmaster()（含修饰称呼）、RELATIONS（30 条原著开篇时江湖公认的人物门派关系，关系图的静态边；原著后文才揭晓的隐秘一概不录）、WORLD_RULES（18 条江湖常识，带检索关键词，语义检索的静态语料）、OPENING_SEEDS（6 个开局情境，present 为 premise 点名的高手，开局即登记在场）、SHICHEN 十二时辰；置于 app 顶层而非 director 内，避免 session → director → pipeline → session 的导入环
errors.py: 统一错误谱系，GameError 携带 status_code 与玩家可读 message；SessionDead/Busy=409，Director/LLM=502
director/: 导演管线（规则裁决 + 提示词协议 + 解析 + 编排），地图见 director/CLAUDE.md
llm/: 大模型适配（协议 + 厂商客户端 + Mock + 工厂），地图见 llm/CLAUDE.md

设计要点
- ui_status_bar 不进大模型契约：它是 next_state 的纯投影，由 GameState.status_bar() 确定性渲染
- 清单不进大模型快照：DirectorOutput.next_state 只有快照四字段与"本回合新增大事"，大模型从结构上无法整体改写标签账，也无法改写或删除旧大事
- 局部环境与增减只进大模型契约不进响应：InteractResponse.of 以记账后的 GameState 显式构造，内部字段天然不外泄
- 世界台账可以无限增长：控制上下文是记忆仓储（memory_service.py）检索层的职责，台账本身只管忠实地存
- 记忆仓储是导演与存储之间唯一的门：管线只认 MemoryService，读走关系图与语义两条检索路径，写走 commit_event / retire_secret；
  换图数据库与向量库时新写一个实现、在 main.py 换一个工厂，管线一行不改
- involved_entities 只进大模型契约与服务端会话（下一回合的检索种子），不进响应，与局部环境同理
- 情报按可见性分存：player_state.secrets 只有玩家知道（对 NPC 不可见），major_events 只收公开的客观事实；
  secrets 不进 ui_status_bar（状态栏保持用户规定的六段格式），随 next_state 整树回传前端
- 错误响应与 FastAPI 原生 422 同形 {"detail": ...}，前端只需一种解析
- 会话纯内存：进程重启后由客户端快照 current_state 冷启动续玩，死亡标志随之丢失属已知取舍

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
