# 天龙八部：平行世界 - 导演大模型驱动的极简文本武侠沙盒，语义标签无数值，作死即永久死亡
Python 3.10+ + FastAPI + Pydantic v2 + pydantic-settings + httpx2 | React 19 + TypeScript 7 + Vite 8 + Tailwind CSS v4

<directory>
backend/ - FastAPI 服务：前后端协议、内存会话、导演管线、大模型适配 (3子目录: app/director 导演管线, app/llm 大模型适配, tests 用例)
frontend/ - React SPA：三段式沉浸 UI、打字机叙事、死亡锁死 (3子目录: src/api 后端门面, src/hooks 状态机与打字机, src/components 视图)
</directory>

<config>
backend/requirements.txt - 运行依赖（fastapi / uvicorn / pydantic-settings / httpx2）
backend/.env.example - 大模型与会话配置模板，复制为 backend/.env 生效；默认 mock 零密钥可跑
frontend/package.json - 前端依赖与脚本（dev / dev:mock / build）
frontend/vite.config.ts - Vite 插件与 /api → :8000 开发代理
</config>

<architecture>
一回合数据流：
  ActionPanel → useGame.act → POST /api/interact → Director.interact
    → Session.acting()   守卫：死者不得行动、上一招未落定不得出下一招
    → lethal.judge()     规则层裁定生死（无绝学 ∧ 敌意 ∧ 点名 ∧ 在场 → 必死）
    → prompts.build_*()  XML 标签组装 User Message，必死时重写为处决指令
    → LLMClient          纯文本进出（mock / openai 兼容 / anthropic）
    → parser             截取 JSON + Pydantic 校验，失败重采样
    → 生死封印            规则判死则强制 game_over，大模型无权赦免
    → Session.advance()  服务端状态唯一权威
  ← InteractResponse（ui_status_bar 由服务端从 next_state 确定性渲染）→ 打字机 → 选项浮现

关键决策：
- 服务端权威：请求里的 current_state 仅用于会话丢失时冷启动恢复，不能覆盖服务端状态（防篡改）
- 生死归规则、叙事归模型：确定性规则先裁决，大模型只负责叙述，永久死亡由服务端 409 守住
- 短期叙事记忆：会话保留最近 N 回合场景原文喂给导演，四个状态字段之外的涌现细节由此延续
- 协议单一来源：backend/app/schemas.py 定义形状，frontend/src/types.ts 逐字段镜像
</architecture>

法则: 极简·稳定·导航·版本精确
