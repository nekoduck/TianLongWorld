# 天龙八部：平行世界 - 导演大模型驱动的极简文本武侠沙盒，语义标签无数值，作死即永久死亡
Python 3.10+ + FastAPI + Pydantic v2 + pydantic-settings + httpx2 | React 19 + TypeScript 7 + Vite 8 + Tailwind CSS v4
TLBB-Engine（engine/）：Python 3.12+ + FastAPI WebSocket + Pydantic v2 + PostgreSQL 16（asyncpg，JSONB 事件账本）+ Neo4j 5.23+（图谱快照）+ Qdrant（长线记忆）

<directory>
backend/ - FastAPI 服务：前后端协议、内存会话、记忆仓储（GraphRAG 接口地基）、导演管线（RAG 上下文注入）、大模型适配 (3子目录: app/director 导演管线, app/llm 大模型适配, tests 用例)
frontend/ - React SPA：三段式沉浸 UI、打字机叙事、死亡锁死；构建期开关 VITE_ENGINE 在 engine（WebSocket 流式，默认入口）与旧 backend（HTTP，dev:backend）之间切换 (3子目录: src/api 后端门面与 engine 套接字, src/hooks 状态机与打字机, src/components 视图)
engine/ - TLBB-Engine 下一代后端：DDD + CQRS + 事件溯源 + Graph RAG，原著播种（T=0 锚点 + 图谱自愈 + T=0 审计、掌故与地理注记）、事件流折叠（渐进式状态）、图谱裁决 + 语义物理引擎（地下城主推演 → 领域闸门：量级、叙事时钟、等价交换；点选凭气运）、世界心跳（命令耗时、过去式进图谱、消息扩散、黎明生态、局部认知）、空间属性图与探索迷雾（出路带方位 / 交通方式 / 耗时，未知去处只露「未知区域」，方位导航单独下发，问路解开迷雾）、分层 NPC 生态（议程大模型立议程、寻路行军不调大模型、撞见与狭路相逢交判官）、「招」菜单与意图风味封装（叙事同一次调用从可供性目录挑招配风味）、流式叙事 (4子目录: app/domain 本体·掌故·空间属性图与迷雾·渐进式状态·三路赌注·叙事时钟·物理闸门·命令耗时·世界心跳（活动 / 痕迹 / 消息 / 人群）·H-Agent（议程·行军·相撞裁决）·事件·聚合·裁决·端口, app/application 总线·解析·一席裁决（地下城主·气运·简报）·世界时钟·NPC 议程与判官·选项（招·风味闸门）·方位导航·叙事·状态栏·编排, app/infrastructure 播种管道·图谱自愈·审计、掌故与地理闸门·持久化·大模型, app/presentation WebSocket；另有 data/source_text 原著, tests 用例)
</directory>

<config>
backend/requirements.txt - 运行依赖（fastapi / uvicorn / pydantic-settings / httpx2）
backend/.env.example - 大模型、会话与上下文配置模板（HISTORY_TURNS 滑动窗口 3~5、GRAPH_LIMIT 关系网行数 1~30、SEMANTIC_TOP_K 语义检索条数 1~10），复制为 backend/.env 生效（.env 存放密钥，永不入库）；默认 mock 零密钥可跑，推荐 gemini
frontend/package.json - 前端依赖与脚本（dev 连 engine，默认 / dev:engine 同义 / dev:backend 连旧 backend / dev:mock 脱离后端 / build 构建 engine 版 / build:backend）
frontend/vite.config.ts - Vite 插件与开发代理：/api → backend :8000，/ws → engine :8001（ws: true）
engine/requirements.txt - 引擎运行依赖（fastapi / uvicorn / pydantic-settings / httpx2 / asyncpg / neo4j / qdrant-client）
engine/.env.example - 引擎配置模板：大模型四选一且意图 / 叙事 / 地下城主 / 抽取 / 议程五职责各配模型与思考档位（附推荐的 Gemini 组合）、LLM_CALL_LIMIT 调用次数保险丝、FORTUNE_ON_CLICK 点选回合的气运开关、NPC_AGENDA 宏观议程开关与 LLM_AGENDA_BUDGET、EVENT_STORE / GRAPH_BACKEND / QDRANT_URL 各自 memory 或生产实现、MEMORY_RECALL_K 1~10、播种参数；默认全内存 + mock 零依赖可跑
engine/docker-compose.yml - 引擎三件套后端 postgres:16 + neo4j:5.26 + qdrant
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
- 协议单一来源：backend/app/schemas.py 定义形状，frontend/src/types.ts 逐字段镜像；engine 的线协议由 engine/app/presentation/protocol.py 定义，frontend/src/engineTypes.ts 逐字段镜像
</architecture>

<engine_architecture>
TLBB-Engine 一回合（engine/app/application/handlers.py）：
  WebSocket 帧 → CommandBus → TurnPipeline
    命令侧（玩家锁内串行）：重放 PostgreSQL 事件流（decode_event 上抛旧账）→ Player 聚合（evolve 纯函数折叠，熟练度与气血只做加法，无状态表）→ 投影检查点自愈
      → Neo4j 局部真理快照 → [Parse] 自由文本经 WorldviewGuard + 意图解析器（选项与导航的点选按快照重算 affordances ∪ navigation 核验、取回 underlying_command，不经大模型）
      → [Validate] domain/rules 纯函数裁决（物理看快照、逻辑看聚合与火候，驳回落为 ActionFailed；出手 / 交涉 / 暗中由 combat / social / covert 圈出可裁区间，经 stakes 统一门面；rules.envelope 把它换算成物理边界 Envelope，结果已定之事为 FIXED；rules.command 按裁决结果查表定下这一招花几刻 time_cost，移动取所走那条出路的耗时）
      → [Resolve] 一席裁决（语义物理引擎）：自由文本在胜负未定或此景挂着时钟时请地下城主——大模型按 属性碰撞 → 量级（爆炸 / 暗流）→ 代价 → 时钟与收敛 推演出 ResolutionOutput（deltas / clock_mutations / new_facts / action_trigger）；点选只在胜负未定时由气运（FortuneResolver，种子 = 玩家 | 对象 | 尝试次数）确定性地取值
      → [Event] 领域闸门 resolution.settle：由属性变化推出结局（出界整份作废取确定性裁决）、钳位、按等价交换补足代价、时钟满格坍缩为硬结算，再经 settle_any 落成路线事件
      → 世界时钟 WorldClock.advance：余波（交手的往事与痕迹、人群受惊溃散、公开之事成一枚只有此地知道的消息）→ TimePassed(time_cost，驳回也花一刻) → 消息沿 CONNECTS_TO 扩散 → 黎明生态（风化、顺手牵羊）→ 微观行军（带议程的 NPC 沿最省时之路一刻一刻推进，走进玩家所在即撞见、走到开篇仇人所在即狭路相逢，中断并停步），死者无心跳；
        定案与心跳同批乐观并发追加（时钟四事件 / FactEmerged / RenownChanged / 心跳七事件一并入账）→ 同步投影 Neo4j 覆盖层
      → H-Agent（application/npc_agent）：中断非空即请判官裁决（撞见在结果已定的物理边界里推演、过 resolution.settle；狭路相逢在可裁区间里挑结局、可致带伤败退；每回合至多一场请判官，其余确定性）
        → 规划时机（初临江湖 / 新的一日 / 江湖震动）才请议程大模型为核心 NPC 立议程、过 npc.admit → 第二批追加与投影
    查询侧（无锁）：新快照 → Qdrant 两路召回（原话一路、焦点与在场者一路）→ turn_resolved（事实白描，含时钟四事件 / 微观事实 / 名望，空串不出声）→ [Options] 先算好三份：可供性目录（≤12 招，按战术轴激化 / 诡道 / 化解 / 旁观轮转，编号 m1…）、退路菜单（3~4 席「招」，四类候选源，带 why 与风险档，同一对象至多两席）、方位导航
      → [Render] Hard Prompt 流式叙事，同一次调用正文之后交 <menu> 挑 3~4 招配风味（目录进 <affordances>，正文只给挑中的招铺垫端倪；<exits> 写「方位｜去处｜交通方式｜路程」、未知去处只写「未知区域」；在场者附来意；<clocks> 与 <emerged> 进真理快照，时钟只作暗流；<time> / <crowds> / <activities> / <traces> / <rumors> 只给此地的切片，跨进新地方那一回合带 <short_term_memory>）∥ 记忆写入 → options.compose 过闸（风味不合格退回朴素标签、不足由退路补）→ turn_completed（options 带 flavor_text 与 tactical_axis、navigation 方位导航；状态栏带人情 bonds、心事 pursuits、名望 renown、时辰 time 与至多 4 只眼前的时钟 clocks）

关键决策：
- 世界播种：原著 TXT → 语料清洗（去水印、去序跋）→ 大模型逐块抽取名称级记录（时间锚点 T=0：开篇之后的变化只进 events，描述防抄）
  → 组装器确定性定案（泛称与描述不成实体、正名互见才合并、本名只在知本名的记录里投票、events 否决被时间线污染的开篇状态、
  落不了地即丢弃、门径落不了地即封存、被武学引用却无处安放的物品留作孤儿）→ 图谱自愈（据原著常识安放孤儿，provenance=推断，过闸门、可审阅）
  → T=0 审计（关系结于何时 era、描述拆出后文剧情、物性、后来才到场）→ 掌故（外显人设、可打探的见闻与无名人群）→ 地理注记（出路的方位、交通方式、耗时，地标与名胜；没注记的由出口标签与处所名推出）→ WorldBlueprint（图谱正典）→ 参数化 Cypher；引擎不凭空捏造地点人物武功，推断永远与原著分得清。
  当前入库：前 40 块（第一回至第九回）的 v6 蓝图、逐块抽取记录与自愈 / 审计 / 掌故 / 地理缓存（engine/data/world/；地理 248 条道路注记、19 条可见性注记）；抽取器、自愈者、审计者与撰写者可换、契约不变——
  export / ingest 让大模型之外的作答者（子代理、人工）经同一道闸门入缓存
- 语义本体：人物以本名 true_name 为主键（称号 titles、别名 aliases 只作指称）；武学分获取要求（门径）与修炼要求（根基）两道门
- 渐进式状态：武学等级 = Σ SkillPracticed 熟练度 × 悟性系数 → 火候（火候不到境界打折）；气血 = Σ HealthChanged（钳位）→ 伤势；拿到秘籍不等于学会
- 逻辑死线 + 模糊裁决：境界是有序等级；出手不再一锤定生死——境界差 × 性情 × 伤势圈出可裁区间，地下城主在物理边界里推演、领域据属性推出结局，
  越级取胜与非极端找死的毙命不在区间里；人情是五档阶梯（敌视 / 戒备 / 漠然 / 友善 / 信赖），信赖只来自物归原主（封闭清单，从物主本人手里拿来的再还不算），交涉止于友善，名声只认开篇羁绊（休戚与共者记恨、开篇仇家升一档），求艺三流须友善、二流以上须信赖，武功只来自肯教之人或原著典籍且两道门逐条核验，兵器不改境界；「招」= (动作, 手段) 查封闭兼容表分战 / 交 / 暗三路，交涉与暗中永不致死，暗取差两境以上绝不到手
- 量级：推演先判爆炸（不可逆，当场硬结算：得手 / 重伤 / 毙命 / 如愿 / 翻脸 / 无痕 / 败露 / 失手）还是暗流（只许软结局：相持 / 轻伤 / 松动 / 无果 / 碰壁 / 未遂，硬结局拉到最近的软结局，且必须挂上或推进一只时钟，没挂由领域按路线补挂）；名望爆炸 [−10, +5]（只有得手类结局才涨）、暗流 [−3, 0]
- 叙事时钟：NarrativeClock（clk: id、≤12 字语义名称、疑心 / 敌意 / 危机 / 进展四种、挂在此地 / 在场之人 / 可见之物 / 你身上、进度 < 阈值 4/6/8、满则如何 ≤30 字），由地下城主动态新建、推进、回退、销毁（总数 ≤6、每个挂处 ≤2、一次至多三格、一回合每只至多动一次）；
  满格即坍缩为确定性的硬结算（疑心：翻脸 + 名望 −5；敌意：敌视并出手伤你，受创 30 留一口气，已被制住则无从出手；危机：受创 30 留一口气，挂在此地或你身上即被迫脱身；进展：人情 +1 至多友善），点选回合从不为时钟请人
- 等价交换：欠的格数 = 结局好过确定性裁决几格 +（得手类结局时）越出舒适区的 strain；付的格数 = 旁人人情一档一格 + 凶险时钟净添的格数（只算满了还会有后果的：挂在被制住之人身上的敌意时钟不算）+ 名望五点一格 +（暗取）气血十点一格；
  不够的由领域补成对象身上一只凶险时钟（旧恨 / 戒心 / 疑心），补满即坍缩，挂不上或对象已被制住折名望——规则与气运好过确定性裁决同样要付
- 菜单跟着剧情走：选项是 (状态, 快照) 的纯函数、世界不变则逐字不变；按焦点（近来亲手打过交道的人与物）、仇人、伤势打分，
  设调养席 / 跟进席，其余按 MMR 取，每项附 ≤12 字的 why；移动不在菜单里，脱身之路是方位导航上的 retreat 标记（不逃回险地）；断线重连走 quiet 续接，只回选项、导航与状态、不调大模型；
  P1 起选项是「招」(动作, 手段)：一人身上按兼容表展开攀谈 / 结交化解 / 打探 / 出手 / 求艺恳请 / 讨要偷取夺物 / 借势，心事未了时给出没试过的手段（换个手段），每项带风险档（稳妥 / 有险 / 凶险，不露结局）
- 意图风味封装：一招 = 引擎可读的标准指令 underlying_command（rules.command 算出，只在服务端）+ 战术维度（激化 / 诡道 / 化解 / 旁观，approach.axis_of 封闭表定，不由措辞定）+ 玩家看见的 flavor_text；
  叙事大模型只在引擎给定的可供性目录里挑 3~4 招配 ≤20 字风味，options.compose 过闸（无结局字眼、不点场景之外的原著名字，不合格退回朴素标签），点了执行的永远是 underlying_command，指令与意图从不进提示词
- 空间属性图与探索迷雾：CONNECTS_TO 带方位 / 交通方式 / 耗时（domain/geography.ways：子代理撰写、经 geo 闸门入库的注记优先，否则由出口标签与处所嵌套推出，往返方位永远相反），移动耗时、NPC 寻路与导航同读它；
  去处的认知（亲历 / 问路 / 远眺 / 名胜 / 未知）是玩家的、不是世界的，未知只露「未知区域」，出口标签与迷雾里的地名不进任何提示词与下发字段；移动从菜单剥离为方位导航（指令只用方位把手），寻常攀谈问此地或去处即问路（PlacesLearned）解开迷雾
- 分层 NPC 生态（H-Agent）：宏观层议程大模型只在初临江湖 / 新的一日 / 江湖震动时为有执念的核心 NPC 立一轮议程（按执念与传到他所在之处的消息，过 npc.admit），微观层按道路耗时寻路行军、一个大模型也不调，
  撞见玩家或与开篇仇人狭路相逢才中断交给判官（地下城主职责）在领域闸门里坍缩——撞见只动时钟 / 事实 / 名望，狭路相逢可致带伤一日、议程败退（带伤者交手与被暗取时境界折一档）；失灵一律退回确定性裁决，别处的事只经消息与快照被感知
- 平行世界：一位玩家 = 一条事件流 = 一个聚合；Neo4j 正典只读，每个世界一层可抹去重放的覆盖层（HELD_BY / CONSUMED / LEARNED / VISITED / HEARD_OF {world}、叙事时钟 (:Clock)-[:ON]->实体、微观事实 (:Emerged)-[:ABOUT]->实体、NPC 此世所在 (:Character)-[:AT {world}]、带伤 WOUNDED {world, until} 等）
- 世界心跳：时间是第一物理量——每条命令都花时间（一刻 15 分钟：移动取所走那条出路的耗时（道路注记，没注记时换处所 4 / 同一处所内 1，远行至多七日）/ 修习调息 8 / 其余与驳回 1）；过去式经 Activity / EnvironmentalTrace 持久化于 Neo4j 覆盖层 (:Activity|Trace|Rumor {world})，痕迹随时间消散，人群（正典 :Swarm，掌故入库）受惊溃散、半日后回来，黎明结算露天之物的风化与顺手牵羊（哈希确定）；
  FactToken 沿 CONNECTS_TO 一刻一两处地物理传播，在场之人只知传到此地的消息、亲眼所见与自己的 T=0 见闻，全局事件流从不进提示词；MOVE 的此行所为随 Moved 入账，跨进新地方那一回合的短期记忆与眼前所见对照写出预期落差
- 大模型五职责皆无状态且无写端口：抽取原著（含自愈推断）、解析意图、地下城主推演（只在自由文本回合，简报只用 T=0 事实，foreshadow 与未知见闻永不进；微观事实点了原著名录里却不在此景的名字即丢；撞见与狭路相逢的判官也是它）、宏观议程（只在规划时机，过 npc.admit）、渲染文本（连同挑招配风味）；推演必经领域闸门，入账的只有闸门放行的属性、时钟、≤3 条不带状态字眼的微观事实，叙事散文不入事件与记忆；
  一回合的调用：意图 + 地下城主 +（撞见 / 狭路相逢的判官，每回合至多一场）+（议程，只在初临江湖 / 新的一日 / 江湖震动时一次）+ 叙事（菜单与正文同一次调用交出）——寻常回合仍至多三次，地下城主与判官不合契约各可重采样一次；
  点选回合不调意图与地下城主，结果已定的文本回合只在眼前挂着时钟时请地下城主；正文只许给挑中的招露成端倪，不许写成结果
- 花钱的边界：原著抽取、图谱自愈、T=0 审计、掌故与地理注记由 Claude 子代理经 export / ingest 担任，绝不调用付费大模型（抽取与自愈须显式 --use-llm 才装配、审计、掌故与地理根本没有这条路，2026-10 曾两次跑空 Gemini 预付额度）；
  付费大模型只服务运行期四职责（意图 / 地下城主（含判官）/ 议程 / 叙事），各职责共用每进程的调用次数保险丝 LLM_CALL_LIMIT，熔断即走各自的退路
- 每个端口都有内存实现，与生产实现共跑契约测试；内存图谱复用领域 evolve，与 Neo4j 快照逐字段相等
</engine_architecture>

法则: 极简·稳定·导航·版本精确
