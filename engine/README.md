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

产物在 `data/world/`：`blueprint.json`（可审阅的中间表示）、`seed.cypher`（可交给 cypher-shell）、`report.txt`（被丢弃的悬空引用、被封存的武学）。
组装器宁严勿宽：落不了地的引用一律丢弃，前置条件引用了原著本体之外的东西的武学一律封存——宁可失传，不可滥传。

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
