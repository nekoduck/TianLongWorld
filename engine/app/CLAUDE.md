# app/
> L2 | 父级: engine/CLAUDE.md

引擎应用包，DDD 分层自内向外单向依赖：domain（本体 + 事件 + 聚合 + 裁决 + 端口）← application（命令总线 + 解析 + 选项 + 叙事 + 编排）
← infrastructure（播种管道 + 持久化 + 大模型）/ presentation（WebSocket）。domain 不认识任何外层；端口（ABC）由内层拥有、外层实现（依赖倒置）。
container.py 是唯一知道"端口背后是谁"的地方，main.py 只是进程入口。

成员清单
__init__.py: 包标识，一行导航注释
errors.py: 共享内核的错误谱系，EngineError(code, message) 及 UnknownPlayer / PlayerDead / Concurrency / OptionExpired / WorldNotSeeded / Projection / LLM / Extraction；presentation 按 code 映射为错误帧
config.py: 配置唯一入口，pydantic-settings 读 engine/.env（锚定于文件而非 cwd）；大模型四选一（mock 为默认），LLMRole 三职责（意图 / 叙事 / 抽取）经 llm_profile 各取（模型, 思考档位），职责专属配置留空即退回缺省档；四类后端各自 memory / 生产实现、memory_recall_k 钉死 1~10、播种参数；blueprint_path 派生属性
container.py: 组合根 build_container(settings, blueprint, llm) → Container（bus / pipeline / coordinator / store / reader / projector / seeder / closers）；无大模型时换上 HeuristicIntentParser 与 TemplateNarrator，有则意图职责的客户端喂 LLMIntentParser、叙事职责的客户端喂 FallbackNarrator(LLMNarrator, TemplateNarrator)（测试注入的 llm 同时顶替两种职责）；memory 图谱自动加载 blueprint.json
main.py: create_app(settings, container_factory) 应用工厂，lifespan 装配与释放容器；挂载 /ws/play 与 GET /health（含是否已播种）；模块级 app 供 uvicorn
seed.py: 播种命令行 `python -m app.seed extract|apply|script`——extract 读原著经抽取职责的大模型抽取、确定性组装，写出 blueprint.json / seed.cypher / report.txt（--apply 立即写图，--max-chunks 取开篇作时间切片）；apply 把蓝图写进 Neo4j（--reset 开新纪元）；script 由蓝图重生成脚本
domain/: 领域层，地图见 domain/CLAUDE.md
application/: 应用层，地图见 application/CLAUDE.md
infrastructure/: 基础设施层，地图见 infrastructure/CLAUDE.md
presentation/: 表现层，地图见 presentation/CLAUDE.md

设计要点
- 读写分离：命令侧（解析 → 裁决 → 追加事件 → 同步投影图谱）持玩家锁串行；查询侧（快照 ∥ 召回 → 选项 ∥ 叙事 ∥ 记忆写入）无锁并行
- 大模型三职责全部无状态：播种时抽取原著、命令侧解析意图、查询侧渲染文本；三者都不持有任何写端口，最远只能产出一条被规则驳回的意图
- 平行世界：一位玩家即一条事件流即一个 Player 聚合，世界相对原著的偏离全在这条流里；正典只读，覆盖层可整体抹去并重放重建
- 每类存储都有内存实现且与生产实现共跑同一套契约测试：零依赖可玩，换后端不换行为

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
