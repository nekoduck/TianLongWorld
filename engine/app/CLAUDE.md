# app/
> L2 | 父级: engine/CLAUDE.md

引擎应用包，DDD 分层自内向外单向依赖：domain（本体与掌故 + 空间属性图 + 事件 + 聚合 + 裁决包 rules/ + 命令耗时与世界物理 + H-Agent 物理 + 端口）← application（命令总线 + 解析 + 一席裁决 + 世界时钟 + NPC 议程与判官 + 选项与导航 + 叙事 + 状态栏 + 编排）
← infrastructure（播种管道 + 持久化 + 大模型）/ presentation（WebSocket）。domain 不认识任何外层；端口（ABC）由内层拥有、外层实现（依赖倒置）。
container.py 是唯一知道"端口背后是谁"的地方，main.py 只是进程入口。

成员清单
__init__.py: 包标识，一行导航注释
errors.py: 共享内核的错误谱系，EngineError(code, message) 及 UnknownPlayer / PlayerDead / Concurrency / OptionExpired / WorldNotSeeded / Projection / LLM（带 retryable：限流与 5xx 可重试，欠费鉴权不可）/ Extraction；presentation 按 code 映射为错误帧
config.py: 配置唯一入口，pydantic-settings 读 engine/.env（锚定于文件而非 cwd）；大模型四选一（mock 为默认），LLMRole 五职责（意图 / 叙事 / 地下城主 / 抽取 / 议程）经 llm_profile 各取（模型, 思考档位），职责专属配置留空即退回缺省档，地下城主另有时间预算 llm_resolution_budget（撞见与狭路相逢的判官共用），议程另有时间预算 llm_agenda_budget（缺省 10 秒）与开关 npc_agenda（缺省开；关则 NullPlanner，NPC 守在原处），点选回合的气运开关 fortune_on_click（缺省开；关则点选一律取确定性裁决），各职责共用调用次数保险丝 llm_call_limit（每进程缺省 500，0 不设限）；四类后端各自 memory / 生产实现、memory_recall_k 钉死 1~10、播种参数；blueprint_path 派生属性
container.py: 组合根 build_container(settings, blueprint, llm, resolver) → Container（bus / pipeline / coordinator / store / reader / projector / seeder / closers）；无大模型时换上 HeuristicIntentParser、CanonicalResolver 与 TemplateNarrator，有则意图职责的客户端喂 LLMIntentParser、地下城主职责的客户端喂 LLMResolutionAgent、地下城主与气运（FortuneResolver，fortune_on_click 关掉即 None）合成一席裁决 AdjudicationSlot 交给流水线、叙事职责的客户端喂 FallbackNarrator(LLMNarrator, TemplateNarrator)，各职责共用一份 CallBudget 保险丝，意图解析的守卫经 WorldviewGuard.for_canon 豁免原著撞词正名（Neo4j 后端读入库的 blueprint.json）（测试注入的 llm 同时顶替意图、地下城主、叙事、判官与议程（npc_agenda 开着时），resolver 可单独注入一个不守规矩的替身以证明钳位由领域守住）；静态地理 Atlas.of 取同一份正典（memory 后端即种下的蓝图，Neo4j 后端读入库的 blueprint.json，都没有则空 Atlas：时间照走、消息只留在发源地、没有核心 NPC）一份，同时交给 WorldClock、TurnPipeline（errands 认家）与 NpcDirector；memory 图谱自动加载 blueprint.json；LLMResolutionAgent 的 canon_names 取正典蓝图的全部名录（人物本名 / 称号 / 别名，地点、武学、物品的名字）作微观事实预筛，风味闸门（options.compose）的 canon_names 是同一份原著名录；分层 NPC 生态 NpcDirector——规划者在有大模型且 NPC_AGENDA 开着时用议程职责（LLMRole.AGENDA，LLM_AGENDA_BUDGET 管时）的 LLMAgendaPlanner，否则 NullPlanner；判官在有大模型时用地下城主职责客户端的 LLMEncounterJudge（LLM_RESOLUTION_BUDGET、原著名录预筛），否则 CanonicalJudge；人设取正典 personas（执念）、relations 取正典关系（开篇恩怨）
main.py: create_app(settings, container_factory) 应用工厂，lifespan 装配与释放容器；挂载 /ws/play 与 GET /health（含是否已播种）；模块级 app 供 uvicorn
seed.py: 播种命令行 `python -m app.seed extract|assemble|export|ingest|heal|audit|lore|geo|apply|script`（INFO 级进度日志）——花钱的路须显式走：extract 只在 --use-llm 时读原著经抽取职责的大模型抽取（不带即拒绝并指路 export / ingest——本项目的抽取由 Claude 子代理完成），确定性组装、依次自动套用自愈 / 审计 / 掌故 / 地理缓存（零费用、确定性），写出 blueprint.json / seed.cypher / report.txt（--apply 立即写图，--max-chunks 取开篇作时间切片；有失败块时只写报告、不覆盖旧蓝图，--allow-partial 例外）；assemble 只读某一版提示词的缓存零费用重新组装（同样依次套用四份缓存）；export 导出抽取铁律与当前版本尚未缓存的块、ingest 把大模型之外的抽取器（子代理、人工）对某块的产出经同一道闸门写入缓存——抽取器可换，契约不变；extract 会把现有 blueprint.json 生成跨块命名参考交给抽取器；heal 体检蓝图里被武学引用却下落不明的孤儿物品（--retry-null 重问已判无从推断的），经 --export / --ingest 交给子代理（或 --use-llm 时经抽取职责的大模型；不带只套缓存）据原著常识推断安放、过闸门后写回蓝图与脚本，--apply 经 MERGE 把推断的边（provenance=推断）写进 Neo4j；heal 重建蓝图后照缓存补回审计、掌故与地理；audit 导出 T=0 审计的分批题面（--batch / --all）、--ingest 过闸后入 audit.json 并写回蓝图（与播种同一个新鲜度判据，只豁免本批重答的人物）；lore 以一地为中心沿 CONNECTS_TO 走若干跳（默认剑湖宫·练武厅两跳）导出人设、见闻与人群题面、--ingest 过闸后入 lore.json 并写回；geo 从剑湖宫·练武厅（--from）起按广度优先把出口按无向的一对分批导出道路题面（--batch-pairs 缺省 40，一对两向同批，--all 连缓存里两向已答的也出）、另出一批全部地点的可见性题面，--ingest 过闸后入 geography.json 并写回；audit / lore / geo 须 --by 署名，没有 --use-llm，不带参数即按缓存重新套用，只改 report.txt 的 [审计] / [掌故] / [地理] 分节（每条折成一行）；audit 与 lore 重建蓝图时同样依次补回后面几段（自愈 → 审计 → 掌故 → 地理）；蓝图带审计痕迹而审计缓存用不上时 audit 与 heal 拒绝并指路 assemble；有审计缓存时套用顺带读证据库，撞上后文的 T=0 描述在 [审计] 标 ⚠；apply 把蓝图写进 Neo4j（--reset 开新纪元）；script 由蓝图重生成脚本；各命令的回显（蓝图已写出 / 审计已套用 / 已入掌故缓存 / 掌故已套用）都点出人群数，蓝图已写出另点出道路注记与可见性注记数
domain/: 领域层，地图见 domain/CLAUDE.md
application/: 应用层，地图见 application/CLAUDE.md
infrastructure/: 基础设施层，地图见 infrastructure/CLAUDE.md
presentation/: 表现层，地图见 presentation/CLAUDE.md

设计要点
- 读写分离：命令侧（解析 → 裁决 → 世界心跳（含 NPC 行军）→ 追加事件 → 同步投影图谱 → H-Agent：相撞的裁决、规划时机的议程 → 第二批追加与投影）持玩家锁串行；查询侧（快照 → 召回 → 可供性目录 / 退路菜单 / 方位导航 → 叙事（同一次调用交 <menu>）∥ 记忆写入 → compose 过闸）无锁
- 一席裁决：一回合只请一位裁决者——自由文本在胜负未定或此景挂着时钟时请地下城主（语义物理引擎：briefs/ 出简报，大模型按 属性碰撞 → 量级 → 代价 → 时钟与收敛 推演出 ResolutionOutput，domain/resolution.settle 过闸定案：推出结局、钳位、补足代价、时钟坍缩），点选只在胜负未定时交给气运 FortuneResolver（种子 = 玩家 | 对象 | 尝试次数，不取版本号，闲谈换不来重掷；canonical 60% / 好一格 25% / 差一格 15%，毙命 / 翻脸 / 败露 / 失手绝不比确定性裁决更重；好过确定性裁决同样欠代价），其余谁也不请；寻常回合至多三次大模型调用（意图 + 地下城主 + 叙事，地下城主不合契约重采样一次是唯一例外），H-Agent 只在相撞（判官 ≤1）与规划时机（议程 ≤1）另添，点选回合不调意图与地下城主
- 语义物理引擎（神经-符号-神经）：大模型描述发生了什么（属性变化、时钟指令、≤3 条微观事实、路由），领域据属性推出结局并逐项过物理闸门——量级（暗流只许软结局且须挂钟）、叙事时钟（满格坍缩为硬结算）、等价交换（越出舒适区的得手要付代价，付不够补成凶险时钟）；入账的只有闸门放行之物，叙事只经入账的白描与快照里的 <clocks> / <emerged> 知道推演
- 世界心跳（精神时光屋、跨房间记忆清空、全知视角幻觉之治）：时间是第一物理量——每条命令都有 time_cost（domain/commands：一刻 15 分钟，移动取所走那条出路的耗时（道路注记，或推出的换处所 4 / 同一处所之内 1）/ 修习调息 8 / 其余与驳回 1，由 rules.command 按裁决结果查表），定案之后 application/world_clock 依次算余波（交手的往事与痕迹、人群受惊溃散、公开之事成一枚只有此地知道的消息）→ TimePassed → 消息沿 CONNECTS_TO 扩散 → 跨过黎明的风化与顺手牵羊 → 带议程的 NPC 寻路行军，与定案同批入账，死者没有心跳；
  过去式（活动 / 痕迹 / 消息）是图谱覆盖层、人群是正典 (:Swarm)，都只以此地的切片进快照——在场之人只知传到此地的消息、亲眼所见与自己的 T=0 见闻，全局事件流从不进提示词；MOVE 的此行所为随 Moved 入账，跨进新地方那一回合出发前的快照经 recollect 成为短期记忆交给说书人写出预期落差；一切由 domain/heartbeat 的纯函数与图谱属性驱动，表现层不打文本补丁
- 意图风味封装：菜单先算好可供性目录（options.catalogue，至多 12 招、四根战术轴轮转、每根有招的轴都在）与退路菜单，目录编号 m1… 作 <affordances> 交给说书人；说书人在叙事的同一次调用里写完正文、另起一行交 <menu>（挑 3~4 招、尽量覆盖不同战术维度、各配 ≤20 字的武侠风味），正文只给挑中的几招铺垫端倪——不许写成已发生的结果、不许替玩家行动、不许列成选项；流式只吐正文，<menu> 由 options.compose 过闸（编号在目录里、风味合格——不写结果、不点场景之外的人与物——才换上 flavor_text，不足由退路菜单补）；前端展示风味文案，玩家点 id，服务端执行重算出来的 underlying_command——选项的指令与意图永不进提示词
- 空间属性图与探索迷雾：CONNECTS_TO 带方位 / 交通方式 / 耗时（domain/geography.ways：子代理撰写、经 geo 闸门入库的道路注记优先，否则由出口标签与处所嵌套推出），移动的耗时、NPC 寻路的权重、导航的方位都读它；去处的认知（亲历 / 问路 / 远眺 / 名胜 / 未知）是玩家的而不是世界的，未知下发「未知区域」；出路一律写「方位｜去处｜交通方式｜路程」，出口标签（常带地名）不进说书人、地下城主与意图解析的任何提示词；意图解析把「往东」落到方位把手、把问路解析为寻常 TALK（话题此地，规则据此记下 PlacesLearned 解开迷雾）
- 分层 NPC 生态（H-Agent）：宏观层议程大模型一日至多一轮（初临江湖 / 新的一日 / 江湖震动，为有执念的核心 NPC 按此地情报立 NpcAgenda，过 npc.admit）；微观层 domain/npc.march 沿道路耗时寻路、一刻一刻推进，不调大模型；裁决层撞见 / 狭路相逢每回合至多一场请判官，都在领域闸门之内（撞见只动时钟 / 事实 / 名望，狭路相逢可致带伤败退——战力洗牌），失灵即确定性裁决；H-Agent 的事件第二批入账，别处的事不宣告（只有玩家眼前的那一面出声）
- 菜单跟着剧情走：选项是 (玩家状态, 快照) 的纯函数，一个选项就是一「招」(动作, 手段)——四类候选源按兼容表展开、按焦点 / 仇人 / 伤势 / 心事线索打分，退路菜单取调养席 / 跟进席 / MMR 3~4 席、同一对象至多两席，每席带一句 why 与风险档（稳妥 / 有险 / 凶险，只看区间最坏的一端）；措辞变体按意图哈希挑，没有版本号轮换；移动从菜单剥离为方位导航（application/navigation：方位、去处或「未知区域」、交通方式、耗时、认知），脱身席变成导航上的 retreat 标记（仇人在侧时标出 rules.retreat 那条），指令只用方位把手，未知去处的名字不进任何下发字段
- 大模型五职责全部无状态、无写端口：播种时抽取原著（与自愈代理推断孤儿的安放；本项目由 Claude 子代理经 export / ingest 担任，T=0 审计、掌故与地理注记同样由子代理撰写，不花钱）、命令侧解析意图、地下城主裁决（只在自由文本回合；撞见与狭路相逢的判官也是它）与宏观议程（只在规划时机）、查询侧渲染文本（连同挑招配风味）；
  最远只能产出一条被规则驳回的意图、一份被闸门钳位或整份作废的推演、一条过不了 npc.admit 的议程、一句退回朴素标签的风味文案、或一条过不了闸门的安放
- 人情是五档阶梯（敌视 / 戒备 / 漠然 / 友善 / 信赖），只比 rank：仇人只认敌视，二流及以上的武学只传信赖之人，信赖的来路是封闭清单（P1 只有物归原主）——东西须不是从物主本人手里拿来的（PlayerState.taken_from），偷来、讨来、夺来再还只是易手
- 渐进式状态：熟练度与气血在事件里只做加法，火候与伤势读取时现算；出手 / 交涉 / 暗中三路的胜负都由领域圈出可裁区间（domain/stakes 统一门面）、地下城主在区间里挑——不再是境界比大小的二极管；交涉止于友善、仁厚者不翻脸，交涉与暗中永不致死
- 「招」= (动作, 手段) 查 domain/approach 的封闭兼容表定路线：表外手段退回寻常，所图缺省推断；心事线索、已知见闻、用掉之物、尝试次数都由事件折叠进 PlayerState
- 平行世界：一位玩家即一条事件流即一个 Player 聚合，世界相对原著的偏离全在这条流里；正典只读，覆盖层可整体抹去并重放重建
- 每类存储都有内存实现且与生产实现共跑同一套契约测试：零依赖可玩，换后端不换行为

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
