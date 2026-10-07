# 天龙八部：平行世界 - 导演大模型驱动的极简文本武侠沙盒，语义标签无数值，作死即永久死亡
Python 3.10+ + FastAPI + Pydantic v2 + pydantic-settings + httpx2 | React 19 + TypeScript 7 + Vite 8 + Tailwind CSS v4

<directory>
backend/ - FastAPI 服务：前后端协议、内存会话、记忆仓储（GraphRAG 接口地基）、导演管线（RAG 上下文注入）、大模型适配 (3子目录: app/director 导演管线, app/llm 大模型适配, tests 用例)
frontend/ - React SPA：三段式沉浸 UI、打字机叙事、死亡锁死 (3子目录: src/api 后端门面, src/hooks 状态机与打字机, src/components 视图)
</directory>

<config>
backend/requirements.txt - 运行依赖（fastapi / uvicorn / pydantic-settings / httpx2）
backend/.env.example - 大模型、会话与上下文配置模板（HISTORY_TURNS 滑动窗口 3~5、GRAPH_LIMIT 关系网行数 1~30、SEMANTIC_TOP_K 语义检索条数 1~10），复制为 backend/.env 生效（.env 存放密钥，永不入库）；默认 mock 零密钥可跑，推荐 gemini
frontend/package.json - 前端依赖与脚本（dev / dev:mock / build）
frontend/vite.config.ts - Vite 插件与 /api → :8000 开发代理
</config>

<architecture>
一回合数据流：
  ActionPanel → useGame.act → POST /api/interact → Director.interact
    → Session.acting()   守卫：死者不得行动、上一招未落定不得出下一招
    → lethal.judge()     规则层裁定生死（无绝学 ∧ 敌意 ∧ 点名 ∧ 在场 → 必死；绝学读 martial_arts，在场读局部环境 present_npcs；
                         敌意与点名只审 perception.surface 剥去内心念头后的表面行为——高手不会读心）
    → Director._context() RAG 上下文注入：MemoryService（app/memory_service.py）是导演与存储之间唯一的门，全量台账止步于此——
                         query_relational_graph(上一回合 involved_entities + 在场 NPC + 所在地 + 公开身份) → 实体关系网（台账大事 + 原著关系）；
                         query_semantic_events(这一招的表面行为) → 相关往事与江湖常识（与关系网去重）；
                         分别作为 [Graph_Context: 当前实体关系网] / [Semantic_History: 历史相关事件] 注入 System Prompt（静态法则恒为前缀）
    → prompts.build_*()  XML 标签组装 User Message（玩家状态 + 私密情报 + 局部环境 + 滑动窗口 + 动作 + 指令），
                         secrets 单独成段并标明 NPC 不可见，回合指令附落笔前自查；必死时重写为处决指令
    → LLMClient          纯文本进出 + 契约 schema（gemini 结构化输出 / openai 兼容 / anthropic / mock）
    → parser             截取 JSON + Pydantic 校验，失败重采样
    → 生死封印            规则判死则强制 game_over，大模型无权赦免
    → Session.advance()  服务端状态唯一权威：evolve = 快照照单全收 + 四本标签账 reconcile；局部环境 observe（同图只认增减，切换地图强制清空）
    → _settle(memory)    情报与大事统一经 commit_event 落账：先 retire_secret 让揭穿的秘密退场，再收新知（absorb：包含去重、满员请走最早的），
                         最后记公开大事（chronicle 只追加；与仍持有的秘密是同一件事的不入账——私密优先）；
                         随后 perception.witnessed 拿落账后的情报账定下一回合的检索种子（在场 / 所在地之外，须出现在剥去念头的叙述里且不牵涉仍持有的秘密）
  ← InteractResponse（ui_status_bar 由服务端从 next_state 确定性渲染）→ 打字机 → 选项浮现

关键决策：
- 服务端权威：请求里的 current_state 仅用于会话丢失时冷启动恢复，不能覆盖服务端状态（防篡改）
- 生死归规则、叙事归模型：确定性规则裁决点名挑衅，System Prompt 内的高手名录让模型裁决"那人"式指代，永久死亡由服务端 409 守住
- 在场人物结构化：叙事可以含蓄（"那魁梧大汉"），local_delta 必须写破（"乔峰"）；局部环境是服务端内部结构，不进前端协议
- 状态是一棵树：current_state / next_state = { player_state, world_state }，前端每次请求整树回传
- 状态按生命周期分存：location/time/weather/health_status 是大模型每回合重写的快照；buffs_debuffs / social_traits / inventory / martial_arts
  四本标签账与 secrets 私密情报账由服务端记账，大模型只能上报增减（player_delta），遗漏不等于失去
- 情报隔离（Fog of War）：情报按可见性分存——secrets 只有玩家知道，major_events 只收天下皆知或已发生物理改变的客观事实，
  social_traits 只记公开名声。导演全知但不外借：secrets 与未示人之物对 NPC 绝对不可见，NPC 只凭自身认知、玩家表面行为与公开世事行事；
  玩家暗中所为在台账里只写旁观者看得到的后果，真相进 secrets；secrets 只进不出——不作 RAG 检索键、不进检索结果，叙事里复述的念头
  与仍是秘密的人事也种不进下一回合的关系图，免得秘密每回合把相关历史拽进上下文；参考模块是导演的案头资料，不是 NPC 的共同记忆；
  NPC 认得出玩家（见过、自报家门、服色、信物、画像）才按 social_traits 与台账旧事对待他；玩家的内心念头对 NPC、规则层与检索都不可见
- 被动沙盒：不为推进剧情凭空制造宿命与巧合，闲逛只得环境的自然反馈，路人按普通人的逻辑生活；平淡的一回合同样合法——
  危机与悬念只能来自玩家行为的合理后果或眼前本就在场的人与事，选项不为制造机缘凭空设局
- 世界台账 major_events = [{tags, event_desc}]：只追加、不合并、不删除、不设上限；大模型只能在 next_state.major_events 写本回合新增，
  复述旧事被去重。台账可以无限增长，喂给大模型的永远只是检索出的至多 GRAPH_LIMIT + SEMANTIC_TOP_K 行
- 记忆仓储（Repository Pattern）：导演管线只认 MemoryService 抽象（图检索 / 语义检索 / commit_event 统一落账 / retire_secret），
  现行 InMemoryMemoryService 以会话状态树为存储（写穿透，单一事实来源）；换图数据库与向量库只需新写实现、在 main.py 换工厂；
  仓储按会话划定命名空间，接口方法因此不带会话参数
- 实体提取：大模型每回合在 next_state.involved_entities 写出本回合涉及的核心专名（地点、人名、武功、门派、物品），
  服务端只留场面上看得见的，作为下一回合关系图检索的种子；它是服务端内部字段，不进前端协议
- Prompt 载荷恒定：System Prompt = 静态法则 + 关系网（至多 GRAPH_LIMIT 行）+ 往事与常识（至多 SEMANTIC_TOP_K 行）；
  User Message = 玩家状态 + 私密情报（至多 16 条 × 60 字）+ 局部环境 + 滑动窗口（3~5 回合）——每一项都有上限，长度与游戏进度、台账长度无关
- 局部环境：同一地图（新地点包含原地点全称）只认到场 / 离场增减；切换地图强制清空旧在场者；在场者经 lore.kin 按身份认人；
  满员时绝顶高手优先留下；开局种子点名的高手按开局地点登记，规则层的生死判定不依赖大模型记得写出他们
- 协议单一来源：backend/app/schemas.py 定义形状，frontend/src/types.ts 逐字段镜像
</architecture>

法则: 极简·稳定·导航·版本精确
