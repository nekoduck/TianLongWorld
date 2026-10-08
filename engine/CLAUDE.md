# engine/
> L2 | 父级: /CLAUDE.md

TLBB-Engine：DDD + CQRS + 事件溯源 + Graph RAG 的武侠沙盒引擎，与 backend/（MVP 状态树导演）并列的下一代后端。
业务代码全部位于 app/ 包内，cwd 须为 engine/（`app.main:app`、`python -m app.seed` 与 pytest 的 pythonpath 均以此为根）。
世界本体只能由 data/source_text 的原著经播种管道产出；事件流是唯一真相，Neo4j 图谱与 Qdrant 记忆都只是它的投影。

成员清单
requirements.txt: 运行依赖，fastapi + uvicorn + pydantic v2 + pydantic-settings + httpx2（大模型 HTTP / SSE）+ asyncpg（事件账本）+ neo4j（图谱）+ qdrant-client（记忆）
requirements-dev.txt: 开发依赖，叠加 pytest + pytest-asyncio + httpx（TestClient）+ ruff + mypy
pytest.ini: asyncio_mode=auto，pythonpath=.；postgres / neo4j 标记对应需要真实后端的契约测试，live 标记对应调用真实大模型的回归测试
ruff.toml: lint 规则（E/F/I/B/SIM/UP/RUF），忽略按显示宽度计的 E501 与全角标点告警；不启用 ruff format（数据密集的编译器代码会被拉成千行）
mypy.ini: pydantic 插件 + check_untyped_defs，app/ 须零告警
.env.example: 配置模板——大模型（mock / anthropic / gemini / openai，意图 / 叙事 / 抽取三职责各自的模型与思考档位，附实测推荐的 Gemini 组合）、四类后端（EVENT_STORE / GRAPH_BACKEND / QDRANT_URL）、记忆召回条数、播种参数；真实密钥写入同目录 .env（已 gitignore）
docker-compose.yml: 三件套后端 postgres:16 + neo4j:5.26（快照查询用到 CALL () {} 作用域子句，须 5.23+）+ qdrant
README.md: 快速开始、WebSocket 协议、播种流程、测试矩阵
data/source_text/: 原著 .txt 存放处（UTF-8 / GBK 均可，.txt 永不入库），README.md 说明播种命令
data/world/: 播种产物 blueprint.json（中间表示）/ seed.cypher / report.txt / cache/（均不入库），README.md 说明各自用途
app/: 应用包（DDD 四层 + 组合根），地图见 app/CLAUDE.md
tests/__init__.py: 测试包标识，使 `from tests.world import WORLD` 可导入
tests/world.py: 测试用微型原著蓝图 WORLD（四地七人七功四物三关系），设计成一条可走通的逻辑死线，也是替身而非引擎数据
tests/conftest.py: ScriptedLLM 剧本替身（记录 system / user / schema，stream 切片吐出，可抛异常）+ settings（全内存 + mock）+ container（经组合根装配并种下 WORLD）+ play / spawned_at / kinds 助手 + 真实后端环境变量
tests/test_domain.py: 本体完整性（悬空引用 / 前置成环 / 无处安放的物品 / 自悟须典籍）、意图规整、九种事件 JSON 往返与不可变、聚合根只凭事件流重算位置与行囊、流须以投胎开头且版本连续、死者不得行动
tests/test_rules.py: 裁决规则——名称落地不猜、移动只沿 CONNECTS_TO、静观无事件、已故不在场、对决矩阵、冒犯绝顶即死、人情沿 HAS_RELATION 一跳且只波及目睹者、兵器不改境界、取物与物归原主、修习门槛逐条（典籍 / 地点 / 前置 / 相冲 / 已会 / 肯教 / 境界）、六脉神剑无从得知
tests/test_event_store.py: 事件账本契约（内存 + 真实 PostgreSQL 共跑）——往返、冲突时一条不写、五路并发只一路胜出；PostgreSQL 触发器拒绝 UPDATE / DELETE / TRUNCATE，JSONB 可按字段查询
tests/test_world_graph.py: 图谱契约（内存 + 真实 Neo4j 共跑）——正典查询、局部快照切片、覆盖层随旅程演化且平行世界互不干扰、投影幂等与拒收缺口、抹去重放得同一世界、死亡投影；另有双实现在 11 个版本上快照逐字段相等的同构证明
tests/test_seeding.py: 播种全链路——GB18030 回退、回目切块、正名互见消歧（泛称不合并、并入者正名成别名）、首次登场即开篇且未知不等于最弱、前置门槛取最严、唯一包含匹配落地而多义不猜、悬空引用丢弃、前置落不了地即封存、成环断环、道路双向、抽取器重采样与磁盘缓存、局部失败不拖垮全书、Cypher 参数化与依赖序、脚本转义；真实 Neo4j 上逐句执行 seed.cypher
tests/test_application.py: 世界观守卫（不调大模型直接 INVALID、放过长枪）、大模型解析（场景词表、输入转义、重采样兜底、被说服的大模型照样撞墙、LLMError 透传）、离线解析八例、选项合法且方向各异且可重算、前置未齐的功夫不是可供性、Hard Prompt 只含局部真理且逐值转义、流式叙事与降级白描、事实白描
tests/test_game_loop.py: 端到端——投胎流式开场、出生点须存在、完整逻辑死线剧情、永久死亡（解析前即拒）、选项重算与防伪、断线重连即重放、投影自愈、静观不写事件、大模型只解析与渲染（它宣称的击杀不入账）、叙事失败降级而真相不动、真实 PostgreSQL + Neo4j 上整局并凭事件流重建覆盖层
tests/test_websocket.py: 线协议——健康检查、投胎→流式→终帧、自由文本与选项点选、选项不下发意图、错误帧不断连、伪造选项与死者
tests/test_llm_clients.py: 厂商客户端——MockTransport 替换传输层：Anthropic 结构化输出且不下发采样参数、SSE 流式与拒答、Gemini 过滤思考片段走 alt=sse、OpenAI 只在需要时开 JSON 模式、[DONE] 哨兵、HTTP 错误收敛、schema 规整、缺凭证启动即失败、三职责各取各的模型与思考档位、Claude 无 minimal 档按 low 下发
tests/test_live_llm.py: 真实大模型回归（标记 live，TLBB_TEST_LIVE_LLM=1 才跑，读 engine/.env）——意图把华丽描写降维并规整为正名、叙事流式分片、结构化抽取被厂商接受并能组装成蓝图；改提示词或换模型后跑一次
tests/fixtures/sample_passage.txt: 自撰的无量山梗概（非原著文本），真实抽取回归的语料

运行
  python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
  .venv/bin/uvicorn app.main:app --reload --port 8000        # 需先有 data/world/blueprint.json（memory 图谱）或已播种的 Neo4j
  .venv/bin/python -m app.seed extract --max-chunks 40        # 原著 → 蓝图 + Cypher（需真实大模型）
  .venv/bin/pytest -q                                         # 全内存；设置 TLBB_TEST_POSTGRES_DSN / TLBB_TEST_NEO4J_URI 后加跑真实后端契约
  TLBB_TEST_LIVE_LLM=1 .venv/bin/pytest -q -m live            # 调用 .env 配置的真实大模型（会产生费用）
  .venv/bin/ruff check . && .venv/bin/mypy app

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
