# backend/
> L2 | 父级: /CLAUDE.md

FastAPI 服务的工程根：依赖声明、配置模板、测试装置。业务代码全部位于 app/ 包内，cwd 须为 backend/（`app.main:app` 与 pytest 的 pythonpath 均以此为根）。

成员清单
requirements.txt: 运行依赖，fastapi + uvicorn[standard] + pydantic v2 + pydantic-settings + httpx2（大模型 HTTP 调用与 TestClient 共用）
requirements-dev.txt: 开发依赖，在运行依赖之上叠加 pytest
pytest.ini: 测试配置，pythonpath=. 使 `import app` 生效，testpaths=tests
.env.example: 配置模板，列出 LLM_PROVIDER / LLM_API_KEY / LLM_MODEL / LLM_THINKING_LEVEL 等全部可调项及 gemini / anthropic / openai 兼容示例；真实密钥写入同目录 .env（已 gitignore）
app/: 应用包（协议、会话、路由、组合根），地图见 app/CLAUDE.md
tests/conftest.py: 公共装置，ScriptedLLM 剧本替身 + alive_reply(scene, present) 报文工厂 + store / director / client 夹具，经 create_app(director=...) 注入
tests/test_api.py: 集成用例，覆盖开局、推演、必死封印、叙述隐名时靠 present 判在场、永久死亡 409、冷启动恢复、服务端权威、重试与 502、422 校验
tests/test_lethal.py: 致死预判单测，守护"敌意 ∧ 点名 ∧ 在场 ∧ 无绝学"四要素与无害复合词剔除
tests/test_parser.py: 解析闸门单测，围栏/寒暄剥离、存活必有选项、死亡可无选项、散文拒收
tests/test_llm_clients.py: 厂商客户端单测，替换 post_json 断言报文形状（Gemini 的 schema/思考档位/思考片段过滤/拒答），以及缺凭证启动即失败

运行
  python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
  .venv/bin/uvicorn app.main:app --reload --port 8000
  .venv/bin/pytest -q

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
