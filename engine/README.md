# TLBB-Engine

《天龙八部》AI 文字世界的下一代后端：DDD + CQRS + 事件溯源 + Graph RAG。
世界本体由原著 TXT 解析而来、定格在时间锚点 T=0（原著开篇，段誉刚离家出走之时），世界状态只由不可变的事件流折叠得出。
大模型只做四件无状态的事——读书抽取（含为孤儿物品推断安放）、解析意图、地下城主在可裁区间里提议胜负、渲染文字——它的一切产出都只是提议，必经领域闸门。

```
玩家输入 ──► [Parse] 意图解析（大模型 / 离线） ──► PlayerIntent
                                                  │
事件流 ──reduce──► Player 聚合 ──┐                 ▼
Neo4j 局部快照 ──────────────────┴──► [Validate] 规则裁决（纯函数）──► 出手？可裁区间 Stakes
                                                  │                         │
                                                  │      [Resolve] 地下城主在区间里提议（大模型 / 离线取确定性裁决）
                                                  ▼                         │
                         [Event] 领域 settle 钳位定案 ◄──────────────────────┘
                                  追加 PostgreSQL ──► 投影 Neo4j ──► 投影 Qdrant
                                                  │
              新快照 ──► [Options] 合法边 → 3~4 个选项  ∥  [Render] Hard Prompt + 地下城主速写 → 金庸风流式叙事
```

## 四个设计支点

| 缺陷（v5 时代） | 支点 | 落在哪里 |
| --- | --- | --- |
| 段延庆以「恶贯满盈」为主键 | **语义本体对齐**：人物以本名 `true_name` 为主键，`titles` 称号与 `aliases` 别名只作指称；蓝图闸门拒收以称号篡位的主键 | `domain/models.py`、组装器本名投票 |
| 段誉开篇即身负北冥神功 | **时间锚点 T=0 与历史事件隔离**：抽取提示词强制注入 T=0，开篇之后的变化只进 `events`；组装器据事件否决被污染的开篇状态 | `knowledge_extractor.py`（v6）、`blueprint_assembler.py` |
| 一阳指因谱诀无处安放而失传 | **图谱完整性自愈**：被武学引用却下落不明的物品留作孤儿；自愈代理据原著常识在候选里安放，`provenance=推断`，过闸门写回蓝图并经 MERGE 进 Neo4j | `graph_linter.py`、`seed heal` |
| 出手非胜即死的二极管 | **渐进式状态 + 模糊裁决**：火候 = Σ 熟练度 × 悟性、气血 = Σ 涨落；境界差 × 性情 × 伤势圈出可裁区间，地下城主在区间里挑结局与扣减 | `progression.py`、`combat.py`、`resolution_agent.py` |

## 快速开始（零依赖）

```bash
cd engine
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q                       # 全内存跑通全部用例
```

真实游玩需要一份原著蓝图（见下文「播种」）。默认配置全内存 + mock：意图解析与叙事退化为确定性的离线实现，
`uvicorn app.main:app --port 8000` 启动后自动加载 `data/world/blueprint.json`。

## 播种：原著 → 图谱

```bash
cp .env.example .env                       # 配置 LLM_PROVIDER / LLM_API_KEY / LLM_MODEL
cp ~/天龙八部.txt data/source_text/        # UTF-8 或 GBK 均可，永不入库
.venv/bin/python -m app.seed extract --max-chunks 40   # 先取开篇：既是试跑，也是世界的时间切片
.venv/bin/python -m app.seed apply --reset             # GRAPH_BACKEND=neo4j 时写入 Neo4j
```

仓库里已有一份：前 40 块（第一回至第九回，大理篇）的 v6 蓝图、逐块抽取记录与自愈缓存，来历与复现方式见 [`data/world/README.md`](data/world/README.md)。
另有 `python -m app.seed assemble`：只读缓存、零费用重新组装（组装器改了规则时用）；
`export` / `ingest`：大模型之外的抽取器（子代理、人工）接手抽取，产出经同一道闸门入缓存——本仓库的 v6 全部 40 块就是这样由 Claude 子代理完成的。

```bash
.venv/bin/python -m app.seed heal                      # 体检孤儿物品：大模型据原著常识推断安放（离线时只套缓存）
.venv/bin/python -m app.seed heal --export /tmp/heal   # 或交给子代理 / 人工作答……
.venv/bin/python -m app.seed heal --ingest answer.json --by claude-subagent   # ……经同一道闸门入缓存
.venv/bin/python -m app.seed heal --apply              # 推断的 LOCATED_IN / BELONGS_TO（provenance=推断）经 MERGE 写进 Neo4j
```

产物在 `data/world/`：`blueprint.json`（图谱正典，可审阅）、`seed.cypher`（可交给 cypher-shell）、`report.txt`（丢弃 / 封存 / 孤儿 / 时间线 / 自愈）、
`healing.json`（每条推断的安放、理由与推断者）。组装器宁严勿宽：落不了地的引用一律丢弃，门径引用了原著本体之外的东西的武学一律封存——宁可失传，不可滥传；
原著提到却没写在哪的典籍不丢，留给自愈代理——推断永远标明是推断。自愈作用于蓝图而不是直接改 Neo4j：图谱只是正典的投影，直接改图会在下一次 `--reset` 时丢失。

## 模型选型（Gemini，2026-10 实测）

大模型的三种职责取舍不同，按职责各配一套（模型, 思考档位），见 `.env.example`：

| 职责 | 模型 | 思考 | 实测 | 取舍 |
| --- | --- | --- | --- | --- |
| 意图解析 | `gemini-3.1-flash-lite` | minimal | ≈1.0s/次，6/6 正确 | 最快，且最守"把代称规整为在场者正名"的纪律 |
| 叙事渲染 | `gemini-3.8-flash` | low | 首字 ≈1.7s，全文 ≈4.5s | 忠于快照：玩家自称"拔出袖中短剑"而行囊为空，照实写成空手；flash-lite 首字 0.9s 但会被话术带偏 |
| 地下城主 | `gemini-3.8-flash` | low | p50 2.8s / p90 3.9s，首发采纳 42/42，速写零硬伤 | 7 组候选 × 14 场景 × 3 次实测选出：flash-lite 系快一倍但不守"仁厚者手下留情"，3.1-pro 太慢且配额紧；**3.8-flash 不收 minimal**（HTTP 400，地下城主整个退回规则）；8s 时间预算，超时交给规则 |
| 原著抽取 / 图谱自愈 | `gemini-3.1-pro-preview` | high | 抽取 ≈35s/块；自愈 p50 7.2s，合成孤儿 4/4 选中同门候选 | 离线一次成型，准确优先；**每天 250 次请求**，抽取与自愈共用，全书须按天分批续跑 |

真实整局实测（29 回合，意图 / 地下城主 / 叙事零报错零降级）：非出手回合首字 p50 3.3s、终帧 5.5s；胜负未定的出手首字 p50 6.3s、终帧 8.6s——
多出来的约 3 秒就是地下城主。改动提示词或换模型后，用 `TLBB_TEST_LIVE_LLM=1 pytest -m live` 跑一遍真实模型回归；
地下城主与自愈的选型基准脚本在实测记录里（14 场景 × 3 次、六脉 / 降龙一对正反题各 6 遍），换提示词后照原样重跑再上线。
真实抽取有随机波动（同一段文本偶尔漏写典籍或换一种关系类别）：全书各块的并集会补齐大部分缺口，`blueprint.json` 落图前值得人工过目。

## 生产后端

```bash
docker compose up -d                       # postgres:16 + neo4j:5.26 + qdrant
# .env：EVENT_STORE=postgres  GRAPH_BACKEND=neo4j  QDRANT_URL=http://localhost:6333
```

## WebSocket 协议（`/ws/play`）

| 方向 | 帧 | 说明 |
| --- | --- | --- |
| → | `{"type":"spawn","name":"阿星","location":"无量山"}` | 投胎，location 可省（确定性分配） |
| → | `{"type":"resume","player_id":"ply:…"}` | 断线重连：重放事件流即恢复 |
| → | `{"type":"act","text":"施展凌波微步向北而去"}` | 自由文本，经意图解析 |
| → | `{"type":"choose","option_id":"explore-1a2b3c4d"}` | 点选选项，不经大模型，服务端按当前快照重算核验 |
| ← | `session` | `player_id` / `name` |
| ← | `turn_resolved` | 结构化意图 + 本回合已入账事件的白描 `facts` |
| ← | `narration_delta` | 叙事分片（流式） |
| ← | `turn_completed` | 叙事全文、`options[{id,label,category}]`、`status`（境界 `tier`、伤势 `health`、`skills` 写作「北冥神功（略有小成）」，只有语义标签不露数值）、`game_over` |
| ← | `error` | `code` + `message`，连接不断 |

## 测试矩阵

```bash
.venv/bin/pytest -q                                                     # 内存实现
TLBB_TEST_POSTGRES_DSN=postgresql://tlbb:tlbb@localhost:5432/tlbb \
TLBB_TEST_NEO4J_URI=bolt://localhost:7687 TLBB_TEST_NEO4J_PASSWORD=tlbb-neo4j \
.venv/bin/pytest -q                                                     # 加跑真实后端契约与整局
.venv/bin/ruff check . && .venv/bin/mypy app
```
