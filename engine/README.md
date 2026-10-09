# TLBB-Engine

《天龙八部》AI 文字世界的下一代后端：DDD + CQRS + 事件溯源 + Graph RAG。
世界本体由原著 TXT 解析而来、定格在时间锚点 T=0（原著开篇，段誉刚离家出走之时），世界状态只由不可变的事件流折叠得出。
大模型只做四件无状态的事——读书抽取（含为孤儿物品推断安放）、解析意图、地下城主在物理边界里推演（语义物理引擎：属性碰撞 → 量级 → 代价 → 时钟与收敛）、渲染文字——它的一切产出都只是提议，必经领域闸门。

```
玩家输入 ──► [Parse] 意图解析（大模型 / 离线） ──► PlayerIntent
                                                  │
事件流 ──reduce──► Player 聚合 ──┐                 ▼
Neo4j 局部快照（含时钟 / 细节）──┴──► [Validate] 规则裁决（纯函数）──► 物理边界 Envelope（出手 / 交涉 / 暗中的可裁区间，或结果已定）
                                                  │                         │
                                                  │      [Resolve] 一席裁决：文本在胜负未定或挂着时钟时请地下城主推演 ResolutionOutput、点选由气运确定性取值（离线取确定性裁决）
                                                  ▼                         │
                         [Event] 领域闸门：推出结局、钳位、补足代价、时钟坍缩 ◄──┘
                                  追加 PostgreSQL ──► 投影 Neo4j ──► 投影 Qdrant
                                                  │
              新快照 ──► [Options] 五类候选源 → 显著性 + 席位 + MMR → 3~4 招（带 why 与风险档） ──► [Render] Hard Prompt + <clocks> 暗流 + <emerged> 此世细节 + 恩怨 + 端倪 <hooks> → 金庸风流式叙事
```

## 五个设计支点

| 缺陷（v5 时代） | 支点 | 落在哪里 |
| --- | --- | --- |
| 段延庆以「恶贯满盈」为主键 | **语义本体对齐**：人物以本名 `true_name` 为主键，`titles` 称号与 `aliases` 别名只作指称；蓝图闸门拒收以称号篡位的主键 | `domain/models.py`、组装器本名投票 |
| 段誉开篇即身负北冥神功 | **时间锚点 T=0 与历史事件隔离**：抽取提示词强制注入 T=0，开篇之后的变化只进 `events`；组装器据事件否决被污染的开篇状态 | `knowledge_extractor.py`（v6）、`blueprint_assembler.py` |
| 一阳指因谱诀无处安放而失传 | **图谱完整性自愈**：被武学引用却下落不明的物品留作孤儿；自愈代理据原著常识在候选里安放，`provenance=推断`，过闸门写回蓝图并经 MERGE 进 Neo4j | `graph_linter.py`、`seed heal` |
| 出手非胜即死的二极管 | **渐进式状态 + 模糊裁决**：火候 = Σ 熟练度 × 悟性、气血 = Σ 涨落；境界差 × 性情 × 伤势圈出可裁区间 | `progression.py`、`combat.py` |
| 地下城主只会在枚举里做单选题 | **语义物理引擎（神经-符号-神经）**：大模型按 属性碰撞 → 量级（爆炸 / 暗流）→ 代价 → 时钟与收敛 推演出 `ResolutionOutput`（属性变化 / 时钟指令 / 微观事实 / 路由）；领域闸门由属性推出结局、钳位、按等价交换补足代价、时钟满格即坍缩为硬结算；时钟与细节挂进 Neo4j 覆盖层、下一张快照召回 | `resolution_agent.py`、`briefs/`、`domain/resolution.py`、`domain/clocks.py` |

## 快速开始（零依赖）

```bash
cd engine
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q                       # 全内存跑通全部用例
```

真实游玩需要一份原著蓝图（见下文「播种」）。默认配置全内存 + mock：意图解析与叙事退化为确定性的离线实现，
`uvicorn app.main:app --port 8000` 启动后自动加载 `data/world/blueprint.json`。
与前端联调时 engine 起在 8001（backend 占 8000，前端 `npm run dev:engine` 经 Vite 把 `/ws` 代理过来）；环境变量优先于 `.env`，
`LLM_PROVIDER=mock EVENT_STORE=memory GRAPH_BACKEND=memory QDRANT_URL=:memory: uvicorn app.main:app --port 8001` 保证全内存、零费用。

## 播种：原著 → 图谱

原著抽取与图谱自愈由 **Claude 子代理**完成，不调用付费大模型（2026-10 实测两次跑空了 Gemini 预付额度）：

```bash
cp ~/天龙八部.txt data/source_text/        # UTF-8 或 GBK 均可，永不入库
.venv/bin/python -m app.seed export --out /tmp/jobs --max-chunks 40   # 抽取铁律 + 跨块命名参考 + 待抽的块
# Claude 子代理照 EXTRACTION_SYSTEM.txt 逐块作答，每块一个 JSON
.venv/bin/python -m app.seed ingest --index 0 --file chunk-000.json   # 逐块经同一道闸门（契约校验 + 防抄清洗）入缓存
.venv/bin/python -m app.seed assemble --max-chunks 40                 # 零费用组装：先取开篇，也是世界的时间切片
.venv/bin/python -m app.seed apply --reset                            # GRAPH_BACKEND=neo4j 时写入 Neo4j
```

仓库里已有一份：前 40 块（第一回至第九回，大理篇）的 v6 蓝图、逐块抽取记录与自愈缓存，来历与复现方式见 [`data/world/README.md`](data/world/README.md)——
v6 全部 40 块就是这样由 Claude 子代理完成的。`assemble` 也用于组装器改了规则后零费用重组。
`extract --use-llm` / `heal --use-llm` 仍可调用 `.env` 配置的大模型（会产生费用），不带这个开关，`extract` 只会拒绝并指路，`heal` 只套缓存。

```bash
.venv/bin/python -m app.seed heal                      # 体检孤儿物品：只套自愈缓存（零费用）
.venv/bin/python -m app.seed heal --export /tmp/heal   # 尚无答案的孤儿交给 Claude 子代理作答……
.venv/bin/python -m app.seed heal --ingest answer.json --by claude-subagent   # ……经同一道闸门入缓存
.venv/bin/python -m app.seed heal --apply              # 推断的 LOCATED_IN / BELONGS_TO（provenance=推断）经 MERGE 写进 Neo4j
```

产物在 `data/world/`：`blueprint.json`（图谱正典，可审阅）、`seed.cypher`（可交给 cypher-shell）、`report.txt`（丢弃 / 封存 / 孤儿 / 时间线 / 自愈）、
`healing.json`（每条推断的安放、理由与推断者）。组装器宁严勿宽：落不了地的引用一律丢弃，门径引用了原著本体之外的东西的武学一律封存——宁可失传，不可滥传；
原著提到却没写在哪的典籍不丢，留给自愈代理——推断永远标明是推断。自愈作用于蓝图而不是直接改 Neo4j：图谱只是正典的投影，直接改图会在下一次 `--reset` 时丢失。

## 模型选型（Gemini，2026-10 实测）

付费大模型只服务运行期三种职责，取舍不同，按职责各配一套（模型, 思考档位），见 `.env.example`；
各职责共用一份调用次数保险丝 `LLM_CALL_LIMIT`（每进程缺省 500 次，约两百回合；熔断后地下城主交给规则、叙事降级为白描、意图解析报错）：

| 职责 | 模型 | 思考 | 实测 | 取舍 |
| --- | --- | --- | --- | --- |
| 意图解析 | `gemini-3.1-flash-lite` | minimal | ≈1.0s/次，6/6 正确 | 最快，且最守"把代称规整为在场者正名"的纪律 |
| 叙事渲染 | `gemini-3.8-flash` | low | 首字 ≈1.7s，全文 ≈4.5s | 忠于快照：玩家自称"拔出袖中短剑"而行囊为空，照实写成空手；flash-lite 首字 0.9s 但会被话术带偏 |
| 地下城主 | `gemini-3.8-flash` | low | 旧单选契约实测 p50 2.8s / p90 3.9s、首发采纳 42/42；语义物理引擎的推演契约（推理四段 + 符号层）尚待真实模型重测 | 7 组候选 × 14 场景 × 3 次实测选出：flash-lite 系快一倍但不守"仁厚者手下留情"，3.1-pro 太慢且配额紧；**3.8-flash 不收 minimal**（HTTP 400，地下城主整个退回规则）；8s 时间预算管整场推演，超时交给规则；只为自由文本回合发言——胜负未定（出手 / 交涉 / 暗中）或此景挂着时钟（结果已定之事只许动时钟、事实、名望）；点选回合由气运（`FORTUNE_ON_CLICK`，缺省开）确定性取值、不花钱，好过确定性裁决时由领域补挂代价时钟 |
| 原著抽取 / 图谱自愈 | **Claude 子代理**（export / ingest） | — | v6 全部 40 块零费用入库 | 不归付费大模型；Gemini 3.1-pro + high 的实测（抽取 ≈35s/块、自愈 p50 7.2s、每天 250 次请求）只留作对照，`--use-llm` 才会调用 |

真实整局实测（29 回合，意图 / 地下城主 / 叙事零报错零降级）：非出手回合首字 p50 3.3s、终帧 5.5s；胜负未定的出手首字 p50 6.3s、终帧 8.6s——
多出来的约 3 秒就是地下城主。改动运行期提示词或换模型后，用 `TLBB_TEST_LIVE_LLM=1 pytest -m live` 跑一遍真实模型回归
（一次回归至多 20 次请求，不含抽取；验证厂商抽取契约另须 `TLBB_TEST_LIVE_EXTRACTION=1`）。
地下城主的选型基准（7 组候选 × 14 场景 × 3 次）未入库，换提示词后按同样的场景与次数、逐组串行重跑——并行会把当日配额一起打光。
抽取有随机波动（同一段文本偶尔漏写典籍或换一种关系类别）：全书各块的并集会补齐大部分缺口，`blueprint.json` 落图前值得人工过目。

## 生产后端

```bash
docker compose up -d                       # postgres:16 + neo4j:5.26 + qdrant
# .env：EVENT_STORE=postgres  GRAPH_BACKEND=neo4j  QDRANT_URL=http://localhost:6333
```

## WebSocket 协议（`/ws/play`）

| 方向 | 帧 | 说明 |
| --- | --- | --- |
| → | `{"type":"spawn","name":"阿星","location":"无量山"}` | 投胎，location 可省（确定性分配） |
| → | `{"type":"resume","player_id":"ply:…","quiet":false}` | 续前缘：重放事件流即恢复。`quiet:true` 用于断线重连与选项过期——只回 `session` 与叙事为空的 `turn_completed`（选项与状态照给），不复述此景、零大模型调用 |
| → | `{"type":"act","text":"施展凌波微步向北而去"}` | 自由文本，经意图解析 |
| → | `{"type":"choose","option_id":"explore-1a2b3c4d"}` | 点选选项，不经大模型，服务端按当前快照重算核验 |
| ← | `session` | `player_id` / `name` |
| ← | `turn_resolved` | 结构化意图（含手段 `approach`、所图 `aim`、话题 `topic`）+ 本回合已入账事件的白描 `facts`（服药回气血那条不出声；语义物理引擎的时钟挂上 / 推进 / 回退 / 坍缩 / 化解、微观事实、名望涨落各有一句，从不露 id） |
| ← | `narration_delta` | 叙事分片（流式） |
| ← | `turn_completed` | 叙事全文、`options[{id,label,category,why,risk?}]`（`why` 是 ≤12 字的上榜缘由，如「仇人在侧，先脱身」「换个手段」「伤重宜调息」；`risk` 是风险档「稳妥 / 有险 / 凶险」，只露可裁区间最坏的一端、不露结局，有才下发；意图不下发）、`status`（境界 `tier`、伤势 `health`、`skills` 写作「北冥神功（略有小成）」；人情 `bonds[{name,attitude,cause}]` 在场者优先、至多 6 条，心事 `pursuits[{label,note}]` 如「求艺 · 晓风拂柳」「尚无眉目；已试：言辞」、至多 3 条，打探只写对象不写见闻；眼前的暗流 `clocks[{name,kind,progress,maximum}]`（种类 疑心 / 敌意 / 危机 / 进展，凶险的与将满的在前、至多 4 只，id 与挂处不下发）；名望 `renown` 只给语义标签（声名狼藉 … 威震江湖）；只有语义标签不露数值（时钟的格数是叙事节拍，照下发），新字段旧客户端可缺省）、`game_over` |
| ← | `error` | `code` + `message`，连接不断 |

## 测试矩阵

```bash
.venv/bin/pytest -q                                                     # 内存实现
TLBB_TEST_POSTGRES_DSN=postgresql://tlbb:tlbb@localhost:5432/tlbb \
TLBB_TEST_NEO4J_URI=bolt://localhost:7687 TLBB_TEST_NEO4J_PASSWORD=tlbb-neo4j \
.venv/bin/pytest -q                                                     # 加跑真实后端契约与整局
.venv/bin/ruff check . && .venv/bin/mypy app
```

`tests/test_option_metrics.py` 是选项菜单的零费用回归基线：把 29 回合真实 Gemini 实录的事件流（`tests/fixtures/live_session_events.jsonl`，只有事件）
在入库蓝图上逐回合重放、重算快照与菜单，守住四条指标——世界不变菜单逐字不变、上回合的对象在眼前就被提到（≥80%）、仇人在侧必有出路、调息按伤势加权。
