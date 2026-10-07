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
```

前端没有 Mock 模式：离线体验由后端默认的 `LLM_PROVIDER=mock` 提供，前端永远只渲染服务端裁决的状态。

**测试**

```bash
cd backend && .venv/bin/pytest -q    # 含 mypy --strict 类型闸门（tests/test_typing.py）
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
| POST | `/api/session` | 入世 / 投胎：body 可选 `{"world_id": null \| "<uuid>"}`，缺省开辟新世界，携带则在该世界重新投胎；返回 `session_id`、`world_id` 与第一幕 |
| POST | `/api/interact` | 出招：推演一个动作（`action_type: choice \| custom`） |
| GET | `/api/health` | 健康检查 |

请求/响应结构见 `backend/app/schemas.py`（前端镜像于 `frontend/src/types.ts`）。补充约定：

- 事件溯源：每一回合都作为不可变事件追加进 SQLite（`backend/data/tianlong.db`，只追加），状态是事件的纯投影；进程重启后会话照常延续。
- 服务端是唯一权威：请求中的 `current_state` 只是客户端回显，服务端从不采信；未知会话返回 **404**。
- `current_state` / `next_state` 是一棵树：`player_state`（`location` / `time` / `weather` / `health_status` + `buffs_debuffs` / `social_traits` / `inventory` / `martial_arts` 四本标签账）与 `world_state.major_events`。
- 四本标签账由服务端记账：导演只上报增减，叙事中没提到的标签不会凭空消失；绝学无法由导演凭空授予。
- 世界大事 `major_events` 是 `{"tags": [...], "event_desc": "..."}` 的只追加列表，无上限、不合并、不删除；每回合只按地点、在场人物与江湖身份检索出相关的几条喂给导演。
- 投胎：此身状态清空，世界大事延续——前世烧掉的庄园，今生依旧是一片焦土。
- `game_over: true` 时 `options` 为 `null`——死者没有选择；死后继续出招返回 **409**。
- 错误统一为 `{"detail": "...", "code": "..."}`：`dead`（409）、`busy`（409）、`not_found`（404）、`llm_unavailable` / `director`（502）；FastAPI 的 422 校验错误只有 `detail`。
- `ui_status_bar` 由服务端渲染：`【位置】 | 【时辰】 | 【身份】 | 【状态】 | 【武学】 | 【行囊】`；状态段是 `health_status` 加上 `buffs_debuffs`，身份 / 武学 / 行囊空缺时依次显示 无名小卒 / 不会武功 / 空无一物。
- 导演输出失败的容错链：厂商层严格 schema → 宽容解析 → 带错重采样 → 确定性兜底（开局退回种子、必死回合确定性处决、普通回合原地停顿且不写入任何事件）。

## 试试作死

开局若在松鹤楼、少林寺山门或星宿海，乔峰、扫地僧或丁春秋就在你眼前（叙述可能只写"那魁梧大汉""那扫地老僧"）。

- 点名挑衅，如「一拳打在乔峰脸上」：规则层在大模型之前判你死刑。
- 不点名挑衅，如「抄起板凳朝那人砸去」：System Prompt 中的高手名录让导演同样一招毙命。
- 恭敬求教、顺手牵羊之类不冒犯高手的举动，则交由导演按常识推演。

项目地图见 [`CLAUDE.md`](./CLAUDE.md)。
