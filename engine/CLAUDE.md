# engine/
> L2 | 父级: /CLAUDE.md

TLBB-Engine：DDD + CQRS + 事件溯源 + Graph RAG 的武侠沙盒引擎，与 backend/（MVP 状态树导演）并列的下一代后端。
业务代码全部位于 app/ 包内，cwd 须为 engine/（`app.main:app`、`python -m app.seed` 与 pytest 的 pythonpath 均以此为根）。
世界本体只能由 data/source_text 的原著经播种管道产出，定格在时间锚点 T=0（原著开篇、段誉刚离家出走之时）；事件流是唯一真相，Neo4j 图谱与 Qdrant 记忆都只是它的投影。
大模型四职责皆无写端口：抽取原著、解析意图、地下城主在可裁区间里提议胜负、渲染文本——一切提议都经领域闸门定案。
花钱的边界：抽取原著与图谱自愈由 Claude 子代理经 export / ingest 完成，绝不调用付费大模型（seed 须显式 --use-llm）；付费大模型只服务运行期三职责，且有每进程的调用次数保险丝 LLM_CALL_LIMIT。

成员清单
requirements.txt: 运行依赖，fastapi + uvicorn + pydantic v2 + pydantic-settings + httpx2（大模型 HTTP / SSE）+ asyncpg（事件账本）+ neo4j（图谱）+ qdrant-client（记忆）
requirements-dev.txt: 开发依赖，叠加 pytest + pytest-asyncio + httpx（TestClient）+ ruff + mypy
pytest.ini: asyncio_mode=auto，pythonpath=.；postgres / neo4j 标记对应需要真实后端的契约测试，live 标记对应调用真实大模型的回归测试
ruff.toml: lint 规则（E/F/I/B/SIM/UP/RUF），忽略按显示宽度计的 E501 与全角标点告警；不启用 ruff format（数据密集的编译器代码会被拉成千行）
mypy.ini: pydantic 插件 + check_untyped_defs，app/ 须零告警
.env.example: 配置模板——大模型（mock / anthropic / gemini / openai，意图 / 叙事 / 地下城主 / 抽取四职责各自的模型与思考档位，附推荐的 Gemini 组合；抽取职责只在 --use-llm 时生效）、LLM_CALL_LIMIT 调用次数保险丝、四类后端（EVENT_STORE / GRAPH_BACKEND / QDRANT_URL）、记忆召回条数、播种参数；真实密钥写入同目录 .env（已 gitignore）
docker-compose.yml: 三件套后端 postgres:16 + neo4j:5.26（快照查询用到 CALL () {} 作用域子句，须 5.23+）+ qdrant
README.md: 快速开始、WebSocket 协议、播种流程、测试矩阵
data/source_text/: 原著 .txt 存放处（UTF-8 / GBK 均可，.txt 永不入库），README.md 说明播种命令
data/world/: 播种产物 blueprint.json（中间表示，即图谱正典）/ seed.cypher / report.txt（丢弃 / 封存 / 孤儿 / 时间线 / 自愈）/ healing.json（自愈缓存：孤儿物品的推断安放及理由与推断者）/ cache/<提示词版本>/（逐块抽取记录，描述经防抄清洗），均入库以便复现与审阅（原著全文不入库）；README.md 说明当前切片（前 40 块 = 第一回至第九回，v6 口径：T=0 时间锚点、本名主键、两道门）、复现命令与已知局限
app/: 应用包（DDD 四层 + 组合根），地图见 app/CLAUDE.md
tests/__init__.py: 测试包标识，使 `from tests.world import WORLD` 可导入
tests/world.py: 测试用微型原著蓝图 WORLD（四地九人七功四物四关系），设计成一条可走通的逻辑死线；段延庆以本名为主键而「恶贯满盈」只是称号，狠辣的龚光杰专供"徒手寻衅、重伤逃脱"；替身而非引擎数据
tests/conftest.py: ScriptedLLM 剧本替身（记录 system / user / schema，stream 切片吐出，可抛异常）+ wire（厂商客户端的传输层换成 MockTransport 并记录请求，不触网）与 sse() + settings（全内存 + mock）+ container（经组合根装配并种下 WORLD）+ play / spawned_at / kinds 助手 + 真实后端环境变量
tests/test_domain.py: 本体完整性（悬空引用 / 根基成环 / 主键须是本名 / 下落不明的物品合法 / 自悟须典籍 / 获取与修炼两道门）、意图规整、十种事件 JSON 往返与不可变、decode_event 唯一读出入口与旧账上抛（SkillLearned、受挫、无悟性的投胎）、火候 = 熟练度 × 悟性且折算境界、伤势分档、聚合根只凭事件流重算位置 / 行囊 / 火候 / 气血、拿到秘籍不等于学会、气血钳位且归零不等于死、流须以投胎开头且版本连续、死者不得行动、火候封顶与越高深越难练
tests/test_rules.py: 裁决规则——名称落地不猜、称号落到本名、移动只沿 CONNECTS_TO、静观无事件、已故不在场、可裁区间矩阵（境界差 × 性情 × 伤势）、settle 钳位（出界取确定性裁决、扣减钳进气血带、非毙命留一口气）、徒手攻击狠辣的龚光杰重伤逃脱、极端找死仍毙命而地下城主可手下留情、中庸绝顶不下杀手、人情沿 HAS_RELATION 一跳且只波及目睹者、火候而非秘籍决定境界、兵器不改境界、只有出手有赌注、取物与物归原主、修习入门（典籍 / 地点 / 根基火候）与精进（参照典籍 / 闭门苦练 / 名师点拨、悟性左右根基）、相冲 / 化境 / 重伤止练、点名的师父不肯即拒绝、六脉神剑无从得知、调息须有伤且无仇人在侧、重伤逃脱沿来路退回（fleeing）、连败两场不逃回第一场的仇家面前而条条出路通险地才留在原地、自拟招式名只算笔墨
tests/test_event_store.py: 事件账本契约（内存 + 真实 PostgreSQL 共跑）——往返、冲突时一条不写、五路并发只一路胜出；PostgreSQL 触发器拒绝 UPDATE / DELETE / TRUNCATE，JSONB 可按字段查询，库里的旧词汇读出时上抛而字节不动
tests/test_world_graph.py: 图谱契约（内存 + 真实 Neo4j 共跑）——正典查询、局部快照切片、称号随快照下发、下落不明的物品不进任何场景、覆盖层随旅程演化（熟练度累加、气血涨落钳位）且平行世界互不干扰、投影幂等与拒收缺口、抹去重放得同一世界、死亡投影、换蓝图不 reset 时报出旧纪元残留；另有双实现在 15 个版本上快照逐字段相等的同构证明
tests/test_seeding.py: 播种全链路——抽取契约 v6（时间锚点、三名分立、两道门、events）与旧缓存 prerequisites 自动升级、本名只在知本名的记录里投票（段延庆不叫「恶贯满盈」）、时间线隔离（后文习得剔出开篇武学、后文所得否决物主、后文身故者开篇健在）、被武学引用却无处安放的物品成为孤儿而武学不封存、泛称不成实体也不撮合两人、描述性称呼不是名字、尊号与「X法」尾缀并入词干、只读缓存零费用重组装并补做旧记录清洗、外部抽取器经 store_extraction 同一道闸门入库、export / ingest、欠费错误不重试、处所与上级地点互通、照抄原文的描述与事件转述在入缓存前清空、GB18030 回退、语料清洗、回目切块、退避重试、首次登场即开篇且未知不等于最弱、修炼门槛取最严、唯一包含匹配落地而多义不猜、悬空引用丢弃、门径落不了地即封存、根基成环断环、道路双向、局部失败不拖垮全书、Cypher 参数化与依赖序（两道门的拓扑、称号、安放来历）、脚本转义；真实 Neo4j 上逐句执行 seed.cypher；配着付费大模型也不带 --use-llm 就不装配（extract 拒绝并指路 export / ingest）、生产抽取器拿到跨块命名参考且铁律一字不改、命名参考只认组装器连的上级边（原文「入谷」不算）、旧蓝图读不了不拦重抽
tests/test_graph_linter.py: 图谱自愈——lint 只找被武学引用的孤儿、大模型神谕把「一阳指穴道谱诀」安放进候选且 provenance 记为推断、候选外的名字重采样后放弃而蓝图不变、不可重试的 LLMError 即止不抛错、二次自愈只用缓存零调用、过期缓存报告并忽略、ingest 闸门（非孤儿 / 候选外 / 坏 JSON 整批拒收）、推断的边带 provenance；真实 Neo4j 上孤儿先无边、播种自愈后的蓝图后补上推断的边、"无从推断"判词入缓存且只在显式要求时重问、候选清单一变旧判词即作废重问、配着付费大模型时整条自愈链路（导出 → 作答 → 入缓存 → 套用）不带 --use-llm 一次也不装配它、字符串 null 与单元素数组宽容收下、无同门关联的安放标 ⚠
tests/test_application.py: 世界观守卫（不调大模型直接 INVALID、放过长枪）、大模型解析（场景词表带称号与火候、输入转义、重采样兜底、被说服的大模型照样撞墙、LLMError 透传）、离线解析（含调息疗伤先于练功）、选项合法且方向各异且可重算、根基未到的功夫不是可供性、修习选项的措辞随凭借而变、有伤且无仇人才给调息、稀缺方向至多两席、Hard Prompt 只含局部真理（称号、火候、伤势、地下城主速写）且逐值转义、流式叙事与降级白描、事实白描；「趁龚光杰调息偷袭他」「一拳打向」是出手、仇人在侧时退路永远在选项里（两席稀缺占满时照样排在寻常方向之首）、夺路而逃的回合交手现场另作 <fled_scene> 且离线白描先打后逃
tests/test_resolution.py: 地下城主——战况简报含双方图谱状态与可裁区间且逐值转义、schema 的枚举只列可裁结局、区间内的提议被采纳而速写传给叙事、区间外与不合契约重采样后兜底、LLMError 兜底不抛错、结果已定不花钱、结局认枚举名也认中文、正的扣减由领域钳位、「徒手攻击狠辣的龚光杰」→ 重伤逃脱、越界的地下城主被钳回区间且速写作废、简报写明结局含义 / 地点 / 对手别名与随身之物、速写超长或夹带元话术整句作废（实测原文）、时间预算管整场裁决（越界重采样不另领预算）
tests/test_game_loop.py: 端到端——投胎流式开场、出生点须存在、完整逻辑死线剧情（入门 → 参照典籍练到略有小成 → 制敌夺剑 → 物归原主 → 拜师）、极端找死的永久死亡（解析前即拒）、重伤后避开仇人调息疗伤、选项重算与防伪、断线重连即重放、投影自愈、静观不写事件、地下城主越界的提议被钳回区间、叙事失败降级而真相不动、真实 PostgreSQL + Neo4j 上整局并凭事件流重建覆盖层；重伤逃脱后在清静处调息、回到仇人跟前照样调息不得、夺路而逃的叙事拿到交手现场与仇人、记忆召回按字面去重恰取 k 条、运行期三职责共用一份调用保险丝（熔断的请求发不出去、回合不写事件）、死者伤势栏写气绝
tests/test_websocket.py: 线协议——健康检查、投胎→流式→终帧（status 含伤势与武学火候）、自由文本与选项点选、选项不下发意图、错误帧不断连、伪造选项与死者
tests/test_llm_clients.py: 厂商客户端——MockTransport 替换传输层：Anthropic 结构化输出且不下发采样参数、SSE 流式与拒答、Gemini 过滤思考片段走 alt=sse、OpenAI 只在需要时开 JSON 模式、[DONE] 哨兵、HTTP 错误收敛、schema 规整、缺凭证启动即失败、四职责各取各的模型与思考档位、Claude 无 minimal 档按 low 下发、当日配额耗尽的 429 不可重试、调用次数保险丝各职责共用且熔断不可重试
tests/test_live_llm.py: 真实大模型回归（标记 live，TLBB_TEST_LIVE_LLM=1 才跑，读 engine/.env，整场至多 20 次请求）——意图把华丽描写降维并规整为正名、叙事流式分片、地下城主的结局落在可裁区间里；结构化抽取被厂商接受并能组装成蓝图另须 TLBB_TEST_LIVE_EXTRACTION=1（抽取归 Claude 子代理）；改运行期提示词或换模型后跑一次
tests/fixtures/sample_passage.txt: 自撰的无量山梗概（非原著文本），真实抽取回归的语料

运行
  python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
  .venv/bin/uvicorn app.main:app --reload --port 8000        # 需先有 data/world/blueprint.json（memory 图谱）或已播种的 Neo4j
  .venv/bin/python -m app.seed export --out DIR --max-chunks 40   # 抽取铁律 + 命名参考 + 待抽的块，交给 Claude 子代理逐块作答
  .venv/bin/python -m app.seed ingest --index I --file F          # 子代理的产出经同一道闸门入缓存
  .venv/bin/python -m app.seed assemble --max-chunks 40           # 零费用组装蓝图 + Cypher（自动套用自愈缓存）
  .venv/bin/python -m app.seed heal [--export DIR | --ingest F --by claude-subagent] [--apply]   # 孤儿物品的安放由子代理推断
  .venv/bin/pytest -q                                         # 全内存；设置 TLBB_TEST_POSTGRES_DSN / TLBB_TEST_NEO4J_URI 后加跑真实后端契约
  TLBB_TEST_LIVE_LLM=1 .venv/bin/pytest -q -m live            # 调用 .env 配置的真实大模型（会产生费用）
  .venv/bin/ruff check . && .venv/bin/mypy app

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
