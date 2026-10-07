# backend/
> L2 | 父级: /CLAUDE.md

FastAPI 服务的工程根：依赖声明、配置模板、测试装置。业务代码全部位于 app/ 包内，cwd 须为 backend/（`app.main:app` 与 pytest 的 pythonpath 均以此为根）。

成员清单
requirements.txt: 运行依赖，fastapi + uvicorn[standard] + pydantic v2 + pydantic-settings + httpx2（大模型 HTTP 调用与 TestClient 共用）
requirements-dev.txt: 开发依赖，在运行依赖之上叠加 pytest
pytest.ini: 测试配置，pythonpath=. 使 `import app` 生效，testpaths=tests
.env.example: 配置模板，列出 LLM_PROVIDER / LLM_API_KEY / LLM_MODEL / LLM_THINKING_LEVEL / HISTORY_TURNS / MEMORY_LIMIT 等全部可调项及 gemini / anthropic / openai 兼容示例；真实密钥写入同目录 .env（已 gitignore）
app/: 应用包（协议、会话、路由、组合根），地图见 app/CLAUDE.md
tests/conftest.py: 公共装置，ScriptedLLM 剧本替身 + alive_reply(scene, arrived, departed, player_delta, major_events, **快照字段) 报文工厂 + game 状态树 / store / director / client 夹具，经 create_app(director=...) 注入
tests/test_api.py: 集成用例，覆盖开局、推演、必死封印、叙述隐名时靠局部环境 present_npcs 判在场（种子点名的高手同样在场）、永久死亡 409、整树冷启动（含带标签的世界台账）、伪造武学被服务端否决、重试与 502、422 校验（含旧版扁平状态与旧版字符串台账被拒）
tests/test_ledger.py: 记账用例，reconcile 五本账（四本标签账 + secrets；遗漏≠失去、先减后加、模糊匹配多义不动、去重）+ chronicle 世界台账（只追加、复述去重、不设上限不合并、大模型无法借 next_state 抹掉旧事）+ 管线集成（五本账多回合不提仍在、快照走私被忽略、解毒/拜帮/烧楼一回合三账、状态栏格式、开局与整树冷启动、Mock 写出带地点标签的大事）
tests/test_memory.py: JIT 动态记忆用例，记忆过滤层（三路相互包含双向命中、动作点名单向第四路、单字不命中、保序、条数封顶）+ 管线接线（经 Director 只注入召回事件、"丐帮弟子"端到端召回、抵达回合即见目的地历史）+ 局部环境（同图只认增减、切换地图强制清空、按身份与修饰称呼认人、满员高手优先、开局种子高手不惧地点漂移）+ 载荷恒定与注入防护（台账 0→500 条 Prompt 一字不变、窗口封顶且成对保留动作与场景、任何段落无法从内部闭合、历史逐行 JSON 往返去重）+ 契约边界（单条大事上限、每回合超额截断而非 502、配置钉死范围）
tests/test_fog.py: 情报隔离与被动沙盒用例，System Prompt 逐字含用户规定的铁律与克制原则且位于记账规则之前、<secrets> 与 <player_state> 结构分离且逐行 JSON 不可伪造不可闭合、回合指令带落笔前自查、secrets 从不作 JIT 检索键（当众打听照常召回）、内心念头不触发点名检索、私密情报进 secrets 不进世界台账不上状态栏（双写时私密优先、同名公开事实照记）、情报账按包含去重且满员请走最早的（经管线端到端验证）、大模型情报增减截断不 502、当众揭穿时移出、next_state 走私被忽略、契约边界、冷启动保留 secrets 且旧客户端兼容、Mock"偷听"走通私密账、放火只记旁观者视角
tests/test_lethal.py: 致死预判单测，守护"敌意 ∧ 点名 ∧ 在场 ∧ 无绝学（读 martial_arts）"四要素与无害复合词剔除；情报隔离：内心念头不是冒犯、念头之后的当面冒犯照杀、点名只算表面行为、"暗自"出手仍看得见
tests/test_parser.py: 解析闸门单测，围栏/寒暄剥离、存活必有选项、死亡可无选项、散文拒收
tests/test_llm_clients.py: 厂商客户端单测，替换 post_json 断言报文形状（Gemini 的 schema/思考档位/思考片段过滤/拒答），以及缺凭证启动即失败

运行
  python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
  .venv/bin/uvicorn app.main:app --reload --port 8000
  .venv/bin/pytest -q

[PROTOCOL]: 变更时更新此头部，然后检查 CLAUDE.md
