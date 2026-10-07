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
- `current_state` / `next_state` 是一棵树：`player_state`（`location` / `time` / `weather` / `health_status` + `buffs_debuffs` / `social_traits` / `inventory` / `martial_arts` 四个标签清单）与 `world_state.major_events`（世界大事记）。前端每次请求必须整树回传。
- 四个标签清单由服务端记账：导演只上报增减（`player_delta`），叙事中没提到的标签不会凭空消失。
- 世界大事记 `major_events` 是 `[{"tags": ["聚贤庄", "游氏双雄", "丐帮"], "event_desc": "..."}]`：只追加、不合并、不删除、不设上限。导演只能在 `next_state.major_events` 里写本回合新发生的大事。
- JIT 动态记忆：每回合调用大模型前，后端只挑出 `tags` 命中当前地点、在场人物（含"丐帮弟子"这类门派群体）、玩家身份或这一招点名的地点人物的最近几条大事注入 Prompt；连同滑动窗口（最近 3~5 回合）与局部环境（切换地点即清空），Prompt 长度是与游戏进度无关的常数。
- `ui_status_bar` 由服务端渲染：`【位置】 | 【时辰】 | 【身份】 | 【状态】 | 【武学】 | 【行囊】`，空缺时依次显示 无名小卒 / 健康 / 不会武功 / 空无一物。

## 试试作死

开局若在松鹤楼、少林寺山门或星宿海，乔峰、扫地僧或丁春秋就在你眼前（叙述可能只写"那魁梧大汉""那扫地老僧"）。

- 点名挑衅，如「一拳打在乔峰脸上」：规则层在大模型之前判你死刑。
- 不点名挑衅，如「抄起板凳朝那人砸去」：System Prompt 中的高手名录让导演同样一招毙命。
- 恭敬求教、顺手牵羊之类不冒犯高手的举动，则交由导演按常识推演。

项目地图见 [`CLAUDE.md`](./CLAUDE.md)。
