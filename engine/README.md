# TLBB-Engine

《天龙八部》AI 文字世界的下一代后端：DDD + CQRS + 事件溯源 + Graph RAG。
世界本体由原著 TXT 解析而来，世界状态只由不可变的事件流折叠得出，大模型只做三件无状态的事——读书抽取、解析意图、渲染文字——无权写入世界。

```
玩家输入 ──► [Parse] 意图解析（大模型 / 离线） ──► PlayerIntent
                                                  │
事件流 ──reduce──► Player 聚合 ──┐                 ▼
Neo4j 局部快照 ──────────────────┴──► [Validate] 规则裁决（纯函数）
                                                  │
                         [Event] 追加 PostgreSQL ◄─┘ ──► 投影 Neo4j ──► 投影 Qdrant
                                                  │
              新快照 ──► [Options] 合法边 → 3~4 个选项  ∥  [Render] 快照为 Hard Prompt → 金庸风流式叙事
```

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

仓库里已有一份：前 40 块（第一回至第九回，大理篇）的蓝图与逐块抽取记录，来历与复现方式见 [`data/world/README.md`](data/world/README.md)。
另有 `python -m app.seed assemble`：只读缓存、零费用重新组装（组装器改了规则时用）。

产物在 `data/world/`：`blueprint.json`（可审阅的中间表示）、`seed.cypher`（可交给 cypher-shell）、`report.txt`（被丢弃的悬空引用、被封存的武学）。
组装器宁严勿宽：落不了地的引用一律丢弃，前置条件引用了原著本体之外的东西的武学一律封存——宁可失传，不可滥传。

## 模型选型（Gemini，2026-10 实测）

大模型的三种职责取舍不同，按职责各配一套（模型, 思考档位），见 `.env.example`：

| 职责 | 模型 | 思考 | 实测 | 取舍 |
| --- | --- | --- | --- | --- |
| 意图解析 | `gemini-3.1-flash-lite` | minimal | ≈1.0s/次，6/6 正确 | 最快，且最守"把代称规整为在场者正名"的纪律 |
| 叙事渲染 | `gemini-3.8-flash` | low | 首字 ≈1.7s，全文 ≈4.5s | 忠于快照：玩家自称"拔出袖中短剑"而行囊为空，照实写成空手；flash-lite 首字 0.9s 但会被话术带偏 |
| 原著抽取 | `gemini-3.1-pro-preview` | high | ≈35s/块 | 离线一次成型，准确优先：状态取开篇而非块末，看不出的境界留空不猜 |

一回合端到端约 5s（落账 ≈1s → 首字 ≈3s → 终帧 ≈5s）。改动提示词或换模型后，用 `TLBB_TEST_LIVE_LLM=1 pytest -m live` 跑一遍真实模型回归。
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
| ← | `turn_completed` | 叙事全文、`options[{id,label,category}]`、`status`、`game_over` |
| ← | `error` | `code` + `message`，连接不断 |

## 测试矩阵

```bash
.venv/bin/pytest -q                                                     # 内存实现
TLBB_TEST_POSTGRES_DSN=postgresql://tlbb:tlbb@localhost:5432/tlbb \
TLBB_TEST_NEO4J_URI=bolt://localhost:7687 TLBB_TEST_NEO4J_PASSWORD=tlbb-neo4j \
.venv/bin/pytest -q                                                     # 加跑真实后端契约与整局
.venv/bin/ruff check . && .venv/bin/mypy app
```
