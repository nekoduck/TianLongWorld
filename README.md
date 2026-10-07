# 天龙八部：平行世界

极简文本武侠沙盒。没有血条、没有等级、没有读档——你是江湖里一个无名小卒，每一个动作都由「导演大模型」依物理逻辑与武侠常识推演，冒犯绝顶高手，一招毙命。

## 快速开始

**后端**（默认 Mock 导演，无需任何 API Key）

```bash
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn app.main:app --reload --port 8000
```

**前端**

```bash
cd frontend
npm install
npm run dev        # 打开 http://localhost:5173 ，/api 自动代理到 :8000
npm run dev:mock   # 或：完全脱离后端，用静态数据跑通 UI
```

**测试**

```bash
cd backend && .venv/bin/pytest -q
cd frontend && npm run build
```

## 接入真实大模型

复制 `backend/.env.example` 为 `backend/.env`（已 gitignore，密钥不会入库），任选其一：

```ini
# Gemini（推荐：原生结构化输出，思考档位 low，实测约 5s/回合）
LLM_PROVIDER=gemini
LLM_API_KEY=...
LLM_MODEL=gemini-flash-latest

# Anthropic
LLM_PROVIDER=anthropic
LLM_API_KEY=sk-ant-...
LLM_MODEL=claude-sonnet-5-5

# OpenAI 兼容协议（OpenAI / DeepSeek / 通义 / Ollama）
LLM_PROVIDER=openai
LLM_API_KEY=sk-...
LLM_MODEL=deepseek-chat
LLM_BASE_URL=https://api.deepseek.com/v1
```

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/session` | 投胎：返回 `session_id` 与第一幕 |
| POST | `/api/interact` | 出招：推演一个动作（`action_type: choice \| custom`） |
| GET | `/api/health` | 健康检查 |

请求/响应结构见 `backend/app/schemas.py`（前端镜像于 `frontend/src/types.ts`）。补充约定：

- `game_over: true` 时 `options` 为 `null`——死者没有选择。
- 死后继续调用 `/api/interact` 返回 **409**；推演失败返回 **502**；所有错误统一为 `{"detail": "..."}`。
- 服务端状态是唯一权威，请求中的 `current_state` 只在服务端丢失会话（如重启）时用于恢复。
- `current_state` / `next_state` 是一棵树：`player_state`（`location` / `time` / `weather` / `health_status` + `buffs_debuffs` / `social_traits` / `inventory` / `martial_arts` 四个标签清单 + `secrets` 私密情报）与 `world_state.major_events`（世界大事记）。前端每次请求必须整树回传。
- 五本账由服务端记账：导演只上报增减（`player_delta`），叙事中没提到的条目不会凭空消失。
- 情报隔离（Fog of War）：`secrets` 记录只有玩家知道的情报（偷听到的秘密、密信的内容、隐藏的身份），对游戏世界里的一切 NPC 不可见；`major_events` 只收天下皆知或已发生物理改变的客观事实。NPC 只凭自身认知、玩家的表面行为与公开的世事行事，不会因为你身上藏着什么就找上门来；认不出你的人，也不会把你的名声与旧事算到你头上。你心里骂乔峰，乔峰听不见——只有当面的冒犯才会招来杀身之祸。世界也不会为了推进剧情而凭空安排巧合。
- 世界大事记 `major_events` 是 `[{"tags": ["聚贤庄", "游氏双雄", "丐帮"], "event_desc": "..."}]`：只追加、不合并、不删除、不设上限。导演只能在 `next_state.major_events` 里写本回合新发生的大事。
- GraphRAG 记忆仓储：每回合调用大模型前，后端经 `MemoryService`（`backend/app/memory_service.py`）走两条检索路径——关系图以上一回合导演提取的 `involved_entities`、在场人物（含"丐帮弟子"这类门派群体）、所在地与玩家的公开身份为种子，查出它们之间的关系与牵涉它们的大事；语义检索以这一招的表面行为（心里的盘算不算）为查询，召回最相关的往事与江湖常识。两者分别作为 `[Graph_Context: 当前实体关系网]` 与 `[Semantic_History: 历史相关事件]` 注入 System Prompt；连同滑动窗口（最近 3~5 回合）、局部环境（切换地点即清空）与封顶的私密情报，Prompt 长度是与游戏进度无关的常数。
- `secrets` 只进不出：它从不作检索键、也从不出现在检索结果里；导演提取的实体只有外人也看得见的才会成为下一回合的检索种子——叙事里复述的念头、仍是秘密的人事都不算，秘密一旦当众揭穿即解禁。
- `ui_status_bar` 由服务端渲染：`【位置】 | 【时辰】 | 【身份】 | 【状态】 | 【武学】 | 【行囊】`，空缺时依次显示 无名小卒 / 健康 / 不会武功 / 空无一物。

## 试试作死

开局若在松鹤楼、少林寺山门或星宿海，乔峰、扫地僧或丁春秋就在你眼前（叙述可能只写"那魁梧大汉""那扫地老僧"）。

- 点名挑衅，如「一拳打在乔峰脸上」：规则层在大模型之前判你死刑。
- 不点名挑衅，如「抄起板凳朝那人砸去」：System Prompt 中的高手名录让导演同样一招毙命。
- 恭敬求教、顺手牵羊之类不冒犯高手的举动，则交由导演按常识推演。

## 记忆仓储（GraphRAG 地基）

导演管线只认 `MemoryService` 这一抽象，从不碰台账的存储形状：

| 方法 | 作用 |
| --- | --- |
| `query_relational_graph(entity_names) -> str` | 图检索：这些实体之间、牵涉它们的关系网，一行一条 |
| `query_semantic_events(action_text, top_k=3) -> list[str]` | 语义检索：与动作最相关的往事与江湖常识 |
| `commit_event(event_data, is_secret)` | 统一落账：`is_secret` 决定进玩家的 `secrets` 还是世界台账 |
| `retire_secret(secret)` | 秘密退场：当众揭穿后移出 `secrets` |

现行实现 `InMemoryMemoryService` 把会话状态树当存储（写穿透，前端拿到的整树与仓储永远是同一份），关系图 = 世界台账里的大事 + `lore.RELATIONS` 的原著关系，语义检索 = 字符二元组相似度 + 点名加权。换成图数据库与向量库时，新写一个实现，在 `backend/app/main.py` 换一个工厂即可；仓储按会话划定命名空间，接口方法因此无需会话参数。检索条数由 `GRAPH_LIMIT`（1~30，默认 12）与 `SEMANTIC_TOP_K`（1~10，默认 3）控制。

项目地图见 [`CLAUDE.md`](./CLAUDE.md)。
