# backend/
> L2 | 父级: /CLAUDE.md

FastAPI 服务的工程根：依赖声明、配置模板、测试装置。业务代码全部位于 app/ 包内，cwd 须为 backend/（`app.main:app` 与 pytest 的 pythonpath 均以此为根）。

成员清单
requirements.txt: 运行依赖，fastapi + uvicorn[standard] + pydantic v2 + pydantic-settings + httpx2（大模型 HTTP 调用与 TestClient 共用）
requirements-dev.txt: 开发依赖，在运行依赖之上叠加 pytest
pytest.ini: 测试配置，pythonpath=. 使 `import app` 生效，testpaths=tests
.env.example: 配置模板，列出 LLM_PROVIDER / LLM_API_KEY / LLM_MODEL / LLM_THINKING_LEVEL 等全部可调项及 gemini / anthropic / openai 兼容示例；真实密钥写入同目录 .env（已 gitignore）
app/: 应用包（协议、会话、路由、组合根），地图见 app/CLAUDE.md
tests/conftest.py: 公共装置，ScriptedLLM 剧本替身 + alive_reply(scene, present, player_delta, world_delta, **快照字段) 报文工厂 + game 状态树 / store / director / client 夹具，经 create_app(director=...) 注入
tests/test_api.py: 集成用例，覆盖开局、推演、必死封印、叙述隐名时靠 present 判在场、永久死亡 409、整树冷启动（含世界台账）、伪造武学被服务端否决、重试与 502、422 校验（含旧版扁平状态被拒）
tests/test_ledger.py: 记账用例，reconcile 标签账（遗漏≠失去、先减后加、模糊匹配多义不动、去重）+ chronicle 世界台账（只增不删、不必要的合并被忽略、合并须点名现存条目、超限折叠）+ 管线集成（四本账参数化多回合不提仍在、快照走私被忽略、解毒/拜帮/烧楼一回合三账、状态栏缺省值与完整格式、开局与整树冷启动、Mock 走两本账）
tests/test_lethal.py: 致死预判单测，守护"敌意 ∧ 点名 ∧ 在场 ∧ 无绝学（读 martial_arts）"四要素与无害复合词剔除
tests/test_parser.py: 解析闸门单测，围栏/寒暄剥离、存活必有选项、死亡可无选项、散文拒收
tests/test_llm_clients.py: 厂商客户端单测，替换 post_json 断言报文形状（Gemini 的 schema/思考档位/思考片段过滤/拒答），以及缺凭证启动即失败

运行
  python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
  .venv/bin/uvicorn app.main:app --reload --port 8000
  .venv/bin/pytest -q

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
