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

复制 `backend/.env.example` 为 `backend/.env`，任选其一：

```ini
# Anthropic
LLM_PROVIDER=anthropic
LLM_API_KEY=sk-ant-...
LLM_MODEL=claude-sonnet-5-5

# OpenAI 兼容协议（OpenAI / DeepSeek / 通义 / Gemini / Ollama）
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

## 试试作死

开局若见乔峰、扫地老僧或丁春秋在场，输入「掀翻乔峰的酒桌」「一脚踢翻扫地老僧的扫帚」「朝丁春秋吐口水」之类——规则层会在大模型之前判你死刑。

项目地图见 [`CLAUDE.md`](./CLAUDE.md)。
